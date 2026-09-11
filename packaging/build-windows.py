#!/usr/bin/env python3
"""Build the self-contained x64 Windows installer and portable application ZIP.

Run on Windows after installing windows-requirements.txt and Inno Setup 7.
The application dependencies and Python runtime are bundled; no model is.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tomllib
import zipfile


ROOT = Path(__file__).resolve().parents[1]


def version() -> str:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]


def compiler_path(explicit: str | None = None) -> str:
    if explicit:
        if not Path(explicit).is_file():
            raise RuntimeError(f"Explicit Inno Setup compiler does not exist: {explicit}")
        return str(Path(explicit).resolve())
    candidates = [shutil.which("ISCC.exe")]
    candidates.extend(str(Path(os.environ.get(key, fallback)) / f"Inno Setup {major}/ISCC.exe")
                      for major in (7, 6)
                      for key, fallback in (("ProgramFiles", "C:/Program Files"),
                                            ("ProgramFiles(x86)", "C:/Program Files (x86)")))
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(Path(candidate).resolve())
    raise RuntimeError("Install Inno Setup 7 from https://jrsoftware.org/isdl.php, or pass --iscc PATH.")


def copy_support_files(bundle: Path) -> None:
    """Keep the shipped guides and both separate training environments usable."""
    for name in ("LICENSE", "README.md", "CONTRIBUTING.md", "AGENTS.md"):
        shutil.copy2(ROOT / name, bundle / name)
    shutil.copytree(ROOT / "docs", bundle / "docs", dirs_exist_ok=True)
    (bundle / "packaging").mkdir(exist_ok=True)
    for name in ("training-requirements.txt", "gemma-training-requirements.txt"):
        shutil.copy2(ROOT / "packaging" / name, bundle / "packaging" / name)


def create_icon(destination: Path) -> None:
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer
    from PIL import Image

    png = destination.with_suffix(".png")
    canvas = QImage(256, 256, QImage.Format.Format_ARGB32)
    canvas.fill(Qt.GlobalColor.transparent)
    renderer = QSvgRenderer(str(ROOT / "letracode/assets/io.letracode.LetraCode.svg"))
    if not renderer.isValid():
        raise RuntimeError("The LetraCode SVG icon is invalid")
    painter = QPainter(canvas)
    renderer.render(painter)
    painter.end()
    if not canvas.save(str(png)):
        raise RuntimeError("Could not render the application icon")
    with Image.open(png) as icon:
        icon.save(destination, sizes=[(n, n) for n in (16, 24, 32, 48, 64, 128, 256)])


def write_licenses(bundle: Path) -> None:
    """Keep wheel license texts and source pointers alongside replaceable DLLs."""
    licenses = bundle / "licenses"
    licenses.mkdir()
    shutil.copytree(ROOT / "packaging/licenses/Qt", licenses / "Qt")
    notes = [
        "LetraCode includes Python, Qt for Python (PySide6/Shiboken), pypdf and the PyInstaller bootloader.",
        "The bundled Qt DLLs remain separate files in _internal and can be replaced with compatible builds.",
        "Qt/PySide source and license information: https://www.qt.io/qt-for-python and https://code.qt.io/",
        "Python source: https://www.python.org/downloads/source/",
        "pypdf source: https://github.com/py-pdf/pypdf",
        "PyInstaller bootloader source and distribution exception: https://github.com/pyinstaller/pyinstaller",
        "See the individual license texts in this directory. No model or llama.cpp binary is bundled.",
        "",
    ]
    for name in ("PySide6", "PySide6_Essentials", "PySide6_Addons", "shiboken6", "pypdf", "pyinstaller"):
        distribution = importlib.metadata.distribution(name)
        notes.append(f"{distribution.metadata['Name']} {distribution.version}")
        for relative in distribution.files or []:
            if any("license" in part.lower() or "copying" in part.lower() for part in relative.parts):
                source = Path(distribution.locate_file(relative))
                if source.is_file():
                    # Wheel paths may contain '..'; preserve only their safe basename.
                    target = licenses / name / str(relative).replace("..", "_").replace("\\", "/").lstrip("/")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if not python_license.is_file():
        raise RuntimeError("Python's LICENSE.txt is required in the Windows runtime distribution")
    shutil.copy2(python_license, licenses / "Python-LICENSE.txt")
    (bundle / "THIRD-PARTY-NOTICES.txt").write_text("\n".join(notes) + "\n", encoding="utf-8")


def portable_archive(bundle: Path, output: Path, release_version: str) -> Path:
    destination = output / f"LetraCode-{release_version}-windows-x64-portable.zip"
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(bundle.rglob("*")):
            if path.is_file():
                archive.write(path, f"LetraCode-{release_version}/{path.relative_to(bundle).as_posix()}")
    return destination


def build(output: Path, iscc: str | None = None) -> list[Path]:
    if sys.platform != "win32" or platform.machine().lower() not in ("amd64", "x86_64"):
        raise RuntimeError("Windows x64 releases must be built using native Windows x64 Python.")
    compiler = compiler_path(iscc)
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    work = ROOT / "build/windows"
    work.mkdir(parents=True, exist_ok=True)
    icon = work / "LetraCode.ico"
    create_icon(icon)
    command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--windowed",
               "--name", "LetraCode", "--noupx", "--icon", str(icon),
               "--paths", str(ROOT), "--distpath", str(work / "dist"), "--workpath", str(work / "cache"),
               "--specpath", str(work), "--collect-data", "letracode", "--collect-submodules", "pypdf",
               "--add-data", str(ROOT / "letracode/training_backend.py") + os.pathsep + "letracode",
               "--hidden-import", "PySide6.QtSvg", str(ROOT / "packaging/windows-launcher.py")]
    subprocess.run(command, cwd=ROOT, check=True)
    bundle = work / "dist/LetraCode"
    copy_support_files(bundle)
    release_version = version()
    (bundle / "version.json").write_text(json.dumps({"version": release_version, "python": platform.python_version(),
        "architecture": "x64"}, indent=2) + "\n", encoding="utf-8")
    write_licenses(bundle)
    subprocess.run([compiler, f"/DAppVersion={release_version}", f"/DBundlePath={bundle}",
        f"/DOutputPath={output}", f"/DSourcePath={ROOT}", f"/DIconPath={icon}",
        str(ROOT / "packaging/windows.iss")], cwd=ROOT, check=True)
    installer = output / f"LetraCode-{release_version}-windows-x64-setup.exe"
    if not installer.is_file():
        raise RuntimeError("Inno Setup did not produce the expected installer")
    artifacts = [installer, portable_archive(bundle, output, release_version)]
    checksum = output / f"LetraCode-{release_version}-windows-SHA256SUMS.txt"
    hashes = []
    for path in artifacts:
        with path.open("rb") as content:
            hashes.append(f"{hashlib.file_digest(content, 'sha256').hexdigest()}  {path.name}\n")
    checksum.write_text("".join(hashes), encoding="utf-8")
    return [*artifacts, checksum]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist")
    parser.add_argument("--iscc")
    arguments = parser.parse_args()
    for path in build(arguments.output_dir, arguments.iscc):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
