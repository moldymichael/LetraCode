"""Platform-specific desktop setup guidance without importing Qt."""
import sys


def is_windows() -> bool:
    return sys.platform == 'win32'


def engine_setup_help() -> str:
    if is_windows():
        return ('On Windows, extract a compatible Windows llama.cpp release and select '
                'llama-server.exe. Keep its DLLs in the same extracted folder.')
    return 'On Fedora, install the engine with: sudo dnf install llama-cpp'


def qt_install_help() -> str:
    if is_windows():
        return 'LetraCode needs PySide6. Run install.ps1 again, or install PySide6 in your Python virtual environment.'
    return 'LetraCode needs the system Qt bindings. On Fedora run: sudo dnf install python3-pyside6'


def pdf_install_help() -> str:
    if is_windows():
        return 'PDF support needs pypdf. Run install.ps1 again, or install pypdf in your Python virtual environment.'
    return 'PDF support needs the Fedora package python3-pypdf.'
