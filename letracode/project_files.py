"""Native project file browser and explicit, conflict-checked text editing."""
from __future__ import annotations

import os
import hashlib
import stat
import threading
from itertools import islice
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QIcon
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QHeaderView,
    QInputDialog, QLabel, QMessageBox, QPlainTextEdit, QPushButton,
    QStyle, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget, QMenu, QToolButton,
)

from .context import MAX_FILE, TEXT_SUFFIXES, read_text, is_link
from .tools import ToolExecutor
from .memory_ui import MemoryDialog
from . import filesystem as fs


MANAGED_TEXT_SUFFIXES = {'.md', '.txt', '.markdown'}


def _check_plain_path(path):
    if any(is_link(part) for part in (path, *path.parents)):
        raise ValueError('This path contains a symlink or junction. Open the real file instead.')


def _snapshot(path):
    """Read bounded original bytes without following a replaced final path."""
    _check_plain_path(path)
    before = path.stat()
    if not stat.S_ISREG(before.st_mode) or before.st_nlink > 1:
        raise ValueError('Only ordinary files with a single hard link can be edited.')
    if before.st_size > MAX_FILE:
        raise ValueError('File exceeds the 2 MiB text limit.')
    flags = os.O_RDONLY | fs.O_NONBLOCK | fs.O_NOFOLLOW | getattr(os, 'O_BINARY', 0)
    with os.fdopen(fs.open(path, flags), 'rb') as file:
        opened = fs.fstat(file.fileno())
        if not stat.S_ISREG(opened.st_mode) or not os.path.samestat(before, opened):
            raise ValueError('The file changed while opening it.')
        raw = file.read(MAX_FILE + 1)
    after = path.stat()
    _check_plain_path(path)
    identity = lambda value: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns)
    if len(raw) > MAX_FILE or identity(before) != identity(after):
        raise ValueError('The file changed while opening it, or exceeds the text limit.')
    return identity(after), raw


class TextFileDialog(QDialog):
    def __init__(self, path, data_dir, parent=None):
        super().__init__(parent)
        self.path = Path(path).expanduser().absolute()
        self.data_dir = Path(data_dir)
        if self.path.suffix.lower() in {'.pdf', '.docx'}:
            raise ValueError('Open PDF and Word documents in their usual application to edit them.')
        _check_plain_path(self.path)
        text = read_text(self.path)
        self._identity, self._original_bytes = _snapshot(self.path)
        if self._original_bytes.decode('utf-8-sig') != text:
            raise ValueError('The file changed while opening it. Open it again.')
        self._resolved_path = self.path.resolve(strict=True)
        self.setWindowTitle(f'Edit {self.path.name}')
        self.resize(760, 560)
        layout = QVBoxLayout(self)
        label = QLabel(str(self.path))
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(label)
        self.editor = QPlainTextEdit()
        self.editor.setPlainText(text)
        self._saved_text = self.editor.toPlainText()
        self.editor.document().setModified(False)
        layout.addWidget(self.editor)
        self.error_label = QLabel()
        self.error_label.setTextFormat(Qt.TextFormat.PlainText)
        self.error_label.setWordWrap(True)
        layout.addWidget(self.error_label)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Close)
        self.save_button = buttons.button(QDialogButtonBox.StandardButton.Save)
        self.save_button.clicked.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _validate_original(self):
        identity, raw = _snapshot(self.path)
        if self.path.resolve(strict=True) != self._resolved_path or identity != self._identity or raw != self._original_bytes:
            raise ValueError('The file changed outside this editor. Your text is still here; copy it or reopen the latest file.')
        return True

    def save(self):
        try:
            self._validate_original()
            content = self.editor.toPlainText()
            if content != self._saved_text:
                if len(content) > 200000 or len(content.encode('utf-8')) > MAX_FILE:
                    raise ValueError('Text exceeds the editor limit (200,000 characters or 2 MiB). Shorten it before saving.')
                # Clicking Save authorizes this edit. Recheck the opening version
                # inside the existing writer's approval boundary, after its read.
                executor = ToolExecutor([], self.data_dir, lambda request: self._validate_original(), threading.Event())
                executor._write_file({'path': str(self.path), 'content': content, 'expected_sha256': hashlib.sha256(self._original_bytes).hexdigest()})
                identity, raw = _snapshot(self.path)
                if raw != content.encode('utf-8'):
                    raise ValueError('The file changed again after saving. Your text is still here; reopen the latest file.')
                self._identity, self._original_bytes = identity, raw
                self._saved_text = content
            self.editor.document().setModified(False)
            self.error_label.clear()
            return True
        except (OSError, ValueError) as error:
            self.error_label.setText(f'Could not save: {error}')
            return False

    def _can_close(self):
        if self.editor.toPlainText() == self._saved_text:
            return True
        answer = QMessageBox.question(
            self, 'Unsaved changes', f'Save changes to {self.path.name}?',
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        return answer == QMessageBox.StandardButton.Discard or (answer == QMessageBox.StandardButton.Save and self.save())

    def reject(self):
        if self._can_close():
            super().reject()

    def closeEvent(self, event):
        if self._can_close():
            event.accept()
        else:
            event.ignore()


class ProjectFilesPanel(QWidget):
    """One file tree; removing a root only removes its database attachment."""
    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.store = store
        self.project_id = None
        self._busy = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.new_button = QPushButton('New note…')
        self.new_folder_button = QPushButton('New folder…')
        self.add_files_button = QPushButton('Add existing files…')
        self.add_folder_button = QPushButton('Add existing folder…')
        self.new_button.setParent(self); self.new_button.hide(); self.new_button.clicked.connect(self.new_note)
        self.new_folder_button.setParent(self); self.new_folder_button.hide(); self.new_folder_button.clicked.connect(self.new_folder)
        row = QHBoxLayout()
        for button, slot in ((self.add_files_button, self.add_files), (self.add_folder_button, self.add_folder)):
            row.addWidget(button)
            button.clicked.connect(slot)
        self.create_menu = QToolButton(); self.create_menu.setText('Create note or folder')
        self.create_menu.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(self.create_menu)
        menu.addAction('New saved note…', self.new_note); menu.addAction('New notes folder…', self.new_folder)
        self.create_menu.setMenu(menu); row.addWidget(self.create_menu)
        row.addStretch()
        layout.addLayout(row)
        self.tree = QTreeWidget()
        self.tree.setAccessibleName('Saved notes and useful source files')
        self.tree.setHeaderLabels(['Name', 'Kind', 'Use by Strand'])
        self.tree.setColumnCount(3)
        self.tree.setIndentation(16)
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.header().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.itemExpanded.connect(self._expand)
        self.tree.itemSelectionChanged.connect(self._update_controls)
        self.tree.itemDoubleClicked.connect(self._activate)
        layout.addWidget(self.tree)
        self.edit_button = QPushButton('Edit')
        self.open_button = QPushButton('Open in usual app')
        self.show_button = QPushButton('Show in folder')
        row = QHBoxLayout()
        for button, slot in ((self.edit_button, self.edit_selected), (self.open_button, self.open_selected), (self.show_button, self.show_selected)):
            row.addWidget(button)
            button.clicked.connect(slot)
        layout.addLayout(row)
        self.manage_button = QPushButton('Note settings, drafts and history…')
        self.manage_button.clicked.connect(self.manage_files)
        layout.addWidget(self.manage_button)
        self.remove_button = QPushButton('Remove source')
        self.remove_button.setToolTip('Remove the selected attachment. Its original file or folder stays on disk.')
        self.refresh_button = QPushButton('Refresh')
        row = QHBoxLayout()
        row.addWidget(self.remove_button)
        row.addWidget(self.refresh_button)
        self.remove_button.clicked.connect(self.remove_selected)
        self.refresh_button.clicked.connect(self.refresh)
        layout.addLayout(row)
        self.status_label = QLabel('Select a project to see its files.')
        self.status_label.setTextFormat(Qt.TextFormat.PlainText)
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        self._update_controls()

    def _ready(self):
        return not self._busy

    def set_project(self, project_id):
        self.project_id = project_id
        self.refresh()

    def set_busy(self, busy):
        self._busy = bool(busy)
        self._update_controls()

    def _selected(self):
        item = self.tree.currentItem()
        return item.data(0, Qt.ItemDataRole.UserRole) if item else None

    def _update_controls(self):
        ready = self._ready()
        for button in (self.new_button, self.new_folder_button, self.refresh_button, self.manage_button):
            button.setEnabled(ready)
        self.create_menu.setEnabled(ready)
        self.add_files_button.setEnabled(ready)
        self.add_folder_button.setEnabled(ready)
        self.tree.setEnabled(True)
        selected = self._selected() or {}
        path = Path(selected['path']) if selected.get('path') else None
        exists = path is not None and path.exists()
        suffixes = MANAGED_TEXT_SUFFIXES if selected.get('managed') else TEXT_SUFFIXES
        supported = path is not None and (path.suffix.lower() in suffixes
            or not path.suffix and not selected.get('managed'))
        editable = exists and selected.get('kind') == 'File' and supported
        self.edit_button.setEnabled(bool(ready and editable))
        self.open_button.setEnabled(bool(exists))
        self.show_button.setEnabled(bool(path is not None and path.parent.is_dir()))
        self.remove_button.setEnabled(bool(ready and selected.get('attachment')))
        if selected:
            self.status_label.setText(str(path) + '\n' + ('Shared across workspaces' if selected.get('project_id') is None else 'Focus for this workspace') +
                (' · Included automatically when local reading is on.' if selected.get('always_active') else ' · Available for retrieval; visibility does not mean Strand has read it.'))

    def _item(self, path, parent, *, attachment=False, label=None):
        path = Path(path)
        try:
            if is_link(path):
                kind = 'Link' if path.exists() else 'Missing link'
            elif not path.exists():
                kind = 'Missing'
            elif path.is_dir():
                kind = 'Folder'
            elif path.is_file():
                kind = 'File'
            else:
                kind = 'Other'
        except OSError:
            kind = 'Unavailable'
        item = QTreeWidgetItem(parent, [label or path.name or str(path), kind, 'Unavailable' if kind in ('Missing', 'Unavailable', 'Missing link') else 'Available'])
        folder = kind == 'Folder'
        fallback = self.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon if folder else QStyle.StandardPixmap.SP_FileIcon)
        item.setIcon(0, QIcon.fromTheme('folder' if folder else 'text-x-generic', fallback))
        item.setToolTip(0, str(path))
        item.setData(0, Qt.ItemDataRole.UserRole, {'path': str(path), 'kind': kind, 'attachment': attachment, 'loaded': False, 'project_id': self.project_id})
        if kind == 'Folder':
            item.setChildIndicatorPolicy(QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator)
        return item

    def _managed_tree(self, project_id, label):
        root = self.store.memory.root_for(project_id)
        parent = self._item(root, self.tree, label=label)
        data = parent.data(0, Qt.ItemDataRole.UserRole)
        data.update(managed=True, project_id=project_id, relative_path='', loaded=True)
        parent.setData(0, Qt.ItemDataRole.UserRole, data)
        parents = {'': parent}
        for entry in sorted(self.store.memory.entries(project_id=project_id),
                            key=lambda row: (row['path'].count('/'), row['path'].casefold())):
            relative = entry['path']
            ancestor = str(Path(relative).parent)
            item = self._item(root / relative, parents.get('' if ancestor == '.' else ancestor, parent))
            data = item.data(0, Qt.ItemDataRole.UserRole)
            data.update(managed=True, project_id=project_id, relative_path=relative, loaded=True,
                        kind=entry['kind'].capitalize(), always_active=entry.get('always_active', False))
            item.setText(1, data['kind'])
            item.setText(2, 'Automatic' if entry.get('always_active') else 'Available')
            item.setData(0, Qt.ItemDataRole.UserRole, data)
            parents[relative] = item
        parent.setExpanded(True)

    def refresh(self):
        selected = self._selected()
        self.tree.clear()
        self.status_label.setText('Edit opens a file with explicit Save. Saved drafts and history remain available.')
        if self.project_id is not None:
            try:
                self.store.ensure_project_files(self.project_id)
            except (OSError, ValueError, RuntimeError) as error:
                self.status_label.setText(f'Current Context could not be imported: {error}\nRefresh to retry.')
            try:
                self._managed_tree(self.project_id, 'Workspace notes')
            except (OSError, ValueError, RuntimeError) as error:
                self.status_label.setText(f'Project folder unavailable: {error}')
        try:
            self._managed_tree(None, 'Shared notes · Strand')
        except (OSError, ValueError, RuntimeError) as error:
            self.status_label.setText(f'Shared files unavailable: {error}')
        for path in self.store.links(self.project_id) if self.project_id else []:
            self._item(path, self.tree, attachment=True)
        for path in self.store.setting('source_roots', []):
            item = self._item(path, self.tree, attachment=True)
            data = item.data(0, Qt.ItemDataRole.UserRole); data['project_id'] = None
            item.setData(0, Qt.ItemDataRole.UserRole, data)
        if selected:
            self._select_path(selected['path'])
        self._update_controls()

    def _select_path(self, path):
        def visit(item):
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if data and data['path'] == str(path):
                self.tree.setCurrentItem(item)
                return True
            return any(visit(item.child(i)) for i in range(item.childCount()))
        for index in range(self.tree.topLevelItemCount()):
            if visit(self.tree.topLevelItem(index)):
                return True
        return False

    def _expand(self, item):
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data or data['loaded'] or data['kind'] != 'Folder':
            return
        data['loaded'] = True
        item.setData(0, Qt.ItemDataRole.UserRole, data)
        path = Path(data['path'])
        try:
            _check_plain_path(path)
            # Cap enumeration itself, before sorting, even for enormous folders.
            with os.scandir(path) as entries:
                children = list(islice(entries, 301))
            for child in sorted(children[:300], key=lambda entry: entry.name.casefold()):
                node = self._item(Path(child.path), item)
                child_data = node.data(0, Qt.ItemDataRole.UserRole)
                child_data['project_id'] = data.get('project_id')
                node.setData(0, Qt.ItemDataRole.UserRole, child_data)
            if len(children) > 300:
                hint = QTreeWidgetItem(item, ['Showing first 300 entries; open folder for all', ''])
                hint.setFlags(Qt.ItemFlag.NoItemFlags)
            item.setChildIndicatorPolicy(QTreeWidgetItem.ChildIndicatorPolicy.DontShowIndicatorWhenChildless)
        except (OSError, ValueError) as error:
            self.status_label.setText(f'Could not open folder: {error}')
            item.setChildIndicatorPolicy(QTreeWidgetItem.ChildIndicatorPolicy.DontShowIndicator)

    def _new_location(self):
        selected = self._selected() or {}
        scope = selected.get('project_id', self.project_id) if selected.get('managed') else self.project_id
        relative = selected.get('relative_path', '') if selected.get('managed') else ''
        if relative and selected.get('kind') != 'Folder':
            relative = str(Path(relative).parent)
        return scope, '' if relative == '.' else relative

    def _create(self, folder=False, name=None):
        if not self._ready():
            return False
        scope, parent = self._new_location()
        if not isinstance(name, str):
            name, accepted = QInputDialog.getText(self, 'New folder' if folder else 'New note',
                'Path within this folder:', text='Folder' if folder else 'Note.md')
            if not accepted:
                return False
        if not name:
            return False
        if not folder and Path(name).suffix.lower() not in MANAGED_TEXT_SUFFIXES:
            name += '.md'
        relative = parent + '/' + name if parent else name
        try:
            if folder:
                self.store.memory.create_folder(relative, project_id=scope)
            else:
                self.store.memory.create_file(relative, project_id=scope)
            self.refresh()
            self._select_path(self.store.memory.root_for(scope) / relative)
            if not folder:
                self.edit_selected()
            return True
        except (OSError, ValueError, RuntimeError) as error:
            self.status_label.setText(f'Could not create {"folder" if folder else "note"}: {error}')
            return False

    def new_note(self, name=None):
        return self._create(name=name)

    def new_folder(self, name=None):
        return self._create(folder=True, name=name)

    def manage_files(self, editor_only=False):
        if not self._ready():
            return
        selected = self._selected() or {}
        scope = selected.get('project_id', self.project_id) if selected.get('managed') else self.project_id
        dialog = MemoryDialog(self.store, self.project_id, self, editor_only=editor_only)
        dialog.scope.setCurrentIndex(dialog.scope.findData(scope))
        if selected.get('managed') and selected.get('relative_path'):
            dialog.select_path(selected['relative_path'])
        dialog.exec()
        self.refresh()

    def add_files(self):
        if not self._ready():
            return
        paths, _ = QFileDialog.getOpenFileNames(self, 'Add useful sources to ' + ('this workspace' if self.project_id else 'Strand’s shared sources'))
        for path in paths:
            self.add_source(path)
        self.refresh()

    def add_folder(self):
        if not self._ready():
            return
        path = QFileDialog.getExistingDirectory(self, 'Choose a useful source folder')
        if path:
            self.add_source(path)
            self.refresh()

    def add_source(self, path):
        if self.project_id:
            self.store.link(self.project_id, path)
        else:
            roots = self.store.setting('source_roots', [])
            path = str(Path(path).expanduser().absolute())
            if path not in roots: self.store.set_setting('source_roots', roots + [path])

    def remove_selected(self):
        selected = self._selected()
        if self._ready() and selected and selected['attachment']:
            if selected.get('project_id'):
                self.store.unlink(selected['project_id'], selected['path'])
            else:
                self.store.set_setting('source_roots', [p for p in self.store.setting('source_roots', []) if p != selected['path']])
            self.refresh()

    def _edit_path(self, path):
        try:
            TextFileDialog(path, self.store.directory, self).exec()
            self.refresh()
        except (OSError, ValueError) as error:
            self.status_label.setText(f'Could not edit file: {error}')

    def edit_selected(self):
        if self._ready() and self.edit_button.isEnabled():
            if self._selected().get('managed'):
                self.manage_files(editor_only=True)
            else:
                self._edit_path(self._selected()['path'])

    def _activate(self, item, column):
        if self.edit_button.isEnabled():
            self.edit_selected()
        elif (self._selected() or {}).get('kind') != 'Folder':
            self.open_selected()

    def _open_path(self, path):
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            self.status_label.setText(f'Could not open {path} in its usual application.')

    def open_selected(self):
        if self.open_button.isEnabled():
            self._open_path(Path(self._selected()['path']))

    def show_selected(self):
        if self.show_button.isEnabled():
            self._open_path(Path(self._selected()['path']).parent)
