"""Fine-Tuning and the installed two-model workflow must remain usable together."""
from __future__ import annotations

import dataclasses
import os
import time
from types import SimpleNamespace

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import QApplication, QDialog

from letracode.dialogs import ModelDialog
from letracode.engine import EngineConfig
from letracode.store import Store
from letracode.ui import MainWindow
from test_training_adoption import completed_version, model_files


@pytest.fixture
def window(tmp_path):
    app = QApplication.instance() or QApplication([])
    win = MainWindow(Store(tmp_path / 'data'))
    yield win
    win.training_panel.job = None
    win.close()
    app.processEvents()


def test_both_workspaces_keep_speaker_labels_and_independent_live_thinking(window):
    assert [window.workspaces.tabText(i) for i in range(window.workspaces.count())] == ['Chat', 'Knowledge', 'Improve', 'Settings']
    chat = window.store.create_chat('Two models thinking')
    saved = window.store.add_message(chat, 'assistant', 'First answer', payload={
        'speaker': {'id': 'a', 'label': 'Model A · <base & adapter>'},
        'reasoning': 'First model reasoning'})
    streaming = window.store.add_message(chat, 'assistant', '', status='streaming', payload={
        'speaker': {'id': 'b', 'label': 'Model B · second.gguf'},
        'reasoning': 'Second model reasoning'})
    window.select_chat(chat)
    window.conversation_mode.setCurrentIndex(1)
    assert window.learn_button.isEnabled()
    assert not window.continue_button.isHidden()
    text = window.transcript.toPlainText()
    assert 'Model A · <base & adapter>' in text and 'Model B · second.gguf' in text
    assert 'First model reasoning' not in text
    assert 'Second model reasoning' in text
    window.open_link(QUrl(f'letracode:thinking/{saved}'))
    window.open_link(QUrl(f'letracode:thinking/{streaming}'))
    assert 'First model reasoning' in window.transcript.toPlainText()
    assert 'Second model reasoning' not in window.transcript.toPlainText()
    window.store.update_message(streaming, 'Second answer', status='complete')
    window.render_chat()
    window.close()
    reopened = MainWindow(Store(window.store.directory))
    try:
        assert reopened.conversation_mode.currentIndex() == 1
        reopened.open_link(QUrl(f'letracode:thinking/{streaming}'))
        assert 'Model B · second.gguf' in reopened.transcript.toPlainText()
        assert 'Second model reasoning' in reopened.transcript.toPlainText()
    finally:
        reopened.close()


def test_learn_from_exchange_uses_latest_complete_answer_without_reasoning(window):
    chat = window.store.create_chat('Learn from Model B')
    window.store.add_message(chat, 'user', 'Compare the options')
    window.store.add_message(chat, 'assistant', 'Model A answer', payload={
        'speaker': {'label': 'Model A'}, 'reasoning': 'First private reasoning'})
    ident = window.store.add_message(chat, 'assistant', 'Model B answer', payload={
        'speaker': {'label': 'Model B'}, 'reasoning': 'Second private reasoning'})
    window.store.add_message(chat, 'assistant', 'Incomplete later reply', status='interrupted')
    window.select_chat(chat)
    window.conversation_mode.setCurrentIndex(1)
    window.learn_button.click()
    panel = window.training_panel
    assert window.workspaces.currentWidget() is panel
    assert panel.prompt.toPlainText().endswith('Compare the options')
    assert 'Model A answer' in panel.prompt.toPlainText()
    assert 'private reasoning' not in panel.prompt.toPlainText()
    assert panel.response.toPlainText() == 'Model B answer'
    assert panel.example_source == f'chat:{chat}/message:{ident}'
    assert panel.repository.examples() == [] and panel.repository.runs() == []
    panel.save_example()
    assert not panel.repository.examples()[0]['approved']


@pytest.mark.parametrize('multi', [False, True])
def test_training_blocks_send_continue_retry_and_mode_changes(window, monkeypatch, multi):
    chat = window.store.create_chat('Paused during training')
    window.store.add_message(chat, 'user', 'Existing question')
    window.select_chat(chat)
    window.conversation_mode.setCurrentIndex(int(multi))
    window.composer.setPlainText('Unsent follow-up')
    before = window.store.messages(chat)
    window.training_panel.job = SimpleNamespace()
    window.set_busy(True)
    window.render_chat()
    for widget in (window.send_button, window.continue_button, window.retry_button,
                   window.learn_button, window.conversation_mode, window.first_speaker, window.reply_count):
        assert not widget.isEnabled()
    assert not window.stop_button.isEnabled()
    monkeypatch.setattr(window, 'prepare_engine', lambda: pytest.fail('Training must block engine preparation'))
    monkeypatch.setattr(window, 'start_worker', lambda: pytest.fail('Training must block reply dispatch'))
    window.send()
    window.retry_reply()
    window.composer.clear()
    window.continue_exchange()
    assert window.store.messages(chat) == before
    window.training_panel.job = None
    window.set_busy(False)
    assert window.send_button.isEnabled()
    assert window.continue_button.isEnabled() == multi
    assert window.retry_button.isEnabled() == (not multi)


def test_model_setup_keeps_both_second_model_and_primary_adapter(tmp_path):
    app = QApplication.instance() or QApplication([])
    executable = tmp_path / 'llama-server.exe'
    executable.write_text('engine'); executable.chmod(0o700)
    paths = [tmp_path / name for name in ('a.gguf', 'b.gguf', 'adapter.gguf')]
    for path in paths:
        path.write_bytes(b'GGUF' + bytes(64))
    config = EngineConfig(executable=str(executable), model_path=str(paths[0]),
        secondary_model_path=str(paths[1]), lora_path=str(paths[2]))
    dialog = ModelDialog(config)
    try:
        dialog.temperature.setValue(0.4)
        dialog.validate()
        assert dialog.result() == QDialog.DialogCode.Accepted
        assert dialog.config() == dataclasses.replace(config, temperature=0.4)
        assert 'only to Strand’s primary model' in dialog.lora.toolTip()
    finally:
        dialog.close()


class ActivationEngine:
    """Only replace the external inference process; keep the activation workflow real."""
    def __init__(self, config, directory):
        self.config = config
        self.running = False

    def start(self, cancel, on_status):
        self.running = True

    def stop(self):
        self.running = False

    cancel = stop


@pytest.mark.parametrize('multi', [False, True])
def test_activation_saves_full_selection_but_loads_current_chat_mode(window, monkeypatch, multi):
    from letracode import training_worker
    monkeypatch.setattr(training_worker, 'LocalEngine', ActivationEngine)
    original = EngineConfig(executable='/llama-server', model_path='/current-a.gguf',
        secondary_model_path='/current-b.gguf', lora_path='/current-adapter.gguf')
    window.engine_config = original
    window.store.set_setting('engine', dataclasses.asdict(original))
    window.conversation_mode.setCurrentIndex(int(multi))
    previous = dataclasses.replace(original, model_path='/previous-a.gguf',
        secondary_model_path='/previous-b.gguf', lora_path='/previous-adapter.gguf')
    window.store.set_setting('training_previous_engine', dataclasses.asdict(previous))
    panel = window.training_panel
    panel.rollback()
    deadline = time.monotonic() + 5
    app = QApplication.instance()
    while panel.job is not None and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert panel.job is None
    assert window.engine_config == previous
    assert window.store.setting('engine') == dataclasses.asdict(previous)
    assert window.store.setting('training_previous_engine') == dataclasses.asdict(original)
    assert window.engine.config == (previous if multi else dataclasses.replace(previous, secondary_model_path=''))
    assert window.engine.running


@pytest.mark.usefixtures('python_engine_peer')
def test_single_mode_can_adopt_base_also_saved_as_model_b(completed_version):
    from letracode.training_worker import ActivationWorker
    repo, run_id, original, adapter = completed_version
    original.secondary_model_path = original.model_path
    worker = ActivationWorker(repo, original, run_id=run_id, secondary_enabled=False)
    ready, failed = [], []
    worker.ready.connect(ready.append)
    worker.failed.connect(failed.append)
    try:
        worker.run()
        assert not failed
        assert len(ready) == 1 and ready[0].running
        assert ready[0].config.secondary_model_path == ''
        assert ready[0].config.lora_path == str(adapter)
        assert worker.selected_config.secondary_model_path == original.model_path
        assert worker.selected_config.lora_path == str(adapter)
        assert original.lora_path == ''
    finally:
        if worker.engine is not None:
            worker.engine.stop()
