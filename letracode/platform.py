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
        return 'LetraCode needs PySide6. In your Python virtual environment, run: python -m pip install PySide6'
    return 'LetraCode needs the system Qt bindings. On Fedora run: sudo dnf install python3-pyside6'


def pdf_install_help() -> str:
    if is_windows():
        return 'PDF support needs pypdf. In your Python virtual environment, run: python -m pip install pypdf'
    return 'PDF support needs the Fedora package python3-pypdf.'
