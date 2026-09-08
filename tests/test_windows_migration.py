"""Native lock handoff required to rename a populated Windows Memory root."""
import os
import sys

import pytest


@pytest.mark.skipif(sys.platform != 'win32', reason='Native Windows directory sharing and lock handoff')
def test_root_rename_retains_lock_identity_and_refuses_existing_directory_reader(tmp_path):
    from letracode import filesystem as fs
    source, destination = tmp_path / 'strand', tmp_path / 'Memory'
    source.mkdir()
    (source / 'notes.md').write_bytes(b'Keep all existing notes')
    with fs.safe_directory(source, allow_move=True) as root, fs.safe_directory(tmp_path) as parent:
        identity = fs.fstat(root)
        lock = [fs.open('.write-lock', os.O_RDWR | os.O_CREAT, 0o600, dir_fd=root)]
        try:
            fs.flock(lock[0], fs.LOCK_EX)
            lock_identity = fs.fstat(lock[0])
            options = {'expected_identity': (identity.st_dev, identity.st_ino), 'migration_lock': lock}
            # A cooperating writer waiting on this lock already holds a root
            # reader. Refuse the handoff without closing its shared lock inode.
            with fs.safe_directory(source):
                with pytest.raises(PermissionError):
                    fs.rename_noreplace(parent, 'strand', parent, 'Memory', **options)
                assert fs.fstat(lock[0]).st_ino == lock_identity.st_ino
                assert source.is_dir() and not destination.exists()
            # A non-cooperating open child makes the native rename itself fail,
            # after the owned lock was closed. The old lock must be reacquired.
            with (source / 'notes.md').open('rb'):
                with pytest.raises(PermissionError):
                    fs.rename_noreplace(parent, 'strand', parent, 'Memory', **options)
            assert fs.fstat(lock[0]).st_ino == lock_identity.st_ino
            assert source.is_dir() and not destination.exists()
            fs.rename_noreplace(parent, 'strand', parent, 'Memory', **options)
            assert destination.stat().st_ino == identity.st_ino
            assert fs.fstat(lock[0]).st_ino == lock_identity.st_ino
            assert (destination / 'notes.md').read_bytes() == b'Keep all existing notes'
            with fs.safe_directory(destination) as renamed:
                competing = fs.open('.write-lock', os.O_RDWR, dir_fd=renamed)
                try:
                    with pytest.raises(BlockingIOError):
                        fs.flock(competing, fs.LOCK_EX | fs.LOCK_NB)
                finally:
                    fs.close(competing)
        finally:
            if lock[0] is not None:
                fs.close(lock[0])
