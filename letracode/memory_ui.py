"""User-owned Memory trees with explicit saves and durable editor drafts."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import PurePosixPath

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QHBoxLayout, QInputDialog, QLabel, QMessageBox, QPlainTextEdit, QPushButton,
    QSplitter, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)


class MemoryFileEditorState:
    def __init__(self, store, path, project_id=None):
        self.store, self.relative_path, self.project_id = store, path, project_id
        key = hashlib.sha256(path.encode('utf-8')).hexdigest()
        self.key = 'memory_draft_' + (project_id or 'global') + '_' + key
        self.snapshot = {'text': '', 'sha256': None, 'path': path,
                         'relative_path': path, 'always_active': False}
        self.available = True
        self.error = ''
        try:
            self.snapshot = store.memory.file_snapshot(path, project_id=project_id)
        except (OSError, ValueError, RuntimeError) as error:
            self.mark_unavailable(error)
        scope = self.snapshot.get('legacy_scope') or store.memory.legacy_scope_for(path, project_id=project_id)
        if scope:
            self.key = 'strand_draft_' + scope + '_' + (project_id or 'global')
        self.text = self.snapshot['text']
        self.active = self.snapshot.get('always_active', False)
        self.set_identity(self.snapshot)
        draft = store.setting(self.key)
        if isinstance(draft, dict) and all(isinstance(draft.get(key), str)
                for key in ('text', 'base_text', 'sha256')):
            legacy_identity = (draft.get('memory_tree_draft') is not True
                and 'file_id' not in draft and 'entry_identity' not in draft
                and scope is not None and self.snapshot.get('legacy_scope') == scope
                and isinstance(self.snapshot.get('file_id'), str))
            # Original Strand drafts had immutable scope aliases. Only that
            # known legacy lineage can supply missing identity fields; a tree
            # draft may refer to an earlier incarnation of this pathname.
            if not legacy_identity:
                self.set_identity(draft)
            self.text = draft['text']
            self.active = draft.get('always_active', self.active) is True
            self.snapshot = dict(self.snapshot, text=draft['base_text'], sha256=draft['sha256'],
                always_active=draft.get('base_active', self.snapshot.get('always_active', False)),
                revision=draft.get('revision', self.snapshot.get('revision')),
                file_id=self.identity_fields.get('file_id'),
                entry_identity=self.identity_fields.get('entry_identity'))
            if not self.identity_known:
                self.error = ('The original file identity is unavailable for this draft. '
                    'Your draft is kept separately. Reload file to review the current file before saving.')

    def set_identity(self, value):
        self.identity_fields = {key: value[key] for key in ('file_id', 'entry_identity') if key in value}
        ident = self.identity_fields.get('file_id')
        inode = self.identity_fields.get('entry_identity')
        self.identity_known = ('file_id' in self.identity_fields
            and (ident is None or isinstance(ident, str) and re.fullmatch(r'[a-f0-9]{32}', ident) is not None)
            and isinstance(inode, list) and len(inode) == 2
            and all(type(part) is int and part >= 0 for part in inode))

    def mark_unavailable(self, error):
        self.available = False
        self.error = (f'Memory file unavailable: {self.snapshot["path"]}\n{error}\n'
            'Your draft is kept separately. Repair the file, then Reload file to continue.')

    def dirty(self, text, active):
        return text != self.snapshot['text'] or active != self.snapshot.get('always_active', False)

    def keep_draft(self, text, active):
        self.text, self.active = text, active
        if isinstance(self.snapshot['sha256'], str):
            if self.dirty(text, active):
                draft = {'text': text, 'always_active': active,
                    'base_text': self.snapshot['text'], 'sha256': self.snapshot['sha256'],
                    'base_active': self.snapshot.get('always_active', False),
                    'revision': self.snapshot.get('revision'),
                    'relative_path': self.relative_path, 'project_id': self.project_id,
                    'memory_tree_draft': True}
                # Keep even incomplete legacy identity fields without adopting
                # the replacement currently occupying the same pathname.
                draft.update(self.identity_fields)
                self.store.set_setting(self.key, draft)
            elif self.store.setting(self.key) is not None:
                self.store.set_setting(self.key, None)

    def save(self, text, active):
        if not self.identity_known:
            self.keep_draft(text, active)
            self.error = ('The original file identity is unavailable for this draft. '
                'Your draft is kept separately. Reload file to review the current file before saving.')
            return False
        if not self.available:
            self.keep_draft(text, active)
            return False
        try:
            # Both changes use the reviewed content version. If only the flag
            # changes, the backend still rejects an externally edited file.
            expected = self.snapshot['sha256']
            if text != self.snapshot['text']:
                receipt = self.store.memory.replace_file(self.relative_path, text, expected,
                    project_id=self.project_id, expected_file_id=self.snapshot.get('file_id'),
                    expected_entry_identity=self.snapshot.get('entry_identity'))
                expected = receipt['after_sha256']
                # The content save may succeed even if the following flag save
                # fails. Retain that confirmed base for a recoverable retry.
                # An external unregistered file acquires an ID during this
                # successful write. Adopt only the identity in its confirmed
                # receipt, never whatever happens to occupy the path afterward.
                confirmed_id = receipt['scope'].removeprefix('file:')
                self.snapshot = dict(self.snapshot, text=text, sha256=expected,
                                     file_id=confirmed_id, entry_identity=receipt['entry_identity'])
                self.set_identity(self.snapshot)
                confirmed = self.store.memory.file_snapshot(self.relative_path, project_id=self.project_id)
                if (confirmed['file_id'] != confirmed_id or confirmed['sha256'] != expected
                        or confirmed['entry_identity'] != receipt['entry_identity']):
                    raise ValueError('Memory file changed after saving content; review it before saving activation.')
            if active != self.snapshot.get('always_active', False):
                self.store.memory.set_active(self.relative_path, active, expected,
                    project_id=self.project_id, expected_revision=self.snapshot.get('revision'),
                    expected_file_id=self.snapshot.get('file_id'),
                    expected_entry_identity=self.snapshot.get('entry_identity'))
            self.snapshot = self.store.memory.file_snapshot(self.relative_path, project_id=self.project_id)
            self.set_identity(self.snapshot)
            self.text = self.snapshot['text']
            self.active = self.snapshot.get('always_active', False)
            if self.store.setting(self.key) is not None:
                self.store.set_setting(self.key, None)
            self.error = ''
            return True
        except (OSError, ValueError, RuntimeError) as error:
            self.keep_draft(text, active)
            self.error = f'{error}\nYour editor draft is saved separately. Reload file to use the external version.'
            try:
                self.store.memory.file_snapshot(self.relative_path, project_id=self.project_id)
            except (OSError, ValueError, RuntimeError) as unavailable:
                self.mark_unavailable(unavailable)
            return False

    def reload(self, text=None, active=None):
        try:
            snapshot = self.store.memory.file_snapshot(self.relative_path, project_id=self.project_id)
        except (OSError, ValueError, RuntimeError) as error:
            self.keep_draft(self.text if text is None else text, self.active if active is None else active)
            self.mark_unavailable(error)
            return False
        self.store.set_setting(self.key, None)
        self.snapshot = snapshot
        self.text = snapshot['text']
        self.active = snapshot.get('always_active', False)
        self.available = True
        self.set_identity(snapshot)
        self.error = ''
        return True


class MemoryDialog(QDialog):
    def __init__(self, store, project_id=None, parent=None):
        super().__init__(parent)
        self.store = store
        self.state = None
        self.selected_snapshot = None
        self.items = {}
        self.setWindowTitle('Memory folders — LetraCode')
        self.resize(960, 680)
        layout = QVBoxLayout(self)
        note = QLabel('Organize ordinary Markdown and text files in your own folders. '
            'Only files marked always active are automatically included; other files can be listed, searched and read when relevant. '
            'Use Save file to apply edits. Navigation and Close keep unsaved drafts separately.')
        note.setWordWrap(True); layout.addWidget(note)
        self.scope = QComboBox(); self.scope.addItem('Global Memory', None)
        if project_id:
            self.scope.addItem('This project’s Memory', project_id)
        layout.addWidget(self.scope)
        splitter = QSplitter(); layout.addWidget(splitter, 1)
        browser = QWidget(); left = QVBoxLayout(browser); left.setContentsMargins(0, 0, 8, 0)
        self.tree = QTreeWidget(); self.tree.setHeaderLabels(['Memory', 'Active'])
        self.tree.setColumnWidth(0, 240); left.addWidget(self.tree, 1)
        for label, callback in [('New folder…', self.create_folder), ('New file…', self.create_file),
                                ('Rename / move…', self.move_selected), ('Delete…', self.delete_selected),
                                ('Refresh tree', self.refresh_tree), ('Open Memory folder', self.open_folder)]:
            button = QPushButton(label); button.clicked.connect(callback); left.addWidget(button)
        splitter.addWidget(browser)
        pane = QWidget(); right = QVBoxLayout(pane); right.setContentsMargins(8, 0, 0, 0)
        self.path_label = QLabel(); self.path_label.setTextFormat(Qt.TextFormat.PlainText)
        self.path_label.setWordWrap(True)
        self.path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        right.addWidget(self.path_label)
        self.editor = QPlainTextEdit(); right.addWidget(self.editor, 1)
        self.always_active = QCheckBox('Always active for this Memory scope')
        self.always_active.setToolTip('Save file applies this choice. Project files are included only in that project.')
        right.addWidget(self.always_active)
        row = QHBoxLayout()
        self.save_button = QPushButton('Save file'); self.save_button.clicked.connect(self.save_current); row.addWidget(self.save_button)
        reload_button = QPushButton('Reload file'); reload_button.clicked.connect(self.reload_current)
        reload_button.setToolTip('Discard this editor draft only after the current file can be read safely.')
        row.addWidget(reload_button); right.addLayout(row)
        self.history = QComboBox(); right.addWidget(self.history)
        self.undo_button = QPushButton('Undo selected saved change'); self.undo_button.clicked.connect(self.undo_selected)
        right.addWidget(self.undo_button)
        splitter.addWidget(pane); splitter.setSizes([300, 640])
        self.message = QLabel(); self.message.setTextFormat(Qt.TextFormat.PlainText)
        self.message.setWordWrap(True); layout.addWidget(self.message)
        self.learning_grant = QCheckBox('Allow appends without review to the original learning file only')
        self.learning_grant.setChecked(store.setting('strand_learning_grant', False) is True)
        self.learning_grant.setToolTip('Only the existing learning-file grant. Other memory writes require review; saved changes retain Undo.')
        self.learning_grant.toggled.connect(lambda enabled: store.set_setting('strand_learning_grant', enabled))
        layout.addWidget(self.learning_grant)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject); layout.addWidget(buttons)
        self.tree.currentItemChanged.connect(self.change_file)
        self.scope.currentIndexChanged.connect(self.change_scope)
        self.change_scope()

    @property
    def project_id(self):
        return self.scope.currentData()

    def keep_current_draft(self):
        if self.state is not None:
            self.state.keep_draft(self.editor.toPlainText(), self.always_active.isChecked())

    def change_scope(self, *_):
        self.keep_current_draft()
        self.state = None
        self.refresh_tree()

    def selected_path(self):
        item = self.tree.currentItem()
        return item.data(0, Qt.ItemDataRole.UserRole) if item else None

    def saved_drafts(self):
        """Keep missing external files and old fixed-scope drafts recoverable."""
        prefix = 'memory_draft_' + (self.project_id or 'global') + '_'
        legacy = {}
        scopes = ('project',) if self.project_id else ('identity', 'preferences', 'global', 'learning')
        for scope in scopes:
            key = 'strand_draft_' + scope + '_' + (self.project_id or 'global')
            try:
                legacy[key] = self.store.strand.path(scope, self.project_id if scope == 'project' else None).relative_to(
                    self.store.memory.root_for(project_id=self.project_id)).as_posix()
            except (OSError, ValueError, RuntimeError):
                continue
        drafts = {}
        for row in self.store.rows("SELECT key,value FROM settings WHERE key LIKE 'memory_draft_%' OR key LIKE 'strand_draft_%'"):
            if not row['key'].startswith(prefix) and row['key'] not in legacy:
                continue
            try:
                draft = json.loads(row['value'])
            except (TypeError, ValueError):
                continue
            if not isinstance(draft, dict) or not all(isinstance(draft.get(key), str)
                    for key in ('text', 'base_text', 'sha256')):
                continue
            path = draft.get('relative_path', legacy.get(row['key']))
            if (not isinstance(path, str) or not path or PurePosixPath(path).is_absolute()
                    or any(part in ('.', '..') for part in path.split('/'))):
                continue
            if draft['text'] != draft['base_text'] or draft.get('always_active', False) != draft.get('base_active', False):
                drafts[path] = draft
        return drafts

    def select_path(self, path):
        item = self.items.get(path)
        if item is None:
            return False
        self.tree.setCurrentItem(item)
        self.tree.scrollToItem(item)
        return True

    def refresh_tree(self, selected=None):
        self.keep_current_draft()
        if not isinstance(selected, str):
            selected = self.selected_path()
        self.state = None
        self.selected_snapshot = None
        self.tree.blockSignals(True)
        self.tree.clear(); self.items = {}
        try:
            entries = self.store.memory.entries(project_id=self.project_id)
            known = {entry['path'] for entry in entries}
            for path in self.saved_drafts():
                if path not in known:
                    entries.append({'path': path, 'kind': 'file', 'missing_draft': True})
                    known.add(path)
                    for parent in PurePosixPath(path).parents:
                        name = str(parent)
                        if name != '.' and name not in known:
                            entries.append({'path': name, 'kind': 'folder'})
                            known.add(name)
            for entry in sorted(entries, key=lambda row: (row['path'].count('/'), row['path'].casefold())):
                path = entry['path']; parent = str(PurePosixPath(path).parent)
                label = PurePosixPath(path).name + (' (missing; draft kept)' if entry.get('missing_draft') else '')
                item = QTreeWidgetItem([label, 'Always' if entry.get('always_active') else ''])
                item.setData(0, Qt.ItemDataRole.UserRole, path)
                item.setData(0, Qt.ItemDataRole.UserRole + 1, entry['kind'])
                if parent in self.items:
                    self.items[parent].addChild(item)
                else:
                    self.tree.addTopLevelItem(item)
                self.items[path] = item
            self.tree.expandAll()
        except (OSError, ValueError, RuntimeError) as error:
            self.message.setText(f'Memory tree unavailable: {error}')
        finally:
            self.tree.blockSignals(False)
        if not selected or not self.select_path(selected):
            self.change_file(None)
        self.refresh_history()

    def change_file(self, item, *_):
        self.keep_current_draft()
        self.state = None; self.selected_snapshot = None
        self.editor.clear(); self.editor.setReadOnly(True)
        self.always_active.setChecked(False); self.always_active.setEnabled(False)
        self.save_button.setEnabled(False)
        self.path_label.setText('Select a memory file, or create a folder or file.')
        if item:
            path = item.data(0, Qt.ItemDataRole.UserRole)
            self.path_label.setText(path)
            try:
                self.selected_snapshot = self.store.memory.snapshot_entry(path, project_id=self.project_id)
            except (OSError, ValueError, RuntimeError) as error:
                self.message.setText(str(error))
            if item.data(0, Qt.ItemDataRole.UserRole + 1) == 'file':
                self.state = MemoryFileEditorState(self.store, path, self.project_id)
                self.show_state()
        self.refresh_history()

    def open_folder(self):
        try:
            path = self.store.memory.root_for(project_id=self.project_id)
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        except (OSError, ValueError, RuntimeError) as error:
            self.message.setText(str(error))

    def show_state(self):
        self.editor.setPlainText(self.state.text)
        self.editor.setReadOnly(not self.state.available)
        self.always_active.setChecked(self.state.active)
        self.always_active.setEnabled(self.state.available)
        self.save_button.setEnabled(self.state.available)
        self.path_label.setText(str(self.state.snapshot['path']))
        self.message.setText(self.state.error or 'External edits are checked before saves. Unsaved drafts are kept separately.')

    def save_current(self):
        if not self.state:
            return False
        ok = self.state.save(self.editor.toPlainText(), self.always_active.isChecked())
        self.show_state()
        if ok:
            self.selected_snapshot = self.store.memory.snapshot_entry(self.state.relative_path, project_id=self.project_id)
            self.tree.currentItem().setText(1, 'Always' if self.state.active else '')
            self.message.setText('Memory file saved. The saved change is available in history.')
        self.refresh_history()
        return ok

    def reload_current(self):
        if not self.state:
            self.refresh_tree()
            return False
        ok = self.state.reload(self.editor.toPlainText(), self.always_active.isChecked())
        self.show_state()
        if ok:
            self.selected_snapshot = self.store.memory.snapshot_entry(self.state.relative_path, project_id=self.project_id)
            self.message.setText('Reloaded the current file. The previous editor draft was discarded.')
        self.refresh_history()
        return ok

    def clean_for_operation(self):
        if self.state and self.state.dirty(self.editor.toPlainText(), self.always_active.isChecked()):
            self.keep_current_draft()
            self.message.setText('Your editor draft is kept separately. Save or Reload file before moving, deleting or undoing changes.')
            return False
        path = self.selected_path()
        if path and any(name == path or name.startswith(path + '/') for name in self.saved_drafts()):
            self.message.setText('This selection contains saved editor drafts. Open those files and Save or Reload before moving, deleting or undoing changes.')
            return False
        return True

    def new_path(self, title, default=''):
        path, ok = QInputDialog.getText(self, title, 'Path within this Memory tree (for example, Research/notes.md):', text=default)
        return path if ok and path else None

    def parent_path(self):
        path = self.selected_path()
        if not path:
            return ''
        item = self.tree.currentItem()
        parent = path if item.data(0, Qt.ItemDataRole.UserRole + 1) == 'folder' else str(PurePosixPath(path).parent)
        return '' if parent == '.' else parent + '/'

    def create_folder(self, path=None):
        if not isinstance(path, str):
            path = self.new_path('New Memory folder', self.parent_path())
        if not path:
            return False
        try:
            self.store.memory.create_folder(path, project_id=self.project_id)
            self.refresh_tree(path)
            self.message.setText('Memory folder created.')
            return True
        except (OSError, ValueError, RuntimeError) as error:
            self.message.setText(str(error)); return False

    def create_file(self, path=None):
        if not isinstance(path, str):
            path = self.new_path('New Markdown or text file', self.parent_path())
        if not path:
            return False
        try:
            self.store.memory.create_file(path, project_id=self.project_id)
            self.refresh_tree(path)
            self.message.setText('Memory file created. Edit its contents, then Save file.')
            return True
        except (OSError, ValueError, RuntimeError) as error:
            self.message.setText(str(error)); return False

    def move_selected(self, destination=None):
        if not self.clean_for_operation():
            return False
        path = self.selected_path()
        if not path or not self.selected_snapshot:
            return False
        if not isinstance(destination, str):
            destination = self.new_path('Rename or move in this Memory tree', path)
        if not destination:
            return False
        try:
            self.store.memory.move(path, destination, self.selected_snapshot['sha256'], project_id=self.project_id,
                expected_file_id=self.selected_snapshot.get('file_id'),
                expected_entry_identity=self.selected_snapshot['entry_identity'])
            self.refresh_tree(destination)
            self.message.setText('Memory moved. Undo is available in history.')
            return True
        except (OSError, ValueError, RuntimeError) as error:
            self.message.setText(str(error)); return False

    def delete_selected(self, *_):
        if not self.clean_for_operation():
            return False
        path = self.selected_path()
        if not path or not self.selected_snapshot:
            return False
        answer = QMessageBox.question(self, 'Delete Memory', f'Delete {path} and any files inside it?\nThe saved deletion remains recoverable through history and Undo.',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return False
        try:
            self.store.memory.delete(path, self.selected_snapshot['sha256'], project_id=self.project_id,
                expected_file_id=self.selected_snapshot.get('file_id'),
                expected_entry_identity=self.selected_snapshot['entry_identity'])
            self.refresh_tree()
            self.message.setText('Memory deleted. Undo is available in history.')
            return True
        except (OSError, ValueError, RuntimeError) as error:
            self.message.setText(str(error)); return False

    def refresh_history(self, selected_id=None):
        self.history.clear()
        try:
            receipts = self.store.memory.history(project_id=self.project_id)
            for receipt in receipts:
                ident = receipt['id']
                operation = receipt.get('operation', receipt.get('origin', 'saved change'))
                target = receipt.get('relative_path', '')
                if operation in ('create', 'restore'):
                    target = receipt.get('destination', target)
                elif operation == 'delete':
                    target = receipt.get('source', target)
                prefix = f'.projects/{self.project_id}/' if self.project_id else ''
                target = target.removeprefix(prefix)
                if operation == 'move':
                    source = receipt.get('source', '').removeprefix(prefix)
                    destination = receipt.get('destination', '').removeprefix(prefix)
                    target = f'{source} → {destination}'
                if receipt.get('undo_of'):
                    operation = 'Undo · ' + operation
                order = f"#{receipt['sequence']} · " if receipt.get('sequence') is not None else ''
                self.history.addItem(f"{order}{receipt.get('date', '')} · {operation} · {target} · {receipt.get('status', '')}", ident)
            if selected_id:
                self.history.setCurrentIndex(self.history.findData(selected_id))
            self.history.setEnabled(True)
            self.undo_button.setEnabled(bool(receipts))
        except (OSError, ValueError, RuntimeError) as error:
            self.history.setEnabled(False); self.undo_button.setEnabled(False)
            self.message.setText(f'History unavailable: {error}\nUndo is unavailable until history is reconciled.')

    def undo_selected(self):
        if not self.clean_for_operation():
            return False
        ident = self.history.currentData()
        if not ident:
            return False
        try:
            self.store.memory.undo(ident)
            self.refresh_tree()
            self.message.setText('Saved Memory change undone.')
            return True
        except (OSError, ValueError, RuntimeError) as error:
            self.refresh_history(ident)
            self.message.setText(str(error)); return False

    def reject(self):
        self.keep_current_draft()
        super().reject()

    def closeEvent(self, event):
        self.keep_current_draft()
        event.accept()
