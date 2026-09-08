import os
from pathlib import Path
import subprocess
import sys
import time

import pytest


def test_stop_kills_descendants_after_parent_exit(tmp_path):
    from letracode.processes import start_process, stop_process
    ready = tmp_path / 'child-ready'
    late = tmp_path / 'child-survived'
    child = (
        f'from pathlib import Path; import time; Path({str(ready)!r}).touch(); '
        f'time.sleep(1.5); Path({str(late)!r}).touch()'
    )
    parent = (
        'import subprocess, sys, time; from pathlib import Path; '
        f'subprocess.Popen([sys.executable, "-c", {child!r}], '
        'stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); '
        f'\nwhile not Path({str(ready)!r}).exists(): time.sleep(0.01)'
    )
    process = start_process([sys.executable, '-c', parent], stdout=subprocess.PIPE)
    try:
        process.wait(timeout=10)
        assert ready.exists()
        stop_process(process, timeout=0.5)
        time.sleep(1.7)
        assert not late.exists()
    finally:
        stop_process(process, timeout=0.5)
        process.stdout.close()


@pytest.mark.skipif(os.name != 'nt', reason='Native Windows job and console APIs')
def test_windows_explicit_stop_has_unsuccessful_exit_code(tmp_path):
    from letracode.processes import start_process, stop_process
    ready = tmp_path / 'ready'
    source = f'from pathlib import Path; import time; Path({str(ready)!r}).touch(); time.sleep(60)'
    process = start_process([sys.executable, '-c', source])
    try:
        deadline = time.monotonic() + 20
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists()
        stopped_at = time.monotonic()
        stop_process(process, timeout=1)
        assert process.returncode not in (None, 0)
        assert time.monotonic() - stopped_at < 3
    finally:
        stop_process(process)


@pytest.mark.skipif(os.name != 'nt', reason='Native Windows job and console APIs')
def test_windows_owned_process_has_no_console_window():
    from letracode.processes import start_process, stop_process
    source = 'import ctypes; print(ctypes.windll.kernel32.GetConsoleWindow(), flush=True)'
    process = start_process([sys.executable, '-c', source], stdout=subprocess.PIPE)
    try:
        output, _ = process.communicate(timeout=10)
        assert output.strip() == b'0'
    finally:
        stop_process(process)


@pytest.mark.skipif(os.name != 'nt', reason='Native Windows job and console APIs')
def test_windows_job_closes_if_application_exits(tmp_path):
    ready = tmp_path / 'job-ready'
    late = tmp_path / 'orphan-survived'
    child = (
        f'from pathlib import Path; import time; Path({str(ready)!r}).touch(); '
        f'time.sleep(1.5); Path({str(late)!r}).touch()'
    )
    owner = (
        'from letracode.processes import start_process; import sys, time; from pathlib import Path; '
        f'process = start_process([sys.executable, "-c", {child!r}]); '
        f'\nwhile not Path({str(ready)!r}).exists(): time.sleep(0.01)'
    )
    subprocess.run([sys.executable, '-c', owner], check=True, timeout=10)
    time.sleep(1.7)
    assert not late.exists()


@pytest.mark.skipif(os.name != 'nt', reason='Native Windows DLL search API')
def test_frozen_launcher_restores_app_dll_search_and_child_uses_system_search(tmp_path, monkeypatch):
    import ctypes
    from ctypes import wintypes
    from letracode.processes import start_process, stop_process
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetDllDirectoryW.argtypes = [wintypes.DWORD, wintypes.LPWSTR]
    kernel.GetDllDirectoryW.restype = wintypes.DWORD
    kernel.SetDllDirectoryW.argtypes = [wintypes.LPCWSTR]
    kernel.SetDllDirectoryW.restype = wintypes.BOOL
    def current():
        value = ctypes.create_unicode_buffer(32768)
        kernel.GetDllDirectoryW(len(value), value)
        return value.value
    original = current()
    bundled = tmp_path / 'Bundled DLLs'
    bundled.mkdir()
    process = None
    try:
        assert kernel.SetDllDirectoryW(str(bundled))
        monkeypatch.setattr(sys, 'frozen', True, raising=False)
        process = start_process([sys.executable, '-c',
            'import ctypes; from ctypes import wintypes; '
            'get_path = ctypes.windll.kernel32.GetDllDirectoryW; '
            'get_path.argtypes = [wintypes.DWORD, wintypes.LPWSTR]; '
            'get_path.restype = wintypes.DWORD; '
            'value = ctypes.create_unicode_buffer(32768); '
            'get_path(len(value), value); print(len(value.value))'], stdout=subprocess.PIPE)
        output, _ = process.communicate(timeout=10)
        assert output.strip() == b'0'
        assert current() == str(bundled)
    finally:
        if process is not None:
            stop_process(process)
        kernel.SetDllDirectoryW(original or None)
