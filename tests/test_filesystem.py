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
