from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from letracode.store import Store


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def test_memory_editor_preserves_external_edit_and_recovers_draft_after_reopen(tmp_path):
    from letracode.memory_ui import MemoryFileEditorState
    store = Store(tmp_path / 'data')
    store.memory.create_file('notes.md', 'Original')
    state = MemoryFileEditorState(store, 'notes.md')
    path = Path(state.snapshot['path'])
    path.write_text('External correction', encoding='utf-8')

    assert state.save('Local draft', True) is False
    assert path.read_text() == 'External correction'
    reopened = MemoryFileEditorState(Store(tmp_path / 'data'), 'notes.md')
    assert reopened.text == 'Local draft'
    assert reopened.active is True
    assert reopened.save(reopened.text, reopened.active) is False
    assert reopened.reload() is True
    assert reopened.text == 'External correction'
    assert not reopened.active


def test_memory_editor_cannot_replace_missing_file_without_explicit_reload(tmp_path):
    from letracode.memory_ui import MemoryFileEditorState
    store = Store(tmp_path / 'data')
    store.memory.create_file('notes.txt', 'Original')
    state = MemoryFileEditorState(store, 'notes.txt')
    path = Path(state.snapshot['path']); path.unlink()
    assert state.save('Keep local draft', False) is False
    assert not state.available
    assert state.reload() is False
    assert state.text == 'Keep local draft'
    path.write_text('Restored externally', encoding='utf-8')
    assert state.save('Must first reload', False) is False
    assert state.reload() is True
    assert state.text == 'Restored externally'


def test_activation_conflict_keeps_reviewed_revision_with_reopened_draft(tmp_path):
    from letracode.memory_ui import MemoryFileEditorState
    store = Store(tmp_path / 'data')
    store.memory.create_file('notes.md', 'Original')
    state = MemoryFileEditorState(store, 'notes.md')
    state.keep_draft('Original', True)
    store.memory.set_active('notes.md', True, state.snapshot['sha256'])
    reopened = MemoryFileEditorState(Store(tmp_path / 'data'), 'notes.md')
    assert reopened.save(reopened.text, reopened.active) is False
    assert reopened.active is True
    assert store.memory.file_snapshot('notes.md')['always_active'] is True
    assert reopened.reload()
    assert reopened.save('Reviewed after reload', False)
    assert store.memory.file_snapshot('notes.md')['always_active'] is False


def test_tree_navigation_and_close_preserve_drafts_without_saving_memory(app, tmp_path):
    from letracode.memory_ui import MemoryDialog
    store = Store(tmp_path / 'data')
    store.memory.create_folder('Research')
    store.memory.create_folder('Research/People')
    store.memory.create_file('Research/People/notes.md', 'Original')
    store.memory.create_file('other.txt', 'Other')
    dialog = MemoryDialog(store)
    assert dialog.select_path('Research/People/notes.md')
    assert dialog.tree.currentItem().parent().text(0) == 'People'
    dialog.editor.setPlainText('Draft survives navigation')
    dialog.always_active.setChecked(True)
    assert dialog.select_path('other.txt')
    assert store.memory.file_snapshot('Research/People/notes.md')['text'] == 'Original'
    assert dialog.select_path('Research/People/notes.md')
    assert dialog.editor.toPlainText() == 'Draft survives navigation'
    dialog.close()
    reopened = MemoryDialog(Store(tmp_path / 'data'))
    assert reopened.select_path('Research/People/notes.md')
    assert reopened.editor.toPlainText() == 'Draft survives navigation'
    assert reopened.always_active.isChecked()
    assert reopened.save_current()
    assert store.memory.file_snapshot('Research/People/notes.md')['text'] == 'Draft survives navigation'
    assert store.memory.file_snapshot('Research/People/notes.md')['always_active']
    reopened.close()


def test_tree_crud_and_delete_undo_preserve_nested_files(app, tmp_path, monkeypatch):
    from letracode.memory_ui import MemoryDialog
    store = Store(tmp_path / 'data')
    dialog = MemoryDialog(store)
    assert dialog.create_folder('Ideas')
    assert dialog.create_folder('Ideas/Stories')
    assert dialog.create_file('Ideas/Stories/first.md')
    dialog.editor.setPlainText('My story plan')
    assert dialog.save_current()
    assert dialog.move_selected('Ideas/Stories/renamed.md')
    assert store.memory.file_snapshot('Ideas/Stories/renamed.md')['text'] == 'My story plan'
    assert dialog.select_path('Ideas/Stories')
    assert dialog.move_selected('Stories')
    assert store.memory.file_snapshot('Stories/renamed.md')['text'] == 'My story plan'
    monkeypatch.setattr(QMessageBox, 'question', lambda *args, **kwargs: QMessageBox.StandardButton.Yes)
    assert dialog.delete_selected()
    assert 'Stories' not in {row['path'] for row in store.memory.entries()}
    assert dialog.undo_selected()
    assert store.memory.file_snapshot('Stories/renamed.md')['text'] == 'My story plan'
    dialog.close()


def test_unsaved_draft_blocks_move_delete_and_undo(app, tmp_path, monkeypatch):
    from letracode.memory_ui import MemoryDialog
    store = Store(tmp_path / 'data')
    store.memory.create_file('notes.md', 'Saved')
    dialog = MemoryDialog(store); dialog.select_path('notes.md')
    dialog.editor.setPlainText('Unsaved')
    monkeypatch.setattr(QMessageBox, 'question', lambda *args, **kwargs: QMessageBox.StandardButton.Yes)
    assert dialog.move_selected('renamed.md') is False
    assert dialog.delete_selected() is False
    assert dialog.undo_selected() is False
    assert store.memory.file_snapshot('notes.md')['text'] == 'Saved'
    assert 'draft' in dialog.message.text().lower()
    dialog.close()


def test_tree_stale_folder_move_preserves_external_additions(app, tmp_path):
    from letracode.memory_ui import MemoryDialog
    store = Store(tmp_path / 'data')
    store.memory.create_folder('Research')
    store.memory.create_file('Research/first.txt', 'First')
    dialog = MemoryDialog(store); dialog.select_path('Research')
    store.memory.create_file('Research/external.txt', 'External change')
    assert dialog.move_selected('Renamed') is False
    assert store.memory.file_snapshot('Research/external.txt')['text'] == 'External change'
    assert 'Renamed' not in {row['path'] for row in store.memory.entries()}
    dialog.close()


def test_folder_move_and_delete_block_saved_descendant_drafts(app, tmp_path, monkeypatch):
    from letracode.memory_ui import MemoryDialog
    store = Store(tmp_path / 'data')
    store.memory.create_folder('Ideas')
    store.memory.create_file('Ideas/notes.md', 'Original')
    dialog = MemoryDialog(store)
    dialog.select_path('Ideas/notes.md'); dialog.editor.setPlainText('Child draft')
    dialog.select_path('Ideas')
    monkeypatch.setattr(QMessageBox, 'question', lambda *args, **kwargs: QMessageBox.StandardButton.Yes)
    assert dialog.move_selected('Other') is False
    assert dialog.delete_selected() is False
    assert 'draft' in dialog.message.text().lower()
    assert dialog.select_path('Ideas/notes.md')
    assert dialog.editor.toPlainText() == 'Child draft'
    dialog.close()


def test_deleted_external_file_draft_is_visible_after_tree_reopen(app, tmp_path):
    from letracode.memory_ui import MemoryDialog
    store = Store(tmp_path / 'data')
    store.memory.create_file('notes.md', 'Original')
    dialog = MemoryDialog(store); dialog.select_path('notes.md')
    dialog.editor.setPlainText('Recover this local draft')
    Path(store.memory.file_snapshot('notes.md')['path']).unlink()
    dialog.close()
    reopened = MemoryDialog(Store(tmp_path / 'data'))
    assert reopened.select_path('notes.md')
    assert reopened.editor.toPlainText() == 'Recover this local draft'
    assert reopened.editor.isReadOnly()
    assert 'draft' in reopened.message.text().lower()
    reopened.close()


def test_tree_project_selector_isolates_files_and_drafts(app, tmp_path):
    from letracode.memory_ui import MemoryDialog
    store = Store(tmp_path / 'data')
    project = store.create_project('Selected project')
    other = store.create_project('Other project')
    store.memory.create_file('notes.md', 'Global notes')
    store.memory.create_file('notes.md', 'Project notes', project_id=project)
    store.memory.create_file('private.md', 'Other private notes', project_id=other)
    dialog = MemoryDialog(store, project)
    dialog.select_path('notes.md'); dialog.editor.setPlainText('Global draft')
    dialog.scope.setCurrentIndex(dialog.scope.findData(project))
    assert dialog.select_path('notes.md')
    assert dialog.editor.toPlainText() == 'Project notes'
    assert not dialog.select_path('private.md')
    dialog.editor.setPlainText('Project draft')
    dialog.scope.setCurrentIndex(dialog.scope.findData(None))
    dialog.select_path('notes.md')
    assert dialog.editor.toPlainText() == 'Global draft'
    assert store.memory.file_snapshot('notes.md', project_id=project)['text'] == 'Project notes'
    dialog.close()


def test_memory_ui_rejects_path_traversal_and_keeps_draft(app, tmp_path):
    from letracode.memory_ui import MemoryDialog
    store = Store(tmp_path / 'data')
    dialog = MemoryDialog(store)
    assert dialog.create_file('../outside.md') is False
    assert dialog.create_folder('/tmp/outside') is False
    assert not (tmp_path / 'outside.md').exists()
    dialog.close()


def test_history_identifies_user_files_instead_of_internal_recovery_paths(app, tmp_path):
    from letracode.memory_ui import MemoryDialog
    store = Store(tmp_path / 'data')
    project = store.create_project('Novel')
    dialog = MemoryDialog(store, project)
    dialog.scope.setCurrentIndex(dialog.scope.findData(project))
    dialog.create_file('my-notes.md')
    assert 'my-notes.md' in dialog.history.currentText()
    assert '.trash' not in dialog.history.currentText()
    assert '.projects' not in dialog.history.currentText()
    dialog.close()


def test_main_window_files_action_opens_tree_and_refreshes_files(app, tmp_path, monkeypatch):
    from letracode.memory_ui import MemoryDialog
    from letracode.ui import MainWindow
    store = Store(tmp_path / 'data')
    window = MainWindow(store)
    legacy_path = next(row['path'] for row in store.memory.entries() if row.get('legacy_scope') == 'global')
    def edit(dialog):
        assert dialog.select_path(legacy_path)
        dialog.editor.setPlainText('Edited in Memory tree')
        assert dialog.save_current()
        return 0
    monkeypatch.setattr(MemoryDialog, 'exec', edit)
    window.files_panel.manage_button.click()
    assert store.memory.file_snapshot(legacy_path)['text'] == 'Edited in Memory tree'
    assert window.files_panel._select_path(store.memory.root_for() / legacy_path)
    window.close()


@pytest.mark.parametrize('scope', ['global', 'project'])
def test_intentionally_deleted_legacy_memory_does_not_block_chat(app, tmp_path, monkeypatch, scope):
    from letracode.ui import MainWindow
    store = Store(tmp_path / 'data')
    project = store.create_project('Novel') if scope == 'project' else None
    window = MainWindow(store); window.show_selection(None, project)
    path = store.strand.path(scope, project).relative_to(store.memory.root_for(project)).as_posix()
    before = store.memory.snapshot_entry(path, project_id=project)
    store.memory.delete(path, before['sha256'], project_id=project)
    window.composer.setPlainText('Chat after removing the old default file')
    window.engine_config.model_path = str(tmp_path / 'unused.gguf')
    window.engine_config.executable = str(tmp_path / 'unused-server')
    monkeypatch.setattr(window, 'start_worker', lambda: None)
    window.send()
    assert [row['content'] for row in store.messages(window.chat_id)] == ['Chat after removing the old default file']
    assert store.memory.alias_deleted(scope, project)
    assert not store.strand.path(scope, project).exists()
    window.close()


def test_deletion_preserves_conflicting_file_editor_draft(app, tmp_path):
    from letracode.ui import MainWindow
    store = Store(tmp_path / 'data'); window = MainWindow(store)
    from letracode.memory_ui import MemoryDialog
    dialog = MemoryDialog(store)
    dialog.select_path(store.strand.path('global').relative_to(store.memory.root_for()).as_posix())
    dialog.editor.setPlainText('Unsaved pane draft')
    path = store.strand.path('global').relative_to(store.memory.root_for()).as_posix()
    before = store.memory.snapshot_entry(path)
    store.memory.delete(path, before['sha256'])
    assert dialog.save_current() is False
    dialog.close()
    assert window.save_editors()
    assert store.setting('strand_draft_global_global')['text'] == 'Unsaved pane draft'
    window.close()


def test_tree_alias_draft_is_not_autosaved_or_discarded_by_chat(app, tmp_path, monkeypatch):
    from letracode.memory_ui import MemoryDialog
    from letracode.ui import MainWindow
    store = Store(tmp_path / 'data'); window = MainWindow(store)
    path = store.strand.path('global').relative_to(store.memory.root_for()).as_posix()
    def draft(dialog):
        dialog.select_path(path)
        dialog.editor.setPlainText('Tree draft for later review')
        dialog.always_active.setChecked(True)
        dialog.keep_current_draft()
        return 0
    monkeypatch.setattr(MemoryDialog, 'exec', draft)
    window.edit_memory()
    window.composer.setPlainText('Independent conversation draft')
    assert window.save_editors()
    assert store.memory.file_snapshot(path)['text'] == ''
    kept = store.setting('strand_draft_global_global')
    assert kept['text'] == 'Tree draft for later review'
    assert kept['always_active'] is True
    window.close()
    dialog = MemoryDialog(Store(tmp_path / 'data')); dialog.select_path(path)
    assert dialog.editor.toPlainText() == 'Tree draft for later review'
    assert dialog.always_active.isChecked()
    assert dialog.save_current()
    dialog.close()


@pytest.mark.parametrize('tree_draft', [False, True])
def test_generic_remember_undo_refreshes_files_or_preserves_pending_tree_draft(app, tmp_path, tree_draft):
    import json
    from PySide6.QtCore import QUrl
    from letracode.memory_ui import MemoryFileEditorState
    from letracode.ui import MainWindow
    store = Store(tmp_path / 'data'); chat = store.create_chat('Memory receipt')
    relative = store.strand.path('global').relative_to(store.memory.root_for()).as_posix()
    before = store.memory.file_snapshot(relative)
    receipt = store.memory.replace_file(relative, 'Saved by generic remember', before['sha256'])
    message = {'role': 'tool', 'name': 'remember', 'tool_call_id': 'remember-1', 'content': json.dumps(receipt)}
    store.add_message(chat, 'tool', message['content'], payload={'message': message})
    if tree_draft:
        state = MemoryFileEditorState(store, relative)
        state.keep_draft('Pending reviewed tree draft', True)
    window = MainWindow(store); window.select_chat(chat)
    window.open_link(QUrl('letracode:undo-memory/' + receipt['id']))
    if tree_draft:
        assert store.memory.file_snapshot(relative)['text'] == 'Saved by generic remember'
        assert store.setting('strand_draft_global_global')['text'] == 'Pending reviewed tree draft'
        assert 'draft' in window.context_hint.text().lower()
    else:
        assert store.memory.file_snapshot(relative)['text'] == ''
        assert window.files_panel._select_path(store.memory.root_for() / relative)
        assert window.save_editors()
    window.close()


@pytest.mark.parametrize('reopen', [False, True])
@pytest.mark.parametrize('change', ['content', 'active'])
def test_editor_draft_cannot_modify_recreated_identity_with_matching_bytes(tmp_path, reopen, change):
    from letracode.memory_ui import MemoryFileEditorState
    store = Store(tmp_path / 'data')
    store.memory.create_file('note.md', 'same original')
    state = MemoryFileEditorState(store, 'note.md')
    original_id = state.snapshot['file_id']
    text = 'draft for deleted identity' if change == 'content' else 'same original'
    active = change == 'active'
    state.keep_draft(text, active)
    other = Store(store.directory)
    other.memory.delete('note.md', other.memory.file_snapshot('note.md')['sha256'])
    other.memory.create_file('note.md', 'same original')
    replacement_id = other.memory.file_snapshot('note.md')['file_id']
    assert replacement_id != original_id
    if reopen:
        state = MemoryFileEditorState(Store(store.directory), 'note.md')
    assert state.save(text, active) is False
    assert state.snapshot['file_id'] == original_id
    assert other.memory.file_snapshot('note.md')['text'] == 'same original'
    assert other.memory.file_snapshot('note.md')['always_active'] is False
    draft = store.setting(state.key)
    assert draft['file_id'] == original_id
    assert draft['text'] == text
    reopened = MemoryFileEditorState(Store(store.directory), 'note.md')
    assert reopened.snapshot['file_id'] == original_id
    assert reopened.text == text
    assert reopened.save(text, active) is False
    assert reopened.reload()
    assert reopened.snapshot['file_id'] == replacement_id
    assert reopened.save('Reviewed replacement', True)


def test_old_tree_draft_without_identity_requires_reload_and_stays_recoverable(tmp_path):
    from letracode.memory_ui import MemoryFileEditorState
    store = Store(tmp_path / 'data')
    store.memory.create_file('note.md', 'Original')
    state = MemoryFileEditorState(store, 'note.md')
    state.keep_draft('Unbound old tree draft', False)
    old_draft = store.setting(state.key)
    old_draft.pop('file_id', None)
    store.set_setting(state.key, old_draft)
    reopened = MemoryFileEditorState(Store(store.directory), 'note.md')
    assert reopened.text == 'Unbound old tree draft'
    assert reopened.save(reopened.text, False) is False
    assert 'reload' in reopened.error.lower()
    assert store.memory.file_snapshot('note.md')['text'] == 'Original'
    assert store.setting(state.key)['text'] == 'Unbound old tree draft'
    assert 'file_id' not in store.setting(state.key)
    again = MemoryFileEditorState(Store(store.directory), 'note.md')
    assert again.save(again.text, False) is False
    assert again.reload()
    assert again.save('Reviewed after reload', False)


def test_original_legacy_draft_can_bind_to_its_immutable_alias(tmp_path):
    from letracode.memory_ui import MemoryFileEditorState
    store = Store(tmp_path / 'data')
    snapshot = store.strand.snapshot('global')
    store.set_setting('strand_draft_global_global', {
        'text': 'Legacy draft retained', 'base_text': snapshot['text'], 'sha256': snapshot['sha256']})
    relative = store.strand.path('global').relative_to(store.memory.root).as_posix()
    state = MemoryFileEditorState(store, relative)
    assert state.text == 'Legacy draft retained'
    assert state.save(state.text, False)
    assert store.strand.snapshot('global')['text'] == 'Legacy draft retained'


@pytest.mark.parametrize('tree_draft', [False, True])
def test_missing_identity_alias_draft_does_not_adopt_a_replacement(tmp_path, tree_draft):
    from letracode.memory_ui import MemoryFileEditorState
    store = Store(tmp_path / 'data')
    snapshot = store.strand.snapshot('global')
    relative = store.strand.path('global').relative_to(store.memory.root).as_posix()
    store.set_setting('strand_draft_global_global', {
        'text': 'Draft for original alias', 'base_text': snapshot['text'],
        'sha256': snapshot['sha256'], 'memory_tree_draft': tree_draft})
    store.memory.delete(relative, snapshot['sha256'])
    store.memory.create_file(relative, snapshot['text'])
    state = MemoryFileEditorState(Store(store.directory), relative)
    assert state.text == 'Draft for original alias'
    assert state.save(state.text, False) is False
    assert store.memory.file_snapshot(relative)['text'] == snapshot['text']
    assert store.setting(state.key)['text'] == 'Draft for original alias'


def test_external_unregistered_draft_keeps_explicit_identity_through_registration(tmp_path):
    from letracode.memory_ui import MemoryFileEditorState
    store = Store(tmp_path / 'data')
    (store.memory.root / 'external.md').write_text('External original')
    state = MemoryFileEditorState(store, 'external.md')
    assert state.snapshot['file_id'] is None
    state.keep_draft('Reviewed external draft', True)
    assert 'file_id' in store.setting(state.key)
    assert store.setting(state.key)['file_id'] is None
    reopened = MemoryFileEditorState(Store(store.directory), 'external.md')
    assert reopened.save(reopened.text, reopened.active)
    assert store.memory.file_snapshot('external.md')['text'] == 'Reviewed external draft'
    assert store.memory.file_snapshot('external.md')['always_active'] is True
    assert reopened.snapshot['file_id'] is not None


@pytest.mark.parametrize('kind', ['file', 'folder'])
@pytest.mark.parametrize('operation', ['move', 'delete'])
def test_tree_structure_actions_reject_same_bytes_recreated_selection(app, tmp_path, monkeypatch, kind, operation):
    from letracode.memory_ui import MemoryDialog
    store = Store(tmp_path / 'data')
    path = 'note.md' if kind == 'file' else 'Notes'
    destination = 'moved.md' if kind == 'file' else 'Moved'
    if kind == 'file':
        store.memory.create_file(path, 'same bytes')
    else:
        store.memory.create_folder(path)
    dialog = MemoryDialog(store)
    assert dialog.select_path(path)
    selected = dict(dialog.selected_snapshot)
    other = Store(store.directory)
    other.memory.delete(path, other.memory.snapshot_entry(path)['sha256'])
    if kind == 'file':
        other.memory.create_file(path, 'same bytes')
    else:
        other.memory.create_folder(path)
    assert other.memory.snapshot_entry(path)['sha256'] == selected['sha256']
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **kw: QMessageBox.StandardButton.Yes)
    changed = dialog.move_selected(destination) if operation == 'move' else dialog.delete_selected()
    assert changed is False
    assert (store.memory.root / path).exists()
    assert not (store.memory.root / destination).exists()
    assert 'identity' in dialog.message.text().lower() or 'changed' in dialog.message.text().lower()
    dialog.refresh_tree(path)
    assert (dialog.move_selected(destination) if operation == 'move' else dialog.delete_selected()) is True
    dialog.close()


@pytest.mark.parametrize('operation', ['move', 'delete'])
def test_tree_structure_actions_reject_external_same_bytes_inode_replacement(app, tmp_path, monkeypatch, operation):
    from letracode.memory_ui import MemoryDialog
    store = Store(tmp_path / 'data')
    store.memory.create_file('note.md', 'same bytes')
    dialog = MemoryDialog(store); dialog.select_path('note.md')
    original_id = dialog.selected_snapshot['file_id']
    path = store.memory.root / 'note.md'
    path.rename(tmp_path / 'preserved-original.md')
    path.write_text('same bytes')
    assert store.memory.file_snapshot('note.md')['file_id'] == original_id
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **kw: QMessageBox.StandardButton.Yes)
    assert (dialog.move_selected('renamed.md') if operation == 'move' else dialog.delete_selected()) is False
    assert path.read_text() == 'same bytes'
    assert (tmp_path / 'preserved-original.md').read_text() == 'same bytes'
    assert not (store.memory.root / 'renamed.md').exists()
    dialog.close()


@pytest.mark.parametrize('reopen', [False, True])
@pytest.mark.parametrize('change', ['content', 'active'])
def test_unregistered_editor_draft_preserves_original_inode_on_path_reuse(tmp_path, reopen, change):
    from letracode.memory_ui import MemoryFileEditorState
    store = Store(tmp_path / 'data')
    path = store.memory.root / 'external.md'; path.write_text('same original')
    state = MemoryFileEditorState(store, 'external.md')
    original_identity = state.snapshot.get('entry_identity')
    text = 'Draft for original external file' if change == 'content' else 'same original'
    active = change == 'active'
    state.keep_draft(text, active)
    path.rename(tmp_path / 'retained-original.md')
    path.write_text('same original')
    if reopen:
        state = MemoryFileEditorState(Store(store.directory), 'external.md')
    assert state.save(text, active) is False
    assert original_identity is not None
    assert state.snapshot['entry_identity'] == original_identity
    assert store.setting(state.key)['entry_identity'] == original_identity
    assert store.setting(state.key)['text'] == text
    assert path.read_text() == 'same original'
    assert store.memory.file_snapshot('external.md')['file_id'] is None
    assert store.memory.file_snapshot('external.md')['always_active'] is False
    assert state.reload()
    assert state.save('Reviewed new external file', True)


def test_missing_inode_in_new_tree_draft_requires_reload(tmp_path):
    from letracode.memory_ui import MemoryFileEditorState
    store = Store(tmp_path / 'data')
    store.memory.create_file('note.md', 'Original')
    state = MemoryFileEditorState(store, 'note.md')
    state.keep_draft('Draft with incomplete identity', False)
    draft = store.setting(state.key); draft.pop('entry_identity', None)
    store.set_setting(state.key, draft)
    reopened = MemoryFileEditorState(Store(store.directory), 'note.md')
    assert reopened.save(reopened.text, False) is False
    assert reopened.text == 'Draft with incomplete identity'
    assert store.memory.file_snapshot('note.md')['text'] == 'Original'
    assert 'entry_identity' not in store.setting(state.key)


def test_content_save_does_not_activate_same_bytes_inode_replaced_before_followup(tmp_path, monkeypatch):
    from letracode.memory_ui import MemoryFileEditorState
    store = Store(tmp_path / 'data')
    store.memory.create_file('note.md', 'Original')
    state = MemoryFileEditorState(store, 'note.md')
    replace = store.memory.replace_file
    path = store.memory.root / 'note.md'

    def replace_then_swap(*args, **kwargs):
        receipt = replace(*args, **kwargs)
        path.rename(tmp_path / 'retained-confirmed-save.md')
        path.write_text('Reviewed content')
        return receipt

    monkeypatch.setattr(store.memory, 'replace_file', replace_then_swap)
    assert state.save('Reviewed content', True) is False
    assert path.read_text() == 'Reviewed content'
    assert (tmp_path / 'retained-confirmed-save.md').read_text() == 'Reviewed content'
    assert store.memory.file_snapshot('note.md')['always_active'] is False
    assert store.setting(state.key)['text'] == 'Reviewed content'
    assert store.setting(state.key)['always_active'] is True
