#!/usr/bin/env python3
"""Build reproducible source and self-extracting per-user installer archives."""
from __future__ import annotations

import argparse
import gzip
import io
import os
from pathlib import Path
import stat
import tarfile
import tempfile
import tomllib
import zipfile


ROOT = Path(__file__).resolve().parents[1]


def release_files() -> list[Path]:
    files = [
        ROOT / name
        for name in ("pyproject.toml", "install.sh", "uninstall.sh", "install.ps1", "uninstall.ps1", "README.md", "LICENSE")
    ]
    files.extend((ROOT / "letracode").glob("*.py"))
    files.append(ROOT / "letracode/assets/io.letracode.LetraCode.svg")
    files.extend((ROOT / "tests").glob("test_*.py"))
    files.append(ROOT / "tests/conftest.py")
    files.extend(
        ROOT / "packaging" / name
        for name in (
            "build-release.py",
            "build-rpm.sh",
            "letracode.spec",
            "windows_install.py",
            "windows-bootstrap.ps1",
            "io.letracode.LetraCode.desktop",
            "io.letracode.LetraCode.metainfo.xml",
        )
    )
    files.extend((ROOT / "docs").rglob("*.md"))
    files.extend((ROOT / "tools").glob("*.py"))
    files.extend((ROOT / ".github/workflows").glob("*.yml"))
    files.extend((ROOT / "packaging/licenses").rglob("*.txt"))
    files.extend(ROOT / "packaging" / name for name in (
        "build-windows.py", "windows-launcher.py", "windows.iss",
        "windows-requirements.txt", "smoke-windows.py",
    ))
    files.append(ROOT / ".gitattributes")
    missing = [path for path in files if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"required release file is missing: {missing[0]}")
    return sorted(set(files))


def file_mode(path: Path) -> int:
    # Windows does not preserve POSIX executable bits. The archives must still
    # contain runnable Fedora installers when the release is built on Windows.
    return 0o755 if path.suffix == ".sh" or path.name == "build-release.py" else 0o644


def release_content(path: Path) -> bytes:
    # Every release_files entry is text. Keep Fedora shell scripts executable
    # and archives reproducible even after a Git for Windows CRLF checkout.
    return path.read_bytes().replace(b"\r\n", b"\n")


def archive_bytes(version: str) -> bytes:
    prefix = f"LetraCode-{version}"
    raw = io.BytesIO()
    with gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as archive:
            for path in release_files():
                relative = path.relative_to(ROOT)
                info = archive.gettarinfo(str(path), arcname=f"{prefix}/{relative.as_posix()}")
                info.uid = info.gid = 0
                info.uname = info.gname = "root"
                info.mtime = 0
                info.mode = file_mode(path)
                content = release_content(path)
                info.size = len(content)
                archive.addfile(info, io.BytesIO(content))
    return raw.getvalue()


def zip_bytes(version: str) -> bytes:
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in release_files():
            info = zipfile.ZipInfo(f"LetraCode-{version}/{path.relative_to(ROOT).as_posix()}", date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | file_mode(path)) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, release_content(path))
    return raw.getvalue()


def run_stub(version: str) -> bytes:
    directory = f"LetraCode-{version}"
    return f"""#!/bin/sh
set -eu
temp_dir=$(mktemp -d "${{TMPDIR:-/tmp}}/letracode-installer.XXXXXX")
cleanup() {{ rm -rf -- "$temp_dir"; }}
trap cleanup EXIT HUP INT TERM
archive_line=$(awk '/^__LETRACODE_ARCHIVE_BELOW__$/ {{ print NR + 1; exit }}' "$0")
tail -n +"$archive_line" "$0" | tar -xzf - -C "$temp_dir"
"$temp_dir/{directory}/install.sh" "$@"
exit $?
__LETRACODE_ARCHIVE_BELOW__
""".encode("utf-8")


def atomic_write(path: Path, content: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary_name, mode)
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist")
    arguments = parser.parse_args()
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    version = metadata["project"]["version"]
    payload = archive_bytes(version)
    source_archive = arguments.output_dir / f"LetraCode-{version}.tar.gz"
    run_installer = arguments.output_dir / f"LetraCode-{version}.run"
    windows_archive = arguments.output_dir / f"LetraCode-{version}.zip"
    atomic_write(source_archive, payload, 0o644)
    atomic_write(run_installer, run_stub(version) + payload, 0o755)
    atomic_write(windows_archive, zip_bytes(version), 0o644)
    print(source_archive)
    print(run_installer)
    print(windows_archive)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
