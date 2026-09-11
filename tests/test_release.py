from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tomllib
import zipfile


ROOT = Path(__file__).resolve().parents[1]


def test_release_metadata_uses_one_current_version():
    from letracode import __version__
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert metadata["project"]["version"] == __version__ == "0.6.0"
    assert f"Version:        {__version__}\n" in (ROOT / "packaging/letracode.spec").read_text()
    assert f"LetraCode-{__version__}.tar.gz" in (ROOT / "packaging/build-rpm.sh").read_text()


def test_portable_archive_preserves_binary_files_and_folder_layout(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("windows_release", ROOT / "packaging/build-windows.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    bundle = tmp_path / "Bundle with spaces"
    (bundle / "_internal").mkdir(parents=True)
    payload = b"MZ\x00\r\n\xffbinary"
    (bundle / "LetraCode.exe").write_bytes(payload)
    (bundle / "_internal/python311.dll").write_bytes(payload)
    zipped = module.portable_archive(bundle, tmp_path, "0.6.0")
    with zipfile.ZipFile(zipped) as archive:
        assert set(archive.namelist()) == {
            "LetraCode-0.6.0/LetraCode.exe", "LetraCode-0.6.0/_internal/python311.dll"}
        assert all(archive.read(name) == payload for name in archive.namelist())


def test_windows_builder_finds_current_compiler_and_honors_explicit_path(tmp_path, monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location("windows_release", ROOT / "packaging/build-windows.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    native = tmp_path / "Program Files"
    legacy = tmp_path / "Program Files (x86)"
    current = native / "Inno Setup 7/ISCC.exe"
    old = legacy / "Inno Setup 6/ISCC.exe"
    for path in (current, old):
        path.parent.mkdir(parents=True)
        path.write_bytes(b"compiler fixture")
    monkeypatch.setenv("ProgramFiles", str(native))
    monkeypatch.setenv("ProgramFiles(x86)", str(legacy))
    monkeypatch.setattr(module.shutil, "which", lambda name: None)
    assert module.compiler_path() == str(current.resolve())
    assert module.compiler_path(str(old)) == str(old.resolve())


def test_windows_bundle_includes_current_guides_and_training_inputs(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("windows_release", ROOT / "packaging/build-windows.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.copy_support_files(tmp_path)
    for name in ("README.md", "CONTRIBUTING.md", "AGENTS.md", "LICENSE",
                 "docs/WINDOWS.md", "packaging/training-requirements.txt",
                 "packaging/gemma-training-requirements.txt"):
        assert (tmp_path / name).read_bytes() == (ROOT / name).read_bytes()


def test_source_archives_include_platform_build_inputs_and_are_reproducible(tmp_path):
    output = tmp_path / "Release Output With Spaces"
    command = [sys.executable, str(ROOT / "packaging/build-release.py"), "--output-dir", str(output)]
    subprocess.run(command, check=True, capture_output=True)
    zipped = output / "LetraCode-0.6.0.zip"
    assert zipped.is_file(), "Source developers need an extractable ZIP"
    first = {file.name: file.read_bytes() for file in output.iterdir()}
    with zipfile.ZipFile(zipped) as archive:
        names = set(archive.namelist())
        assert archive.testzip() is None
        archive.extractall(tmp_path / "Extracted Source")
    prefix = "LetraCode-0.6.0/"
    for name in (
        "install.sh", "uninstall.sh", "AGENTS.md", "CONTRIBUTING.md", "packaging/build-windows.py",
        "packaging/windows-launcher.py", "packaging/windows.iss",
        "packaging/windows-requirements.txt", "packaging/smoke-windows.py",
        "tests/test_release.py", "tests/conftest.py",
        "packaging/training-requirements.txt", "packaging/gemma-training-requirements.txt",
        ".github/pull_request_template.md", ".github/ISSUE_TEMPLATE/bug_report.md",
        "letracode/training_backend.py",
    ):
        assert prefix + name in names
    assert not any("__pycache__" in name or name.endswith(".pyc") for name in names)
    assert not any("packaging-brief" in name or "packaging-report" in name for name in names)
    with tarfile.open(output / "LetraCode-0.6.0.tar.gz") as archive:
        assert set(archive.getnames()) == names
        assert archive.getmember(prefix + "install.sh").mode & 0o111
    if os.name != "nt":
        assert (output / "LetraCode-0.6.0.run").stat().st_mode & 0o111
    subprocess.run(command, check=True, capture_output=True)
    assert first == {file.name: file.read_bytes() for file in output.iterdir()}

    extracted = tmp_path / "Extracted Source/LetraCode-0.6.0"
    # Git for Windows may check out text as CRLF; Linux installers must remain runnable.
    for name in names:
        path = tmp_path / "Extracted Source" / name
        path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    subprocess.run([sys.executable, str(extracted / "packaging/build-release.py"), "--output-dir", str(tmp_path / "Rebuilt")], check=True, capture_output=True)
    assert first == {file.name: file.read_bytes() for file in (tmp_path / "Rebuilt").iterdir()}
