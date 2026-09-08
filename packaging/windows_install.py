"""Native per-user Windows installer; invoked by install.ps1/uninstall.ps1."""
from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid


APP_ID = "io.letracode.LetraCode"
MARKER = ".letracode-install"
SOURCE_FILES = (
    "pyproject.toml", "README.md", "LICENSE", "install.ps1", "uninstall.ps1",
    "packaging/windows_install.py", "packaging/windows-bootstrap.ps1",
)


def is_link(path: Path) -> bool:
    """Include NTFS junctions and other reparse points on Python 3.11."""
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def require_managed_app(app_dir: Path) -> None:
    if is_link(app_dir):
        raise RuntimeError(f"Refusing a symbolic link or reparse-point application directory: {app_dir}")
    marker = app_dir / MARKER
    if is_link(marker) or not marker.is_file() or marker.read_text(encoding="utf-8").strip() != APP_ID:
        raise RuntimeError(f"Refusing to change {app_dir}: not a marked LetraCode installation.")


def shortcut_path(roaming: Path) -> Path:
    return roaming / "Microsoft/Windows/Start Menu/Programs/LetraCode.lnk"


def powershell(script: str, **values: str) -> str:
    executable = shutil.which("powershell.exe")
    if not executable:
        raise RuntimeError("Windows PowerShell is required to manage the Start Menu shortcut.")
    env = os.environ.copy()
    env.update(values)
    result = subprocess.run(
        [executable, "-NoProfile", "-NonInteractive", "-Command",
         "$ErrorActionPreference = 'Stop'; $ProgressPreference = 'SilentlyContinue'; "
         "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new(); " + script],
        env=env, text=True, encoding="utf-8", capture_output=True,
    )
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"Windows shortcut operation failed: {detail or result.returncode}")
    return result.stdout.strip()


def shortcut_details(path: Path) -> dict:
    return json.loads(powershell(
        "$shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut($env:LETRACODE_SHORTCUT); "
        "$shortcut | Select-Object Description, TargetPath, Arguments | ConvertTo-Json -Compress",
        LETRACODE_SHORTCUT=str(path),
    ))


def managed_shortcut(path: Path, app_dir: Path) -> bool:
    if is_link(path) or not path.is_file():
        return False
    try:
        details = shortcut_details(path)
        if details.get("Description") != APP_ID:
            return False
        target = os.path.normcase(details.get("TargetPath", ""))
        arguments = details.get("Arguments", "")
        legacy = os.path.normcase(str(app_dir / ".venv/Scripts/letracode-gui.exe"))
        if target == legacy and not arguments:
            return True
        return (target == os.path.normcase(shutil.which("powershell.exe") or "") and
                arguments == shortcut_arguments(app_dir))
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError):
        return False


def write_shortcut(path: Path, app_dir: Path) -> None:
    # WScript's TargetPath setter rejects some non-ANSI user paths. Keep its
    # target and arguments ASCII; PowerShell decodes the Unicode launcher path.
    # The private Python is isolated, so no working-directory import is needed.
    powershell(
        "$shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut($env:LETRACODE_SHORTCUT); "
        "$shortcut.TargetPath = $env:LETRACODE_TARGET; "
        "$shortcut.Arguments = $env:LETRACODE_ARGUMENTS; "
        "$shortcut.WorkingDirectory = $env:SystemRoot; "
        "$shortcut.Description = $env:LETRACODE_APP_ID; "
        "$shortcut.WindowStyle = 7; "
        "$shortcut.Save()",
        LETRACODE_SHORTCUT=str(path),
        LETRACODE_TARGET=shutil.which("powershell.exe") or "powershell.exe",
        LETRACODE_ARGUMENTS=shortcut_arguments(app_dir), LETRACODE_APP_ID=APP_ID,
    )


def shortcut_arguments(app_dir: Path) -> str:
    # Escape a literal single-quoted PowerShell string before UTF-16 encoding;
    # even names containing apostrophes or '$()' remain ordinary path text.
    pythonw = str(app_dir / ".venv/Scripts/pythonw.exe").replace("'", "''")
    command = f"& '{pythonw}' -I -m letracode"
    encoded = base64.b64encode(command.encode("utf-16-le")).decode("ascii")
    return "-NoLogo -NoProfile -NonInteractive -WindowStyle Hidden -EncodedCommand " + encoded


def create_runtime(app_dir: Path) -> None:
    # Venv launchers embed absolute paths: always build at the final destination.
    subprocess.run([sys.executable, "-I", "-X", "utf8", "-m", "venv", str(app_dir / ".venv")], check=True)
    python = app_dir / ".venv/Scripts/python.exe"
    subprocess.run(
        [str(python), "-I", "-X", "utf8", "-m", "pip", "--isolated", "install", "--disable-pip-version-check", str(app_dir)],
        check=True, cwd=app_dir.parent,
    )
    subprocess.run(
        [str(python), "-I", "-X", "utf8", "-c", "from PySide6 import QtWidgets; import pypdf"],
        check=True, cwd=app_dir.parent,
    )
    subprocess.run([str(python), "-I", "-X", "utf8", "-m", "letracode", "--version"], check=True, cwd=app_dir.parent)
    if not (app_dir / ".venv/Scripts/letracode-gui.exe").is_file():
        raise RuntimeError("The Windows GUI launcher was not created by pip.")


@contextmanager
def installation_lock(local: Path):
    local.mkdir(parents=True, exist_ok=True)
    lock = local / ".letracode-install.lock"
    try:
        descriptor = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise RuntimeError(f"Another installation may be running. If it stopped unexpectedly, remove {lock} and retry.") from None
    try:
        os.close(descriptor)
        yield
    finally:
        lock.unlink()


def install(source: Path, local: Path, roaming: Path) -> Path:
    required = (*SOURCE_FILES, "letracode/app.py", "letracode/__main__.py", "letracode/assets/io.letracode.LetraCode.svg")
    for relative in required:
        if not (source / relative).is_file():
            raise RuntimeError(f"Installer source is incomplete: missing {relative}")
    app_dir = local / "letracode-app"
    shortcut = shortcut_path(roaming)
    with installation_lock(local):
        if app_dir.exists() or is_link(app_dir):
            require_managed_app(app_dir)
        if (shortcut.exists() or is_link(shortcut)) and not managed_shortcut(shortcut, app_dir):
            raise RuntimeError(f"Refusing to replace {shortcut}: not managed by LetraCode.")
        stage = Path(tempfile.mkdtemp(prefix=".letracode-app.new.", dir=local))
        backup = local / f".letracode-app.old.{uuid.uuid4().hex}"
        temporary_shortcut = shortcut.with_name(f".LetraCode.{uuid.uuid4().hex}.lnk")
        activated = False
        try:
            shutil.copytree(source / "letracode", stage / "letracode", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            for relative in SOURCE_FILES:
                target = stage / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source / relative, target)
            (stage / MARKER).write_text(APP_ID + "\n", encoding="utf-8")
            (stage / "letracode.cmd").write_text(
                '@echo off\n"%~dp0.venv\\Scripts\\python.exe" -I -m letracode %*\n',
                encoding="utf-8", newline="\r\n",
            )
            if app_dir.exists():
                app_dir.rename(backup)
            stage.rename(app_dir)
            activated = True
            create_runtime(app_dir)
            shortcut.parent.mkdir(parents=True, exist_ok=True)
            write_shortcut(temporary_shortcut, app_dir)
            os.replace(temporary_shortcut, shortcut)
        except BaseException:
            if activated:
                shutil.rmtree(app_dir)
            if backup.exists():
                backup.rename(app_dir)
            raise
        finally:
            if stage.exists():
                shutil.rmtree(stage)
            temporary_shortcut.unlink(missing_ok=True)
        if backup.exists():
            shutil.rmtree(backup)
    return app_dir


def uninstall(local: Path, roaming: Path) -> None:
    app_dir = local / "letracode-app"
    shortcut = shortcut_path(roaming)
    with installation_lock(local):
        require_managed_app(app_dir)
        owned_shortcut = managed_shortcut(shortcut, app_dir)
        marker_content = (app_dir / MARKER).read_bytes()
        removal = local / f".letracode-app.remove.{uuid.uuid4().hex}"
        # A parent terminal or another process may hold this directory open.
        # Rename first: Windows refuses that operation before any files are lost.
        app_dir.rename(removal)
        try:
            shutil.rmtree(removal)
        except BaseException:
            if removal.exists():
                try:
                    marker = removal / MARKER
                    if not marker.is_file() or marker.read_bytes() != marker_content:
                        marker.write_bytes(marker_content)
                    if app_dir.exists() or is_link(app_dir):
                        raise FileExistsError(f"The installation path is now occupied: {app_dir}")
                    removal.rename(app_dir)
                except OSError as recovery_error:
                    raise RuntimeError(
                        f"Removal was incomplete. Remaining application files are in {removal}. "
                        f"Close processes using them, then restore that directory to {app_dir} before retrying."
                    ) from recovery_error
            raise
        if owned_shortcut:
            shortcut.unlink()


def environment_directory(name: str) -> Path:
    value = os.environ.get(name, "")
    if not value or not Path(value).is_absolute():
        raise RuntimeError(f"{name} must contain an absolute Windows directory path.")
    return Path(value)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("install", "uninstall"))
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    arguments = parser.parse_args()
    try:
        if sys.platform != "win32" or sys.version_info < (3, 11):
            raise RuntimeError("Use native Windows Python 3.11 or newer. On Fedora, run ./install.sh.")
        windows = sys.getwindowsversion()
        if arguments.action == "install" and (windows.major < 10 or windows.build < 17763):
            raise RuntimeError("LetraCode requires Windows 10 version 1809 or newer, or Windows 11.")
        local = environment_directory("LOCALAPPDATA")
        roaming = environment_directory("APPDATA")
        if arguments.action == "install":
            print("Installing LetraCode and its private Python, PySide6 and pypdf environment...", flush=True)
            app_dir = install(arguments.source.resolve(), local, roaming)
            print(f"Installation complete. Start LetraCode from the Start Menu, or run: {app_dir / 'letracode.cmd'}")
        else:
            uninstall(local, roaming)
            print("LetraCode was uninstalled.")
        print(f"User data was kept in: {local / 'letracode'}")
        return 0
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"LetraCode {arguments.action} failed: {error}", file=sys.stderr)
        print("Close LetraCode before updating or removing it. Installation requires Python with venv/pip and internet access to download dependencies.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
