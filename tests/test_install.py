from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile

import pytest


pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="Fedora shell installer")

ROOT = Path(__file__).resolve().parents[1]
APP_ID = "io.letracode.LetraCode"


def isolated_environment(tmp_path: Path) -> tuple[dict[str, str], Path, Path]:
    home = tmp_path / "Home With Spaces"
    data_home = home / "Data With Spaces"
    home.mkdir()
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "XDG_DATA_HOME": str(data_home),
            "XDG_CONFIG_HOME": str(home / "Config With Spaces"),
            "XDG_CACHE_HOME": str(home / "Cache With Spaces"),
            "LETRACODE_PYTHON": sys.executable,
        }
    )
    return env, home, data_home


def copy_source_to_path_with_spaces(tmp_path: Path) -> Path:
    source = tmp_path / "Extracted LetraCode Source"
    source.mkdir()
    for name in ("install.sh", "uninstall.sh", "pyproject.toml", "README.md", "LICENSE"):
        shutil.copy2(ROOT / name, source / name)
    shutil.copytree(ROOT / "letracode", source / "letracode")
    shutil.copytree(ROOT / "packaging", source / "packaging")
    return source


def run_script(script: Path, env: dict[str, str], *args: str, check: bool = True):
    return subprocess.run(
        ["bash", str(script), *args],
        env=env,
        text=True,
        capture_output=True,
        check=check,
    )


def test_install_update_launch_and_uninstall_preserve_user_data(tmp_path: Path):
    env, home, data_home = isolated_environment(tmp_path)
    source = copy_source_to_path_with_spaces(tmp_path)
    persistent_data = data_home / "letracode"
    persistent_data.mkdir(parents=True)
    sentinel = persistent_data / "keep-me.txt"
    sentinel.write_text("my chats", encoding="utf-8")

    first = run_script(source / "install.sh", env, "--no-deps")
    assert "Installation complete" in first.stdout

    app_dir = data_home / "letracode-app"
    launcher = home / ".local/bin/letracode"
    desktop = data_home / "applications" / f"{APP_ID}.desktop"
    icon = data_home / "icons/hicolor/scalable/apps" / f"{APP_ID}.svg"
    metainfo = data_home / "metainfo" / f"{APP_ID}.metainfo.xml"
    assert (app_dir / ".letracode-install").read_text(encoding="utf-8").strip() == APP_ID
    assert launcher.stat().st_mode & 0o111
    assert icon.is_file()
    assert metainfo.is_file()

    desktop_text = desktop.read_text(encoding="utf-8")
    assert desktop_text.startswith("[Desktop Entry]\n")
    assert "Type=Application\n" in desktop_text
    assert f"Icon={APP_ID}\n" in desktop_text
    assert "Terminal=false\n" in desktop_text
    assert f'Exec="{launcher}"\n' in desktop_text

    version = subprocess.run(
        [str(launcher), "--version"], env=env, text=True, capture_output=True, check=True
    )
    assert version.stdout.strip() == "LetraCode 0.5.0"

    (app_dir / "obsolete-file").write_text("old", encoding="utf-8")
    run_script(source / "install.sh", env, "--no-deps")
    assert not (app_dir / "obsolete-file").exists()
    assert sentinel.read_text(encoding="utf-8") == "my chats"

    shutil.rmtree(source)
    version_after_source_removal = subprocess.run(
        [str(launcher), "--version"], env=env, text=True, capture_output=True, check=True
    )
    assert version_after_source_removal.stdout.strip() == "LetraCode 0.5.0"

    uninstall = run_script(app_dir / "uninstall.sh", env)
    assert "User data was kept" in uninstall.stdout
    assert not app_dir.exists()
    assert not launcher.exists()
    assert not desktop.exists()
    assert not icon.exists()
    assert not metainfo.exists()
    assert sentinel.read_text(encoding="utf-8") == "my chats"


def test_launcher_uses_installed_build_even_inside_another_checkout(tmp_path: Path):
    env, home, _ = isolated_environment(tmp_path)
    run_script(ROOT / 'install.sh', env, '--no-deps')
    checkout = tmp_path / 'older-checkout'
    package = checkout / 'letracode'
    package.mkdir(parents=True)
    (package / '__init__.py').write_text('')
    (package / '__main__.py').write_text("print('Wrong checkout')")
    result = subprocess.run([str(home / '.local/bin/letracode'), '--version'],
        cwd=checkout, env=env, text=True, capture_output=True, check=True)
    from letracode import __version__
    assert result.stdout.strip() == 'LetraCode ' + __version__


def test_install_refuses_to_replace_unmarked_directory(tmp_path: Path):
    env, _, data_home = isolated_environment(tmp_path)
    destination = data_home / "letracode-app"
    destination.mkdir(parents=True)
    valuable = destination / "valuable.txt"
    valuable.write_text("leave this alone", encoding="utf-8")

    result = run_script(ROOT / "install.sh", env, "--no-deps", check=False)

    assert result.returncode != 0
    assert "not a LetraCode installation" in result.stderr
    assert valuable.read_text(encoding="utf-8") == "leave this alone"


def test_rpm_launcher_uses_packaged_code_inside_another_checkout(tmp_path):
    from letracode import __version__
    spec = (ROOT / 'packaging/letracode.spec').read_text()
    script = spec.split("<<'EOF'\n", 1)[1].split('\nEOF', 1)[0]
    data = tmp_path / 'share'
    shutil.copytree(ROOT / 'letracode', data / 'letracode/letracode')
    launcher = tmp_path / 'rpm-launcher'
    launcher.write_text(script.replace('%{_datadir}', str(data)).replace('%{_bindir}/python3', sys.executable))
    checkout = tmp_path / 'checkout'
    (checkout / 'letracode').mkdir(parents=True)
    (checkout / 'letracode/__init__.py').write_text('')
    (checkout / 'letracode/__main__.py').write_text("print('Wrong checkout')")
    result = subprocess.run(['sh', str(launcher), '--version'], cwd=checkout,
        text=True, capture_output=True, check=True)
    assert result.stdout.strip() == 'LetraCode ' + __version__


@pytest.mark.parametrize("packaged", [False, True], ids=["source", "run-installer"])
def test_normal_install_succeeds_with_fedora_44_packages_and_no_docx(tmp_path: Path, packaged):
    env, home, _ = isolated_environment(tmp_path)
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    sudo_log = tmp_path / "sudo.log"
    fake_sudo = fake_bin / "sudo"
    fake_sudo.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' \"$*\" > \"$LETRACODE_TEST_SUDO_LOG\"\n"
        "[ \"$1\" = dnf ] && [ \"$2\" = install ] || exit 2\n"
        "shift 2\n"
        "for package do\n"
        "  case \"$package\" in\n"
        "    -y|python3|python3-pyside6|python3-pypdf) ;;\n"
        "    *) printf 'No match for argument: %s\\n' \"$package\" >&2; exit 1 ;;\n"
        "  esac\n"
        "done\n",
        encoding="utf-8",
    )
    fake_sudo.chmod(0o755)
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["LETRACODE_TEST_SUDO_LOG"] = str(sudo_log)
    import_log = tmp_path / "imports.log"
    fake_modules = tmp_path / "fake-modules"
    (fake_modules / "PySide6").mkdir(parents=True)
    (fake_modules / "PySide6/__init__.py").write_text("", encoding="utf-8")
    module_code = (
        "import os\n"
        "with open(os.environ['LETRACODE_IMPORT_LOG'], 'a', encoding='utf-8') as log:\n"
        "    log.write(__name__ + '\\n')\n"
    )
    (fake_modules / "PySide6/QtWidgets.py").write_text(module_code, encoding="utf-8")
    (fake_modules / "pypdf.py").write_text(module_code, encoding="utf-8")
    # Even a developer machine with python-docx installed must reproduce its absence.
    (fake_modules / "docx.py").write_text(
        "raise ImportError('python-docx is unavailable on this Fedora 44 system')\n",
        encoding="utf-8",
    )
    env["PYTHONPATH"] = str(fake_modules)
    env["LETRACODE_IMPORT_LOG"] = str(import_log)

    installer = ROOT / "install.sh"
    if packaged:
        output = tmp_path / "release output"
        subprocess.run(
            [sys.executable, str(ROOT / "packaging/build-release.py"), "--output-dir", str(output)],
            check=True, capture_output=True,
        )
        installer = output / "LetraCode-0.5.0.run"
    installed = run_script(installer, env, check=False)

    assert installed.returncode == 0, installed.stdout + installed.stderr
    assert "Installation complete" in installed.stdout
    launcher = home / ".local/bin/letracode"
    subprocess.run([str(launcher), "--version"], env=env, check=True, capture_output=True)
    assert set(import_log.read_text(encoding="utf-8").splitlines()) == {
        "PySide6.QtWidgets",
        "pypdf",
    }


def test_install_refuses_to_overwrite_unmanaged_launcher(tmp_path: Path):
    env, home, data_home = isolated_environment(tmp_path)
    launcher = home / ".local/bin/letracode"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("#!/bin/sh\necho unrelated\n", encoding="utf-8")

    result = run_script(ROOT / "install.sh", env, "--no-deps", check=False)

    assert result.returncode != 0
    assert "not managed by the LetraCode installer" in result.stderr
    assert launcher.read_text(encoding="utf-8") == "#!/bin/sh\necho unrelated\n"
    assert not (data_home / "letracode-app").exists()


@pytest.mark.parametrize("argument", ["--unknown", "--no-deps=yes"])
def test_install_rejects_unknown_options(tmp_path: Path, argument: str):
    env, _, _ = isolated_environment(tmp_path)
    result = run_script(ROOT / "install.sh", env, argument, check=False)
    assert result.returncode == 2
    assert "Usage:" in result.stderr


def test_release_builder_makes_clean_source_archive_and_runnable_installer(tmp_path: Path):
    output = tmp_path / "release output"
    subprocess.run(
        [sys.executable, str(ROOT / "packaging/build-release.py"), "--output-dir", str(output)],
        text=True,
        capture_output=True,
        check=True,
    )
    source_archive = output / "LetraCode-0.5.0.tar.gz"
    run_installer = output / "LetraCode-0.5.0.run"
    assert source_archive.is_file()
    assert run_installer.stat().st_mode & 0o111

    with tarfile.open(source_archive, "r:gz") as archive:
        names = set(archive.getnames())
    prefix = "LetraCode-0.5.0/"
    assert prefix + "README.md" in names
    assert prefix + "LICENSE" in names
    assert prefix + "tests/test_install.py" in names
    assert prefix + "letracode/app.py" in names
    assert not any("packaging-brief" in name or "packaging-report" in name for name in names)
    assert not any("__pycache__" in name or name.endswith(".pyc") for name in names)

    env, home, data_home = isolated_environment(tmp_path)
    subprocess.run(
        [str(run_installer), "--no-deps"], env=env, text=True, capture_output=True, check=True
    )
    launcher = home / ".local/bin/letracode"
    version = subprocess.run(
        [str(launcher), "--version"], env=env, text=True, capture_output=True, check=True
    )
    assert version.stdout.strip() == "LetraCode 0.5.0"
    run_script(data_home / "letracode-app/uninstall.sh", env)
