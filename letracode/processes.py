"""Launch and stop owned process trees without opening Windows consoles."""
from __future__ import annotations

import ctypes
import os
import signal
import subprocess
import sys
import threading
from contextlib import contextmanager


_launch_lock = threading.Lock()


@contextmanager
def _external_dll_search():
    """External engines use their own DLLs, not the frozen app's Qt/Python DLLs.

    See PyInstaller's common-issues guide, Windows external programs section.
    Restore the app search path immediately after CreateProcess returns.
    """
    if not getattr(sys, 'frozen', False):
        yield
        return
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    get_path = kernel.GetDllDirectoryW
    get_path.argtypes = [wintypes.DWORD, wintypes.LPWSTR]
    get_path.restype = wintypes.DWORD
    set_path = kernel.SetDllDirectoryW
    set_path.argtypes = [wintypes.LPCWSTR]
    set_path.restype = wintypes.BOOL
    with _launch_lock:
        length = get_path(0, None)
        original = ctypes.create_unicode_buffer(length + 1)
        if length:
            get_path(len(original), original)
        if not set_path(None):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            yield
        finally:
            if not set_path(original.value if length else None):
                raise ctypes.WinError(ctypes.get_last_error())


def start_process(argv, **kwargs):
    if sys.platform != 'win32':
        return subprocess.Popen(argv, start_new_session=True, **kwargs)
    job = _WindowsJob()
    process = None
    try:
        # Assign the job before any user code can create descendants. Popen
        # closes the primary thread handle, so resume it through Toolhelp.
        with _external_dll_search():
            process = subprocess.Popen(argv, creationflags=0x08000000 | 0x00000004, **kwargs)
        job.assign(process.pid)
        process._letracode_job = job
        job.resume(process.pid)
        return process
    except BaseException:
        job.close()
        if process is not None:
            try:
                process.kill()
                process.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                pass
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    try:
                        stream.close()
                    except OSError:
                        pass
        raise


def stop_process(process, timeout=2.0):
    """Stop descendants too, including when the original parent already exited."""
    if sys.platform == 'win32':
        job = getattr(process, '_letracode_job', None)
        if job is not None:
            job.close()
        elif process.poll() is None:
            process.kill()
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            pass
        # A dead/reaped parent does not mean its process group is empty.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        pass


class _WindowsJob:
    """A non-inheritable, kill-on-close Win32 Job Object.

    Only instantiated on Windows. Structures and APIs follow Microsoft Learn:
    winnt/JOBOBJECT_EXTENDED_LIMIT_INFORMATION, jobapi2/AssignProcessToJobObject,
    and tlhelp32/THREADENTRY32. No breakaway flags are granted to descendants.
    """

    def __init__(self):
        from ctypes import wintypes

        class BasicLimits(ctypes.Structure):
            _fields_ = [
                ('PerProcessUserTimeLimit', ctypes.c_longlong),
                ('PerJobUserTimeLimit', ctypes.c_longlong),
                ('LimitFlags', wintypes.DWORD),
                ('MinimumWorkingSetSize', ctypes.c_size_t),
                ('MaximumWorkingSetSize', ctypes.c_size_t),
                ('ActiveProcessLimit', wintypes.DWORD),
                ('Affinity', ctypes.c_size_t),
                ('PriorityClass', wintypes.DWORD),
                ('SchedulingClass', wintypes.DWORD),
            ]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [
                ('BasicLimitInformation', BasicLimits),
                ('IoInfo', ctypes.c_ulonglong * 6),
                ('ProcessMemoryLimit', ctypes.c_size_t),
                ('JobMemoryLimit', ctypes.c_size_t),
                ('PeakProcessMemoryUsed', ctypes.c_size_t),
                ('PeakJobMemoryUsed', ctypes.c_size_t),
            ]

        class ThreadEntry(ctypes.Structure):
            _fields_ = [
                ('dwSize', wintypes.DWORD), ('cntUsage', wintypes.DWORD),
                ('th32ThreadID', wintypes.DWORD), ('th32OwnerProcessID', wintypes.DWORD),
                ('tpBasePri', wintypes.LONG), ('tpDeltaPri', wintypes.LONG),
                ('dwFlags', wintypes.DWORD),
            ]

        self._thread_entry = ThreadEntry
        self._kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        signatures = {
            'CreateJobObjectW': ([ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
            'SetInformationJobObject': ([wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD], wintypes.BOOL),
            'AssignProcessToJobObject': ([wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
            'OpenProcess': ([wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
            'CloseHandle': ([wintypes.HANDLE], wintypes.BOOL),
            'CreateToolhelp32Snapshot': ([wintypes.DWORD, wintypes.DWORD], wintypes.HANDLE),
            'Thread32First': ([wintypes.HANDLE, ctypes.POINTER(ThreadEntry)], wintypes.BOOL),
            'Thread32Next': ([wintypes.HANDLE, ctypes.POINTER(ThreadEntry)], wintypes.BOOL),
            'OpenThread': ([wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
            'ResumeThread': ([wintypes.HANDLE], wintypes.DWORD),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(self._kernel, name)
            function.argtypes = arguments
            function.restype = result
        self._handle = self._kernel.CreateJobObjectW(None, None)
        if not self._handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = 0x00002000  # KILL_ON_JOB_CLOSE
        if not self._kernel.SetInformationJobObject(self._handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error

    def assign(self, pid):
        # PROCESS_SET_QUOTA | PROCESS_TERMINATE
        handle = self._kernel.OpenProcess(0x0100 | 0x0001, False, pid)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if not self._kernel.AssignProcessToJobObject(self._handle, handle):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            self._kernel.CloseHandle(handle)

    def resume(self, pid):
        snapshot = self._kernel.CreateToolhelp32Snapshot(0x00000004, 0)  # SNAPTHREAD
        if snapshot == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            entry = self._thread_entry()
            entry.dwSize = ctypes.sizeof(entry)
            found = self._kernel.Thread32First(snapshot, ctypes.byref(entry))
            while found:
                if entry.th32OwnerProcessID == pid:
                    thread = self._kernel.OpenThread(0x0002, False, entry.th32ThreadID)
                    if not thread:
                        raise ctypes.WinError(ctypes.get_last_error())
                    try:
                        if self._kernel.ResumeThread(thread) == 0xFFFFFFFF:
                            raise ctypes.WinError(ctypes.get_last_error())
                    finally:
                        self._kernel.CloseHandle(thread)
                    return
                entry.dwSize = ctypes.sizeof(entry)
                found = self._kernel.Thread32Next(snapshot, ctypes.byref(entry))
            raise OSError('Could not find the suspended child process thread')
        finally:
            self._kernel.CloseHandle(snapshot)

    def close(self):
        if self._handle:
            self._kernel.CloseHandle(self._handle)
            self._handle = None
