import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

from PySide6.QtCore import QUrl
from PySide6.QtWidgets import QApplication

from letracode.store import Store
from letracode.ui import MainWindow


def deny_file_reads(path):
    """Make a disposable fixture unreadable with the host's permission model."""
    if os.name == 'nt':
        account = os.environ['USERNAME']
        subprocess.run(['icacls', str(path), '/deny', account + ':(RD)'],
                       check=True, capture_output=True, timeout=10)
        return lambda: subprocess.run(['icacls', str(path), '/remove:d', account],
                                      check=True, capture_output=True, timeout=10)
    mode = stat.S_IMODE(path.stat().st_mode)
    path.chmod(0)
    return lambda: path.chmod(mode)


def file_dialog(store, project=None):
    """Open an existing legacy file through the explicit file editor."""
    from letracode.memory_ui import MemoryDialog
    dialog = MemoryDialog(store, project)
    dialog.scope.setCurrentIndex(dialog.scope.findData(project))
    scope = 'project' if project else 'global'
    relative = store.strand.path(scope, project).relative_to(store.memory.root_for(project)).as_posix()
    # Missing aliases with no draft need an explicit state to inspect their
    # unavailable condition; the normal file browser does not recreate them.
    if not dialog.select_path(relative):
        from letracode.memory_ui import MemoryFileEditorState
        dialog.state = MemoryFileEditorState(store, relative, project)
        dialog.show_state()
    return dialog


def test_global_memory_is_editable_and_external_correction_is_not_overwritten(tmp_path):
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path / 'data'); chat = store.create_chat('Global')
    w = MainWindow(store); w.select_chat(chat)
    dialog = file_dialog(store)
    assert not dialog.editor.isReadOnly()
    dialog.editor.setPlainText('User preference')
    assert dialog.save_current()
    dialog.close()
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
    dialog = file_dialog(store, project)
    dialog.editor.setPlainText('Local unsaved change')
    path = Path(store.strand.snapshot('project', project)['path'])
    path.write_text('External edit wins', encoding='utf-8')
    assert dialog.save_current() is False
    dialog.close()
    assert w.save_editors()
    assert path.read_text() == 'External edit wins'
    w.close()
    again = MainWindow(Store(tmp_path / 'data')); again.select_chat(chat)
    reopened = file_dialog(again.store, project)
    assert reopened.editor.toPlainText() == 'Local unsaved change'
    reopened.close()
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
    dialog = file_dialog(store)
    assert 'Saved preference' not in dialog.editor.toPlainText()
    dialog.close()
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


def test_chat_autosave_leaves_file_cursor_and_editor_undo_untouched(tmp_path):
    from PySide6.QtGui import QTextCursor
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path / 'data'); w = MainWindow(store)
    dialog = file_dialog(store)
    editor = dialog.editor
    editor.setPlainText('First word')
    cursor = editor.textCursor(); cursor.movePosition(QTextCursor.MoveOperation.End)
    editor.setTextCursor(cursor); editor.insertPlainText(' added')
    position = editor.textCursor().position()
    assert editor.document().isUndoAvailable()
    w.save_editors()
    assert editor.textCursor().position() == position
    assert editor.document().isUndoAvailable()
    assert store.strand.snapshot('global')['text'] == ''
    dialog.close()
    w.close()


def test_dialog_resolution_does_not_revive_old_file_draft(tmp_path, monkeypatch):
    from letracode.memory_ui import MemoryDialog
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path / 'data'); w = MainWindow(store)
    dialog = file_dialog(store)
    dialog.editor.setPlainText('Stale file draft')
    Path(store.strand.snapshot('global')['path']).write_text('External correction')
    assert dialog.save_current() is False
    dialog.close()
    def resolve(dialog):
        relative = store.strand.path('global').relative_to(store.memory.root_for()).as_posix()
        dialog.select_path(relative)
        dialog.reload_current()
        dialog.editor.setPlainText('Resolved user version')
        assert dialog.save_current()
        dialog.close()
        return 0
    monkeypatch.setattr(MemoryDialog, 'exec', resolve)
    w.edit_memory()
    assert store.strand.snapshot('global')['text'] == 'Resolved user version'
    assert store.setting('strand_draft_global_global') is None
    assert w.save_editors()
    w.close()


def test_retry_preserves_conflicting_file_draft_and_external_original(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path / 'data'); chat = store.create_chat('Global')
    store.add_message(chat, 'user', 'An earlier request')
    w = MainWindow(store); w.select_chat(chat)
    dialog = file_dialog(store)
    dialog.editor.setPlainText('Pending local correction')
    path = Path(store.strand.snapshot('global')['path']); path.write_text('External correction')
    assert not dialog.save_current()
    dialog.close()
    observed = []
    monkeypatch.setattr(w, 'start_worker', lambda: observed.append(True))
    w.retry_reply()
    assert observed == [True]
    assert len(store.messages(chat)) == 2
    assert path.read_text() == 'External correction'
    assert store.setting('strand_draft_global_global')['text'] == 'Pending local correction'
    w.close()


def test_retry_uses_explicitly_saved_file_before_starting_the_next_turn(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path / 'data'); chat = store.create_chat('Global')
    store.add_message(chat, 'user', 'An earlier request')
    w = MainWindow(store); w.select_chat(chat)
    dialog = file_dialog(store)
    dialog.editor.setPlainText('Corrected before retry')
    assert dialog.save_current(); dialog.close()
    observed = []
    monkeypatch.setattr(w, 'start_worker', lambda:observed.append(store.strand.snapshot('global')['text']))
    w.retry_reply()
    assert observed == ['Corrected before retry']
    assert len(store.messages(chat)) == 2
    w.close()


@pytest.mark.parametrize('scope', ['global', 'project'])
def test_first_send_preserves_separate_conflicting_file_draft_after_reopen(tmp_path, monkeypatch, scope):
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path / 'data')
    project = store.create_project('Novel') if scope == 'project' else None
    w = MainWindow(store); w.show_selection(None, project)
    w.engine_config.model_path = str(tmp_path / 'unused.gguf')
    w.engine_config.executable = str(tmp_path / 'unused-server')
    monkeypatch.setattr(w, 'start_worker', lambda: None)
    draft = '  An unsent first question\nwith another line.  '
    w.composer.setPlainText(draft)
    dialog = file_dialog(store, project)
    dialog.editor.setPlainText('Local memory draft')
    path = Path(store.strand.snapshot(scope, project)['path'])
    path.write_text('External memory correction', encoding='utf-8')
    assert not dialog.save_current()
    dialog.close()
    w.send()
    assert [message['content'] for message in store.messages(w.chat_id)] == [draft.strip()]
    assert path.read_text() == 'External memory correction'
    w.close()
    again = MainWindow(Store(tmp_path / 'data'))
    dialog = file_dialog(again.store, project)
    assert dialog.editor.toPlainText() == 'Local memory draft'
    assert dialog.reload_current()
    assert dialog.editor.toPlainText() == 'External memory correction'
    dialog.close(); again.close()


@pytest.mark.parametrize('scope', ['global', 'project'])
def test_successful_first_send_uses_saved_memory_and_clears_only_sent_draft(tmp_path, monkeypatch, scope):
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path / 'data')
    project = store.create_project('Novel') if scope == 'project' else None
    store.set_setting('unbound_draft_other-project', 'Keep this unrelated draft')
    w = MainWindow(store); w.show_selection(None, project)
    w.engine_config.model_path = str(tmp_path / 'unused.gguf')
    w.engine_config.executable = str(tmp_path / 'unused-server')
    monkeypatch.setattr(w, 'start_worker', lambda:None)
    w.composer.setPlainText('My first question')
    dialog = file_dialog(store, project)
    dialog.editor.setPlainText('Memory for the first question')
    assert dialog.save_current(); dialog.close()

    w.send()

    assert [message['content'] for message in store.messages(w.chat_id)] == ['My first question']
    assert store.chat(w.chat_id)['project_id'] == project
    assert store.strand.snapshot(scope, project)['text'] == 'Memory for the first question'
    assert w.composer.toPlainText() == ''
    assert store.chat(w.chat_id)['draft'] == ''
    assert store.setting('unbound_draft_' + (project or 'global')) == ''
    assert store.setting('unbound_draft_other-project') == 'Keep this unrelated draft'
    w.close()


def test_memory_disappearing_during_first_chat_creation_does_not_recreate_file(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path / 'data'); w = MainWindow(store)
    w.engine_config.model_path = str(tmp_path / 'unused.gguf')
    w.engine_config.executable = str(tmp_path / 'unused-server')
    monkeypatch.setattr(w, 'start_worker', lambda: None)
    w.composer.setPlainText('Keep this first question')
    path = Path(store.strand.snapshot('global')['path'])
    create_chat = store.create_chat
    def remove_memory_after_creation(*args):
        chat = create_chat(*args)
        path.unlink()
        return chat
    monkeypatch.setattr(store, 'create_chat', remove_memory_after_creation)
    w.send()
    assert [row['content'] for row in store.messages(w.chat_id)] == ['Keep this first question']
    assert not path.exists()
    w.close()


@pytest.mark.parametrize('damage', ['missing', 'inaccessible', 'malformed'])
def test_unavailable_project_memory_editor_requires_reload_after_repair(tmp_path, damage):
    from letracode.strand_ui import MemoryEditorState
    store = Store(tmp_path / 'data'); project = store.create_project('Novel')
    path = Path(store.strand.snapshot('project', project)['path'])
    path.write_text('Authoritative memory', encoding='utf-8')
    restore_reads = lambda: None
    if damage == 'missing':
        path.unlink()
    elif damage == 'inaccessible':
        restore_reads = deny_file_reads(path)
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
            restore_reads()
            expected = b'Authoritative memory' if damage == 'inaccessible' else b'\xff malformed memory'
            assert path.read_bytes() == expected
        path.write_text('Repaired external memory', encoding='utf-8')
        assert state.save('Still require an explicit reload') is False
        assert path.read_text() == 'Repaired external memory'
        assert state.reload() is True
        assert state.available and state.text == 'Repaired external memory'
        assert state.save('User correction after reload') is True
    finally:
        restore_reads()
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
    restore_reads = lambda: None
    if damage == 'missing': path.unlink()
    elif damage == 'inaccessible': restore_reads = deny_file_reads(path)
    else: path.write_bytes(b'\xff malformed memory')
    w = None
    try:
        w = MainWindow(Store(tmp_path / 'data'))
        assert w.chat_id == affected
        assert 'Still readable history' in w.transcript.toPlainText()
        dialog = file_dialog(w.store, damaged)
        assert dialog.editor.isReadOnly()
        assert 'unavailable' in dialog.message.text().lower()
        dialog.close()
        w.composer.setPlainText('Keep this affected draft'); w.save_editors()
        w.select_chat(unrelated)
        dialog = file_dialog(w.store, healthy)
        assert not dialog.editor.isReadOnly()
        dialog.editor.setPlainText('Healthy memory'); assert dialog.save_current(); dialog.close()
        w.composer.setPlainText('Unrelated draft'); assert w.save_editors()
        assert store.strand.snapshot('project', healthy)['text'] == 'Healthy memory'
        assert store.chat(unrelated)['draft'] == 'Unrelated draft'
        w.select_chat(affected)
        assert w.composer.toPlainText() == 'Keep this affected draft'
        restore_reads()
        path.write_text('Externally repaired memory', encoding='utf-8')
        dialog = file_dialog(w.store, damaged)
        assert not dialog.editor.isReadOnly()
        assert dialog.editor.toPlainText() == 'Externally repaired memory'
        dialog.close()
    finally:
        restore_reads()
        if path.exists(): path.chmod(0o600)
        if w: w.close()


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


@pytest.fixture
def tied_receipt_clock_and_reversed_ids(monkeypatch):
    from datetime import datetime
    from itertools import count
    from types import SimpleNamespace
    from uuid import UUID
    import letracode.strand as strand_module

    numbers = count(1000000, -1)
    monkeypatch.setattr(strand_module, 'datetime', SimpleNamespace(
        now=lambda zone:datetime(2026, 9, 5, 12, 0, 0, tzinfo=zone)))
    monkeypatch.setattr(strand_module, 'uuid', SimpleNamespace(
        uuid4=lambda:UUID(int=next(numbers))))


def click_dialog_undo(dialog):
    from PySide6.QtWidgets import QPushButton
    next(button for button in dialog.findChildren(QPushButton)
         if button.text() == 'Undo selected saved change').click()


def test_default_undo_follows_save_order_through_reopen_and_repeated_saves(tmp_path, tied_receipt_clock_and_reversed_ids):
    from letracode.strand_ui import StrandDialog
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path / 'data'); project = store.create_project('Undo lifecycle')
    other = store.create_project('Unrelated project')
    path = store.strand.path('project', project); path.write_text('Original memory', encoding='utf-8')
    before = store.strand.snapshot('project', project)
    first = store.strand.replace('project', 'First save', before['sha256'], project)
    second = store.strand.replace('project', 'Second save', first['after_sha256'], project)
    assert first['date'] == second['date'] and first['id'] > second['id']
    store.update_project(other, memory='Other project stays intact')
    store.strand.replace('global', 'Global memory stays intact', store.strand.snapshot('global')['sha256'])
    dialog = StrandDialog(Store(tmp_path / 'data'), project)
    dialog.scope.setCurrentIndex(dialog.scope.findData('project'))
    try:
        assert dialog.history.currentData() == second['id']
        assert dialog.history.itemText(0) != dialog.history.itemText(1)
        click_dialog_undo(dialog)
        assert path.read_text() == 'First save'
        assert dialog.editor.toPlainText() == 'First save'
        assert 'Undo of' in dialog.history.currentText()
        dialog.editor.setPlainText('Third save')
        assert dialog.save_current()
        click_dialog_undo(dialog)
        assert path.read_text() == 'First save'
        assert store.strand.snapshot('project', other)['text'] == 'Other project stays intact'
        assert store.strand.snapshot('global')['text'] == 'Global memory stays intact'
        count_before_close = len(store.strand.receipts(1000, scope='project', project_id=project))
    finally:
        dialog.close()
    assert len(store.strand.receipts(1000, scope='project', project_id=project)) == count_before_close
    again = StrandDialog(Store(tmp_path / 'data'), project)
    again.scope.setCurrentIndex(again.scope.findData('project'))
    assert again.editor.toPlainText() == 'First save'
    again.close()


def test_failed_selected_undo_keeps_the_selected_receipt_visible(tmp_path, monkeypatch):
    from datetime import datetime, timedelta
    from itertools import count
    from types import SimpleNamespace
    import letracode.strand as strand_module
    from letracode.strand_ui import StrandDialog
    ticks = count()
    monkeypatch.setattr(strand_module, 'datetime', SimpleNamespace(
        now=lambda zone:datetime(2026, 9, 5, tzinfo=zone) + timedelta(seconds=next(ticks))))
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path / 'data'); project = store.create_project('Stale selected Undo')
    first = store.strand.replace('project', 'First save', store.strand.snapshot('project', project)['sha256'], project)
    store.strand.replace('project', 'Second save', first['after_sha256'], project)
    dialog = StrandDialog(store, project)
    dialog.scope.setCurrentIndex(dialog.scope.findData('project'))
    dialog.history.setCurrentIndex(dialog.history.findData(first['id']))
    try:
        click_dialog_undo(dialog)
        assert store.strand.snapshot('project', project)['text'] == 'Second save'
        assert any(word in dialog.message.text().lower() for word in ('changed', 'latest', 'newer'))
        assert dialog.history.currentData() == first['id']
    finally:
        dialog.close()


def test_undo_reload_failure_keeps_unavailable_diagnostic_and_recovers_after_reopen(tmp_path, monkeypatch):
    from letracode.strand_ui import StrandDialog
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path / 'data'); project = store.create_project('Reload after Undo')
    path = store.strand.path('project', project); path.write_text('Original memory', encoding='utf-8')
    store.strand.replace('project', 'Saved change', store.strand.snapshot('project', project)['sha256'], project)
    dialog = StrandDialog(store, project)
    dialog.scope.setCurrentIndex(dialog.scope.findData('project'))
    undo = store.strand.undo
    def remove_after_undo(ident):
        receipt = undo(ident)
        path.unlink()
        return receipt
    monkeypatch.setattr(store.strand, 'undo', remove_after_undo)
    try:
        click_dialog_undo(dialog)
        assert dialog.editor.isReadOnly()
        assert 'unavailable' in dialog.message.text().lower()
        assert str(path) in dialog.message.text()
    finally:
        dialog.close()
    again = StrandDialog(Store(tmp_path / 'data'), project)
    again.scope.setCurrentIndex(again.scope.findData('project'))
    assert again.editor.isReadOnly()
    assert not path.exists()
    path.write_text('Original memory', encoding='utf-8')
    again.reload_current()
    assert again.editor.toPlainText() == 'Original memory'
    assert not again.editor.isReadOnly()
    again.close()


def test_undo_preserves_external_correction_and_local_draft_through_reopen(tmp_path):
    from letracode.strand_ui import StrandDialog
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path / 'data'); project = store.create_project('Conflicting Undo')
    path = store.strand.path('project', project)
    receipt = store.strand.replace('project', 'Saved memory', store.strand.snapshot('project', project)['sha256'], project)
    dialog = StrandDialog(store, project)
    dialog.scope.setCurrentIndex(dialog.scope.findData('project'))
    dialog.editor.setPlainText('Keep this pending memory draft')
    path.write_text('External correction', encoding='utf-8')
    click_dialog_undo(dialog)
    assert path.read_text() == 'External correction'
    assert dialog.editor.toPlainText() == 'Keep this pending memory draft'
    assert dialog.history.currentData() == receipt['id']
    dialog.close()
    again = StrandDialog(Store(tmp_path / 'data'), project)
    again.scope.setCurrentIndex(again.scope.findData('project'))
    try:
        assert again.editor.toPlainText() == 'Keep this pending memory draft'
        click_dialog_undo(again)
        assert path.read_text() == 'External correction'
        assert again.editor.toPlainText() == 'Keep this pending memory draft'
        again.reload_current()
        click_dialog_undo(again)
        assert path.read_text() == 'External correction'
        assert 'changed' in again.message.text().lower()
    finally:
        again.close()


def test_dialog_undo_does_not_publish_a_pending_editor_draft(tmp_path):
    from letracode.strand_ui import StrandDialog
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path / 'data'); project = store.create_project('Pending draft')
    receipt = store.strand.replace('project', 'Saved memory', store.strand.snapshot('project', project)['sha256'], project)
    dialog = StrandDialog(store, project)
    dialog.scope.setCurrentIndex(dialog.scope.findData('project'))
    dialog.editor.setPlainText('Unpublished local draft')
    try:
        click_dialog_undo(dialog)
        assert store.strand.snapshot('project', project)['text'] == 'Saved memory'
        assert dialog.editor.toPlainText() == 'Unpublished local draft'
        assert dialog.history.currentData() == receipt['id']
        assert store.setting(dialog.state.key)['text'] == 'Unpublished local draft'
        assert len(store.strand.receipts(scope='project', project_id=project)) == 1
        assert 'Save' in dialog.message.text() and 'Reload' in dialog.message.text()
        assert dialog.save_current()
        assert store.strand.snapshot('project', project)['text'] == 'Unpublished local draft'
    finally:
        dialog.close()


def test_ambiguous_legacy_undo_history_has_no_default_selection(tmp_path, tied_receipt_clock_and_reversed_ids):
    from letracode.strand_ui import StrandDialog
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path / 'data'); project = store.create_project('Ambiguous legacy')
    path = store.strand.path('project', project); path.write_text('A', encoding='utf-8')
    for text in ('B', 'A', 'B'):
        snapshot = store.strand.snapshot('project', project)
        receipt = store.strand.replace('project', text, snapshot['sha256'], project)
        receipt_path = store.strand.root / '.receipts' / (receipt['id'] + '.json')
        old_record = json.loads(receipt_path.read_text())
        old_record.pop('sequence', None); old_record.pop('write_id', None)
        receipt_path.write_text(json.dumps(old_record), encoding='utf-8')
    dialog = StrandDialog(Store(tmp_path / 'data'), project)
    dialog.scope.setCurrentIndex(dialog.scope.findData('project'))
    try:
        assert dialog.history.count() == 3
        assert dialog.history.currentIndex() == -1
        assert 'uncertain' in dialog.message.text().lower() or 'ambiguous' in dialog.message.text().lower()
        click_dialog_undo(dialog)
        assert path.read_text() == 'B'
    finally:
        dialog.close()


def test_reopened_default_undo_keeps_latest_selection_when_external_file_changed(tmp_path, tied_receipt_clock_and_reversed_ids):
    from letracode.strand_ui import StrandDialog
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path / 'data'); project = store.create_project('External after latest')
    first = store.strand.replace('project', 'First', store.strand.snapshot('project', project)['sha256'], project)
    latest = store.strand.replace('project', 'Latest', first['after_sha256'], project)
    path = store.strand.path('project', project); path.write_text('External correction', encoding='utf-8')
    dialog = StrandDialog(Store(tmp_path / 'data'), project)
    dialog.scope.setCurrentIndex(dialog.scope.findData('project'))
    try:
        assert dialog.history.currentData() == latest['id']
        click_dialog_undo(dialog)
        assert path.read_text() == 'External correction'
        assert dialog.history.currentData() == latest['id']
        assert 'changed' in dialog.message.text().lower() or 'conflict' in dialog.message.text().lower()
    finally:
        dialog.close()


def test_memory_receipt_link_blocks_undo_with_pending_affected_memory(tmp_path):
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path / 'data'); chat = store.create_chat('Global')
    receipt = store.strand.remember('global', 'Saved memory', expected_sha256=store.strand.snapshot('global')['sha256'])
    message = {'role':'tool', 'name':'remember', 'tool_call_id':'saved', 'content':json.dumps(receipt)}
    store.add_message(chat, 'tool', message['content'], payload={'message':message})
    w = MainWindow(store); w.select_chat(chat)
    dialog = file_dialog(store)
    dialog.editor.setPlainText('Pending local draft'); dialog.close()
    try:
        w.open_link(QUrl(f'letracode:undo-memory/{receipt["id"]}'))
        assert 'Saved memory' in store.strand.snapshot('global')['text']
        assert dialog.editor.toPlainText() == 'Pending local draft'
        assert store.setting(dialog.state.key)['text'] == 'Pending local draft'
        assert len(store.messages(chat)) == 1
        assert 'Save' in w.context_hint.text() and 'Reload' in w.context_hint.text()
    finally:
        w.close()


def test_memory_receipt_link_does_not_save_an_unrelated_scope_draft(tmp_path):
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path / 'data'); project = store.create_project('Project pane')
    chat = store.create_chat('Global memory tool', project)
    store.update_project(project, memory='Saved project memory')
    receipt = store.strand.remember('global', 'Global save to undo', expected_sha256=store.strand.snapshot('global')['sha256'])
    message = {'role':'tool', 'name':'remember', 'tool_call_id':'saved', 'content':json.dumps(receipt)}
    store.add_message(chat, 'tool', message['content'], payload={'message':message})
    w = MainWindow(store); w.select_chat(chat)
    dialog = file_dialog(store, project)
    dialog.editor.setPlainText('Pending project draft'); dialog.close()
    try:
        w.open_link(QUrl(f'letracode:undo-memory/{receipt["id"]}'))
        assert store.strand.snapshot('global')['text'] == ''
        assert store.strand.snapshot('project', project)['text'] == 'Saved project memory'
        assert dialog.editor.toPlainText() == 'Pending project draft'
    finally:
        w.close()


def test_reload_memory_history_reflects_a_later_save_from_another_store(tmp_path):
    from letracode.strand_ui import StrandDialog
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path / 'data'); project = store.create_project('Later save')
    first = store.strand.replace('project', 'First save', store.strand.snapshot('project', project)['sha256'], project)
    dialog = StrandDialog(store, project)
    dialog.scope.setCurrentIndex(dialog.scope.findData('project'))
    other = Store(tmp_path / 'data')
    latest = other.strand.replace('project', 'Later save', first['after_sha256'], project)
    try:
        dialog.reload_current()
        assert dialog.editor.toPlainText() == 'Later save'
        assert dialog.history.currentData() == latest['id']
        click_dialog_undo(dialog)
        assert store.strand.snapshot('project', project)['text'] == 'First save'
    finally:
        dialog.close()


def test_unconfirmed_later_write_blocks_default_undo_until_explicit_save_and_reopen(tmp_path, tied_receipt_clock_and_reversed_ids):
    from letracode.strand_ui import StrandDialog
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path / 'data'); project = store.create_project('Unresolved later write')
    path = store.strand.path('project', project); path.write_text('Original memory', encoding='utf-8')
    older = store.strand.replace('project', 'Older saved memory', store.strand.snapshot('project', project)['sha256'], project)
    later = store.strand.replace('project', 'Unresolved later memory', older['after_sha256'], project)
    receipt_path = store.strand.root / '.receipts' / (later['id'] + '.json')
    unresolved = json.loads(receipt_path.read_text())
    unresolved['status'] = 'prepared'
    unresolved.pop('write_id')
    receipt_path.write_text(json.dumps(unresolved), encoding='utf-8')
    # Matching an older version does not establish that it is safe to undo it.
    path.write_text('Older saved memory', encoding='utf-8')
    dialog = StrandDialog(Store(tmp_path / 'data'), project)
    dialog.scope.setCurrentIndex(dialog.scope.findData('project'))
    try:
        assert dialog.history.count() == 2
        assert dialog.history.currentIndex() == -1
        assert 'uncertain' in dialog.message.text().lower() or 'ambiguous' in dialog.message.text().lower()
        click_dialog_undo(dialog)
        assert path.read_text() == 'Older saved memory'
        dialog.editor.setPlainText('Fresh explicitly saved memory')
        dialog.save_button.click()
        assert path.read_text() == 'Fresh explicitly saved memory'
        fresh_id = dialog.history.currentData()
        assert fresh_id not in (None, older['id'], later['id'])
        assert store.strand.receipt(fresh_id)['sequence'] > later['sequence']
    finally:
        dialog.close()
    again = StrandDialog(Store(tmp_path / 'data'), project)
    again.scope.setCurrentIndex(again.scope.findData('project'))
    try:
        assert again.history.currentData() == fresh_id
        assert again.editor.toPlainText() == 'Fresh explicitly saved memory'
        click_dialog_undo(again)
        assert path.read_text() == 'Older saved memory'
        assert again.editor.toPlainText() == 'Older saved memory'
    finally:
        again.close()
