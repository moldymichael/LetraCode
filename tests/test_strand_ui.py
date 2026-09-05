import json
from pathlib import Path

import pytest

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


def test_model_markdown_cannot_create_internal_undo_controls():
    from letracode.ui import assistant_html
    from PySide6.QtGui import QTextDocument
    app = QApplication.instance() or QApplication([])
    rendered = assistant_html('[Read docs](letracode:undo-memory/123) [Official](https://example.org)', app.font())
    doc = QTextDocument(); doc.setHtml(rendered)
    assert not doc.find('Read docs').charFormat().isAnchor()
    assert doc.find('Official').charFormat().anchorHref() == 'https://example.org'


def test_autosave_keeps_memory_cursor_and_editor_undo(tmp_path):
    from PySide6.QtGui import QTextCursor
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path / 'data'); w = MainWindow(store)
    editor = w.context_editors['memory']
    editor.setPlainText('First word')
    cursor = editor.textCursor(); cursor.movePosition(QTextCursor.MoveOperation.End)
    editor.setTextCursor(cursor); editor.insertPlainText(' added')
    position = editor.textCursor().position()
    assert editor.document().isUndoAvailable()
    w.save_editors()
    assert editor.textCursor().position() == position
    assert editor.document().isUndoAvailable()
    w.close()


def test_dialog_resolution_does_not_revive_old_pane_draft(tmp_path, monkeypatch):
    from letracode.strand_ui import StrandDialog
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path / 'data'); w = MainWindow(store)
    w.context_editors['memory'].setPlainText('Stale pane draft')
    Path(store.strand.snapshot('global')['path']).write_text('External correction')
    assert w.save_editors() is False
    def resolve(dialog):
        dialog.scope.setCurrentIndex(dialog.scope.findData('global'))
        dialog.reload_current()
        dialog.editor.setPlainText('Resolved user version')
        assert dialog.save_current()
        return 0
    monkeypatch.setattr(StrandDialog, 'exec', resolve)
    w.edit_strand()
    assert w.context_editors['memory'].toPlainText() == 'Resolved user version'
    assert store.setting('strand_draft_global_global') is None
    assert w.save_editors()
    w.close()


def test_retry_preserves_conflicting_memory_and_does_not_add_a_turn(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path / 'data'); chat = store.create_chat('Global')
    store.add_message(chat, 'user', 'An earlier request')
    w = MainWindow(store); w.select_chat(chat)
    w.context_editors['memory'].setPlainText('Pending local correction')
    path = Path(store.strand.snapshot('global')['path']); path.write_text('External correction')
    before = store.messages(chat)
    monkeypatch.setattr(w, 'start_worker', lambda:None)
    w.retry_reply()
    assert store.messages(chat) == before
    assert path.read_text() == 'External correction'
    assert store.setting('strand_draft_global_global')['text'] == 'Pending local correction'
    w.close()


def test_retry_saves_pending_memory_before_starting_the_next_turn(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path / 'data'); chat = store.create_chat('Global')
    store.add_message(chat, 'user', 'An earlier request')
    w = MainWindow(store); w.select_chat(chat)
    w.context_editors['memory'].setPlainText('Corrected before retry')
    observed = []
    monkeypatch.setattr(w, 'start_worker', lambda:observed.append(store.strand.snapshot('global')['text']))
    w.retry_reply()
    assert observed == ['Corrected before retry']
    assert len(store.messages(chat)) == 2
    w.close()


@pytest.mark.parametrize('scope', ['global', 'project'])
def test_first_send_memory_conflict_preserves_unsent_draft_after_reopen(tmp_path, monkeypatch, scope):
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path / 'data')
    project = store.create_project('Novel') if scope == 'project' else None
    w = MainWindow(store); w.show_selection(None, project)
    w.engine_config.model_path = str(tmp_path / 'unused.gguf')
    w.engine_config.executable = str(tmp_path / 'unused-server')
    monkeypatch.setattr(w, 'start_worker', lambda:pytest.fail('Conflicted send started inference'))
    draft = '  An unsent first question\nwith another line.  '
    w.composer.setPlainText(draft)
    w.context_editors['memory'].setPlainText('Local memory draft')
    path = Path(store.strand.snapshot(scope, project)['path'])
    path.write_text('External memory correction', encoding='utf-8')

    w.send()

    assert w.composer.toPlainText() == draft
    assert store.chats() == []
    assert path.read_text() == 'External memory correction'
    w.close()
    again = MainWindow(Store(tmp_path / 'data')); again.show_selection(None, project)
    assert again.composer.toPlainText() == draft
    assert again.context_editors['memory'].toPlainText() == 'Local memory draft'
    again.reload_memory()
    again.engine_config.model_path = str(tmp_path / 'unused.gguf')
    again.engine_config.executable = str(tmp_path / 'unused-server')
    monkeypatch.setattr(again, 'start_worker', lambda:None)
    again.send()
    assert [message['content'] for message in store.messages(again.chat_id)] == [draft.strip()]
    assert path.read_text() == 'External memory correction'
    again.close()


@pytest.mark.parametrize('scope', ['global', 'project'])
def test_successful_first_send_saves_memory_and_clears_only_sent_draft(tmp_path, monkeypatch, scope):
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path / 'data')
    project = store.create_project('Novel') if scope == 'project' else None
    store.set_setting('unbound_draft_other-project', 'Keep this unrelated draft')
    w = MainWindow(store); w.show_selection(None, project)
    w.engine_config.model_path = str(tmp_path / 'unused.gguf')
    w.engine_config.executable = str(tmp_path / 'unused-server')
    monkeypatch.setattr(w, 'start_worker', lambda:None)
    w.composer.setPlainText('My first question')
    w.context_editors['memory'].setPlainText('Memory for the first question')

    w.send()

    assert [message['content'] for message in store.messages(w.chat_id)] == ['My first question']
    assert store.chat(w.chat_id)['project_id'] == project
    assert store.strand.snapshot(scope, project)['text'] == 'Memory for the first question'
    assert w.composer.toPlainText() == ''
    assert store.chat(w.chat_id)['draft'] == ''
    assert store.setting('unbound_draft_' + (project or 'global')) == ''
    assert store.setting('unbound_draft_other-project') == 'Keep this unrelated draft'
    w.close()


def test_memory_disappearing_during_first_chat_creation_keeps_transferred_draft(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path / 'data'); w = MainWindow(store)
    w.engine_config.model_path = str(tmp_path / 'unused.gguf')
    w.engine_config.executable = str(tmp_path / 'unused-server')
    monkeypatch.setattr(w, 'start_worker', lambda:pytest.fail('Unavailable memory started inference'))
    w.composer.setPlainText('Keep this first question')
    w.context_editors['memory'].setPlainText('Memory before it disappears')
    path = Path(store.strand.snapshot('global')['path'])
    create_chat = store.create_chat
    def remove_memory_after_creation(*args):
        chat = create_chat(*args)
        path.unlink()
        return chat
    monkeypatch.setattr(store, 'create_chat', remove_memory_after_creation)

    w.send()

    assert w.composer.toPlainText() == 'Keep this first question'
    assert store.chat(w.chat_id)['draft'] == 'Keep this first question'
    assert store.messages(w.chat_id) == []
    assert not path.exists()
    w.close()
    again = MainWindow(Store(tmp_path / 'data'))
    assert again.composer.toPlainText() == 'Keep this first question'
    again.close()


@pytest.mark.parametrize('damage', ['missing', 'inaccessible', 'malformed'])
def test_unavailable_project_memory_editor_requires_reload_after_repair(tmp_path, damage):
    from letracode.strand_ui import MemoryEditorState
    store = Store(tmp_path / 'data'); project = store.create_project('Novel')
    path = Path(store.strand.snapshot('project', project)['path'])
    path.write_text('Authoritative memory', encoding='utf-8')
    if damage == 'missing':
        path.unlink()
    elif damage == 'inaccessible':
        path.chmod(0)
    else:
        path.write_bytes(b'\xff malformed memory')
    try:
        state = MemoryEditorState(store, 'project', project)
        assert not state.available
        assert str(path) in state.error
        assert state.save('Must not replace unavailable memory') is False
        if damage == 'missing':
            assert not path.exists()
        else:
            path.chmod(0o600)
            expected = b'Authoritative memory' if damage == 'inaccessible' else b'\xff malformed memory'
            assert path.read_bytes() == expected
        path.write_text('Repaired external memory', encoding='utf-8')
        assert state.save('Still require an explicit reload') is False
        assert path.read_text() == 'Repaired external memory'
        assert state.reload() is True
        assert state.available and state.text == 'Repaired external memory'
        assert state.save('User correction after reload') is True
    finally:
        if path.exists():
            path.chmod(0o600)


def test_failed_memory_reload_keeps_conflicting_draft_for_reopen(tmp_path):
    from letracode.strand_ui import MemoryEditorState
    store = Store(tmp_path / 'data'); project = store.create_project('Novel')
    state = MemoryEditorState(store, 'project', project)
    path = Path(state.snapshot['path']); path.write_text('External edit', encoding='utf-8')
    assert state.save('Local conflict draft') is False
    path.unlink()

    assert state.reload() is False

    assert state.text == 'Local conflict draft'
    assert store.setting(state.key)['text'] == 'Local conflict draft'
    reopened = MemoryEditorState(Store(tmp_path / 'data'), 'project', project)
    assert reopened.text == 'Local conflict draft'
    assert reopened.save(reopened.text) is False
    assert not path.exists()


@pytest.mark.parametrize('damage', ['missing', 'inaccessible', 'malformed'])
def test_unavailable_last_project_memory_does_not_block_app_or_other_chats(tmp_path, damage):
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path / 'data')
    damaged = store.create_project('Damaged project'); affected = store.create_chat('Affected chat', damaged)
    healthy = store.create_project('Healthy project'); unrelated = store.create_chat('Unrelated chat', healthy)
    store.add_message(affected, 'user', 'Still readable history')
    store.set_setting('last_chat', affected)
    path = Path(store.strand.snapshot('project', damaged)['path'])
    if damage == 'missing':
        path.unlink()
    elif damage == 'inaccessible':
        path.chmod(0)
    else:
        path.write_bytes(b'\xff malformed memory')
    w = None
    try:
        w = MainWindow(Store(tmp_path / 'data'))
        assert w.chat_id == affected
        assert 'Still readable history' in w.transcript.toPlainText()
        assert w.context_editors['memory'].isReadOnly()
        assert str(path) in w.context_hint.text()
        assert 'unavailable' in w.context_hint.text().lower()
        w.context_editors['current_context'].setPlainText('Project context remains editable')
        w.composer.setPlainText('Keep this affected draft')
        w.save_editors()
        w.select_chat(unrelated)
        assert not w.context_editors['memory'].isReadOnly()
        w.context_editors['memory'].setPlainText('Healthy memory')
        w.composer.setPlainText('Unrelated draft')
        assert w.save_editors()
        assert store.strand.snapshot('project', healthy)['text'] == 'Healthy memory'
        assert store.chat(unrelated)['draft'] == 'Unrelated draft'
        w.select_chat(affected)
        assert w.composer.toPlainText() == 'Keep this affected draft'
        assert w.context_editors['current_context'].toPlainText() == 'Project context remains editable'
        if damage == 'inaccessible':
            path.chmod(0o600)
        path.write_text('Externally repaired memory', encoding='utf-8')
        w.reload_memory()
        assert not w.context_editors['memory'].isReadOnly()
        assert w.context_editors['memory'].toPlainText() == 'Externally repaired memory'
        assert w.save_editors()
    finally:
        if path.exists():
            path.chmod(0o600)
        if w:
            w.close()


def test_strand_dialog_unavailable_memory_is_readonly_and_reload_recovers(tmp_path):
    from letracode.strand_ui import StrandDialog
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path / 'data'); project = store.create_project('Novel')
    path = Path(store.strand.snapshot('project', project)['path']); path.unlink()
    dialog = StrandDialog(store, project)
    dialog.scope.setCurrentIndex(dialog.scope.findData('project'))
    try:
        assert dialog.editor.isReadOnly()
        assert str(path) in dialog.message.text()
        assert dialog.save_current() is False
        assert not path.exists()
        path.write_text('Restored project memory', encoding='utf-8')
        dialog.reload_current()
        assert not dialog.editor.isReadOnly()
        assert dialog.editor.toPlainText() == 'Restored project memory'
        assert dialog.save_current()
    finally:
        dialog.close()


def test_project_undo_history_survives_more_than_fifty_unrelated_saves(tmp_path, monkeypatch):
    from datetime import datetime, timedelta
    from itertools import count
    from types import SimpleNamespace
    from PySide6.QtWidgets import QPushButton
    import letracode.strand as strand_module
    from letracode.strand_ui import StrandDialog

    # Whole-second receipt dates must put the unrelated saves after this project.
    ticks = count()
    monkeypatch.setattr(strand_module, 'datetime', SimpleNamespace(
        now=lambda zone:datetime(2026, 9, 5, tzinfo=zone) + timedelta(seconds=next(ticks))))
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path / 'data')
    project = store.create_project('Quiet project')
    other = store.create_project('Unrelated project')
    store.strand.path('project', project).write_text('Original project memory', encoding='utf-8')
    before = store.strand.snapshot('project', project)
    receipt = store.strand.replace('project', 'Project change to undo', before['sha256'], project)
    store.update_project(other, memory='Keep unrelated project memory')
    for index in range(51):
        snapshot = store.strand.snapshot('global')
        store.strand.replace('global', f'Unrelated global save {index}', snapshot['sha256'])
    dialog = StrandDialog(store, project)
    dialog.scope.setCurrentIndex(dialog.scope.findData('project'))
    try:
        assert dialog.history.count() == 1
        assert dialog.history.currentData() == receipt['id']
        undo = next(button for button in dialog.findChildren(QPushButton)
                    if button.text() == 'Undo selected saved change')
        undo.click()
        assert store.strand.snapshot('project', project)['text'] == 'Original project memory'
        assert dialog.editor.toPlainText() == 'Original project memory'
        assert store.strand.snapshot('project', other)['text'] == 'Keep unrelated project memory'
        assert store.strand.snapshot('global')['text'] == 'Unrelated global save 50'
    finally:
        dialog.close()
