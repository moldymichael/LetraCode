"""Native editing of ordinary Strand files, with recoverable conflicting drafts."""
from __future__ import annotations

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout)


class MemoryEditorState:
    def __init__(self, store, scope, project_id=None):
        self.store, self.scope, self.project_id = store, scope, project_id
        self.key = 'strand_draft_' + scope + '_' + (project_id or 'global')
        self.snapshot = {'text': '', 'sha256': None, 'path': str(store.strand.path(scope, project_id))}
        self.available = True
        self.error = ''
        try:
            self.snapshot = store.strand.snapshot(scope, project_id)
        except (OSError, ValueError) as error:
            self.mark_unavailable(error)
        draft = store.setting(self.key)
        self.text = self.snapshot['text']
        if isinstance(draft, dict) and all(isinstance(draft.get(key), str) for key in ('text', 'base_text', 'sha256')):
            self.text = draft['text']
            self.snapshot = dict(self.snapshot, text=draft['base_text'], sha256=draft['sha256'])

    def mark_unavailable(self, error):
        self.available = False
        self.error = (f'Memory file unavailable: {self.snapshot["path"]}\n{error}\n'
            'Memory is read-only. Any editor draft is kept separately. Repair the file, then Reload file to continue.')

    def keep_draft(self, text):
        self.text = text
        if isinstance(self.snapshot['sha256'], str):
            self.store.set_setting(self.key, {'text':text, 'base_text':self.snapshot['text'], 'sha256':self.snapshot['sha256']})

    def save(self, text):
        if not self.available:
            return False
        try:
            if text != self.snapshot['text']:
                self.store.strand.replace(self.scope, text, self.snapshot['sha256'],
                    project_id=self.project_id, origin='user editor')
            self.snapshot = self.store.strand.snapshot(self.scope, self.project_id)
            self.text = self.snapshot['text']
            if self.store.setting(self.key) is not None:
                self.store.set_setting(self.key, None)
            self.error = ''
            return True
        except (OSError, ValueError, RuntimeError) as error:
            self.keep_draft(text)
            self.error = f'{error} Your editor draft is saved separately. Copy it if needed, then Reload file to use the external version.'
            try:
                self.store.strand.snapshot(self.scope, self.project_id)
            except (OSError, ValueError) as unavailable:
                self.mark_unavailable(unavailable)
            return False

    def reload(self, text=None):
        try:
            snapshot = self.store.strand.snapshot(self.scope, self.project_id)
        except (OSError, ValueError) as error:
            self.keep_draft(self.text if text is None else text)
            self.mark_unavailable(error)
            return False
        # Discard the old draft only after its replacement was read safely.
        self.store.set_setting(self.key, None)
        self.snapshot = snapshot
        self.text = self.snapshot['text']
        self.available = True
        self.error = ''
        return True


class StrandDialog(QDialog):
    def __init__(self, store, project_id=None, parent=None):
        super().__init__(parent)
        self.store, self.project_id = store, project_id
        self.setWindowTitle('Strand identity and memory — LetraCode')
        self.resize(800, 640)
        layout = QVBoxLayout(self)
        note = QLabel('These are ordinary editable files. Save records your changes; Reload file uses the latest external version. Memory does not train or replace the model.')
        note.setWordWrap(True); layout.addWidget(note)
        self.scope = QComboBox()
        for label, scope in [('Identity', 'identity'), ('Working preferences', 'preferences'), ('Global memory', 'global'), ('Programming learning record', 'learning')]:
            self.scope.addItem(label, scope)
        if project_id:
            self.scope.addItem('This project’s memory', 'project')
        layout.addWidget(self.scope)
        self.path_label = QLabel(); self.path_label.setTextFormat(Qt.TextFormat.PlainText)
        self.path_label.setWordWrap(True); self.path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.path_label)
        self.editor = QPlainTextEdit(); layout.addWidget(self.editor, 1)
        self.message = QLabel(); self.message.setTextFormat(Qt.TextFormat.PlainText); self.message.setWordWrap(True); layout.addWidget(self.message)
        row = QHBoxLayout()
        self.save_button = QPushButton('Save file'); self.save_button.clicked.connect(self.save_current); row.addWidget(self.save_button)
        reload = QPushButton('Reload file'); reload.clicked.connect(self.reload_current); row.addWidget(reload)
        folder = QPushButton('Open Strand folder')
        folder.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(store.directory / 'strand')))); row.addWidget(folder)
        layout.addLayout(row)
        self.learning_grant = QCheckBox('Allow Strand to append programming learning notes without asking each time')
        self.learning_grant.setChecked(store.setting('strand_learning_grant', False) is True)
        self.learning_grant.setToolTip('Only learning/programming.md. This does not allow other memory changes, source edits, identity changes, commands or training. Saved updates still have Undo receipts.')
        self.learning_grant.toggled.connect(lambda enabled:store.set_setting('strand_learning_grant', enabled))
        layout.addWidget(self.learning_grant)
        self.history = QComboBox(); layout.addWidget(self.history)
        undo = QPushButton('Undo selected saved change'); undo.clicked.connect(self.undo_selected); layout.addWidget(undo)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject); layout.addWidget(buttons)
        self.state = None
        self.scope.currentIndexChanged.connect(self.change_scope)
        self.change_scope()

    def change_scope(self, *_):
        if self.state is not None:
            # Conflicts keep their draft under the old file's key.
            self.state.save(self.editor.toPlainText())
        scope = self.scope.currentData()
        self.state = MemoryEditorState(self.store, scope, self.project_id if scope == 'project' else None)
        self.editor.setPlainText(self.state.text)
        self.editor.setReadOnly(not self.state.available)
        self.save_button.setEnabled(self.state.available)
        self.path_label.setText(str(self.state.snapshot['path']))
        self.message.setText(self.state.error or 'External edits are checked before saving. A conflicting local draft is kept separately.')
        self.refresh_receipts()

    def save_current(self):
        ok = self.state.save(self.editor.toPlainText())
        self.editor.setReadOnly(not self.state.available)
        self.save_button.setEnabled(self.state.available)
        self.message.setText('Saved to ' + str(self.state.snapshot['path']) if ok else self.state.error)
        if ok:
            self.editor.setPlainText(self.state.text)
        self.refresh_receipts()
        return ok

    def reload_current(self):
        ok = self.state.reload(self.editor.toPlainText())
        self.editor.setPlainText(self.state.text)
        self.editor.setReadOnly(not self.state.available)
        self.save_button.setEnabled(self.state.available)
        self.message.setText('Reloaded the current file. The previous editor draft was discarded.' if ok else self.state.error)

    def refresh_receipts(self):
        self.history.clear()
        for receipt in self.store.strand.receipts():
            if receipt['scope'] != self.state.scope or receipt.get('project_id') != self.state.project_id:
                continue
            self.history.addItem(f"{receipt['date']} · {receipt['origin']} · {receipt['status']}", receipt['id'])

    def undo_selected(self):
        ident = self.history.currentData()
        if not ident:
            return
        if not self.save_current():
            return
        try:
            self.store.strand.undo(ident)
            self.reload_current(); self.refresh_receipts(); self.message.setText('Change undone. The previous file contents were restored.')
        except (OSError, ValueError, RuntimeError) as error:
            self.message.setText(str(error))

    def reject(self):
        self.save_current()
        super().reject()

    def closeEvent(self, event):
        self.save_current()
        event.accept()
