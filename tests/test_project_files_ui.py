import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from pathlib import Path
from PySide6.QtWidgets import QApplication, QTabWidget
from letracode.store import Store
from letracode.ui import MainWindow


def test_file_panel_replaces_autosave_tabs_and_keeps_project_scope(tmp_path):
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path / 'data')
    project = store.create_project('Novel')
    other = store.create_project('Private')
    store.memory.create_file('notes.md', 'Project evidence', project_id=project)
    store.memory.create_file('shared.md', 'Shared evidence')
    store.memory.create_file('secret.md', 'Other evidence', project_id=other)
    attachment = tmp_path / 'outline.txt'; attachment.write_text('Original')
    store.link(project, str(attachment))
    window = MainWindow(store)
    try:
        window.show_selection(None, project)
        assert not window.context_panel.findChildren(QTabWidget)
        panel = window.files_panel
        assert panel.project_id == project
        assert panel._select_path(store.memory.root_for(project) / 'notes.md')
        assert panel._selected()['project_id'] == project
        assert panel._select_path(store.memory.root_for() / 'shared.md')
        assert not panel._select_path(store.memory.root_for(other) / 'secret.md')
        assert panel._select_path(attachment)
        panel.remove_selected()
        assert store.links(project) == []
        assert attachment.read_text() == 'Original'
        window.set_busy(True)
        assert not panel.new_button.isEnabled()
        assert panel.tree.isEnabled()  # Browsing stays available while mutations are blocked.
        window.set_busy(False)
        assert panel.new_button.isEnabled()
    finally:
        window.close()


def test_panel_editor_uses_selected_scope_and_keeps_explicit_drafts(tmp_path, monkeypatch):
    from letracode.memory_ui import MemoryDialog
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path / 'data'); project = store.create_project('Novel')
    store.memory.create_file('notes.md', 'Project original', project_id=project)
    store.memory.create_file('notes.md', 'Global original')
    window = MainWindow(store); window.show_selection(None, project)
    seen = []
    def edit(dialog):
        seen.append(dialog.project_id)
        assert dialog.state.relative_path == 'notes.md'
        dialog.editor.setPlainText('Unsaved user note')
        dialog.reject()
        return 0
    monkeypatch.setattr(MemoryDialog, 'exec', edit)
    try:
        window.files_panel._select_path(store.memory.root_for(project) / 'notes.md')
        window.files_panel.edit_selected()
        assert seen == [project]
        assert store.memory.file_snapshot('notes.md', project_id=project)['text'] == 'Project original'
        window.composer.setPlainText('Chat draft'); window.save_editors()
        dialog = MemoryDialog(store, project)
        dialog.scope.setCurrentIndex(dialog.scope.findData(project))
        assert dialog.select_path('notes.md')
        assert dialog.editor.toPlainText() == 'Unsaved user note'
        assert dialog.save_current()
        assert store.memory.file_snapshot('notes.md', project_id=project)['text'] == 'Unsaved user note'
        assert store.memory.file_snapshot('notes.md')['text'] == 'Global original'
        dialog.close()
    finally:
        window.close()


def test_external_editor_explicit_save_keeps_bytes_backup_and_refuses_conflict(tmp_path):
    from letracode.project_files import TextFileDialog
    app = QApplication.instance() or QApplication([])
    data = tmp_path / 'data'; data.mkdir()
    source = tmp_path / 'original.txt'; source.write_bytes(b'\xef\xbb\xbfFirst\r\nLast\r\n')
    dialog = TextFileDialog(source, data)
    assert dialog.save()
    assert source.read_bytes() == b'\xef\xbb\xbfFirst\r\nLast\r\n'
    dialog.editor.setPlainText('Reviewed replacement')
    assert dialog.save()
    assert source.read_text() == 'Reviewed replacement'
    assert next((data / 'file-backups').iterdir()).read_bytes() == b'\xef\xbb\xbfFirst\r\nLast\r\n'
    dialog.editor.setPlainText('Local correction')
    source.write_text('External correction')
    assert dialog.save() is False
    assert source.read_text() == 'External correction'
    assert dialog.editor.toPlainText() == 'Local correction'
    dialog.editor.setPlainText(dialog._saved_text)
    dialog.close()


def test_new_note_and_folder_are_history_backed_and_missing_draft_accessible(tmp_path, monkeypatch):
    from letracode.memory_ui import MemoryDialog
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path / 'data'); project = store.create_project('Notebook')
    window = MainWindow(store); window.show_selection(None, project)
    panel = window.files_panel
    def edit(dialog):
        assert dialog.project_id == project
        assert dialog.state.relative_path == 'Research/Note.md'
        dialog.editor.setPlainText('Draft for deleted file')
        dialog.reject()
        return 0
    monkeypatch.setattr(MemoryDialog, 'exec', edit)
    try:
        assert panel.new_folder('Research')
        assert panel.new_note('Note')
        assert store.memory.history(project_id=project)
        assert store.memory.file_snapshot('Research/Note.md', project_id=project)['text'] == ''
        (store.memory.root_for(project) / 'Research/Note.md').unlink()
        panel.refresh()
        dialog = MemoryDialog(store, project)
        dialog.scope.setCurrentIndex(dialog.scope.findData(project))
        assert dialog.select_path('Research/Note.md')
        assert dialog.editor.toPlainText() == 'Draft for deleted file'
        assert dialog.editor.isReadOnly()
        dialog.close()
        assert not panel.new_folder('../escape')
        assert not (store.memory.root_for(project).parent / 'escape').exists()
    finally:
        window.close()


def test_project_instructions_require_explicit_save_and_busy_blocks_changes(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QDialog, QPlainTextEdit
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path / 'data'); project = store.create_project('Instructions')
    store.update_project(project, instructions='Original instructions')
    window = MainWindow(store); window.show_selection(None, project)
    accepted = [False]
    def edit(dialog):
        dialog.findChild(QPlainTextEdit).setPlainText('User instructions')
        return QDialog.DialogCode.Accepted if accepted[0] else QDialog.DialogCode.Rejected
    monkeypatch.setattr(QDialog, 'exec', edit)
    try:
        window.edit_instructions()
        assert store.project(project)['instructions'] == 'Original instructions'
        accepted[0] = True
        window.edit_instructions()
        assert store.project(project)['instructions'] == 'User instructions'
        window.set_busy(True)
        assert not window.instructions_action.isEnabled()
        assert window.files_panel.new_folder('While busy') is False
        assert 'While busy' not in {row['path'] for row in store.memory.entries(project)}
        window.set_busy(False)
        window.show_selection(None, None)
        assert not window.instructions_action.isEnabled()
    finally:
        window.close()


def test_external_editor_rejects_same_bytes_replaced_identity_and_link(tmp_path):
    import pytest
    from letracode.project_files import TextFileDialog
    app = QApplication.instance() or QApplication([])
    data = tmp_path / 'data'; data.mkdir()
    source = tmp_path / 'original.txt'; source.write_text('Original')
    dialog = TextFileDialog(source, data)
    replacement = tmp_path / 'replacement.txt'; replacement.write_text('Original')
    replacement.replace(source)
    dialog.editor.setPlainText('Do not overwrite replacement')
    assert not dialog.save()
    assert source.read_text() == 'Original'
    assert not (data / 'file-backups').exists()
    dialog.editor.setPlainText(dialog._saved_text); dialog.close()
    link = tmp_path / 'link.txt'; link.symlink_to(source)
    with pytest.raises(ValueError, match='symlink'):
        TextFileDialog(link, data)


def test_managed_editing_honors_backend_file_types_and_preserves_markdown_suffix(tmp_path, monkeypatch):
    from letracode.memory_ui import MemoryDialog
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path / 'data'); project = store.create_project('Formats')
    root = store.memory.root_for(project)
    store.memory.create_file('existing.markdown', 'A supported note', project_id=project)
    (root / 'script.py').write_text('print("not a managed note")')
    external = tmp_path / 'attached.py'; external.write_text('print("source file")')
    store.link(project, str(external))
    window = MainWindow(store); window.show_selection(None, project)
    panel = window.files_panel
    opened = []
    def edit(dialog):
        opened.append(dialog.state.relative_path)
        dialog.reject()
        return 0
    monkeypatch.setattr(MemoryDialog, 'exec', edit)
    try:
        assert panel._select_path(root / 'existing.markdown')
        assert panel.edit_button.isEnabled()
        panel.edit_selected()
        assert opened == ['existing.markdown']
        assert panel._select_path(root / 'script.py')
        assert not panel.edit_button.isEnabled()
        assert panel.tree.currentItem().text(1) == 'Unavailable'
        assert panel._select_path(external)
        assert panel.edit_button.isEnabled()
        assert panel.new_note('New.markdown')
        assert (root / 'New.markdown').is_file()
        assert not (root / 'New.markdown.md').exists()
        assert opened == ['existing.markdown', 'New.markdown']
    finally:
        window.close()


def test_normal_managed_edit_is_compact_with_optional_history_and_safe_drafts(tmp_path, monkeypatch):
    from letracode.memory_ui import MemoryDialog
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path / 'data'); project = store.create_project('Simple editor')
    store.memory.create_file('Note.md', 'Original note', project_id=project)
    path = store.memory.root_for(project) / 'Note.md'
    window = MainWindow(store); window.show_selection(None, project)
    observed = []
    def edit(dialog):
        observed.append(dialog.editor_only)
        assert dialog.editor_only
        assert dialog.scope.isHidden()
        assert dialog.browser.isHidden()
        assert dialog.always_active.isHidden()
        assert dialog.learning_grant.isHidden()
        assert not dialog.editor.isHidden()
        assert not dialog.save_button.isHidden()
        assert dialog.project_id == project
        dialog.editor.setPlainText('Explicit save')
        assert path.read_text() == 'Original note'
        assert dialog.save_current()
        assert path.read_text() == 'Explicit save'
        dialog.editor.setPlainText('Draft survives conflict')
        path.write_text('External edit')
        assert not dialog.save_current()
        assert path.read_text() == 'External edit'
        dialog.details_toggle.click()
        assert not dialog.browser.isHidden()
        assert not dialog.history.isHidden()
        dialog.reject()
        return 0
    monkeypatch.setattr(MemoryDialog, 'exec', edit)
    try:
        window.files_panel._select_path(path)
        window.files_panel.edit_selected()
        assert observed == [True]
        recovered = MemoryDialog(store, project, editor_only=True)
        recovered.scope.setCurrentIndex(recovered.scope.findData(project))
        assert recovered.select_path('Note.md')
        assert recovered.editor.toPlainText() == 'Draft survives conflict'
        recovered.close()
        full = MemoryDialog(store, project)
        assert not full.editor_only
        assert not full.browser.isHidden()
        full.close()
    finally:
        window.close()
