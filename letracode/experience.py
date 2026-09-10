"""Purposeful navigation surfaces around the existing storage and engine contracts."""
from __future__ import annotations

import dataclasses
import json
import os
import sys
import threading
from pathlib import Path

from PySide6.QtCore import QThread, QUrl, Signal, Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QFileDialog, QGroupBox, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QScrollArea, QVBoxLayout, QWidget, QComboBox)

from . import __version__
from .engine import Cancelled


def paragraph(text):
    label = QLabel(text)
    label.setWordWrap(True)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return label


def heading(text):
    label = paragraph(text)
    font = label.font(); font.setBold(True); font.setPointSizeF(font.pointSizeF() + 2)
    label.setFont(font)
    return label


class KnowledgePanel(QWidget):
    def __init__(self, store, files, window):
        super().__init__()
        self.store, self.files, self.window = store, files, window
        layout = QVBoxLayout(self)
        layout.addWidget(heading('What Strand can draw on'))
        layout.addWidget(paragraph('Strand is the same assistant in every workspace. Shared notes follow you; workspace notes focus the current work. '
            'A saved chat is history, a saved note is reference information, and training changes a model version.'))
        self.scope = paragraph(''); layout.addWidget(self.scope)
        row = QHBoxLayout()
        row.addWidget(QLabel('Workspace focus'))
        self.focus_choice = QComboBox(); self.focus_choice.setAccessibleName('Knowledge workspace focus')
        self.focus_choice.currentIndexChanged.connect(self.change_focus)
        row.addWidget(self.focus_choice)
        self.instructions = QPushButton('Workspace instructions…')
        self.instructions.clicked.connect(window.edit_instructions)
        row.addWidget(self.instructions)
        self.shared = QPushButton('Show shared notes')
        self.shared.clicked.connect(self.show_shared)
        row.addWidget(self.shared); row.addStretch()
        layout.addLayout(row)
        layout.addWidget(files, 1)
        layout.addWidget(paragraph('Use your files where they already live: open them in Dolphin, Obsidian, or their usual app. '
            'Adding a source prioritizes it; it does not limit Strand’s reading permission. Removing a source keeps the original. '
            '“Automatic” notes are included when local reading is on; “Available” files are consulted when relevant. '
            'See “Used for this reply” in Chat for actual request contents and limits.'))

    def show_shared(self):
        self.files._select_path(self.store.memory.root_for())

    def change_focus(self):
        ident = self.focus_choice.currentData()
        if ident != self.window.project_id:
            self.window.show_selection(None, ident)
            self.window.refresh_tree()

    def refresh(self):
        self.focus_choice.blockSignals(True)
        self.focus_choice.clear(); self.focus_choice.addItem('Everyday · shared sources', None)
        for row in self.store.projects(): self.focus_choice.addItem(row['title'], row['id'])
        self.focus_choice.setCurrentIndex(max(0, self.focus_choice.findData(self.window.project_id)))
        self.focus_choice.blockSignals(False)
        project = self.store.project(self.window.project_id) if self.window.project_id else None
        self.scope.setText('Focus: ' + (project['title'] if project else 'Everyday') +
                           ' · Shared notes are available across all workspaces.')
        self.instructions.setEnabled(project is not None and not self.window.busy)


def configuration_readiness(config):
    """Cheap local checks, explicitly separate from a successful model load."""
    checks = []
    executable = Path(config.executable).expanduser() if config.executable else None
    checks.append(('Engine', bool(executable and executable.is_file() and os.access(executable, os.R_OK if os.name == 'nt' else os.X_OK)),
                   'Choose the local llama-server program in Model setup.'))
    model = Path(config.model_path).expanduser() if config.model_path else None
    valid = False
    if model:
        try:
            with model.open('rb') as source: valid = source.read(4) == b'GGUF'
        except OSError: pass
    checks.append(('Model file', valid, 'Choose a complete local GGUF chat model in Model setup.'))
    if config.lora_path:
        from .engine import validated_lora_path
        try:
            validated_lora_path(config.lora_path); valid = True
        except (ValueError, RuntimeError, OSError): valid = False
        checks.append(('Trained adapter', valid, 'Restore a valid version in Improve or clear the adapter in Advanced model setup.'))
    return checks


class ModelCheck(QThread):
    status = Signal(str)
    result = Signal(bool, str)

    def __init__(self, engine):
        super().__init__()
        self.engine = engine
        self.cancel_event = threading.Event()

    def request_stop(self):
        self.cancel_event.set()
        threading.Thread(target=self.engine.cancel, daemon=True).start()

    def run(self):
        try:
            self.engine.start(self.cancel_event, self.status.emit)
            response = self.engine.complete([
                {'role': 'system', 'content': 'You are Strand, a local assistant. Answer briefly.'},
                {'role': 'user', 'content': 'Say hello in one short sentence.'}],
                None, self.cancel_event, lambda _: None, False)
            text = response.get('content', '').strip()
            if not text: raise ValueError('The model loaded but returned no visible answer. Try a chat model or review the engine log.')
            self.result.emit(True, 'Local reply received: ' + text[:1500] +
                             '\nThis checks loading and replying; tool use, reasoning and answer quality are not established by this check.')
        except Exception as error:
            self.result.emit(False, 'Stopped. Your configuration is kept.' if isinstance(error, Cancelled) or self.cancel_event.is_set()
                             else 'Could not produce a reply. Your configuration is kept.\n' + str(error))


class SettingsPanel(QScrollArea):
    def __init__(self, store, window):
        super().__init__()
        self.store, self.window, self.job = store, window, None
        self.setWidgetResizable(True); self.setFrameShape(self.Shape.NoFrame)
        body = QWidget(); layout = QVBoxLayout(body); self.setWidget(body)
        layout.addWidget(heading('Get Strand ready'))
        layout.addWidget(paragraph('A local model supplies Strand’s language abilities. Your notes, conversations and files stay yours when you change models. '
                                   'Start with the CPU defaults if you are unsure; larger models need more memory.'))
        self.readiness = paragraph(''); layout.addWidget(self.readiness)
        row = QHBoxLayout()
        self.setup = QPushButton('Choose or change model…'); self.setup.clicked.connect(window.model_setup)
        self.test = QPushButton('Test local reply'); self.test.clicked.connect(self.check_model)
        self.stop = QPushButton('Stop check'); self.stop.clicked.connect(self.stop_check); self.stop.setEnabled(False)
        for button in (self.setup, self.test, self.stop): row.addWidget(button)
        row.addStretch(); layout.addLayout(row)
        self.result = paragraph('No model test has run in this session.'); layout.addWidget(self.result)
        layout.addWidget(heading('Permissions'))
        layout.addWidget(paragraph('Read local files: Strand can read supported files anywhere your account can read, including hidden files. '
            'Useful sources narrow its attention, not its access. Turn local reading off in Chat to omit automatic file text and file-reading tools. '
            'Saved conversation text and workspace instructions remain available.\n\n'
            'Edits & commands: Strand asks for a reviewable preview before changes and commands. An explicit automatic-additions grant for the saved learning note is managed in Note settings. '
            'An approved command runs with your account and can use the network. Web research asks before each outgoing query or URL. '
            'Turning off Web research disables those web tools; it does not sandbox approved commands.'))
        layout.addWidget(heading('Help Strand understand LetraCode'))
        layout.addWidget(paragraph('Strand can inspect its current installation. Choose the development checkout for actual source, documentation and Git history. '
                                   'This is a useful source, and never grants permission to edit or run development commands.'))
        row = QHBoxLayout()
        default_root = Path(__file__).resolve().parent.parent
        default = str(default_root) if (default_root / '.git').exists() else ''
        self.development = QLineEdit(store.setting('development_root', default))
        self.development.setAccessibleName('LetraCode development folder')
        self.development.setPlaceholderText('Optional LetraCode source checkout')
        choose = QPushButton('Choose folder…'); choose.clicked.connect(self.choose_development)
        save = QPushButton('Save folder'); save.clicked.connect(self.save_development)
        row.addWidget(self.development, 1); row.addWidget(choose); row.addWidget(save); layout.addLayout(row)
        self.development_status = paragraph(''); layout.addWidget(self.development_status)
        layout.addWidget(heading('Your data and recovery'))
        self.installation = paragraph(''); layout.addWidget(self.installation)
        row = QHBoxLayout()
        for title, callback in [('Back up LetraCode…', window.backup), ('Open data folder', self.open_data), ('Engine log…', window.show_log)]:
            button = QPushButton(title); button.clicked.connect(callback); row.addWidget(button)
        row.addStretch(); layout.addLayout(row)
        layout.addWidget(paragraph('Backups include saved chats, notes, drafts and retained recovery records. Keep separate backups of linked originals and model/training artifacts. '
                                   'Uninstalling LetraCode keeps your data. Model rollback is in Improve → Compare and choose.'))
        layout.addStretch()
        self.refresh()

    def refresh(self):
        checks = configuration_readiness(self.window.engine_config)
        self.readiness.setText('\n'.join(('✓ ' + name + ' found' if ok else name + ' needed — ' + fix) for name, ok, fix in checks))
        busy = self.window.busy
        self.test.setEnabled(not busy and all(check[1] for check in checks))
        self.setup.setEnabled(not busy)
        model = Path(self.window.engine_config.model_path).name or 'Not selected'
        self.installation.setText(f'LetraCode {__version__}\nRunning from: {Path(__file__).resolve().parent.parent}\n'
                                  f'Data: {self.store.directory}\nModel: {model}\nPython: {sys.version.split()[0]}')

    def choose_development(self):
        folder = QFileDialog.getExistingDirectory(self, 'LetraCode development folder', self.development.text())
        if folder: self.development.setText(folder); self.save_development()

    def save_development(self):
        raw = self.development.text().strip()
        folder = Path(raw).expanduser() if raw else None
        if folder and (not folder.is_dir() or not (folder / 'letracode').is_dir()):
            self.development_status.setText('Choose the LetraCode source folder containing letracode/. Your previous choice is kept.'); return
        self.store.set_setting('development_root', str(folder.resolve()) if folder else '')
        self.development_status.setText('Development source saved.' if folder else 'Using current installation only.')

    def open_data(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.store.directory)))

    def check_model(self):
        if self.window.busy or self.job: return
        self.window.sync_engine()
        self.job = ModelCheck(self.window.engine)
        self.job.status.connect(self.result.setText)
        self.job.result.connect(lambda ok, text: self.result.setText(text))
        self.job.finished.connect(self.finished)
        self.window.set_busy(True); self.stop.setEnabled(True)
        self.result.setText('Loading locally. You can keep reading and drafting while this check runs.')
        self.job.start()

    def stop_check(self):
        if self.job: self.job.request_stop(); self.stop.setEnabled(False)

    def finished(self):
        job = self.job; self.job = None
        if job: job.deleteLater()
        self.stop.setEnabled(False); self.window.set_busy(False); self.refresh()
        if self.window.closing_when_stopped: self.window.close()
