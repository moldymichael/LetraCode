"""Local SQLite persistence. Connections are short-lived and safe across workers."""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import uuid
import zipfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def data_home() -> Path:
    return Path(os.environ.get('XDG_DATA_HOME', str(Path.home() / '.local/share'))) / 'letracode'


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


class Store:
    def __init__(self, directory: Path | None = None):
        self.directory = Path(directory or data_home())
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.directory.chmod(0o700)
        self.path = self.directory / 'letracode.sqlite3'
        with self.connection() as db:
            version = db.execute('PRAGMA user_version').fetchone()[0]
            if version > 1:
                raise RuntimeError('This database belongs to a newer LetraCode. Please upgrade the app.')
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
                PRAGMA user_version=1;
            ''')
        self.path.chmod(0o600)

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
        with self.connection() as db:
            db.execute('INSERT INTO projects(id,title,created) VALUES (?,?,?)', (ident, title.strip() or 'Untitled project', now()))
        return ident

    def projects(self):
        return self.rows('SELECT * FROM projects ORDER BY lower(title),created')

    def project(self, ident):
        rows = self.rows('SELECT * FROM projects WHERE id=?', (ident,))
        return rows[0] if rows else None

    def update_project(self, ident, **fields):
        allowed = {'title', 'memory', 'current_context', 'instructions'}
        if not fields or not set(fields) <= allowed:
            raise ValueError('Invalid project field')
        with self.connection() as db:
            db.execute('UPDATE projects SET ' + ','.join(f'{k}=?' for k in fields) + ' WHERE id=?', (*fields.values(), ident))

    def delete_project(self, ident):
        with self.connection() as db:
            db.execute('DELETE FROM projects WHERE id=?', (ident,))

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
                archive.writestr('RESTORE.txt', 'Close LetraCode. Keep a copy of the current data folder. Extract letracode.sqlite3 into a NEW empty data folder, then run letracode --data-dir /path/to/folder. Linked files and model weights are not included. The JSON export is also human-readable.\n')
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
