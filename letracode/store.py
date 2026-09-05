"""Local SQLite persistence. Connections are short-lived and safe across workers."""
from __future__ import annotations

import json
import os
import sqlite3
import stat
import tempfile
import uuid
import zipfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .strand import StrandFiles, digest, safe_directory, safe_read, safe_write


def data_home() -> Path:
    return Path(os.environ.get('XDG_DATA_HOME', str(Path.home() / '.local/share'))) / 'letracode'


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


class Store:
    def __init__(self, directory: Path | None = None):
        self.directory = Path(directory or data_home()).absolute()
        with safe_directory(self.directory, create=True) as fd:
            os.fchmod(fd, 0o700)
        self.path = self.directory / 'letracode.sqlite3'
        if self.path.is_symlink() or (self.path.exists() and self.path.stat().st_nlink != 1):
            raise ValueError('Unsafe linked database')
        with self.connection() as db:
            version = db.execute('PRAGMA user_version').fetchone()[0]
            if version > 2:
                raise RuntimeError('This database belongs to a newer LetraCode. Please upgrade the app.')
            if version == 0:
                db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL,
                    memory TEXT NOT NULL DEFAULT '', current_context TEXT NOT NULL DEFAULT '',
                    instructions TEXT NOT NULL DEFAULT '', created TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS chats (
                    id TEXT PRIMARY KEY, project_id TEXT REFERENCES projects(id) ON DELETE CASCADE,
                    title TEXT NOT NULL, draft TEXT NOT NULL DEFAULT '', created TEXT NOT NULL,
                    updated TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
                    role TEXT NOT NULL, content TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'complete',
                    payload TEXT NOT NULL DEFAULT '{}', created TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS messages_chat ON messages(chat_id, id);
                CREATE TABLE IF NOT EXISTS links (
                    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    path TEXT NOT NULL, PRIMARY KEY(project_id, path));
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                PRAGMA user_version=2;
            ''')
        self.path.chmod(0o600)
        if version == 1:
            self._migrate_memory()
        else:
            self.strand = StrandFiles(self.directory / 'strand')

    def _migration_backup(self):
        directory = self.directory / 'migration-backups'
        with safe_directory(directory, create=True):
            pass
        destination = directory / f'v1-{uuid.uuid4().hex}.sqlite3'
        safe_write(destination, b'', None)
        with self.connection() as source:
            target = sqlite3.connect(destination)
            try:
                source.backup(target)
            finally:
                target.close()
        with destination.open('rb') as saved:
            os.fsync(saved.fileno())
        with safe_directory(directory) as fd:
            os.fsync(fd)

    def _migrate_memory(self):
        changes = []
        with self.connection() as db:
            # Lock legacy writers before backing up or reading their values.
            db.execute('BEGIN IMMEDIATE')
            version = db.execute('PRAGMA user_version').fetchone()[0]
            if version == 2:
                self.strand = StrandFiles(self.directory / 'strand')
                return
            if version != 1:
                raise RuntimeError('Database version changed before memory migration')
            self._migration_backup()
            self.strand = StrandFiles(self.directory / 'strand')
            rows = db.execute('SELECT id,memory FROM projects').fetchall()
            # Check every destination before moving any legacy text. Matching
            # files permit safe recovery after a crash preceding SQLite commit.
            for row in rows:
                path = self.strand.path('project', row['id'])
                old = safe_read(path)
                text = row['memory'].encode('utf-8')
                if old is not None and text and old != text:
                    raise ValueError(f'Memory migration conflict: {path}')
                if old is None:
                    changes.append((path, text))
            created = []
            try:
                for path, content in changes:
                    safe_write(path, content, None)
                    created.append((path, content))
                db.execute("UPDATE projects SET memory=''")
                db.execute('PRAGMA user_version=2')
                db.commit()
            except BaseException:
                db.rollback()
                for path, content in reversed(created):
                    # Never roll back over a user edit made during migration.
                    with safe_directory(path.parent) as fd:
                        if safe_read(path) == content:
                            os.unlink(path.name, dir_fd=fd)
                raise

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:
                yield db
        finally:
            db.close()

    def rows(self, sql, args=()):
        with self.connection() as db:
            return [dict(row) for row in db.execute(sql, args)]

    def create_project(self, title: str) -> str:
        ident = uuid.uuid4().hex
        self.strand.ensure('project', ident)
        with self.connection() as db:
            db.execute('INSERT INTO projects(id,title,created) VALUES (?,?,?)', (ident, title.strip() or 'Untitled project', now()))
        return ident

    def projects(self):
        # Navigation must remain usable when any individual memory file fails.
        return self.rows('SELECT id,title,current_context,instructions,created FROM projects ORDER BY lower(title),created')

    def project(self, ident):
        rows = self.rows('SELECT * FROM projects WHERE id=?', (ident,))
        if rows:
            try:
                rows[0]['memory'] = self.strand.snapshot('project', ident)['text']
            except (OSError, ValueError) as error:
                rows[0]['memory'] = ''
                rows[0]['memory_error'] = str(error)
        return rows[0] if rows else None

    def update_project(self, ident, **fields):
        allowed = {'title', 'memory', 'current_context', 'instructions'}
        if not fields or not set(fields) <= allowed:
            raise ValueError('Invalid project field')
        if not self.rows('SELECT id FROM projects WHERE id=?', (ident,)):
            return
        if 'memory' in fields:
            snapshot = self.strand.snapshot('project', ident)
            self.strand.replace('project', fields.pop('memory'), snapshot['sha256'], ident, origin='user:project-editor')
        if not fields:
            return
        with self.connection() as db:
            db.execute('UPDATE projects SET ' + ','.join(f'{k}=?' for k in fields) + ' WHERE id=?', (*fields.values(), ident))

    def delete_project(self, ident):
        """Archive the actual memory inode before deleting database ownership.

        A crash between the archive move and SQLite commit leaves the project
        present with unavailable memory and a prepared recovery record. Never
        recreate or overwrite that memory automatically.
        """
        from .strand import rename_noreplace

        with self.strand._operation(), self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT id,title FROM projects WHERE id=?', (ident,)).fetchone()
            if row is None:
                return None
            archive = self._prepare_project_archive(row)
            moved = False
            try:
                if archive is not None:
                    original, destination = Path(archive['original_path']), Path(archive['path'])
                    with safe_directory(original.parent) as source, safe_directory(destination.parent) as target:
                        rename_noreplace(source, original.name, target, destination.name)
                        moved = True
                        os.fsync(source)
                        os.fsync(target)
                        info = os.stat(destination.name, dir_fd=target, follow_symlinks=False)
                        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                            raise ValueError('Unsafe linked project memory changed during deletion')
                        try:
                            os.stat(original.name, dir_fd=source, follow_symlinks=False)
                        except FileNotFoundError:
                            pass
                        else:
                            raise ValueError('Project memory changed during deletion')
                db.execute('DELETE FROM projects WHERE id=?', (ident,))
                db.commit()
            except BaseException as error:
                db.rollback()
                if moved:
                    try:
                        with safe_directory(destination.parent) as source, safe_directory(original.parent) as target:
                            rename_noreplace(source, destination.name, target, original.name)
                            os.fsync(source)
                            os.fsync(target)
                    except (OSError, ValueError) as recovery_error:
                        self._finish_project_archive(archive, 'recovery_required')
                        raise ValueError(
                            f'Project deletion failed. Memory is preserved at {destination}; '
                            'the current memory path was not overwritten. Recover the archive before retrying.'
                        ) from recovery_error
                    self._finish_project_archive(archive, 'restored')
                raise error
            if archive is not None:
                self._finish_project_archive(archive, 'deleted')
            return archive

    def _prepare_project_archive(self, project):
        from .strand import recover_file

        original = self.strand.path('project', project['id'])
        try:
            # Resolve any interrupted save before taking away database ownership;
            # later receipt reads must not resurrect its old authoritative name.
            # Recovery alone does not decode or size-limit the current raw file.
            recover_file(original)
            with safe_directory(original.parent) as directory:
                try:
                    info = os.stat(original.name, dir_fd=directory, follow_symlinks=False)
                except FileNotFoundError:
                    return None
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise ValueError('Unsafe linked project memory cannot be deleted')
        except ValueError as error:
            if isinstance(error.__cause__, FileNotFoundError):
                return None
            raise
        directory = self.strand.root / '.deleted-projects' / project['id']
        with safe_directory(directory, create=True):
            pass
        archive_id = uuid.uuid4().hex
        record = {'project_id': project['id'], 'title': project['title'], 'date': now(),
                  'path': str(directory / f'{archive_id}.md'), 'original_path': str(original),
                  'record_path': str(directory / f'{archive_id}.json'), 'status': 'prepared'}
        # This durable record exists before removing the authoritative name.
        safe_write(Path(record['record_path']), json.dumps(record, ensure_ascii=False, indent=2).encode('utf-8'), None)
        return record

    def _finish_project_archive(self, record, status):
        record['status'] = status
        path = Path(record['record_path'])
        try:
            previous = safe_read(path)
            if previous is None:
                raise ValueError('Deletion recovery record is missing')
            safe_write(path, json.dumps(record, ensure_ascii=False, indent=2).encode('utf-8'), digest(previous))
        except (OSError, ValueError) as error:
            # SQLite/file changes already completed. Keep the durable prepared
            # record and report its location without pretending deletion failed.
            record['warning'] = f'Recovery record could not be finalized: {error}. Inspect {path}.'

    def create_chat(self, title='New chat', project_id=None):
        ident = uuid.uuid4().hex
        with self.connection() as db:
            db.execute('INSERT INTO chats(id,project_id,title,created,updated) VALUES (?,?,?,?,?)', (ident, project_id, title.strip() or 'New chat', now(), now()))
        return ident

    def chats(self, query=''):
        if query:
            return self.rows('''SELECT * FROM chats WHERE instr(lower(title),lower(?))>0 OR id IN
                (SELECT chat_id FROM messages WHERE instr(lower(content),lower(?))>0) ORDER BY updated DESC,created DESC''', (query, query))
        return self.rows('SELECT * FROM chats ORDER BY updated DESC,created DESC')

    def chat(self, ident):
        rows = self.rows('SELECT * FROM chats WHERE id=?', (ident,))
        return rows[0] if rows else None

    def rename_chat(self, ident, title):
        with self.connection() as db:
            db.execute('UPDATE chats SET title=?,updated=? WHERE id=?', (title.strip() or 'Untitled chat', now(), ident))

    def set_draft(self, ident, text):
        with self.connection() as db:
            db.execute('UPDATE chats SET draft=? WHERE id=?', (text, ident))

    def delete_chat(self, ident):
        with self.connection() as db:
            db.execute('DELETE FROM chats WHERE id=?', (ident,))

    def messages(self, chat_id):
        return self.rows('SELECT * FROM messages WHERE chat_id=? ORDER BY id', (chat_id,))

    def tool_result_page(self, chat_id, result_id, offset=0, max_chars=4000):
        if type(offset) is not int or offset < 0 or type(max_chars) is not int or not 1 <= max_chars <= 16000:
            raise ValueError('Invalid saved result page bounds')
        rows = self.rows("SELECT * FROM messages WHERE id=? AND chat_id=? AND role='tool'", (result_id, chat_id))
        if not rows:
            raise ValueError('Saved tool result not found in this chat')
        message = json.loads(rows[0]['payload']).get('message', {})
        content = message.get('content', rows[0]['content'])
        if not isinstance(content, str):
            content = json.dumps(content, ensure_ascii=False)
        end = min(len(content), offset + max_chars)
        return {'result_id': rows[0]['id'], 'name': message.get('name', ''),
                'tool_call_id': message.get('tool_call_id', ''), 'content': content[offset:end],
                'offset': offset, 'next_offset': end if end < len(content) else None,
                'total_chars': len(content), 'sha256': digest(content.encode('utf-8'))}

    def add_message(self, chat_id, role, content, status='complete', payload=None):
        with self.connection() as db:
            cursor = db.execute('INSERT INTO messages(chat_id,role,content,status,payload,created) VALUES (?,?,?,?,?,?)',
                (chat_id, role, content, status, json.dumps(payload or {}, ensure_ascii=False), now()))
            db.execute('UPDATE chats SET updated=? WHERE id=?', (now(), chat_id))
            return cursor.lastrowid

    def update_message(self, ident, content, status='complete', payload=None):
        with self.connection() as db:
            if payload is None:
                db.execute('UPDATE messages SET content=?,status=? WHERE id=?', (content, status, ident))
            else:
                db.execute('UPDATE messages SET content=?,status=?,payload=? WHERE id=?', (content, status, json.dumps(payload, ensure_ascii=False), ident))

    def recover_interrupted(self):
        with self.connection() as db:
            db.execute("UPDATE messages SET status='interrupted' WHERE status='streaming'")

    def link(self, project_id, path):
        # Preserve selected absolute path; permission checks always resolve afresh.
        path = str(Path(path).expanduser().absolute())
        with self.connection() as db:
            db.execute('INSERT OR IGNORE INTO links VALUES (?,?)', (project_id, path))

    def unlink(self, project_id, path):
        with self.connection() as db:
            db.execute('DELETE FROM links WHERE project_id=? AND path=?', (project_id, path))

    def links(self, project_id):
        return [r['path'] for r in self.rows('SELECT path FROM links WHERE project_id=? ORDER BY path', (project_id,))]

    def setting(self, key, default=None):
        rows = self.rows('SELECT value FROM settings WHERE key=?', (key,))
        return json.loads(rows[0]['value']) if rows else default

    def set_setting(self, key, value):
        with self.connection() as db:
            db.execute('INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, json.dumps(value, ensure_ascii=False)))

    def export_markdown(self, chat_id):
        chat = self.chat(chat_id)
        chunks = [f"# {chat['title']}\n"]
        for m in self.messages(chat_id):
            title = {'user':'You', 'assistant':'LetraCode', 'tool':'Action', 'notice':'Notice'}.get(m['role'], m['role'])
            suffix = '' if m['status'] == 'complete' else f" · {m['status']}"
            chunks.append(f"## {title}{suffix}\n\n{m['content']}\n")
        return '\n'.join(chunks)

    def backup(self, destination: Path):
        destination = Path(destination)
        with tempfile.TemporaryDirectory(dir=self.directory) as scratch:
            copy = Path(scratch) / 'letracode.sqlite3'
            with self.connection() as source:
                target = sqlite3.connect(copy)
                try:
                    source.backup(target)
                finally:
                    target.close()
            db = sqlite3.connect(copy)
            db.row_factory = sqlite3.Row
            try:
                exported = {table: [dict(r) for r in db.execute(f'SELECT * FROM {table}')]
                    for table in ('projects','chats','messages','links','settings')}
            finally:
                db.close()
            stage = Path(scratch) / 'backup.zip'
            with zipfile.ZipFile(stage, 'w', zipfile.ZIP_DEFLATED) as archive:
                archive.write(copy, 'letracode.sqlite3')
                archive.writestr('letracode.json', json.dumps(exported, ensure_ascii=False, indent=2))
                for relative, content in self.strand.backup_entries():
                    archive.writestr('strand/' + relative, content)
                archive.writestr('RESTORE.txt',
                    'Close LetraCode. Keep a copy of the current data folder. Extract the complete archive, '
                    'including strand/ and its hidden .history/ and .receipts/ directories, into a NEW empty data folder.\n'
                    'Before opening a restored database, remove or retarget linked source roots and any writable '
                    'destinations to isolated test locations. A copied database retains the original links and settings. '
                    'For a safe test, use sqlite3 /new/folder/letracode.sqlite3 "DELETE FROM links;" and review settings '
                    'before launch. Never point a restored test at the original sources.\n'
                    'Then run letracode --data-dir /new/folder. Ordinary Strand files remain editable there; '
                    'Undo uses the restored private history. Linked originals and GGUF model weights are excluded. '
                    'Safe ordinary Strand manifests are included. The JSON export is human-readable; its legacy '
                    'project memory column is empty because strand/memory/ is authoritative.\n')
            # Stage alongside destination to keep replacing an existing backup atomic.
            fd, name = tempfile.mkstemp(prefix='.letracode-backup-', dir=destination.parent)
            try:
                with os.fdopen(fd, 'wb') as out, stage.open('rb') as incoming:
                    import shutil
                    shutil.copyfileobj(incoming, out)
                    out.flush(); os.fsync(out.fileno())
                os.replace(name, destination)
            finally:
                Path(name).unlink(missing_ok=True)
