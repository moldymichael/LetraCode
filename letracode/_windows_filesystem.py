"""Native Windows implementation of the guarded storage boundary.

Every ordinary directory guard retains ALL ancestor handles without
FILE_SHARE_DELETE, preventing pathname substitution while an operation runs.
CreateFileW opens the final component itself (OPEN_REPARSE_POINT); reparse
points and hard-linked file writes are refused. File handles share deletion so
our recovery journal retains the real inode. Snapshot handles deny write
sharing: close a busy editor write handle before reading or saving.
Renames use SetFileInformationByHandle with ReplaceIfExists=False, never an
existence-check/replace sequence. OS byte-range locks coordinate processes.

Windows has no unprivileged documented directory-fsync equivalent. File data
and journals use FlushFileBuffers; Windows manages directory metadata ordering.
Recovery retains previous files, but does not promise survival of arbitrary
power loss beyond the underlying Windows filesystem's guarantees.

API references: Microsoft Learn CreateFileW, FILE_RENAME_INFO,
GetFileInformationByHandle, LockFileEx, FlushFileBuffers.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes as w
import errno
import msvcrt
import os
import sys
from pathlib import Path
import stat as stat_module
from contextlib import contextmanager

from .filesystem import O_DIRECTORY, O_NOFOLLOW, O_NONBLOCK, windows_component

kernel = ctypes.WinDLL('kernel32', use_last_error=True)
INVALID_HANDLE = ctypes.c_void_p(-1).value
READ, WRITE, DELETE = 0x80000000, 0x40000000, 0x00010000
READ_ATTRIBUTES = 0x80
SHARE_READ, SHARE_WRITE, SHARE_DELETE = 1, 2, 4
DIRECTORY, REPARSE, READONLY = 0x10, 0x400, 1
BACKUP_SEMANTICS, OPEN_REPARSE_POINT = 0x02000000, 0x00200000
CREATE_NEW, OPEN_EXISTING, OPEN_ALWAYS = 1, 3, 4
LOCK_EX, LOCK_SH, LOCK_UN, LOCK_NB = 2, 1, 8, 4


class FileInfo(ctypes.Structure):
    _fields_ = [('attributes', w.DWORD), ('creation', w.FILETIME),
                ('access', w.FILETIME), ('write', w.FILETIME),
                ('volume', w.DWORD), ('size_high', w.DWORD), ('size_low', w.DWORD),
                ('links', w.DWORD), ('index_high', w.DWORD), ('index_low', w.DWORD)]


class FileIdInfo(ctypes.Structure):
    _fields_ = [('volume', ctypes.c_ulonglong), ('identity', ctypes.c_ubyte * 16)]


class RenameInfo(ctypes.Structure):
    _fields_ = [('replace', w.DWORD), ('root', w.HANDLE),
                ('length', w.DWORD), ('name', w.WCHAR * 1)]


class Overlapped(ctypes.Structure):
    _fields_ = [('internal', ctypes.c_size_t), ('internal_high', ctypes.c_size_t),
                ('offset', w.DWORD), ('offset_high', w.DWORD), ('event', w.HANDLE)]


kernel.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, w.LPVOID, w.DWORD, w.DWORD, w.HANDLE]
kernel.CreateFileW.restype = w.HANDLE
kernel.GetFinalPathNameByHandleW.argtypes = [w.HANDLE, w.LPWSTR, w.DWORD, w.DWORD]
kernel.GetFinalPathNameByHandleW.restype = w.DWORD
kernel.CloseHandle.argtypes = [w.HANDLE]
kernel.CloseHandle.restype = w.BOOL
kernel.GetFileInformationByHandle.argtypes = [w.HANDLE, ctypes.POINTER(FileInfo)]
kernel.GetFileInformationByHandle.restype = w.BOOL
kernel.GetFileInformationByHandleEx.argtypes = [w.HANDLE, ctypes.c_int, w.LPVOID, w.DWORD]
kernel.GetFileInformationByHandleEx.restype = w.BOOL
kernel.SetFileInformationByHandle.argtypes = [w.HANDLE, ctypes.c_int, w.LPVOID, w.DWORD]
kernel.SetFileInformationByHandle.restype = w.BOOL
kernel.LockFileEx.argtypes = [w.HANDLE, w.DWORD, w.DWORD, w.DWORD, w.DWORD, ctypes.POINTER(Overlapped)]
kernel.LockFileEx.restype = w.BOOL
kernel.UnlockFileEx.argtypes = [w.HANDLE, w.DWORD, w.DWORD, w.DWORD, ctypes.POINTER(Overlapped)]
kernel.UnlockFileEx.restype = w.BOOL


kernel.CompareStringOrdinal.argtypes = [w.LPCWSTR, ctypes.c_int, w.LPCWSTR, ctypes.c_int, w.BOOL]
kernel.CompareStringOrdinal.restype = ctypes.c_int


def windows_path_equal(left, right):
    result = kernel.CompareStringOrdinal(left, len(left.encode('utf-16-le')) // 2,
                                          right, len(right.encode('utf-16-le')) // 2, True)
    if not result:
        _error()
    return result == 2  # CSTR_EQUAL


def _error(path=None):
    error = ctypes.get_last_error()
    if error in (80, 183):
        raise FileExistsError(errno.EEXIST, 'Destination already exists', path)
    if error in (2, 3):
        raise FileNotFoundError(errno.ENOENT, 'File or directory not found', path)
    if error in (5, 32):
        raise PermissionError(errno.EACCES, ctypes.FormatError(error), path)
    raise ctypes.WinError(error)


def _native(path):
    text = str(path)
    return '\\\\?\\' + text


def _handle(path, access=READ_ATTRIBUTES, *, share_delete=True, share_write=True, disposition=OPEN_EXISTING):
    sharing = SHARE_READ | (SHARE_WRITE if share_write else 0) | (SHARE_DELETE if share_delete else 0)
    handle = kernel.CreateFileW(_native(path), access, sharing, None, disposition,
                                BACKUP_SEMANTICS | OPEN_REPARSE_POINT, None)
    if handle == INVALID_HANDLE:
        _error(str(path))
    try:
        length = kernel.GetFinalPathNameByHandleW(handle, None, 0, 0)
        if not length:
            _error(str(path))
        buffer = ctypes.create_unicode_buffer(length + 1)
        written = kernel.GetFinalPathNameByHandleW(handle, buffer, len(buffer), 0)
        if not written or written >= len(buffer):
            _error(str(path))
        # Compare normalized long names, refusing DOS 8.3 aliases as well as
        # redirection through a remapped drive or unexpected namespace.
        if not windows_path_equal(buffer.value.rstrip('\\'), _native(path).rstrip('\\')):
            raise ValueError(f'Unsafe Windows pathname alias: {path}')
        return handle
    except BaseException:
        kernel.CloseHandle(handle)
        raise


def _info(handle):
    info = FileInfo()
    if not kernel.GetFileInformationByHandle(handle, ctypes.byref(info)):
        _error()
    return info


def _stat(handle):
    info = _info(handle)
    def ns(value):
        return (((value.dwHighDateTime << 32) | value.dwLowDateTime) - 116444736000000000) * 100
    atime, mtime, ctime = ns(info.access), ns(info.write), ns(info.creation)
    mode = stat_module.S_IFDIR | 0o777 if info.attributes & DIRECTORY else stat_module.S_IFREG | 0o666
    if info.attributes & REPARSE:
        mode = stat_module.S_IFLNK | 0o777
    if info.attributes & READONLY:
        mode &= ~0o222
    inode, volume = (info.index_high << 32) | info.index_low, info.volume
    if sys.version_info >= (3, 12):
        identity = FileIdInfo()
        if kernel.GetFileInformationByHandleEx(handle, 18, ctypes.byref(identity), ctypes.sizeof(identity)):
            inode, volume = int.from_bytes(identity.identity, 'little'), identity.volume
        elif ctypes.get_last_error() not in (1, 50, 87):
            _error()
    return os.stat_result((mode, inode, volume,
                           info.links, 0, 0, (info.size_high << 32) | info.size_low,
                           atime / 1e9, mtime / 1e9, ctime / 1e9),
                          {'st_atime_ns': atime, 'st_mtime_ns': mtime, 'st_ctime_ns': ctime,
                           'st_file_attributes': info.attributes})


class Directory:
    def __init__(self, path, handles):
        self.path, self.handles = path, handles

    @property
    def handle(self):
        if not self.handles:
            raise ValueError('Closed directory guard')
        return self.handles[-1]

    def child(self, name):
        self.handle
        return self.path / windows_component(name)

    def close(self):
        while self.handles:
            kernel.CloseHandle(self.handles.pop())


def _directory(path, *, share_delete=False):
    handle = _handle(path, READ, share_delete=share_delete)
    try:
        info = _info(handle)
        if not info.attributes & DIRECTORY or info.attributes & REPARSE:
            raise ValueError(f'Unsafe directory (links and reparse points are refused): {path}')
        case_flags = w.DWORD()
        if kernel.GetFileInformationByHandleEx(handle, 23, ctypes.byref(case_flags), ctypes.sizeof(case_flags)):
            if case_flags.value & 1:
                raise ValueError(f'Guarded storage refuses case-sensitive Windows directories: {path}')
        elif ctypes.get_last_error() not in (1, 50, 87):
            _error(str(path))
        return handle
    except BaseException:
        kernel.CloseHandle(handle)
        raise


@contextmanager
def safe_directory(path, create=False, *, allow_move=False):
    path = Path(path).absolute()
    # Device/UNC roots can redirect through namespaces we cannot pin here.
    if len(path.drive) != 2 or path.drive[1] != ':' or not path.root:
        raise ValueError('Guarded storage requires a local Windows drive path')
    handles = []
    guard = Directory(path, handles)
    try:
        current = Path(path.anchor)
        handles.append(_directory(current))
        for index, name in enumerate(path.parts[1:], 1):
            current /= windows_component(name)
            if create:
                try:
                    os.mkdir(_native(current), 0o700)
                except FileExistsError:
                    pass
            try:
                handles.append(_directory(current, share_delete=allow_move and index == len(path.parts) - 1))
            except OSError as error:
                raise ValueError(f'Unsafe or missing directory (links are refused): {path}') from error
        yield guard
    finally:
        guard.close()


def open(path, flags, mode=0o777, *, dir_fd=None):
    if dir_fd is None:
        # Guard path-based calls too, so an unsupported no-follow flag can
        # never silently become an ordinary Python path open.
        target = Path(path).absolute()
        with safe_directory(target.parent) as parent:
            return open(target.name, flags, mode, dir_fd=parent)
    target = dir_fd.child(path)
    if flags & O_DIRECTORY:
        return Directory(target, [_directory(target)])
    access = READ | WRITE if flags & os.O_RDWR else WRITE if flags & os.O_WRONLY else READ
    disposition = CREATE_NEW if flags & os.O_CREAT and flags & os.O_EXCL else OPEN_ALWAYS if flags & os.O_CREAT else OPEN_EXISTING
    # Windows only guarantees updated timestamps once writer handles close.
    # Deny write sharing during snapshots; a busy editor fails closed instead
    # of supplying a potentially mixed read with unchanged timestamps.
    handle = _handle(target, access, share_write=access != READ, disposition=disposition)
    try:
        info = _info(handle)
        if info.attributes & (REPARSE | DIRECTORY) or info.links != 1:
            raise ValueError(f'Unsafe file or linked target: {path}')
        fd = msvcrt.open_osfhandle(handle, (flags & (os.O_APPEND | os.O_RDWR | os.O_WRONLY)) | os.O_BINARY)
    except BaseException:
        kernel.CloseHandle(handle)
        raise
    if flags & os.O_TRUNC:
        try:
            os.ftruncate(fd, 0)
        except BaseException:
            os.close(fd)
            raise
    return fd


def fstat(fd):
    return _stat(fd.handle if isinstance(fd, Directory) else msvcrt.get_osfhandle(fd))


def stat(path, *, dir_fd=None, follow_symlinks=True):
    if dir_fd is None:
        return os.stat(path, follow_symlinks=follow_symlinks)
    handle = _handle(dir_fd.child(path))
    try:
        return _stat(handle)
    finally:
        kernel.CloseHandle(handle)


def listdir(directory):
    if isinstance(directory, Directory):
        directory.handle
        return os.listdir(_native(directory.path))
    return os.listdir(directory)


def mkdir(path, mode=0o777, *, dir_fd=None):
    return os.mkdir(_native(dir_fd.child(path)) if dir_fd is not None else path, mode)


def close(fd):
    return fd.close() if isinstance(fd, Directory) else os.close(fd)


def fsync(fd):
    if isinstance(fd, Directory):
        fd.handle  # Validate lifetime; no unprivileged Windows directory fsync.
        return
    os.fsync(fd)


def fchmod(fd, mode):
    # Windows ACLs are inherited from the user's profile. POSIX permission bits
    # have no Windows equivalent; do not turn a saved file read-only accidentally.
    if isinstance(fd, Directory):
        fd.handle
    else:
        msvcrt.get_osfhandle(fd)


def unlink(path, *, dir_fd=None):
    return os.unlink(_native(dir_fd.child(path)) if dir_fd is not None else path)


def _rename(src_fd, src, dst_fd, dst, *, replace=False, expected_identity=None):
    source, destination = src_fd.child(src), dst_fd.child(dst)
    # Source handle pins the actual entry through the atomic rename. All
    # ancestors are already held without delete sharing by the callers.
    handle = _handle(source, DELETE | READ_ATTRIBUTES, share_delete=False)
    try:
        info = _stat(handle)
        if stat_module.S_ISLNK(info.st_mode) or (stat_module.S_ISREG(info.st_mode) and info.st_nlink != 1):
            raise ValueError(f'Unsafe linked rename source: {source}')
        if expected_identity is not None and (info.st_dev, info.st_ino) != tuple(expected_identity):
            raise ValueError('Directory identity changed before migration')
        # SetFileInformationByHandle consumes a Win32 path. Supply its
        # terminating WCHAR as well as the byte count (excluding that NUL);
        # otherwise path conversion can read beyond the variable buffer.
        name = _native(destination).encode('utf-16-le')
        size = max(ctypes.sizeof(RenameInfo), RenameInfo.name.offset + len(name) + ctypes.sizeof(w.WCHAR))
        buffer = ctypes.create_string_buffer(size)
        record = RenameInfo.from_buffer(buffer)
        record.replace, record.root, record.length = int(replace), None, len(name)
        ctypes.memmove(ctypes.addressof(buffer) + RenameInfo.name.offset, name, len(name))
        if not kernel.SetFileInformationByHandle(handle, 3, buffer, size):
            _error(str(destination))
    finally:
        kernel.CloseHandle(handle)


def rename_noreplace(src_fd, src, dst_fd, dst, *, expected_identity=None):
    return _rename(src_fd, src, dst_fd, dst, expected_identity=expected_identity)


def replace(src, dst, *, src_dir_fd=None, dst_dir_fd=None):
    if src_dir_fd is None and dst_dir_fd is None:
        return os.replace(src, dst)
    if src_dir_fd is None or dst_dir_fd is None:
        raise ValueError('Both rename directories must be guarded')
    return _rename(src_dir_fd, src, dst_dir_fd, dst, replace=True)


def flock(fd, operation):
    handle, overlapped = msvcrt.get_osfhandle(fd), Overlapped()
    if operation & LOCK_UN:
        if not kernel.UnlockFileEx(handle, 0, 1, 0, ctypes.byref(overlapped)):
            _error()
        return
    flags = (2 if operation & LOCK_EX else 0) | (1 if operation & LOCK_NB else 0)
    if not kernel.LockFileEx(handle, flags, 0, 1, 0, ctypes.byref(overlapped)):
        if ctypes.get_last_error() == 33:
            raise BlockingIOError(errno.EAGAIN, 'Storage is busy in another process')
        _error()
