"""Backup recovery and filesystem/snapshot boundaries, using disposable data only."""
from letracode import filesystem as fs
import hashlib
import json
import os
import sqlite3
import threading
import zipfile
from contextlib import contextmanager
from pathlib import Path

import pytest

from letracode.store import Store
from letracode.tools import ToolExecutor


def test_oversized_opaque_deleted_memory_streams_and_restores(tmp_path, monkeypatch):
    store = Store(tmp_path / 'data')
    project = store.create_project('Preserved manuscript')
    raw = b'\xff\xfe\x00retained\r\n' * 300000
    memory = store.strand.path('project', project)
    memory.write_bytes(raw)
    with pytest.raises(ValueError, match='too large'):
        store.strand.snapshot('project', project)
    record = store.delete_project(project)
    retained = Path(record['path'])
    retained_inode = retained.stat().st_ino
    real_fdopen = os.fdopen
    reads = []

    class BoundedReader:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return self.stream.__exit__(*args)

        def __getattr__(self, name):
            return getattr(self.stream, name)

        def read(self, size=-1):
            assert 0 < size <= 256 * 1024, 'Retained data must use bounded reads'
            reads.append(size)
            return self.stream.read(size)

    def fdopen(fd, *args, **kwargs):
        is_retained = fs.fstat(fd).st_ino == retained_inode
        stream = real_fdopen(fd, *args, **kwargs)
        return BoundedReader(stream) if is_retained else stream

    monkeypatch.setattr(os, 'fdopen', fdopen)
    destination = tmp_path / 'backup.zip'
    store.backup(destination)
    restored = tmp_path / 'restored'
    with zipfile.ZipFile(destination) as archive:
        archive.extractall(restored)
    recovered = restored / retained.relative_to(store.directory)
    assert recovered.read_bytes() == raw
    assert hashlib.sha256(recovered.read_bytes()).digest() == hashlib.sha256(raw).digest()
    assert reads
    assert json.loads((restored / Path(record['record_path']).relative_to(store.directory)).read_text())['status'] == 'deleted'
    with sqlite3.connect(restored / 'letracode.sqlite3') as db:
        assert db.execute('SELECT count(*) FROM projects').fetchone()[0] == 0


def test_real_source_edit_backup_is_recoverable_without_originals(tmp_path):
    store = Store(tmp_path / 'data')
    sources = tmp_path / 'sources'
    sources.mkdir()
    original = sources / 'chapter.md'
    raw = b'\xef\xbb\xbfOriginal chapter\r\n'
    original.write_bytes(raw)
    tools = ToolExecutor([str(sources)], store.directory, lambda _: True, threading.Event())
    result = json.loads(tools.execute('write_file', {
        'path': str(original), 'content': 'Revised chapter\n',
        'expected_sha256': hashlib.sha256(raw).hexdigest(),
    }))
    retained = Path(result['backup'])
    destination = tmp_path / 'backup.zip'
    store.backup(destination)
    with zipfile.ZipFile(destination) as archive:
        assert archive.read(retained.relative_to(store.directory).as_posix()) == raw
        assert not any(name.startswith('sources/') for name in archive.namelist())
        archive.extractall(tmp_path / 'restored')
    assert (tmp_path / 'restored' / retained.relative_to(store.directory)).read_bytes() == raw
    assert original.read_text() == 'Revised chapter\n'


@pytest.mark.parametrize('tree', ['Memory', 'file-backups'])
@pytest.mark.parametrize('kind', ['symlink', 'hardlink', 'directory-link', 'fifo'])
def test_malformed_retained_entries_abort_without_replacing_backup(tmp_path, tree, kind):
    store = Store(tmp_path / 'data')
    retained = store.directory / tree
    retained.mkdir(exist_ok=True)
    outside = tmp_path / 'outside'
    outside.mkdir()
    private = outside / 'private.md'
    private.write_bytes(b'not app-owned')
    bad = retained / 'bad'
    if kind == 'symlink':
        bad.symlink_to(private)
    elif kind == 'hardlink':
        os.link(private, bad)
    elif kind == 'directory-link':
        bad.symlink_to(outside, target_is_directory=True)
    else:
        if os.name == 'nt':
            pytest.skip('Windows has no POSIX FIFO entries; native reparse points are tested separately')
        os.mkfifo(bad)
    destination = tmp_path / 'backup.zip'
    destination.write_bytes(b'previous snapshot')
    with pytest.raises((ValueError, OSError)):
        store.backup(destination)
    assert destination.read_bytes() == b'previous snapshot'
    assert private.read_bytes() == b'not app-owned'


@pytest.mark.parametrize('failure', ['zip-write', 'publish'])
def test_backup_write_failure_preserves_existing_destination(tmp_path, monkeypatch, failure):
    store = Store(tmp_path / 'data')
    destination = tmp_path / 'backup.zip'
    destination.write_bytes(b'previous snapshot')
    if failure == 'zip-write':
        def fail(*args, **kwargs):
            raise OSError('Synthetic disk full')
        monkeypatch.setattr(zipfile._ZipWriteFile, 'write', fail)
    else:
        real_replace = os.replace
        def fail(source, target, *args, **kwargs):
            if Path(target) == destination:
                raise OSError('Synthetic destination write failure')
            return real_replace(source, target, *args, **kwargs)
        monkeypatch.setattr(os, 'replace', fail)
    with pytest.raises(OSError, match='Synthetic'):
        store.backup(destination)
    assert destination.read_bytes() == b'previous snapshot'
    assert not list(tmp_path.glob('.letracode-backup-*'))


@pytest.mark.parametrize('change', ['replace-before-open', 'write-during-read', 'link-during-read'])
def test_retained_file_identity_or_content_race_aborts_backup(tmp_path, monkeypatch, change):
    store = Store(tmp_path / 'data')
    retained = store.directory / 'file-backups'
    retained.mkdir()
    source = retained / 'retained.md'
    source.write_bytes(b'a' * 400000)
    inode = source.stat().st_ino
    destination = tmp_path / 'backup.zip'
    destination.write_bytes(b'previous snapshot')
    if change == 'replace-before-open':
        real_open = fs.open

        def replaced_open(name, flags, *args, **kwargs):
            if name == source.name and flags & fs.O_NOFOLLOW:
                source.rename(tmp_path / 'old-inode')
                source.write_bytes(b'new bytes under the same name')
            return real_open(name, flags, *args, **kwargs)

        monkeypatch.setattr(fs, 'open', replaced_open)
    else:
        real_fdopen = os.fdopen

        class MutatingReader:
            def __init__(self, stream):
                self.stream, self.changed = stream, False

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return self.stream.__exit__(*args)

            def __getattr__(self, name):
                return getattr(self.stream, name)

            def read(self, size=-1):
                content = self.stream.read(size)
                if not self.changed:
                    self.changed = True
                    if change == 'write-during-read':
                        with source.open('r+b', buffering=0) as editor:
                            editor.write(b'X')
                    else:
                        os.link(source, tmp_path / 'unexpected-hardlink')
                return content

        def fdopen(fd, *args, **kwargs):
            selected = fs.fstat(fd).st_ino == inode
            stream = real_fdopen(fd, *args, **kwargs)
            return MutatingReader(stream) if selected else stream

        monkeypatch.setattr(os, 'fdopen', fdopen)
    with pytest.raises((ValueError, PermissionError)):
        store.backup(destination)
    assert destination.read_bytes() == b'previous snapshot'


def test_redirected_source_backup_root_is_rejected(tmp_path):
    store = Store(tmp_path / 'data')
    private = tmp_path / 'private'
    private.mkdir()
    (private / 'secret.md').write_bytes(b'private')
    (store.directory / 'file-backups').symlink_to(private, target_is_directory=True)
    destination = tmp_path / 'backup.zip'
    destination.write_bytes(b'previous snapshot')
    with pytest.raises(ValueError, match='links'):
        store.backup(destination)
    assert destination.read_bytes() == b'previous snapshot'


def test_memory_save_waits_for_sqlite_and_strand_snapshot_without_deadlock(tmp_path, monkeypatch):
    store = Store(tmp_path / 'data')
    project = store.create_project('Worker memory')
    chat = store.create_chat(project_id=project)
    worker_store = Store(store.directory)
    tools = ToolExecutor([], store.directory, lambda _: True, threading.Event(), store=worker_store, chat_id=chat)
    attempted, finished = threading.Event(), threading.Event()
    results = []
    real_operation = worker_store.strand._operation

    @contextmanager
    def worker_operation():
        attempted.set()
        with real_operation():
            yield

    monkeypatch.setattr(worker_store.strand, '_operation', worker_operation)

    def save():
        try:
            result = tools.execute('remember', {'scope': 'project', 'text': 'Worker saved fact'})
            results.append(json.loads(result))
            worker_store.add_message(chat, 'tool', result)
        finally:
            finished.set()

    writer = threading.Thread(target=save, daemon=True)
    real_connection = store.connection

    class BackupConnection:
        def __init__(self, db):
            self.db = db

        def backup(self, target):
            self.db.backup(target)
            # Separate open description probes the cross-process lock without
            # scheduling guesses. The snapshot must already exclude writers.
            with (store.strand.root / '.write-lock').open('r+b') as guard:
                with pytest.raises(BlockingIOError):
                    fs.flock(guard.fileno(), fs.LOCK_EX | fs.LOCK_NB)
            writer.start()
            assert attempted.wait(5), 'Worker did not reach memory save'
            assert not finished.is_set()

    @contextmanager
    def connection():
        with real_connection() as db:
            yield BackupConnection(db)

    monkeypatch.setattr(store, 'connection', connection)
    destination = tmp_path / 'backup.zip'
    try:
        store.backup(destination)
    finally:
        if writer.ident is not None:
            writer.join(5)
    assert finished.is_set(), 'Strand/SQLite lock ordering deadlocked the worker'
    assert results and 'error' not in results[0]
    with zipfile.ZipFile(destination) as archive:
        assert archive.read(f'Memory/.projects/{project}/Memory.md') == b''
        assert json.loads(archive.read('letracode.json'))['messages'] == []
    assert 'Worker saved fact' in worker_store.project(project)['memory']
    assert len(worker_store.messages(chat)) == 1
