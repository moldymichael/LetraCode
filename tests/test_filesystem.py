"""Portable guarded filesystem contracts; native safeguards run on Windows CI."""
import os
import subprocess
import sys
from pathlib import Path

import pytest


def test_guarded_move_never_overwrites_existing_name(tmp_path):
    from letracode import filesystem as fs
    (tmp_path / 'old').write_bytes(b'original')
    (tmp_path / 'new').write_bytes(b'external')
    with fs.safe_directory(tmp_path) as directory:
        with pytest.raises(FileExistsError):
            fs.rename_noreplace(directory, 'old', directory, 'new')
    assert (tmp_path / 'old').read_bytes() == b'original'
    assert (tmp_path / 'new').read_bytes() == b'external'


@pytest.mark.parametrize('name', ['..', 'a/b', 'a\\b', 'file:stream', 'NUL.txt', 'COM1', 'x.', 'x ', 'a\x00b', 'LPT².log', 'CONOUT$'])
def test_windows_names_reject_aliases(name):
    from letracode import filesystem as fs
    with pytest.raises(ValueError):
        fs.windows_component(name)


def test_guarded_file_stat_matches_named_identity(tmp_path):
    from letracode import filesystem as fs
    (tmp_path / 'note.md').write_bytes(b'hello')
    with fs.safe_directory(tmp_path) as directory:
        fd = fs.open('note.md', os.O_RDONLY | fs.O_NOFOLLOW, dir_fd=directory)
        try:
            named = fs.stat('note.md', dir_fd=directory, follow_symlinks=False)
            opened = fs.fstat(fd)
            external = (tmp_path / 'note.md').stat()
            assert (named.st_dev, named.st_ino) == (opened.st_dev, opened.st_ino)
            assert (opened.st_dev, opened.st_ino) == (external.st_dev, external.st_ino)
            assert opened.st_nlink == 1 and opened.st_size == 5
        finally:
            fs.close(fd)


@pytest.mark.skipif(sys.platform != 'win32', reason='Native Windows directory share semantics')
def test_windows_guard_pins_every_ancestor(tmp_path):
    from letracode import filesystem as fs
    root = tmp_path / 'parent'
    child = root / 'child'
    child.mkdir(parents=True)
    with fs.safe_directory(child):
        with pytest.raises(PermissionError):
            root.rename(tmp_path / 'redirected')
        with pytest.raises(PermissionError):
            child.rename(root / 'redirected')
    root.rename(tmp_path / 'released')


@pytest.mark.skipif(sys.platform != 'win32', reason='Native Windows junction semantics')
def test_windows_junction_is_never_followed(tmp_path):
    from letracode import filesystem as fs
    outside = tmp_path / 'outside'
    outside.mkdir()
    junction = tmp_path / 'junction'
    subprocess.run(['cmd', '/c', 'mklink', '/J', str(junction), str(outside)], check=True, capture_output=True)
    try:
        with pytest.raises(ValueError, match='links|reparse'):
            with fs.safe_directory(junction):
                pytest.fail('Junction was followed')
    finally:
        junction.rmdir()


def test_lock_blocks_other_process_and_releases_on_close(tmp_path):
    from letracode import filesystem as fs
    program = '''
import sys
from pathlib import Path
from letracode import filesystem as fs
with fs.safe_directory(Path(sys.argv[1])) as root:
    fd = fs.open('lock', fs.O_RDWR | fs.O_CREAT | fs.O_NOFOLLOW, 0o600, dir_fd=root)
    try:
        try:
            fs.flock(fd, fs.LOCK_EX | fs.LOCK_NB)
        except BlockingIOError:
            sys.exit(73)
    finally:
        fs.close(fd)
'''
    with fs.safe_directory(tmp_path) as root:
        fd = fs.open('lock', os.O_RDWR | os.O_CREAT | fs.O_NOFOLLOW, 0o600, dir_fd=root)
        try:
            fs.flock(fd, fs.LOCK_EX)
            locked = subprocess.run([sys.executable, '-c', program, str(tmp_path)], capture_output=True, timeout=10)
            assert locked.returncode == 73, locked.stderr.decode()
        finally:
            fs.close(fd)
        unlocked = subprocess.run([sys.executable, '-c', program, str(tmp_path)], capture_output=True, timeout=10)
        assert unlocked.returncode == 0, unlocked.stderr.decode()


def test_windows_data_home_uses_local_app_data(tmp_path, monkeypatch):
    from letracode import filesystem as fs
    from letracode.store import data_home
    monkeypatch.setattr(fs, 'IS_WINDOWS', True)
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'Local'))
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path / 'unrelated'))
    assert data_home() == tmp_path / 'Local' / 'LetraCode'


def test_windows_registry_rejects_case_aliases(tmp_path, monkeypatch):
    import copy
    from letracode import filesystem as fs
    from letracode.store import Store
    memory = Store(tmp_path / 'data').memory
    memory.create_file('Note.md', 'original')
    metadata, _ = memory._metadata()
    row = next(row for row in metadata['files'].values() if row['path'] == 'Note.md')
    alias = copy.deepcopy(row)
    alias['path'] = 'note.md'
    alias['history_paths'] = ['note.md']
    metadata['files']['a' * 32] = alias
    monkeypatch.setattr(fs, 'IS_WINDOWS', True)
    with pytest.raises(ValueError, match='Duplicate'):
        memory._validate_metadata(metadata)


@pytest.mark.parametrize('stage', ['capture', 'publish', 'journal', 'done', 'archive'])
def test_subprocess_crash_retains_recoverable_memory(tmp_path, stage):
    from letracode.store import Store
    store = Store(tmp_path / 'data')
    project = store.create_project('Interrupted operation')
    scope = 'project' if stage == 'archive' else 'global'
    project_id = project if stage == 'archive' else None
    original = store.memory.path(scope, project_id)
    original.write_text('Original notes', encoding='utf-8')
    program = r'''
import os
import sys
from pathlib import Path
from letracode import filesystem as fs
from letracode.store import Store
import letracode.strand as strand
store = Store(Path(sys.argv[1]))
stage, project = sys.argv[2:]
scope = 'project' if stage == 'archive' else 'global'
project_id = project if stage == 'archive' else None
target = store.memory.path(scope, project_id)
before = store.memory.snapshot(scope, project_id)
real_move = strand.rename_noreplace
def move(src_fd, src, dst_fd, dst, **options):
    result = real_move(src_fd, src, dst_fd, dst, **options)
    if (stage in ('capture', 'archive') and src == target.name or
            stage == 'publish' and dst == target.name and src.endswith('.proposed')):
        fs.fsync(src_fd)
        fs.fsync(dst_fd)
        os._exit(73)
    return result
strand.rename_noreplace = move
real_fdopen = os.fdopen
class InterruptedRecord:
    def __init__(self, stream): self.stream = stream
    def __enter__(self): return self
    def __exit__(self, *args): self.stream.close()
    def __getattr__(self, name): return getattr(self.stream, name)
    def write(self, data):
        prefix = b'{"before_sha256"' if stage == 'journal' else b'{"status": "saved"'
        if stage in ('journal', 'done') and data.startswith(prefix):
            self.stream.write(data[:1])
            self.stream.flush()
            fs.fsync(self.stream.fileno())
            os._exit(73)
        return self.stream.write(data)
os.fdopen = lambda *args, **kwargs: InterruptedRecord(real_fdopen(*args, **kwargs))
if stage == 'archive':
    store.delete_project(project)
else:
    store.memory.replace(scope, 'Published notes', before['sha256'], project_id)
os._exit(74)
'''
    completed = subprocess.run([sys.executable, '-c', program, str(store.directory), stage, project],
                               capture_output=True, timeout=20)
    assert completed.returncode == 73, completed.stderr.decode()
    reopened = Store(store.directory)
    if stage == 'archive':
        assert reopened.project(project)['memory_error']
        assert not original.exists()
        archived = list((reopened.memory.root / '.deleted-projects' / project).glob('*.md'))
        assert len(archived) == 1 and archived[0].read_text(encoding='utf-8') == 'Original notes'
    else:
        expected = 'Original notes' if stage in ('capture', 'journal') else 'Published notes'
        assert reopened.memory.snapshot(scope, project_id)['text'] == expected
        if stage in ('publish', 'done'):
            receipt = reopened.memory.receipts()[0]
            assert receipt['status'] == 'saved'
            reopened.memory.undo(receipt['id'])
            assert reopened.memory.snapshot(scope, project_id)['text'] == 'Original notes'


@pytest.mark.skipif(sys.platform != 'win32', reason='Native Windows file sharing and inode retention')
def test_windows_busy_editor_blocks_save_without_losing_bytes(tmp_path):
    from letracode import filesystem as fs
    from letracode.store import Store
    store = Store(tmp_path / 'data')
    before = store.memory.snapshot('global')
    target = store.memory.path('global')
    fd = fs.open(target, os.O_RDWR | fs.O_NOFOLLOW)
    try:
        with pytest.raises(PermissionError):
            store.memory.replace('global', 'App save', before['sha256'])
        os.write(fd, b'External save through retained handle')
        fs.fsync(fd)
    finally:
        fs.close(fd)
    assert Store(store.directory).memory.snapshot('global')['text'] == 'External save through retained handle'
    assert target.read_bytes() == b'External save through retained handle'


def test_guarded_move_publishes_complete_unicode_name(tmp_path):
    from letracode import filesystem as fs
    destination = tmp_path / 'Recovered 日本語 é'
    destination.mkdir()
    (tmp_path / 'staging').write_bytes(b'complete')
    with fs.safe_directory(tmp_path) as source, fs.safe_directory(destination) as target:
        fs.rename_noreplace(source, 'staging', target, 'complete proposed 日本語 é.md')
        assert fs.stat('complete proposed 日本語 é.md', dir_fd=target, follow_symlinks=False).st_size == 8
    assert not (tmp_path / 'staging').exists()
    assert (destination / 'complete proposed 日本語 é.md').read_bytes() == b'complete'


def test_windows_ordinal_names_do_not_alias_private_memory(tmp_path, monkeypatch):
    from letracode import filesystem as fs
    from letracode.store import Store
    memory = Store(tmp_path / 'data').memory
    memory.create_file('Straße.md', 'private unselected notes')
    (memory.root / 'Strasse.md').write_text('public reviewed notes', encoding='utf-8')
    monkeypatch.setattr(fs, 'IS_WINDOWS', True)
    reviewed = memory.file_snapshot('Strasse.md')
    assert reviewed['file_id'] is None
    memory.set_active('Strasse.md', True, reviewed['sha256'],
                      expected_file_id=reviewed['file_id'],
                      expected_entry_identity=reviewed['entry_identity'])
    assert memory.file_snapshot('Straße.md')['always_active'] is False
    context = memory.core()
    assert 'public reviewed notes' in context
    assert 'private unselected notes' not in context
