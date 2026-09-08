from __future__ import annotations

import importlib.util
import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def installer(monkeypatch):
    module_path = ROOT / "packaging/windows_install.py"
    spec = importlib.util.spec_from_file_location("windows_install", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    # Real filesystem transactions; only native venv/pip and Windows COM are substituted.
    def create_runtime(app_dir):
        executable = app_dir / ".venv/Scripts/letracode-gui.exe"
        executable.parent.mkdir(parents=True)
        executable.write_bytes(b"test executable")

    def write_shortcut(path, app_dir):
        path.write_text(json.dumps({
            "Description": "io.letracode.LetraCode",
            "TargetPath": str(app_dir / ".venv/Scripts/letracode-gui.exe"),
        }), encoding="utf-8")

    monkeypatch.setattr(module, "create_runtime", create_runtime)
    monkeypatch.setattr(module, "write_shortcut", write_shortcut)
    monkeypatch.setattr(module, "shortcut_details", lambda path: json.loads(path.read_text(encoding="utf-8")))
    return module


@pytest.fixture
def locations(tmp_path):
    return tmp_path / "Local App Data", tmp_path / "Roaming App Data"


def test_install_update_and_uninstall_preserve_data(installer, locations):
    local, roaming = locations
    data = local / "letracode/chats.txt"
    data.parent.mkdir(parents=True)
    data.write_text("keep my chats", encoding="utf-8")

    app = installer.install(ROOT, local, roaming)
    shortcut = roaming / "Microsoft/Windows/Start Menu/Programs/LetraCode.lnk"
    assert app == local / "letracode-app"
    assert (app / "letracode/app.py").is_file()
    assert (app / "uninstall.ps1").is_file()
    assert (app / "packaging/windows_install.py").is_file()
    assert (app / "packaging/windows-bootstrap.ps1").is_file()
    assert (app / "letracode.cmd").is_file()
    assert Path(installer.shortcut_details(shortcut)["TargetPath"]).is_file()
    (app / "obsolete-file").write_text("old version", encoding="utf-8")

    installer.install(ROOT, local, roaming)
    assert not (app / "obsolete-file").exists()
    assert not list(local.glob(".letracode-app.*"))
    installer.uninstall(local, roaming)
    assert not app.exists()
    assert not shortcut.exists()
    assert data.read_text(encoding="utf-8") == "keep my chats"


def test_install_refuses_to_replace_unmarked_directory(installer, locations):
    local, roaming = locations
    valuable = local / "letracode-app/valuable.txt"
    valuable.parent.mkdir(parents=True)
    valuable.write_text("unrelated", encoding="utf-8")
    with pytest.raises(RuntimeError, match="not a marked LetraCode installation"):
        installer.install(ROOT, local, roaming)
    assert valuable.read_text(encoding="utf-8") == "unrelated"


def test_install_refuses_to_replace_unmanaged_shortcut(installer, locations):
    local, roaming = locations
    shortcut = roaming / "Microsoft/Windows/Start Menu/Programs/LetraCode.lnk"
    shortcut.parent.mkdir(parents=True)
    shortcut.write_text('{"Description":"other app","TargetPath":"other.exe"}', encoding="utf-8")
    with pytest.raises(RuntimeError, match="not managed by LetraCode"):
        installer.install(ROOT, local, roaming)
    assert json.loads(shortcut.read_text(encoding="utf-8"))["Description"] == "other app"
    assert not (local / "letracode-app").exists()


def test_failed_update_restores_previous_app_and_shortcut(installer, locations, monkeypatch):
    local, roaming = locations
    app = installer.install(ROOT, local, roaming)
    previous = app / "old-version.txt"
    previous.write_text("working version", encoding="utf-8")
    shortcut = roaming / "Microsoft/Windows/Start Menu/Programs/LetraCode.lnk"
    shortcut_before = shortcut.read_bytes()

    def fail_runtime(app_dir):
        assert app_dir == app  # A venv must be built at its permanent path.
        raise RuntimeError("dependency download failed")

    monkeypatch.setattr(installer, "create_runtime", fail_runtime)
    with pytest.raises(RuntimeError, match="dependency download failed"):
        installer.install(ROOT, local, roaming)
    assert previous.read_text(encoding="utf-8") == "working version"
    assert shortcut.read_bytes() == shortcut_before
    assert not list(local.glob(".letracode-app.*"))


def test_failed_shortcut_creation_restores_previous_install(installer, locations, monkeypatch):
    local, roaming = locations
    app = installer.install(ROOT, local, roaming)
    (app / "keep-old").write_text("old", encoding="utf-8")
    shortcut = roaming / "Microsoft/Windows/Start Menu/Programs/LetraCode.lnk"
    original_shortcut = shortcut.read_bytes()

    def fail_shortcut(path, app_dir):
        path.write_bytes(b"incomplete shortcut")
        raise OSError("shortcut save failed")

    monkeypatch.setattr(installer, "write_shortcut", fail_shortcut)
    with pytest.raises(OSError, match="shortcut save failed"):
        installer.install(ROOT, local, roaming)
    assert (app / "keep-old").read_text(encoding="utf-8") == "old"
    assert shortcut.read_bytes() == original_shortcut
    assert list(shortcut.parent.iterdir()) == [shortcut]


def test_incomplete_source_cannot_damage_existing_install(installer, locations, tmp_path):
    local, roaming = locations
    app = installer.install(ROOT, local, roaming)
    (app / "old-version").write_bytes(b"working")
    source = tmp_path / "incomplete source"
    source.mkdir()
    with pytest.raises(RuntimeError, match="source is incomplete"):
        installer.install(source, local, roaming)
    assert (app / "old-version").read_bytes() == b"working"


def test_uninstall_preserves_a_replaced_shortcut(installer, locations):
    local, roaming = locations
    app = installer.install(ROOT, local, roaming)
    shortcut = roaming / "Microsoft/Windows/Start Menu/Programs/LetraCode.lnk"
    shortcut.write_text('{"Description":"other app","TargetPath":"other.exe"}', encoding="utf-8")
    installer.uninstall(local, roaming)
    assert not app.exists()
    assert shortcut.exists()


def test_uninstall_preserves_a_shortcut_with_changed_arguments(installer, locations):
    local, roaming = locations
    app = installer.install(ROOT, local, roaming)
    shortcut = installer.shortcut_path(roaming)
    details = installer.shortcut_details(shortcut)
    details["Arguments"] = "--data-dir another-folder"
    shortcut.write_text(json.dumps(details), encoding="utf-8")
    installer.uninstall(local, roaming)
    assert not app.exists()
    assert shortcut.exists()


def test_uninstall_refuses_unmarked_directory(installer, locations):
    local, roaming = locations
    app = local / "letracode-app"
    app.mkdir(parents=True)
    with pytest.raises(RuntimeError, match="not a marked LetraCode installation"):
        installer.uninstall(local, roaming)
    assert app.is_dir()


def test_uninstall_rename_denial_leaves_entire_installation_untouched(installer, locations, monkeypatch):
    local, roaming = locations
    app = installer.install(ROOT, local, roaming)
    original = {path.relative_to(app): path.read_bytes() for path in app.rglob("*") if path.is_file()}
    shortcut = roaming / "Microsoft/Windows/Start Menu/Programs/LetraCode.lnk"
    original_shortcut = shortcut.read_bytes()
    rename = Path.rename

    def deny_locked_directory(path, target):
        if path == app:
            raise PermissionError("directory is held open by another terminal")
        return rename(path, target)

    monkeypatch.setattr(Path, "rename", deny_locked_directory)
    with pytest.raises(PermissionError, match="held open"):
        installer.uninstall(local, roaming)
    assert original == {path.relative_to(app): path.read_bytes() for path in app.rglob("*") if path.is_file()}
    assert shortcut.read_bytes() == original_shortcut
    assert not list(local.glob(".letracode-app.*"))


def test_uninstall_partial_deletion_restores_marker_and_can_be_retried(installer, locations, monkeypatch):
    local, roaming = locations
    app = installer.install(ROOT, local, roaming)
    shortcut = roaming / "Microsoft/Windows/Start Menu/Programs/LetraCode.lnk"
    original_shortcut = shortcut.read_bytes()
    rmtree = shutil.rmtree

    def fail_after_partial_deletion(path, *args, **kwargs):
        path = Path(path)
        if path == app or path.name.startswith(".letracode-app.remove."):
            (path / ".letracode-install").unlink()
            (path / "letracode.cmd").unlink()
            raise PermissionError("a remaining file is still in use")
        return rmtree(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(shutil, "rmtree", fail_after_partial_deletion)
        with pytest.raises(PermissionError, match="still in use"):
            installer.uninstall(local, roaming)
    assert (app / ".letracode-install").read_text(encoding="utf-8").strip() == "io.letracode.LetraCode"
    assert shortcut.read_bytes() == original_shortcut
    assert not list(local.glob(".letracode-app.*"))
    installer.uninstall(local, roaming)
    assert not app.exists()
    assert not shortcut.exists()


def test_shortcut_errors_include_the_windows_diagnostic(installer, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda executable: "powershell.exe")
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs:
        subprocess.CompletedProcess(args[0], 1, "", "COM could not save the Unicode shortcut"))
    with pytest.raises(RuntimeError, match="COM could not save the Unicode shortcut"):
        installer.powershell("unused")


def test_unicode_shortcut_command_preserves_literal_path(installer, tmp_path):
    app = tmp_path / "O'Brien 日本語 $(literal)/letracode-app"
    arguments = installer.shortcut_arguments(app)
    assert arguments.isascii()
    command = base64.b64decode(arguments.split()[-1]).decode("utf-16-le")
    escaped = str(app / ".venv/Scripts/pythonw.exe").replace("'", "''")
    assert command == f"& '{escaped}' -I -m letracode"


@pytest.mark.skipif(sys.platform != "win32", reason="Needs native Windows PowerShell and COM")
def test_native_shortcut_with_unicode_paths(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("native_windows_install", ROOT / "packaging/windows_install.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    app = tmp_path / "Local App Data é 日本語/letracode-app"
    roaming = tmp_path / "Roaming App Data é 日本語"
    monkeypatch.setenv("LOCALAPPDATA", str(app.parent))
    monkeypatch.setenv("APPDATA", str(roaming))
    target = app / ".venv/Scripts/letracode-gui.exe"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"placeholder")
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(app / ".venv")], check=True)
    package = app / ".venv/Lib/site-packages/letracode"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "__main__.py").write_text(
        "import os\nfrom pathlib import Path\n"
        "Path(os.environ['LETRACODE_SHORTCUT_SMOKE_MARKER']).write_text('launched 日本語', encoding='utf-8')\n",
        encoding="utf-8",
    )
    shortcut = module.shortcut_path(roaming)
    shortcut.parent.mkdir(parents=True)
    temporary = shortcut.with_name(".LetraCode.0123456789abcdef0123456789abcdef.lnk")
    module.write_shortcut(temporary, app)
    temporary.rename(shortcut)
    assert module.managed_shortcut(shortcut, app)
    details = module.shortcut_details(shortcut)
    marker = tmp_path / "shortcut-launch.txt"
    env = dict(os.environ, LETRACODE_SHORTCUT_SMOKE_MARKER=str(marker))
    subprocess.run([details["TargetPath"], *details["Arguments"].split()], env=env, check=True, timeout=20)
    deadline = time.monotonic() + 5
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(0.1)
    assert marker.read_text(encoding="utf-8") == "launched 日本語"


@pytest.mark.parametrize("action", ["install", "uninstall"])
def test_installer_refuses_application_symlink(installer, locations, tmp_path, action):
    local, roaming = locations
    destination = tmp_path / "unrelated"
    destination.mkdir()
    (destination / ".letracode-install").write_text("io.letracode.LetraCode", encoding="utf-8")
    local.mkdir()
    try:
        (local / "letracode-app").symlink_to(destination, target_is_directory=True)
    except OSError:
        pytest.skip("Creating symlinks requires Windows Developer Mode or elevated privileges")
    with pytest.raises(RuntimeError, match="symbolic link|reparse"):
        if action == "install":
            installer.install(ROOT, local, roaming)
        else:
            installer.uninstall(local, roaming)
    assert destination.is_dir()


@pytest.mark.skipif(sys.platform != "win32", reason="Needs native Windows PowerShell, Python, venv and COM")
@pytest.mark.timeout(300)
def test_native_powershell_install_update_launch_and_uninstall(tmp_path):
    source = tmp_path / "Source With Spaces é 日本語"
    shutil.copytree(ROOT, source, ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache", ".venv", "venv", "*.egg-info", "dist", "build"))
    local = tmp_path / "Local App Data é 日本語"
    roaming = tmp_path / "Roaming App Data é 日本語"
    # Exercise the probe's stdout with a real Unicode interpreter path. A venv
    # alone would still report the original ASCII sys._base_executable path.
    runtime = tmp_path / "Python With Spaces é 日本語"
    runtime.mkdir()
    base = Path(sys.base_prefix)
    for name in ("python.exe", "pythonw.exe"):
        shutil.copy2(base / name, runtime / name)
    for dll in base.glob("*.dll"):
        shutil.copy2(dll, runtime / dll.name)
    for name in ("Lib", "DLLs"):
        if (base / name).is_dir():
            shutil.copytree(base / name, runtime / name, ignore=shutil.ignore_patterns("site-packages", "__pycache__"))
    env = os.environ.copy()
    env.update(LOCALAPPDATA=str(local), APPDATA=str(roaming), LETRACODE_PYTHON=str(runtime / "python.exe"))
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONUTF8", None)
    env.pop("PYTHONIOENCODING", None)
    data = local / "letracode/keep.txt"
    data.parent.mkdir(parents=True)
    data.write_text("my chats", encoding="utf-8")

    def run_script(path, cwd=None, check=True):
        result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(path)], cwd=cwd, env=env, text=True, encoding="utf-8", capture_output=True, timeout=600)
        if check:
            assert result.returncode == 0, result.stdout + result.stderr
        return result

    app = local / "letracode-app"
    run_script(source / "install.ps1")
    (app / "obsolete-file").write_bytes(b"old")
    run_script(source / "install.ps1")
    assert not (app / "obsolete-file").exists()
    shutil.rmtree(source)
    result = subprocess.run([str(app / ".venv/Scripts/python.exe"), "-I", "-m", "letracode", "--version"], cwd=tmp_path, env=env, text=True, capture_output=True, check=True)
    assert result.stdout.strip() == "LetraCode 0.3.0"
    cli_check = tmp_path / "check launcher.ps1"
    cli_check.write_text("& (Join-Path $env:LOCALAPPDATA 'letracode-app\\letracode.cmd') --version\nexit $LASTEXITCODE\n", encoding="utf-8")
    assert run_script(cli_check).stdout.strip() == "LetraCode 0.3.0"
    subprocess.run([str(app / ".venv/Scripts/python.exe"), "-I", "-c", "from PySide6 import QtWidgets; import pypdf"], cwd=tmp_path, env=env, check=True)
    assert (app / ".venv/Scripts/letracode-gui.exe").is_file()
    gui_data = tmp_path / "GUI Launch Data"
    gui_env = dict(env, QT_QPA_PLATFORM="offscreen")
    gui = subprocess.Popen([str(app / ".venv/Scripts/letracode-gui.exe"), "--data-dir", str(gui_data)], cwd=tmp_path, env=gui_env)
    try:
        deadline = time.monotonic() + 30
        while not (gui_data / "app.lock").is_file() and time.monotonic() < deadline and gui.poll() is None:
            time.sleep(0.1)
        assert (gui_data / "letracode.sqlite3").is_file(), "GUI launcher did not initialize its data store"
        assert (gui_data / "app.lock").is_file(), "GUI launcher did not acquire the application lock"
        # Give Qt initialization time to expose an immediate startup crash.
        with pytest.raises(subprocess.TimeoutExpired):
            gui.wait(timeout=2)
    finally:
        if gui.poll() is None:
            subprocess.run(["taskkill.exe", "/PID", str(gui.pid), "/T", "/F"], check=True, capture_output=True)
        gui.wait(timeout=10)
    shortcut = roaming / "Microsoft/Windows/Start Menu/Programs/LetraCode.lnk"
    assert shortcut.is_file()
    original_marker = (app / ".letracode-install").read_bytes()
    original_shortcut = shortcut.read_bytes()
    # A separate parent terminal cannot have its cwd changed by our bootstrap.
    holder = subprocess.Popen([sys.executable, "-I", "-c", "import sys; sys.stdin.read()"], cwd=app, stdin=subprocess.PIPE)
    try:
        blocked = run_script(app / "uninstall.ps1", cwd=app, check=False)
        assert blocked.returncode != 0, "Uninstall must fail before deleting a directory held open by another process"
        assert (app / ".letracode-install").read_bytes() == original_marker
        assert (app / ".venv/Scripts/letracode-gui.exe").is_file()
        assert (app / "uninstall.ps1").is_file()
        assert shortcut.read_bytes() == original_shortcut
    finally:
        holder.stdin.close()
        holder.wait(timeout=10)
    run_script(app / "uninstall.ps1", cwd=app)
    assert not app.exists()
    assert not shortcut.exists()
    assert data.read_text(encoding="utf-8") == "my chats"
