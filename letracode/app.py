"""Desktop entry point."""
import argparse
import os
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description='LetraCode — local AI chat for KDE')
    parser.add_argument('--data-dir',type=Path,help='Use a separate local data directory (also useful when restoring a backup).')
    parser.add_argument('--version',action='store_true')
    args = parser.parse_args()
    from . import __version__
    if args.version:
        print('LetraCode '+__version__); return 0
    os.umask(0o077)
    try:
        from PySide6.QtCore import QLockFile
        from PySide6.QtWidgets import QApplication, QMessageBox
    except ImportError as error:
        print('LetraCode needs the system Qt bindings. On Fedora run: sudo dnf install python3-pyside6',file=sys.stderr)
        print(str(error),file=sys.stderr)
        return 1
    app = QApplication([sys.argv[0]])
    app.setApplicationName('LetraCode')
    app.setApplicationVersion(__version__)
    app.setOrganizationName('LetraCode')
    app.setDesktopFileName('io.letracode.LetraCode')
    from .store import Store
    try:
        store = Store(args.data_dir)
        lock = QLockFile(str(store.directory/'app.lock'))
        lock.setStaleLockTime(0)
        if not lock.tryLock(0):
            QMessageBox.information(None,'LetraCode is already open','Another LetraCode instance is using this data folder. Switch to its window to continue.'); return 0
        store.recover_interrupted()
        from .ui import MainWindow
        window = MainWindow(store)
        window.show()
    except Exception as error:
        QMessageBox.critical(None,'Could not open LetraCode',str(error)); return 1
    code = app.exec()
    lock.unlock()
    return code
