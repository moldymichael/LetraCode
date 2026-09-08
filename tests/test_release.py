from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tarfile
import zipfile


ROOT = Path(__file__).resolve().parents[1]


def test_release_archives_include_both_platform_installers_and_are_reproducible(tmp_path):
    output = tmp_path / "Release Output With Spaces"
    command = [sys.executable, str(ROOT / "packaging/build-release.py"), "--output-dir", str(output)]
    subprocess.run(command, check=True, capture_output=True)
    zipped = output / "LetraCode-0.3.0.zip"
    assert zipped.is_file(), "Windows users need an extractable source ZIP"
    first = {file.name: file.read_bytes() for file in output.iterdir()}
    with zipfile.ZipFile(zipped) as archive:
        names = set(archive.namelist())
        assert archive.testzip() is None
        archive.extractall(tmp_path / "Extracted Source")
    prefix = "LetraCode-0.3.0/"
    for name in ("install.ps1", "uninstall.ps1", "install.sh", "uninstall.sh", "packaging/windows_install.py", "packaging/windows-bootstrap.ps1", "tests/test_windows_install.py", "tests/test_release.py", "tests/conftest.py"):
        assert prefix + name in names
    assert not any("__pycache__" in name or name.endswith(".pyc") for name in names)
    assert not any("packaging-brief" in name or "packaging-report" in name for name in names)
    with tarfile.open(output / "LetraCode-0.3.0.tar.gz") as archive:
        assert set(archive.getnames()) == names
        assert archive.getmember(prefix + "install.sh").mode & 0o111
    if os.name != "nt":
        assert (output / "LetraCode-0.3.0.run").stat().st_mode & 0o111
    subprocess.run(command, check=True, capture_output=True)
    assert first == {file.name: file.read_bytes() for file in output.iterdir()}

    extracted = tmp_path / "Extracted Source/LetraCode-0.3.0"
    # Git for Windows may check out text as CRLF; Linux installers must remain runnable.
    for name in names:
        path = tmp_path / "Extracted Source" / name
        path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    subprocess.run([sys.executable, str(extracted / "packaging/build-release.py"), "--output-dir", str(tmp_path / "Rebuilt")], check=True, capture_output=True)
    assert first == {file.name: file.read_bytes() for file in (tmp_path / "Rebuilt").iterdir()}
