#!/usr/bin/env python3
"""Exercise the actual packaged installer and native Qt window on a clean Windows user.

This makes a per-user installation, uses a separate data directory, reinstalls,
then uninstalls. Refuses an existing LetraCode Start Menu entry. Run on a
disposable CI account; reports and screenshots remain in --work-dir.
"""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
import zipfile


def native_window(process_id: int):
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    user32.IsWindowVisible.argtypes = (wintypes.HWND,)
    user32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    windows = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    @callback_type
    def collect(handle, unused):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(handle, ctypes.byref(pid))
        if pid.value == process_id and user32.IsWindowVisible(handle):
            title = ctypes.create_unicode_buffer(512)
            user32.GetWindowTextW(handle, title, len(title))
            windows.append((handle, title.value))
        return True

    user32.EnumWindows.argtypes = (callback_type, wintypes.LPARAM)
    if not user32.EnumWindows(collect, 0):
        raise ctypes.WinError(ctypes.get_last_error())
    return windows


def launch_and_close(executable: Path, data: Path, work: Path, stage: str, env: dict, while_running=None) -> None:
    # No offscreen plugin: require an actual native Windows top-level window.
    child = subprocess.Popen([str(executable), "--data-dir", str(data)], cwd=work, env=env)
    try:
        deadline = time.monotonic() + 60
        window = None
        observed = []
        while time.monotonic() < deadline and child.poll() is None:
            observed = native_window(child.pid)
            errors = [(handle, title) for handle, title in observed
                      if title in ("Could not open LetraCode", "Unhandled exception in script")]
            if errors:
                from PIL import ImageGrab
                ImageGrab.grab(all_screens=True).save(work / f"{stage}-error.png")
                detail = window_accessible_text(errors[0][0])
                (work / f"{stage}-error.log").write_text(detail, encoding="utf-8")
                raise RuntimeError(f"{stage}: {errors[0][1]}: {detail}")
            window = next((handle for handle, title in observed if title == "LetraCode"), None)
            if window and (data / "letracode.sqlite3").is_file():
                break
            time.sleep(0.2)
        if not window or child.poll() is not None:
            from PIL import ImageGrab
            ImageGrab.grab(all_screens=True).save(work / f"{stage}-error.png")
            raise RuntimeError(f"{stage}: the native app window did not open; exit={child.poll()}, windows={observed}")
        time.sleep(1)
        if child.poll() is not None:
            raise RuntimeError(f"{stage}: application crashed after opening")
        from PIL import ImageGrab
        ImageGrab.grab(all_screens=True).save(work / f"{stage}.png")
        if while_running is not None:
            while_running(child)
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.PostMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
        if not user32.PostMessageW(window, 0x0010, 0, 0):  # WM_CLOSE
            raise ctypes.WinError(ctypes.get_last_error())
        if child.wait(timeout=30) != 0:
            raise RuntimeError(f"{stage}: application exited unsuccessfully")
        if (data / "app.lock").exists():
            raise RuntimeError(f"{stage}: closing did not release the app lock")
    finally:
        if child.poll() is None:
            subprocess.run(["taskkill.exe", "/PID", str(child.pid), "/T", "/F"], capture_output=True)
            child.wait(timeout=15)


def window_accessible_text(handle: int) -> str:
    """Read a Qt error dialog through Windows UI Automation, without app hooks."""
    env = os.environ.copy()
    env["LETRACODE_SMOKE_WINDOW"] = str(handle)
    script = (
        "$ErrorActionPreference = 'Stop'; "
        "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new(); "
        "Add-Type -AssemblyName UIAutomationClient; Add-Type -AssemblyName UIAutomationTypes; "
        "$window = [System.Windows.Automation.AutomationElement]::FromHandle([IntPtr][long]$env:LETRACODE_SMOKE_WINDOW); "
        "$elements = $window.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition); "
        "$elements | ForEach-Object { $_.Current.Name }"
    )
    result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                            env=env, capture_output=True, text=True, encoding="utf-8", timeout=25)
    return (result.stdout + result.stderr).strip()


def run_installer(executable: Path, work: Path, log_name: str, *extra: str) -> None:
    result = subprocess.run([str(executable), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
        f"/LOG={work / log_name}", *extra], cwd=work, timeout=240)
    if result.returncode != 0:
        raise RuntimeError(f"{executable.name} exited {result.returncode}; see {work / log_name}")


def installation_hashes(app: Path) -> dict:
    result = {}
    for path in app.rglob("*"):
        if path.is_file():
            with path.open("rb") as content:
                result[path.relative_to(app).as_posix()] = hashlib.file_digest(content, "sha256").hexdigest()
    return result


def verify_running_app_protected(child, installer: Path, app: Path, work: Path) -> None:
    before = installation_hashes(app)
    for executable, stage, arguments in (
        (installer, "blocked-update", [f"/DIR={app}"]),
        (app / "unins000.exe", "blocked-uninstall", []),
    ):
        result = subprocess.run([str(executable), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
            f"/LOG={work / (stage + '.log')}", *arguments], cwd=work, timeout=45)
        if result.returncode == 0:
            raise RuntimeError(f"{stage}: installer accepted changes while the app was running")
        if child.poll() is not None or not any(title == "LetraCode" for _, title in native_window(child.pid)):
            raise RuntimeError(f"{stage}: the installer interrupted the open application")
        if installation_hashes(app) != before:
            raise RuntimeError(f"{stage}: a refused operation changed installed application files")


def smoke(installer: Path, portable: Path, work: Path) -> dict:
    if sys.platform != "win32":
        raise RuntimeError("The installer smoke test requires native Windows")
    work = work.resolve()
    work.mkdir(parents=True, exist_ok=True)
    shortcut = Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs/LetraCode.lnk"
    if shortcut.exists():
        raise RuntimeError("Use a clean Windows CI account: a LetraCode shortcut already exists")
    import winreg
    registry_path = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\io.letracode.LetraCode_is1"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, registry_path):
            raise RuntimeError("Use a clean Windows CI account: LetraCode is already installed")
    except FileNotFoundError:
        pass
    app = work / "Installed App With Spaces é 日本語"
    data = work / "User Data With Spaces é 日本語"
    env = os.environ.copy()
    for key in ("QT_QPA_PLATFORM", "PYTHONPATH", "PYTHONHOME", "QT_PLUGIN_PATH", "QML2_IMPORT_PATH"):
        env.pop(key, None)
    # A bundled application must not find the CI Python installation via PATH.
    env["PATH"] = os.environ["SystemRoot"] + r"\System32;" + os.environ["SystemRoot"]
    run_installer(installer, work, "install.log", f"/DIR={app}")
    if not shortcut.is_file():
        raise RuntimeError("The installer did not create its Start Menu shortcut")
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, registry_path) as entry:
        installed_version = winreg.QueryValueEx(entry, "DisplayVersion")[0]
    version = json.loads((app / "version.json").read_text(encoding="utf-8"))["version"]
    if version != installed_version:
        raise RuntimeError("Installer and bundled application versions differ")
    trainer = app / "_internal/letracode/training_backend.py"
    if not trainer.is_file() or not (app / "packaging/training-requirements.txt").is_file():
        raise RuntimeError("The package is missing the standalone local trainer or its dependency list")
    # The trainer must run under a separately selected Python, outside the frozen app.
    subprocess.run([sys.executable, str(trainer), "--help"], check=True,
                   capture_output=True, timeout=30)
    launch_and_close(app / "LetraCode.exe", data, work, "installed-window", env,
                     while_running=lambda child: verify_running_app_protected(child, installer, app, work))
    # Seed real chat rows and ordinary project/recovery files after the packaged
    # application has initialized its own current schema.
    with sqlite3.connect(data / "letracode.sqlite3") as db:
        if db.execute("PRAGMA user_version").fetchone()[0] != 3:
            raise RuntimeError("The packaged app did not initialize schema 3")
        db.execute("INSERT INTO chats(id,title,created,updated) VALUES ('installer-smoke','Keep my chat','2026-09-08','2026-09-08')")
        db.execute("INSERT INTO messages(chat_id,role,content,created) VALUES ('installer-smoke','user','Keep this conversation 日本語','2026-09-08')")
    sentinel = data / "installation-retention.txt"
    sentinel.write_text("Keep my chats, notes and recovery data 日本語", encoding="utf-8")
    from letracode.store import Store
    store = Store(data)
    store.memory.create_file("Installer note.md", "Before the saved edit")
    note = store.memory.file_snapshot("Installer note.md")
    store.memory.replace_file("Installer note.md", "Keep my saved note 日本語", note["sha256"])
    retained_files = {path: path.read_bytes() for directory in (".history", ".receipts")
                      for path in (store.memory.root / directory).rglob("*") if path.is_file()}
    if not retained_files:
        raise RuntimeError("The retention fixture did not create recovery history")
    retained_files[store.memory.root / "Installer note.md"] = "Keep my saved note 日本語".encode("utf-8")
    # Alter an installed app file to prove that a subsequent install replaces it.
    (app / "version.json").write_text('{"version":"obsolete"}', encoding="utf-8")
    run_installer(installer, work, "update.log", f"/DIR={app}")
    if json.loads((app / "version.json").read_text(encoding="utf-8"))["version"] != version:
        raise RuntimeError("Update did not replace installed application files")
    launch_and_close(app / "LetraCode.exe", data, work, "updated-window", env)
    run_installer(app / "unins000.exe", work, "uninstall.log")
    deadline = time.monotonic() + 20
    while app.exists() and time.monotonic() < deadline:
        time.sleep(0.2)
    if app.exists() or shortcut.exists():
        raise RuntimeError("Uninstall left application files or the Start Menu shortcut")
    with sqlite3.connect(data / "letracode.sqlite3") as db:
        content = db.execute("SELECT content FROM messages WHERE chat_id='installer-smoke'").fetchone()[0]
        if content != "Keep this conversation 日本語" or db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("The retained conversation was damaged")
    if sentinel.read_text(encoding="utf-8") != "Keep my chats, notes and recovery data 日本語":
        raise RuntimeError("Uninstall changed retained user data")
    for path, content in retained_files.items():
        if path.read_bytes() != content:
            raise RuntimeError(f"Update or uninstall changed project/recovery data: {path}")
    extracted = work / "Extracted Portable With Spaces é 日本語"
    with zipfile.ZipFile(portable) as archive:
        if archive.testzip() is not None:
            raise RuntimeError("The portable ZIP is corrupt")
        archive.extractall(extracted)
    executable = extracted / f"LetraCode-{version}/LetraCode.exe"
    launch_and_close(executable, data, work, "portable-window", env)
    report = {"version": version, "installer": installer.name, "native_window": True,
              "install": True, "same_version_update": True, "uninstall": True,
              "data_retained": True, "schema": 3, "portable_launch": True,
              "project_notes_and_recovery_retained": True,
              "running_app_blocks_update_and_uninstall": True,
              "space_and_unicode_paths": True, "python_removed_from_path": True}
    (work / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installer", type=Path, required=True)
    parser.add_argument("--portable", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(smoke(args.installer.resolve(), args.portable.resolve(), args.work_dir), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
