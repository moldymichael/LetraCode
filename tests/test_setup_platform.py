import sys

import pytest
from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QMessageBox

from letracode.dialogs import ModelDialog
from letracode.engine import EngineConfig


@pytest.mark.parametrize('platform, expected, absent', [
    ('win32', 'llama-server.exe', 'sudo dnf'),
    ('linux', 'sudo dnf install llama-cpp', 'DLLs'),
])
def test_model_setup_shows_native_guidance(platform, expected, absent, monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(sys, 'platform', platform)
    dialog = ModelDialog(EngineConfig(executable='selected-engine'))
    text = '\n'.join(label.text() for label in dialog.findChildren(QLabel))
    assert expected in text
    assert absent not in text
    assert ('.exe' in dialog.executable.placeholderText()) == (platform == 'win32')
    assert not dialog.styleSheet()
    dialog.close()


def test_windows_engine_picker_filters_exe(monkeypatch):
    from letracode.dialogs import QFileDialog
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(sys, 'platform', 'win32')
    dialog = ModelDialog(EngineConfig(executable='selected-engine'))
    filters = []
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *args: (filters.append(args[-1]) or '', ''))
    dialog.browse(QLineEdit(), 'engine')
    dialog.browse(QLineEdit(), 'model')
    assert filters == ['Windows executable (*.exe)', 'GGUF models (*.gguf)']
    dialog.close()


def test_windows_engine_discovery_requests_native_exe(monkeypatch):
    import letracode.dialogs as dialogs
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(sys, 'platform', 'win32')
    requests = []
    monkeypatch.setattr(dialogs.shutil, 'which', lambda name: requests.append(name))
    dialog = ModelDialog(EngineConfig())
    assert requests == ['llama-server.exe']
    dialog.close()


def test_windows_setup_rejects_batch_file(monkeypatch, tmp_path):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(sys, 'platform', 'win32')
    executable = tmp_path / 'llama-server.cmd'
    executable.write_text('@echo wrong engine', encoding='utf-8')
    executable.chmod(0o700)
    warnings = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda parent, title, message: warnings.append(message))
    dialog = ModelDialog(EngineConfig(executable=str(executable)))
    dialog.validate()
    assert warnings and 'llama-server.exe' in warnings[0]
    dialog.close()
