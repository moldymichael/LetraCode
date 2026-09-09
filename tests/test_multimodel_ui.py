import os
import time
import json

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from letracode.dialogs import ModelDialog
from letracode.engine import EngineConfig
from letracode.store import Store
from letracode.ui import MainWindow


@pytest.fixture
def window(tmp_path):
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path / 'data')
    win = MainWindow(store)
    yield win
    if win.worker:
        win.stop()
        finish(app, win)
    win.close()


def finish(app, window):
    deadline = time.monotonic() + 5
    while window.worker and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert window.worker is None
    app.processEvents()


def test_exchange_preferences_survive_chat_switch_and_reopen(window):
    one = window.store.create_chat('One')
    two = window.store.create_chat('Two')
    window.select_chat(one)
    window.conversation_mode.setCurrentIndex(1)
    window.first_speaker.setCurrentIndex(1)
    window.reply_count.setValue(4)
    window.select_chat(two)
    assert window.conversation_mode.currentIndex() == 0
    window.select_chat(one)
    assert window.conversation_mode.currentIndex() == 1
    assert window.first_speaker.currentIndex() == 1
    assert window.reply_count.value() == 4
    window.close()
    again = MainWindow(Store(window.store.directory))
    try:
        assert again.conversation_mode.currentIndex() == 1
        assert again.first_speaker.currentIndex() == 1
        assert again.reply_count.value() == 4
    finally:
        again.close()


def test_exchange_controls_bound_replies_and_keep_tool_preferences(window):
    window.conversation_mode.setCurrentIndex(1)
    assert not window.actions.isEnabled()
    assert not window.internet.isEnabled()
    assert window.computer.isEnabled()
    assert 'no tools' in window.dialogue_hint.text().lower()
    assert not window.continue_button.isEnabled()
    window.reply_count.setValue(999)
    assert window.reply_count.value() == 4
    window.reply_count.setValue(0)
    assert window.reply_count.value() == 1
    window.set_busy(True)
    for widget in (window.conversation_mode, window.first_speaker, window.reply_count, window.continue_button):
        assert not widget.isEnabled()
    window.set_busy(False)
    assert not window.actions.isEnabled()
    window.conversation_mode.setCurrentIndex(0)
    assert window.actions.isEnabled() and window.actions.isChecked()
    assert window.internet.isEnabled() and window.internet.isChecked()


def test_saved_speaker_label_is_plain_text_and_survives_model_change(window):
    chat = window.store.create_chat('History')
    window.store.add_message(chat, 'assistant', 'Draft', status='interrupted', payload={
        'speaker': {'id': 'old', 'label': 'Model B · <old & model>.gguf', 'model': 'local-b'}})
    window.select_chat(chat)
    window.engine_config.model_path = '/new/model.gguf'
    window.render_chat()
    assert 'Model B · <old & model>.gguf · interrupted' in window.transcript.toPlainText()


def test_continue_requires_empty_composer_and_never_duplicates_user_turn(window):
    chat = window.store.create_chat('Dialogue')
    window.store.add_message(chat, 'user', 'Discuss this')
    window.select_chat(chat)
    window.conversation_mode.setCurrentIndex(1)
    assert window.continue_button.isEnabled()
    assert not window.retry_button.isEnabled()
    window.composer.setPlainText('A new instruction waiting to send')
    assert not window.continue_button.isEnabled()
    before = window.store.messages(chat)
    window.continue_exchange()
    assert window.store.messages(chat) == before
    assert window.composer.toPlainText() == 'A new instruction waiting to send'


def test_optional_second_model_validation_preserves_single_model_setup(tmp_path, monkeypatch):
    executable = tmp_path / 'llama-server.exe'
    executable.write_text('engine')
    executable.chmod(0o700)
    first = tmp_path / 'a.gguf'
    first.write_bytes(b'GGUF' + bytes(64))
    config = EngineConfig(executable=str(executable), model_path=str(first))
    app = QApplication.instance() or QApplication([])
    dialog = ModelDialog(config)
    assert dialog.secondary_model.text() == ''
    warnings = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: warnings.append(args[2]))
    dialog.secondary_model.setText(str(first))
    dialog.validate()
    assert warnings and dialog.result() != QDialog.DialogCode.Accepted
    second = tmp_path / 'b.gguf'
    second.write_bytes(b'incomplete')
    dialog.secondary_model.setText(str(second))
    warnings.clear()
    dialog.validate()
    assert warnings and dialog.result() != QDialog.DialogCode.Accepted
    second.write_bytes(b'GGUF' + bytes(64))
    dialog.validate()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.config().secondary_model_path == str(second)
    single = ModelDialog(config)
    single.validate()
    assert single.result() == QDialog.DialogCode.Accepted
    assert single.config().secondary_model_path == ''


class RecordingEngine:
    def __init__(self, config, data_dir):
        self.config = config
        self.requests = []
        self.running = False
        self.loaded_models = ()

    def start(self, cancel, on_status=lambda _: None):
        self.running = True
        self.loaded_models = ('local', 'local-b') if self.config.secondary_model_path else ('local',)

    def complete(self, messages, tools, cancel, on_delta, thinking=False, model='local'):
        self.requests.append((model, messages, tools))
        text = f'{model} reply {len(self.requests)}'
        on_delta(text)
        return {'role': 'assistant', 'content': text}

    def request_usage(self, messages, tools, cancel, thinking=False, model='local'):
        from letracode.budgeting import RequestUsage
        size = len(json.dumps(messages)) + len(json.dumps(tools))
        return RequestUsage((size + 1) // 2, self.config.max_tokens, 128, 'synthetic tokenizer')

    def stop(self):
        self.running = False
        self.loaded_models = ()

    cancel = stop


def configured_window(tmp_path, monkeypatch):
    from letracode import ui
    monkeypatch.setattr(ui, 'LocalEngine', RecordingEngine)
    store = Store(tmp_path / 'data')
    store.set_setting('engine', {'executable': '/llama-server', 'model_path': '/a.gguf', 'secondary_model_path': '/b.gguf'})
    return MainWindow(store)


def test_exchange_dispatch_is_bounded_and_continue_adds_no_user_message(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    win = configured_window(tmp_path, monkeypatch)
    try:
        win.conversation_mode.setCurrentIndex(1)
        win.reply_count.setValue(1)
        win.composer.setPlainText('Discuss the tradeoffs')
        win.send()
        assert not win.continue_button.isEnabled()
        finish(app, win)
        assert len(win.engine.requests) == 1
        assert win.engine.requests[0][0] == 'local'
        assert win.first_speaker.currentIndex() == 1
        assert win.conversation_mode.currentIndex() == 1
        assert win.continue_button.isEnabled()
        assert not win.retry_button.isEnabled()
        win.continue_exchange()
        finish(app, win)
        assert [request[0] for request in win.engine.requests] == ['local', 'local-b']
        rows = win.store.messages(win.chat_id)
        assert [r['content'] for r in rows if r['role'] == 'user'] == ['Discuss the tradeoffs']
        assert len([r for r in rows if r['role'] == 'assistant']) == 2
        assert all(request[2] is None for request in win.engine.requests)
        assert 'local reply 1' in str(win.engine.requests[1][1])
        assert win.first_speaker.currentIndex() == 0
        # Processing idle events must not dispatch another model reply.
        app.processEvents()
        assert len(win.engine.requests) == 2
    finally:
        if win.worker:
            win.stop()
            finish(app, win)
        win.close()


def test_single_mode_uses_only_primary_and_keeps_legacy_retry(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    win = configured_window(tmp_path, monkeypatch)
    try:
        assert win.engine.config.secondary_model_path == ''
        win.composer.setPlainText('Hello')
        win.send()
        finish(app, win)
        assert [r['role'] for r in win.store.messages(win.chat_id)] == ['user', 'assistant']
        assert win.retry_button.isEnabled()
        win.retry_reply()
        finish(app, win)
        assert len([r for r in win.store.messages(win.chat_id) if r['role'] == 'user']) == 2
        previous = win.engine
        win.conversation_mode.setCurrentIndex(1)
        assert not previous.running
        assert win.engine.config.secondary_model_path == '/b.gguf'
        win.conversation_mode.setCurrentIndex(0)
        assert win.engine.config.secondary_model_path == ''
    finally:
        if win.worker:
            win.stop()
            finish(app, win)
        win.close()


def test_model_state_distinguishes_loading_loaded_and_paused(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    win = configured_window(tmp_path, monkeypatch)
    try:
        win.conversation_mode.setCurrentIndex(1)
        assert 'Paused' in win.model_label.text()
        assert win.model_label.text().count('not loaded') == 2
        win.worker = object()
        win.render_chat()
        assert 'Loading models' in win.model_label.text()
        win.engine.loaded_models = ('local', 'local-b')
        win.render_chat()
        assert 'Exchange running' in win.model_label.text()
        assert 'not loaded' not in win.model_label.text()
        win.worker = None
        win.render_chat()
        assert 'Paused' in win.model_label.text()
        win.unload_model()
        assert win.model_label.text().count('not loaded') == 2
    finally:
        win.worker = None
        win.close()


@pytest.mark.parametrize('payload', ['null', '[]', '{invalid'])
def test_invalid_speaker_metadata_keeps_history_readable(window, payload):
    chat = window.store.create_chat('Readable history')
    message = window.store.add_message(chat, 'assistant', 'Saved prose')
    with window.store.connection() as db:
        db.execute('UPDATE messages SET payload=? WHERE id=?', (payload, message))
    window.select_chat(chat)
    assert 'LetraCode · Response saved · Task outcome unverified' in window.transcript.toPlainText()
    assert 'Saved prose' in window.transcript.toPlainText()


def test_model_speaker_preserves_status_and_model_action_link_boundary(window):
    chat = window.store.create_chat('Safe model contributions')
    window.store.add_message(chat, 'assistant', '[Unsafe undo](letracode:undo-memory/fake)',
        payload={'speaker': {'label': 'Model A · <local>.gguf'}})
    window.select_chat(chat)
    window.conversation_mode.setCurrentIndex(1)
    assert 'Model A · <local>.gguf · Response saved · Task outcome unverified' in window.transcript.toPlainText()
    assert 'letracode:undo-memory/fake' not in window.transcript.toHtml()
