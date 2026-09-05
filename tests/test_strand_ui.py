import json
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtWidgets import QApplication

from letracode.store import Store
from letracode.ui import MainWindow


def test_global_memory_is_editable_and_external_correction_is_not_overwritten(tmp_path):
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path / 'data'); chat = store.create_chat('Global')
    w = MainWindow(store); w.select_chat(chat)
    assert w.context_editors['memory'].isEnabled()
    w.context_editors['memory'].setPlainText('User preference')
    w.save_editors()
    path = Path(store.strand.snapshot('global')['path'])
    assert path.read_text() == 'User preference'
    path.write_text('External correction', encoding='utf-8')
    w.composer.setPlainText('Draft only'); w.save_editors()
    assert path.read_text() == 'External correction'
    w.close()


def test_conflicted_memory_draft_survives_close_and_reopen(tmp_path):
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path / 'data'); project = store.create_project('Novel')
    chat = store.create_chat('Planning', project)
    w = MainWindow(store); w.select_chat(chat)
    w.context_editors['memory'].setPlainText('Local unsaved change')
    path = Path(store.strand.snapshot('project', project)['path'])
    path.write_text('External edit wins', encoding='utf-8')
    assert w.save_editors() is False
    assert path.read_text() == 'External edit wins'
    w.close()
    again = MainWindow(Store(tmp_path / 'data')); again.select_chat(chat)
    assert again.context_editors['memory'].toPlainText() == 'Local unsaved change'
    assert path.read_text() == 'External edit wins'
    again.close()


def test_strand_editor_and_learning_grant_are_explicit_and_persistent(tmp_path):
    from letracode.strand_ui import StrandDialog
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path / 'data')
    dialog = StrandDialog(store)
    assert not dialog.learning_grant.isChecked()
    dialog.scope.setCurrentIndex(dialog.scope.findData('preferences'))
    dialog.editor.setPlainText('Use brief explanations.')
    assert dialog.save_current()
    assert store.strand.snapshot('preferences')['text'] == 'Use brief explanations.'
    dialog.learning_grant.setChecked(True)
    assert store.setting('strand_learning_grant') is True
    dialog.close()


def test_visible_memory_receipt_can_undo_and_refresh_editor(tmp_path):
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path / 'data'); chat = store.create_chat('Global')
    receipt = store.strand.remember('global', 'Saved preference', origin='user', expected_sha256=store.strand.snapshot('global')['sha256'])
    message = {'role':'tool','name':'remember','tool_call_id':'save','content':json.dumps(receipt)}
    store.add_message(chat,'tool',message['content'],payload={'message':message})
    w = MainWindow(store); w.select_chat(chat); w.render_chat()
    visible = w.transcript.toPlainText()
    assert 'Saved preference' in visible and 'Undo' in visible
    ident = receipt.get('receipt_id', receipt.get('id'))
    w.open_link(QUrl(f'letracode:undo-memory/{ident}'))
    assert 'Saved preference' not in store.strand.snapshot('global')['text']
    assert 'Saved preference' not in w.context_editors['memory'].toPlainText()
    w.close()


def test_app_acquires_data_lock_before_opening_or_migrating_store(tmp_path, monkeypatch):
    import sys
    from PySide6.QtCore import QLockFile
    from PySide6.QtWidgets import QMessageBox
    import PySide6.QtWidgets
    from letracode.app import main
    app = QApplication.instance() or QApplication([])
    data = tmp_path / 'data'; data.mkdir()
    lock = QLockFile(str(data / 'app.lock'))
    assert lock.tryLock(0)
    monkeypatch.setattr(sys, 'argv', ['letracode', '--data-dir', str(data)])
    monkeypatch.setattr(PySide6.QtWidgets, 'QApplication', lambda *a:app)
    monkeypatch.setattr(QMessageBox, 'information', lambda *a:None)
    monkeypatch.setattr(QMessageBox, 'critical', lambda *a:None)
    constructed = []
    def forbidden_store(*args, **kwargs):
        constructed.append(True)
        raise RuntimeError('Store constructed before lock')
    monkeypatch.setattr('letracode.store.Store', forbidden_store)
    try:
        main()
        assert constructed == []
    finally:
        lock.unlock()
