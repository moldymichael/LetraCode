"""Small, explicit filesystem boundary for guarded storage.

POSIX keeps descriptor-relative operations. Windows uses retained native handles
and refuses reparse points and pathname aliases; importing this module never
changes Python's os module. See _windows_filesystem for native guarantees.
"""
from __future__ import annotations

import ctypes
import os
import re
from contextlib import contextmanager
from pathlib import Path

IS_WINDOWS = os.name == 'nt'
O_NOFOLLOW = getattr(os, 'O_NOFOLLOW', 0x10000000)
O_DIRECTORY = getattr(os, 'O_DIRECTORY', 0x20000000)
O_NONBLOCK = getattr(os, 'O_NONBLOCK', 0x40000000)


def windows_component(name):
    """Reject traversal, device names, streams and Win32 normalization aliases."""
    name = os.fspath(name)
    if (not isinstance(name, str) or not name or name in ('.', '..') or
            any(ord(char) < 32 or char in '<>:"/\\|?*' for char in name) or
            name.endswith((' ', '.')) or
            re.fullmatch(r'(?i:CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|COM[1-9¹²³]|LPT[1-9¹²³])', name.split('.')[0])):
        raise ValueError(f'Unsafe Windows path component: {name!r}')
    return name


def __getattr__(name):
    # Resolve dynamically so ordinary os fault-injection tests still exercise
    # the original POSIX implementation. Callers opt in with fs.operation().
    return getattr(os, name)


@contextmanager
def safe_directory(path: Path, create=False, *, allow_move=False):
    """Open every POSIX ancestor without following symbolic links."""
    path = Path(path).absolute()
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for name in path.parts[1:]:
            if name in ('.', '..'):
                raise ValueError('Unsafe directory component')
            if create:
                try:
                    os.mkdir(name, 0o700, dir_fd=fd)
                    os.fsync(fd)
                except FileExistsError:
                    pass
            try:
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            except OSError as error:
                raise ValueError(f'Unsafe or missing directory (links are refused): {path}') from error
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


def rename_noreplace(src_fd, src, dst_fd, dst, *, expected_identity=None):
    """Atomic Linux move that never replaces another writer's destination."""
    if expected_identity is not None:
        info = os.stat(src, dir_fd=src_fd, follow_symlinks=False)
        if (info.st_dev, info.st_ino) != tuple(expected_identity):
            raise ValueError('Directory identity changed before migration')
    libc = ctypes.CDLL(None, use_errno=True)
    rename = getattr(libc, 'renameat2', None)
    if rename is None:
        raise OSError('Safe saves require Linux renameat2 support')
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(src_fd, os.fsencode(src), dst_fd, os.fsencode(dst), 1):
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), dst)


if IS_WINDOWS:
    from ._windows_filesystem import (safe_directory, open, stat, fstat, listdir,
        mkdir, close, fsync, fchmod, unlink, replace, rename_noreplace, flock,
        LOCK_EX, LOCK_SH, LOCK_UN, LOCK_NB)
else:
    import fcntl
    from fcntl import LOCK_EX, LOCK_SH, LOCK_UN, LOCK_NB

    def flock(fd, operation):
        return fcntl.flock(fd, operation)
