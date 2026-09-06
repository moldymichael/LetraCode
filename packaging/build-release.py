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


ROOT = Path(__file__).resolve().parents[1]


def release_files() -> list[Path]:
    files = [
        ROOT / name
        for name in ("pyproject.toml", "install.sh", "uninstall.sh", "README.md", "LICENSE")
    ]
    files.extend((ROOT / "letracode").glob("*.py"))
    files.append(ROOT / "letracode/assets/io.letracode.LetraCode.svg")
    files.extend((ROOT / "tests").glob("test_*.py"))
    files.extend(
        ROOT / "packaging" / name
        for name in (
            "build-release.py",
            "build-rpm.sh",
            "letracode.spec",
            "io.letracode.LetraCode.desktop",
            "io.letracode.LetraCode.metainfo.xml",
        )
    )
    # Ship capability claims with their linked current/historical explanations.
    # Include portable verification entry points, never raw scratch or models.
    files.extend((ROOT / "docs").rglob("*.md"))
    files.extend((ROOT / "tools").glob("*.py"))
    missing = [path for path in files if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"required release file is missing: {missing[0]}")
    return sorted(set(files))


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
                info.mode = 0o755 if os.access(path, os.X_OK) else 0o644
                with path.open("rb") as source:
                    archive.addfile(info, source)
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
    atomic_write(source_archive, payload, 0o644)
    atomic_write(run_installer, run_stub(version) + payload, 0o755)
    print(source_archive)
    print(run_installer)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
