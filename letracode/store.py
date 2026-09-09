"""Local SQLite persistence. Connections are short-lived and safe across workers."""
from __future__ import annotations

import json
import copy
import os

from . import filesystem as fs
import sqlite3
import stat
import tempfile
import uuid
import zipfile
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .continuation import interrupted_outcome
from .memory import MemoryFiles
from .strand import BACKUP_CHUNK_BYTES, MAX_FILE_BYTES, StrandFiles, backup_tree, digest, safe_directory, safe_read, safe_write


def data_home() -> Path:
    if fs.IS_WINDOWS:
        return Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData' / 'Local') / 'LetraCode'
    return Path(os.environ.get('XDG_DATA_HOME', str(Path.home() / '.local/share'))) / 'letracode'


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def message_status(message):
    """Share application outcome labels between transcript and Markdown export.

    A complete SQLite row means storage succeeded, not that the task did.
    Assistant prose never supplies verification or action status.
    """
    try:
        data = json.loads(message.get('payload') or '{}')
        data = data if isinstance(data, dict) else {}
        reply = data.get('message') or {}
        reply = reply if isinstance(reply, dict) else {}
    except (TypeError, ValueError):
        data, reply = {}, {}
    status = message['status']
    if message['role'] == 'assistant':
        if data.get('task_outcome') == 'source_incomplete':
            return 'Provisional response · Source coverage incomplete · Task outcome unverified'
        if status == 'complete' and not reply.get('tool_calls'):
            return 'Response saved · Task outcome unverified'
    if message['role'] == 'tool':
        if status != 'complete':
            return 'Outcome unknown · ' + status
        try:
            result = json.loads(reply.get('content', ''))
        except (TypeError, ValueError):
            return 'Outcome unknown'
        if not isinstance(result, dict):
            return 'Outcome unknown'
        if 'denied' in result:
            return 'Denied'
        if result.get('executed') is False:
            return 'Not executed'
        if interrupted_outcome(result):
            return 'Interrupted · effects require review'
        if reply.get('name') == 'run_command' and 'error' not in result and type(result.get('exit_code')) is not int:
            return 'Outcome unknown'
        if 'error' in result or result.get('exit_code', 0) != 0:
            return 'Failed'
        label = 'Executed successfully'
        if reply.get('name') in ('read_file', 'search_project'):
            from .evidence import source_evidence
            try:
                sources = source_evidence(reply['name'], result)
                if not sources or sources != data.get('source_evidence'):
                    return label + ' · Source coverage untracked'
                partial = any(source['source_truncated'] or
                    sum(end - start for start, end in source['ranges']) < source['total_chars']
                    for source in sources)
                label += ' · Source coverage partial' if partial else ' · Supported source text retrieved'
                label += ' · Retrieval alone does not verify model exposure'
            except (TypeError, ValueError):
                label += ' · Source coverage untracked'
        return label
    return '' if status == 'complete' else status


class Store:
    def __init__(self, directory: Path | None = None):
        self.directory = Path(directory or data_home()).absolute()
        with safe_directory(self.directory, create=True) as fd:
            fs.fchmod(fd, 0o700)
        self.path = self.directory / 'letracode.sqlite3'
        if self.path.is_symlink() or (self.path.exists() and self.path.stat().st_nlink != 1):
            raise ValueError('Unsafe linked database')
        initializing = self.directory / 'memory-initialization.json'
        if not self.path.exists() and not os.path.lexists(initializing):
            safe_write(initializing, json.dumps({'version': 1, 'status': 'prepared'}).encode(), None)
        with self.connection() as db:
            version = db.execute('PRAGMA user_version').fetchone()[0]
            if version > 3:
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
        self._migrate_memory_tree(version)
        self.strand = self.memory

    def _migrate_memory_tree(self, initial_version):
        old, target = self.directory / 'strand', self.directory / 'Memory'
        marker = self.directory / 'memory-tree-migration.json'
        initialization = safe_read(self.directory / 'memory-initialization.json', 4096)
        creating = initialization is not None and json.loads(initialization).get('status') == 'prepared'
        if not os.path.lexists(old) and not os.path.lexists(target) and creating:
            if safe_read(marker, 4096) is None:
                safe_write(marker, json.dumps({'version': 1, 'source': 'strand', 'destination': 'Memory',
                    'legacy': False, 'status': 'prepared'}).encode(), None)
            with safe_directory(target, create=True):
                pass
        root = old if os.path.lexists(old) else target
        if not os.path.lexists(root):
            raise ValueError('Memory directory is missing; restore it before opening. No defaults were created.')
        # Retain and lock the existing root descriptor without initializing
        # Strand defaults. The whole-directory rename carries this same lock
        # inode; existing cooperating saves finish before migration proceeds.
        with safe_directory(root, allow_move=True) as directory:
            # Windows hands this owned handle through an exclusive directory
            # barrier during the root rename, retaining the same lock inode.
            lock = [fs.open('.write-lock', os.O_RDWR | os.O_CREAT | fs.O_NOFOLLOW | fs.O_NONBLOCK,
                            0o600, dir_fd=directory)]
            try:
                info = fs.fstat(lock[0])
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise ValueError('Unsafe linked Memory migration lock')
                fs.flock(lock[0], fs.LOCK_EX)
                # Lock order matches normal backup/deletion: Memory, SQLite.
                with self.connection() as db:
                    db.execute('BEGIN IMMEDIATE')
                    current = db.execute('PRAGMA user_version').fetchone()[0]
                    effective = 3 if current == 3 else initial_version
                    self._migrate_memory_tree_locked(effective, db, fs.fstat(directory), lock)
            finally:
                if lock[0] is not None:
                    fs.close(lock[0])

    def _migrate_memory_tree_locked(self, initial_version, migration_db, locked_root, migration_lock):
        from .strand import rename_noreplace
        old, target = self.directory / 'strand', self.directory / 'Memory'
        marker = self.directory / 'memory-tree-migration.json'
        if initial_version == 3:
            if not os.path.lexists(target):
                raise ValueError('Memory directory is missing; restore it from backup. No defaults were created.')
            self.memory = MemoryFiles(target, initialize=False, migration_locked=True)
            return
        old_exists, target_exists = os.path.lexists(old), os.path.lexists(target)
        if old_exists and target_exists:
            raise ValueError('Memory migration conflict: both strand and Memory directories exist. Neither was overwritten.')
        prior = safe_read(marker, 4096)
        if target_exists and prior is None:
            raise ValueError('Memory migration conflict: destination Memory already exists. Existing files are preserved.')
        initialization = safe_read(self.directory / 'memory-initialization.json', 4096)
        is_initializing = initialization is not None and json.loads(initialization).get('status') == 'prepared'
        if not old_exists and not target_exists and initial_version != 0 and not is_initializing:
            raise ValueError('Legacy Strand memory is missing. Restore it before migration; no defaults were created.')
        if prior is None:
            if initial_version not in (0, 1):
                self._migration_backup()
            record = {'version': 1, 'source': 'strand', 'destination': 'Memory',
                      'legacy': old_exists, 'status': 'prepared'}
            safe_write(marker, json.dumps(record).encode(), None)
        else:
            record = json.loads(prior)
            if record.get('version') != 1 or record.get('destination') != 'Memory':
                raise ValueError('Memory migration record is invalid; existing files are preserved')
        if old_exists:
            with safe_directory(self.directory) as directory:
                # Move the entire ordinary tree, including opaque history,
                # recovery inodes, receipts, unknown files and deleted projects.
                with safe_directory(old):
                    pass
                options = {'expected_identity': (locked_root.st_dev, locked_root.st_ino),
                           'migration_lock': migration_lock} if fs.IS_WINDOWS else {}
                rename_noreplace(directory, 'strand', directory, 'Memory', **options)
                fs.fsync(directory)
        self.memory = MemoryFiles(target, legacy=record['legacy'], migration_locked=True)
        migration_db.execute('PRAGMA user_version=3')
        migration_db.commit()
        previous = safe_read(marker, 4096)
        record['status'] = 'complete'
        safe_write(marker, json.dumps(record).encode(), digest(previous))
        if initialization is not None and is_initializing:
            safe_write(self.directory / 'memory-initialization.json', json.dumps({'version': 1, 'status': 'complete'}).encode(), digest(initialization))

    def _migration_backup(self):
        directory = self.directory / 'migration-backups'
        with safe_directory(directory, create=True):
            pass
        with self.connection() as db:
            version = db.execute('PRAGMA user_version').fetchone()[0]
        destination = directory / f'v{version}-{uuid.uuid4().hex}.sqlite3'
        safe_write(destination, b'', None)
        with self.connection() as source:
            target = sqlite3.connect(destination)
            try:
                source.backup(target)
            finally:
                target.close()
        with destination.open('r+b') as saved:
            fs.fsync(saved.fileno())
        with safe_directory(directory) as fd:
            fs.fsync(fd)

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
            try:
                for path, content in changes:
                    safe_write(path, content, None)
                db.execute("UPDATE projects SET memory=''")
                db.execute('PRAGMA user_version=2')
                db.commit()
            except BaseException:
                db.rollback()
                # Keep prepared files and their actual inodes. Checking bytes
                # then unlinking can lose intervening edits, including later
                # writes through an editor's already-open descriptor. Retry
                # validates these files above and reuses matching destinations;
                # the legacy values and backup remain intact until DB commit.
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

    def ensure_project_files(self, ident) -> Path:
        """Export the former context field once, retaining the SQLite original.

        Memory already lives in this tree. Use its durable create operation so
        the remaining legacy field gets the same history and backup protection.
        A failed marker commit can retry without replacing any existing file.
        """
        with self.memory._operation():
            rows = self.rows('SELECT current_context FROM projects WHERE id=?', (ident,))
            if not rows:
                raise ValueError('Unknown project')
            folder = self.memory.root_for(ident)
            key = 'project_files_migrated:' + ident
            if self.setting(key, False):
                return folder
            text = rows[0]['current_context']
            name = None
            if text:
                original = text.encode('utf-8')
                suffix = 0
                while True:
                    label = '' if suffix == 0 else ' (legacy)' if suffix == 1 else f' (legacy {suffix})'
                    name = f'Current Context{label}.md'
                    if os.path.lexists(folder / name):
                        # Only reuse safely readable identical bytes after an
                        # interrupted publication. Never overwrite a collision.
                        try:
                            if safe_read(folder / name, max_bytes=len(original)) == original:
                                break
                        except (OSError, ValueError, RuntimeError):
                            pass
                        suffix += 1
                        continue
                    try:
                        self.memory.create_file(name, text, ident, max_bytes=max(MAX_FILE_BYTES, len(original)))
                        break
                    except FileExistsError:
                        suffix += 1
            self.set_setting(key, {'current_context': name})
            return folder

    def project_for_context(self, ident):
        self.ensure_project_files(ident)
        project = self.project(ident)
        return {**project, 'current_context': ''} if project else None

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
            entries = self.memory.entries(ident)
            primary = self.strand.path('project', ident).relative_to(self.memory.root_for(ident)).as_posix()
            if any(entry['path'] != primary for entry in entries):
                return self._delete_project_tree(db, dict(row))
            archive = self._prepare_project_archive(row)
            moved = False
            registry_before = registry_after = None
            try:
                if archive is not None:
                    original, destination = Path(archive['original_path']), Path(archive['path'])
                    with safe_directory(original.parent) as source, safe_directory(destination.parent) as target:
                        rename_noreplace(source, original.name, target, destination.name)
                        moved = True
                        fs.fsync(source)
                        fs.fsync(target)
                        info = fs.stat(destination.name, dir_fd=target, follow_symlinks=False)
                        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                            raise ValueError('Unsafe linked project memory changed during deletion')
                        try:
                            fs.stat(original.name, dir_fd=source, follow_symlinks=False)
                        except FileNotFoundError:
                            pass
                        else:
                            raise ValueError('Project memory changed during deletion')
                registry, registry_raw = self.memory._metadata()
                updated = copy.deepcopy(registry)
                for entry in updated['files'].values():
                    if entry.get('project_id') == ident:
                        entry['deleted'] = True
                self.memory._save_metadata(updated, registry_raw)
                registry_before, registry_after = registry, updated
                db.execute('DELETE FROM projects WHERE id=?', (ident,))
                db.commit()
            except BaseException as error:
                db.rollback()
                if registry_before is not None:
                    current, current_raw = self.memory._metadata()
                    if current != registry_after:
                        raise ValueError('Project deletion failed and Memory metadata changed; retained files and registry need reconciliation.') from error
                    self.memory._save_metadata(registry_before, current_raw)
                if moved:
                    try:
                        with safe_directory(destination.parent) as source, safe_directory(original.parent) as target:
                            rename_noreplace(source, destination.name, target, original.name)
                            fs.fsync(source)
                            fs.fsync(target)
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

    def _delete_project_tree(self, db, project):
        ident = project['id']
        source = f'.projects/{ident}'
        target = '.trash/deleted-project-' + uuid.uuid4().hex
        before = self.memory._entry_digest(self.memory.root / source)
        receipt = self.memory._relocate(source, target, before, ident, 'delete')
        try:
            db.execute('DELETE FROM projects WHERE id=?', (ident,))
            db.commit()
        except BaseException as error:
            db.rollback()
            try:
                self.memory.undo(receipt['id'])
            except (OSError, ValueError) as recovery_error:
                raise ValueError(f'Project deletion failed. Memory is preserved at {self.memory.root / target}; '
                                 'the current memory path was not overwritten. Inspect its operation history before retrying.') from recovery_error
            raise error
        return {'project_id': ident, 'title': project['title'], 'date': now(),
                'path': str(self.memory.root / target), 'original_path': str(self.memory.root / source),
                'record_path': str(self.memory._operation_path(receipt['id'])), 'status': 'deleted',
                'operation_id': receipt['id']}

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
                    info = fs.stat(original.name, dir_fd=directory, follow_symlinks=False)
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

    def export_evaluation(self, chat_id, destination, notes=''):
        from .evaluation import export_evaluation
        return export_evaluation(self, chat_id, destination, notes)

    def export_markdown(self, chat_id):
        chat = self.chat(chat_id)
        chunks = [f"# {chat['title']}\n"]
        for m in self.messages(chat_id):
            title = {'user':'You', 'assistant':'LetraCode', 'tool':'Action', 'notice':'Notice'}.get(m['role'], m['role'])
            if m['role'] == 'assistant':
                try:
                    data = json.loads(m.get('payload') or '{}')
                    speaker = data.get('speaker') if isinstance(data, dict) else None
                    if isinstance(speaker, dict) and isinstance(speaker.get('label'), str):
                        title = speaker['label'] or title
                except (TypeError, ValueError):
                    pass
            status = message_status(m)
            suffix = f' · {status}' if status else ''
            chunks.append(f"## {title}{suffix}\n\n{m['content']}\n")
        return '\n'.join(chunks)

    def backup(self, destination: Path):
        destination = Path(destination)
        def copy_entries(archive, prefix, entries):
            for relative, incoming in entries:
                remaining = fs.fstat(incoming.fileno()).st_size
                with archive.open(prefix + relative, 'w', force_zip64=True) as out:
                    while remaining:
                        chunk = incoming.read(min(remaining, BACKUP_CHUNK_BYTES))
                        if not chunk:
                            raise ValueError(f'File shrank during backup: {relative}')
                        out.write(chunk)
                        remaining -= len(chunk)
                    if incoming.read(1):
                        raise ValueError(f'File grew during backup: {relative}')

        with tempfile.TemporaryDirectory(dir=self.directory) as scratch:
            copy = Path(scratch) / 'letracode.sqlite3'
            stage = Path(scratch) / 'backup.zip'
            # Freeze cooperating memory saves/deletions before copying SQLite.
            # No SQLite transaction remains open while waiting on a writer,
            # generating model output or displaying a user approval dialog.
            with self.strand.backup_entries() as entries:
                with self.connection() as source:
                    target = sqlite3.connect(copy)
                    try:
                        source.backup(target)
                    finally:
                        target.close()
                db = sqlite3.connect(copy)
                db.row_factory = sqlite3.Row
                try:
                    tables = ['projects','chats','messages','links','settings']
                    available = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                    tables.extend(t for t in ('training_examples', 'training_runs') if t in available)
                    exported = {table: [dict(r) for r in db.execute(f'SELECT * FROM {table}')]
                        for table in tables}
                finally:
                    db.close()
                with zipfile.ZipFile(stage, 'w', zipfile.ZIP_DEFLATED) as archive:
                    archive.write(copy, 'letracode.sqlite3')
                    archive.writestr('letracode.json', json.dumps(exported, ensure_ascii=False, indent=2))
                    copy_entries(archive, 'Memory/', entries)
                    source_backups = self.directory / 'file-backups'
                    # lexists also detects a broken link so it is refused,
                    # rather than silently omitting a redirected backup root.
                    if os.path.lexists(source_backups):
                        with closing(backup_tree(source_backups)) as retained:
                            copy_entries(archive, 'file-backups/', retained)
                    archive.writestr('RESTORE.txt',
                        'Close LetraCode. Keep a copy of the current data folder. Extract the complete archive, '
                        'including Memory/ and its hidden .history/, .receipts/, .deleted-projects/ and recovery '
                        'directories, plus file-backups/, into a NEW empty data folder.\n'
                        'Included: the SQLite database, a readable JSON export, ordinary Memory notes/manifests, '
                        'retained memory/history/recovery bytes, and app-owned pre-edit source copies in file-backups/. '
                        'Excluded: linked original source trees, GGUF model weights, temporary runtime files, logs '
                        'and migration-backups/ database snapshots. Training examples, configuration, frozen run '
                        'datasets and results are included in SQLite and JSON. The training/ output directory '
                        '(adapters, logs and converted models) and original training weights are excluded; '
                        'back these up separately. Restored runs never restart automatically.\n'
                        'Before opening a restored database, remove or retarget linked source roots and any writable '
                        'destinations to isolated test locations. A copied database retains the original links and settings. '
                        'For a safe test, use sqlite3 /new/folder/letracode.sqlite3 "DELETE FROM links;" and review settings '
                        'before launch. Never point a restored test at the original sources.\n'
                        'Then run letracode --data-dir /new/folder. Ordinary Memory files remain editable there; '
                        'Undo uses the restored private history. The JSON export legacy project memory column is empty '
                        'because Memory/ is authoritative. Oversized or undecodable preserved files are copied '
                        'as opaque bytes; active memory parsing limits still apply.\n'
                        'Inspect a file-backups/ copy as bytes or in a suitable editor. Its UUID-prefixed basename '
                        'preserves the source filename; a saved tool result may identify the original path. Copy a '
                        'chosen version to a separate review location and compare it before any explicit restoration. '
                        'Restoration does not replay an edit, command, or pending tool call.\n'
                        'Consistency: Strand saves and project deletions are excluded from the SQLite/file snapshot. '
                        'A completed memory save may precede its saved chat tool result: inspect restored receipts '
                        'when a chat action has no outcome, and never retry it automatically. Source pre-edit copies '
                        'and external editor writes do not share the Strand lock; changes detected while copying '
                        'abort the backup. Files changed after they were copied belong to a later backup. For a '
                        'quiescent snapshot, finish active tools and close external editors first. Retained recovery '
                        'records preserve original paths and may require manual reconciliation in the new folder.\n')
            # Stage alongside destination to keep replacing an existing backup atomic.
            fd, name = tempfile.mkstemp(prefix='.letracode-backup-', dir=destination.parent)
            try:
                with os.fdopen(fd, 'wb') as out, stage.open('rb') as incoming:
                    import shutil
                    shutil.copyfileobj(incoming, out)
                    out.flush(); fs.fsync(out.fileno())
                fs.replace(name, destination)
            finally:
                Path(name).unlink(missing_ok=True)
