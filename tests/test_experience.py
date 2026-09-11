import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QUrl
from PySide6.QtWidgets import QApplication
from letracode.store import Store
from letracode.ui import MainWindow


def test_daily_shell_and_compact_reading_room(tmp_path):
    app = QApplication.instance() or QApplication([])
    w = MainWindow(Store(tmp_path / 'data'))
    try:
        assert [w.workspaces.tabText(i) for i in range(w.workspaces.count())] == ['Chat', 'Knowledge', 'Improve', 'Settings']
        w.resize(850, 570); w.show(); app.processEvents()
        assert w.transcript.height() > w.composer.height() * 2
        assert w.composer.accessibleName() and w.tree.accessibleName()
        assert not w.context_panel.isVisible()
    finally:
        w.close()


def test_drafting_and_navigation_while_job_keeps_its_origin(tmp_path):
    app = QApplication.instance() or QApplication([])
    s = Store(tmp_path / 'data'); one = s.create_chat('Running'); two = s.create_chat('Read me')
    s.add_message(two, 'assistant', 'Earlier saved answer')
    w = MainWindow(s); w.select_chat(one)
    class Job:
        chat_id = one
        def deleteLater(self): pass
    try:
        w.worker = Job(); w.set_busy(True)
        assert w.composer.isEnabled() and w.tree.isEnabled() and w.search.isEnabled()
        assert not w.send_button.isEnabled()
        w.composer.setPlainText('Follow-up for running chat')
        w.select_chat(two)
        assert 'Earlier saved answer' in w.transcript.toPlainText()
        w.composer.setPlainText('Draft for second chat')
        w.worker_finished()
        assert w.chat_id == two
        assert s.chat(one)['draft'] == 'Follow-up for running chat'
        assert s.chat(two)['draft'] == 'Draft for second chat'
        assert w.composer.toPlainText() == 'Draft for second chat'
    finally:
        w.worker = None; w.close()


def test_older_messages_load_without_losing_newest_or_draft(tmp_path):
    app = QApplication.instance() or QApplication([])
    s = Store(tmp_path / 'data'); c = s.create_chat('Long conversation')
    for i in range(230): s.add_message(c, 'user', f'Saved message {i:03}')
    w = MainWindow(s)
    try:
        w.select_chat(c); w.composer.setPlainText('Keep draft')
        assert 'Saved message 000' not in w.transcript.toPlainText()
        assert 'Saved message 229' in w.transcript.toPlainText()
        w.load_older_messages()
        assert 'Saved message 000' in w.transcript.toPlainText()
        assert w.composer.toPlainText() == 'Keep draft'
    finally: w.close()


def test_shared_source_links_do_not_move_or_edit_originals(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    app = QApplication.instance() or QApplication([])
    s = Store(tmp_path / 'data'); original = tmp_path / 'Obsidian.md'; original.write_text('Ordinary note')
    monkeypatch.setattr(QFileDialog, 'getOpenFileNames', lambda *a: ([str(original)], ''))
    w = MainWindow(s)
    try:
        w.files_panel.add_files()
        assert str(original) in s.setting('source_roots')
        assert w.files_panel._select_path(original)
        w.files_panel.remove_selected()
        assert str(original) not in s.setting('source_roots')
        assert original.read_text() == 'Ordinary note'
    finally: w.close()


def test_model_setup_keeps_expert_options_collapsed_and_roundtrips(tmp_path):
    from letracode.dialogs import ModelDialog
    from letracode.engine import EngineConfig
    app = QApplication.instance() or QApplication([])
    cfg = EngineConfig(executable=str(tmp_path / 'llama-server'), model_path=str(tmp_path / 'model.gguf'),
                       secondary_model_path=str(tmp_path / 'other.gguf'), gpu_layers=17,
                       lora_path=str(tmp_path / 'adapter.gguf'))
    dialog = ModelDialog(cfg)
    try:
        assert not dialog.advanced.isVisible()
        assert dialog.config() == cfg
        dialog.advanced_toggle.setChecked(True)
        assert not dialog.advanced.isHidden()
    finally: dialog.close()


def test_readiness_does_not_claim_load_or_change_paths(tmp_path):
    from letracode.experience import configuration_readiness
    from letracode.engine import EngineConfig
    cfg = EngineConfig(model_path=str(tmp_path / 'missing.gguf'))
    checks = configuration_readiness(cfg)
    assert not any(ok for _, ok, _ in checks)
    assert cfg.model_path == str(tmp_path / 'missing.gguf')


def test_selection_cleared_after_reply_reveals_pending_saved_output(tmp_path):
    import time
    from PySide6.QtGui import QTextCursor
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path / 'data'); chat = store.create_chat('Reading')
    store.add_message(chat, 'assistant', 'Original answer')
    w = MainWindow(store)
    try:
        w.select_chat(chat)
        cursor = w.transcript.textCursor(); cursor.select(QTextCursor.SelectionType.Document)
        w.transcript.setTextCursor(cursor)
        store.add_message(chat, 'assistant', 'The completed new answer')
        w.render_chat()
        assert 'The completed new answer' not in w.transcript.toPlainText()
        cursor.clearSelection(); w.transcript.setTextCursor(cursor)
        deadline = time.monotonic() + 1
        while 'The completed new answer' not in w.transcript.toPlainText() and time.monotonic() < deadline:
            app.processEvents(); time.sleep(.01)
        assert 'The completed new answer' in w.transcript.toPlainText()
    finally: w.close()


def test_find_older_message_replaces_existing_selection(tmp_path, monkeypatch):
    from PySide6.QtGui import QTextCursor
    from PySide6.QtWidgets import QInputDialog
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path / 'data'); chat = store.create_chat('Long conversation')
    for i in range(230): store.add_message(chat, 'user', f'Unique-{i:03}')
    w = MainWindow(store)
    try:
        w.select_chat(chat)
        cursor = w.transcript.textCursor(); cursor.select(QTextCursor.SelectionType.Document)
        w.transcript.setTextCursor(cursor)
        monkeypatch.setattr(QInputDialog, 'getText', lambda *a: ('Unique-007', True))
        w.find_in_chat()
        assert w.transcript.textCursor().selectedText() == 'Unique-007'
    finally: w.close()


def test_training_busy_blocks_destructive_context_menu_and_retry(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    app = QApplication.instance() or QApplication([])
    s = Store(tmp_path / 'data'); project = s.create_project('Writing')
    chat = s.create_chat('Kept'); s.add_message(chat, 'user', 'Do something')
    w = MainWindow(s); w.select_chat(chat)
    called = []
    monkeypatch.setattr(QMessageBox, 'question', lambda *a: called.append(True) or QMessageBox.StandardButton.Yes)
    try:
        w.set_busy(True)
        w.delete_selected()
        assert s.chat(chat) is not None and not called
        before = s.messages(chat)
        w.retry_reply()
        assert s.messages(chat) == before
        w.show_selection(None, project)
        assert not w.instructions_action.isEnabled()
    finally: w.set_busy(False); w.close()


def test_changing_base_model_clears_hidden_adapter_but_cancel_keeps_config(tmp_path):
    from letracode.dialogs import ModelDialog
    from letracode.engine import EngineConfig
    app = QApplication.instance() or QApplication([])
    cfg = EngineConfig(model_path=str(tmp_path / 'old.gguf'), lora_path=str(tmp_path / 'trained.gguf'))
    dialog = ModelDialog(cfg)
    try:
        dialog.model.setText(str(tmp_path / 'new.gguf'))
        assert dialog.lora.text() == ''
        assert 'cleared' in dialog.adapter_status.text().lower()
        dialog.reject()
        assert cfg.lora_path == str(tmp_path / 'trained.gguf')
    finally: dialog.close()


def test_shared_source_child_retains_shared_scope_in_a_workspace(tmp_path):
    app = QApplication.instance() or QApplication([])
    source = tmp_path / 'Notes'; source.mkdir(); (source / 'ordinary.md').write_text('Note')
    s = Store(tmp_path / 'data'); p = s.create_project('Focus'); s.set_setting('source_roots', [str(source)])
    w = MainWindow(s)
    try:
        w.show_selection(None, p)
        panel = w.files_panel
        assert panel._select_path(source)
        panel._expand(panel.tree.currentItem())
        assert panel._select_path(source / 'ordinary.md')
        assert panel._selected()['project_id'] is None
        assert 'Shared across workspaces' in panel.status_label.text()
    finally: w.close()


def test_settings_model_check_gets_real_loopback_reply_without_chat(tmp_path, python_engine_peer):
    from letracode.experience import ModelCheck
    from letracode.engine import EngineConfig, LocalEngine
    from test_engine import PEER_SOURCE
    binary = tmp_path / 'llama-server-peer.exe'; binary.write_text(PEER_SOURCE); binary.chmod(0o700)
    model = tmp_path / 'model.gguf'; model.write_bytes(b'GGUF' + bytes(64))
    engine = LocalEngine(EngineConfig(executable=str(binary), model_path=str(model)), tmp_path / 'engine')
    results = []
    job = ModelCheck(engine); job.result.connect(lambda ok, text: results.append((ok, text)))
    try:
        job.run()
        assert results and results[0][0] is True
        assert 'ok' in results[0][1]
        assert 'not established' in results[0][1]
    finally: engine.stop()
