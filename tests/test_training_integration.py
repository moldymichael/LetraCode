import json
import zipfile

from PySide6.QtWidgets import QApplication, QFileDialog

from letracode.store import Store
from letracode.ui import MainWindow


def test_backup_keeps_current_training_editor_text(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(Store(tmp_path / 'data'))
    target = tmp_path / 'backup.zip'
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a, **kw: (str(target), ''))
    try:
        window.training_panel.prompt.setPlainText('Unsaved training prompt')
        window.training_panel.response.setPlainText('Unsaved desired response')
        window.backup()
        with zipfile.ZipFile(target) as archive:
            export = json.loads(archive.read('letracode.json'))
        settings = {r['key']: json.loads(r['value']) for r in export['settings']}
        assert settings['training_editor']['prompt'] == 'Unsaved training prompt'
        assert settings['training_editor']['response'] == 'Unsaved desired response'
    finally:
        window.close()


def test_retry_and_direct_worker_start_cannot_overlap_training(tmp_path):
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path / 'data')
    chat = store.create_chat('Existing conversation')
    store.add_message(chat, 'user', 'Question')
    window = MainWindow(store)
    try:
        window.select_chat(chat)
        window.training_panel.job = object()
        window.set_busy(True)
        window.render_chat()
        assert not window.retry_button.isEnabled()
        window.retry_reply()
        window.start_worker()
        assert window.worker is None
        assert len(store.messages(chat)) == 1
    finally:
        window.training_panel.job = None
        window.set_busy(False)
        window.close()
