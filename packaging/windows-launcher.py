"""Frozen GUI entry point, deliberately independent of the current directory."""
import multiprocessing
import os
import sys


APP_MUTEX = r"Local\io.letracode.LetraCode.Running"


def create_application_mutex():
    """Retain an Inno Setup AppMutex for this process's entire lifetime."""
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
    kernel.CreateMutexW.restype = wintypes.HANDLE
    handle = kernel.CreateMutexW(None, False, APP_MUTEX)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    # Deliberately do not close early: the OS releases the handle when the
    # process fully terminates. Every simultaneously running instance holds it.
    return handle


if __name__ == "__main__":
    multiprocessing.freeze_support()
    application_mutex = create_application_mutex()
    # A Windows GUI process does not have a console. Libraries may still write
    # diagnostics; provide real file objects just as pythonw-compatible apps do.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    from letracode.app import main
    raise SystemExit(main())
