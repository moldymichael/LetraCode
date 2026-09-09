"""System-styled setup, approval, and project-link dialogs."""
from __future__ import annotations

import dataclasses
import os
import shutil
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFontDatabase
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QMessageBox, QPlainTextEdit, QPushButton, QSpinBox, QVBoxLayout, QWidget)

from .engine import EngineConfig, EngineError, validated_lora_path
from .platform import engine_setup_help, is_windows


class ApprovalDialog(QDialog):
    stop_requested = Signal()

    def __init__(self, pending, parent=None):
        super().__init__(parent)
        self.pending = pending
        self.setWindowTitle(pending.request.title)
        self.resize(740, 510)
        self.setModal(True)
        layout = QVBoxLayout(self)
        title = QLabel(pending.request.title)
        font = title.font(); font.setBold(True); title.setFont(font)
        layout.addWidget(title)
        warning = QLabel(pending.request.warning)
        warning.setWordWrap(True)
        layout.addWidget(warning)
        details = QPlainTextEdit(pending.request.details)
        details.setReadOnly(True)
        details.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        layout.addWidget(details)
        self.confirm = QCheckBox('I reviewed this command and allow it to run with my account.')
        self.confirm.setVisible(pending.request.kind == 'command')
        layout.addWidget(self.confirm)
        buttons = QDialogButtonBox()
        stop = buttons.addButton('Stop task', QDialogButtonBox.ButtonRole.ActionRole)
        stop.setAutoDefault(False)
        stop.clicked.connect(self.stop_task)
        deny = buttons.addButton('Deny', QDialogButtonBox.ButtonRole.RejectRole)
        allow = buttons.addButton('Approve once', QDialogButtonBox.ButtonRole.AcceptRole)
        deny.setDefault(True)
        allow.setAutoDefault(False)
        if pending.request.kind == 'command':
            allow.setEnabled(False)
            self.confirm.toggled.connect(allow.setEnabled)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.finished.connect(lambda result: pending.decide(result == QDialog.DialogCode.Accepted))

    def stop_task(self):
        self.stop_requested.emit()
        self.reject()


class ModelDialog(QDialog):
    def __init__(self, config: EngineConfig, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Model Setup — LetraCode')
        self.resize(690, 660)
        layout = QVBoxLayout(self)
        intro = QLabel('Choose the local engine and model. LetraCode starts and stops the engine for you. Your conversations stay on this computer.')
        intro.setWordWrap(True)
        layout.addWidget(intro)
        form = QFormLayout()
        self.executable = QLineEdit(config.executable or shutil.which('llama-server.exe' if is_windows() else 'llama-server') or '')
        self.executable.setPlaceholderText(r'C:\llama.cpp\llama-server.exe' if is_windows() else '/usr/bin/llama-server')
        self.model = QLineEdit(config.model_path)
        self.model.setPlaceholderText('Choose a local .gguf instruction/chat model')
        self.secondary_model = QLineEdit(config.secondary_model_path)
        self.secondary_model.setPlaceholderText('Optional: a different GGUF for two-model conversations')
        self.lora = QLineEdit(config.lora_path)
        self.lora.setPlaceholderText('Optional local .gguf LoRA adapter for Model A')
        self.lora.setToolTip('The adapter applies only to Model A and must match its base model. Changing Model A keeps this selection; clear it to use only the base model.')
        for label, field, mode in [('Engine executable',self.executable,'engine'),('Model A file',self.model,'model'),('Model B file (optional)',self.secondary_model,'model'),('Model A LoRA adapter (optional)',self.lora,'adapter')]:
            row = QWidget(); line = QHBoxLayout(row); line.setContentsMargins(0,0,0,0)
            line.addWidget(field)
            browse = QPushButton('Browse…')
            browse.clicked.connect(lambda checked=False, f=field, m=mode: self.browse(f,m))
            line.addWidget(browse)
            if mode == 'adapter':
                clear = QPushButton('Clear adapter')
                clear.clicked.connect(self.lora.clear)
                line.addWidget(clear)
            form.addRow(label,row)
        adapter_note = QLabel('The adapter applies only to Model A and must match its base model. Clear the adapter when selecting an unrelated Model A.')
        adapter_note.setWordWrap(True)
        form.addRow('', adapter_note)
        self.context = QSpinBox(); self.context.setRange(8192,131072); self.context.setSingleStep(4096); self.context.setValue(config.context_size)
        self.context.setToolTip('Space for project evidence, conversation and reply. Larger values use more memory and must be supported by the model.')
        self.tokens = QSpinBox(); self.tokens.setRange(256,16384); self.tokens.setSingleStep(256); self.tokens.setValue(config.max_tokens)
        self.layers = QSpinBox(); self.layers.setRange(0,999); self.layers.setValue(config.gpu_layers)
        self.layers.setSpecialValueText('0 — CPU only')
        self.layers.setToolTip('Requires a llama.cpp build compatible with your GPU. Start at 0; increase gradually. 999 requests all layers on the GPU.')
        self.threads = QSpinBox(); self.threads.setRange(1,128); self.threads.setValue(config.threads)
        self.temperature = QDoubleSpinBox(); self.temperature.setRange(0,2); self.temperature.setSingleStep(0.1); self.temperature.setValue(config.temperature)
        for label, field in [('Context size (tokens)',self.context),('Maximum reply (tokens)',self.tokens),('GPU layers',self.layers),('CPU threads',self.threads),('Temperature',self.temperature)]:
            form.addRow(label,field)
        layout.addLayout(form)
        memory_note = QLabel('Two-model conversations keep both models loaded and use these settings for each model. Allow enough RAM/VRAM for both weights and context buffers. A current llama.cpp build with multi-model support is required.')
        memory_note.setWordWrap(True)
        layout.addWidget(memory_note)
        note = QLabel(engine_setup_help() + ' Then select an instruction/chat GGUF. Model weights are separate downloads and can be several GB. Tool use and Thinking depend on the model and its chat template. If loading fails, check the engine log under Help.')
        note.setWordWrap(True)
        layout.addWidget(note)
        docs = QPushButton('Open official model / engine guide')
        docs.clicked.connect(lambda: QDesktopServices.openUrl(QUrl('https://github.com/ggml-org/llama.cpp#quick-start')))
        layout.addWidget(docs)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.validate)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def browse(self, field, mode):
        file_filter = ('GGUF models (*.gguf)' if mode == 'model' else
                       'GGUF adapters (*.gguf)' if mode == 'adapter' else
                       'Windows executable (*.exe)' if is_windows() else 'All files (*)')
        title = {'model': 'Choose local model', 'adapter': 'Choose local LoRA adapter'}.get(mode, 'Choose llama-server')
        file, _ = QFileDialog.getOpenFileName(self, title, field.text() or str(Path.home()), file_filter)
        if file:
            field.setText(file)

    def validate(self):
        executable = Path(self.executable.text()).expanduser()
        model = Path(self.model.text()).expanduser()
        access = os.R_OK if is_windows() else os.R_OK | os.X_OK
        if not executable.is_file() or not os.access(executable, access) or (is_windows() and executable.suffix.lower() != '.exe'):
            QMessageBox.warning(self,'Engine not found','Select an executable llama-server. ' + engine_setup_help())
            return
        models = [('Model A', model)]
        if self.secondary_model.text().strip():
            models.append(('Model B', Path(self.secondary_model.text()).expanduser()))
        for label, path in models:
            if not path.is_file():
                QMessageBox.warning(self,'Model not found',f'{label}: choose an existing local GGUF file.')
                return
            try:
                with path.open('rb') as file:
                    valid = file.read(4) == b'GGUF'
            except OSError as error:
                QMessageBox.warning(self,'Cannot read model',str(error)); return
            if not valid:
                QMessageBox.warning(self,'Not a GGUF model',f'{label}: the selected file does not have a GGUF header. Choose a complete GGUF model download.'); return
        if len(models) == 2:
            try:
                same = models[0][1].samefile(models[1][1])
            except OSError as error:
                QMessageBox.warning(self,'Cannot compare models',str(error)); return
            if same:
                QMessageBox.warning(self,'Choose different models','Model A and Model B must be different local GGUF files.'); return
        try:
            validated_lora_path(self.lora.text())
        except EngineError as error:
            QMessageBox.warning(self, 'Invalid LoRA adapter', str(error)); return
        if self.tokens.value() >= self.context.value() // 2:
            QMessageBox.warning(self,'Reply budget too large','Keep the reply budget below half the context size, leaving room for your conversation and files.'); return
        self.accept()

    def config(self):
        adapter = str(Path(self.lora.text()).expanduser().resolve()) if self.lora.text().strip() else ''
        secondary = str(Path(self.secondary_model.text()).expanduser().resolve()) if self.secondary_model.text().strip() else ''
        return EngineConfig(executable=str(Path(self.executable.text()).expanduser().resolve()),model_path=str(Path(self.model.text()).expanduser().resolve()),secondary_model_path=secondary,context_size=self.context.value(),gpu_layers=self.layers.value(),threads=self.threads.value(),max_tokens=self.tokens.value(),temperature=self.temperature.value(),lora_path=adapter)


class LinksDialog(QDialog):
    def __init__(self, store, project_id, parent=None):
        super().__init__(parent)
        self.store, self.project_id = store, project_id
        self.setWindowTitle('Linked files and folders — LetraCode')
        self.resize(700,430)
        layout = QVBoxLayout(self)
        note = QLabel('Link ordinary files where they already live. Every chat in this project can read these links. Unlinking never deletes the original. Hidden files and paths outside these links require approval.')
        note.setWordWrap(True); layout.addWidget(note)
        self.files = QListWidget(); layout.addWidget(self.files)
        row = QHBoxLayout()
        for text, callback in [('Link files…',self.add_files),('Link folder…',self.add_folder),('Unlink selected',self.remove)]:
            button = QPushButton(text); button.clicked.connect(callback); row.addWidget(button)
        layout.addLayout(row)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject); layout.addWidget(buttons)
        self.refresh()

    def refresh(self):
        self.files.clear()
        for path in self.store.links(self.project_id):
            self.files.addItem(path)
            if not Path(path).exists():
                self.files.item(self.files.count()-1).setToolTip('Missing or unavailable. Relink if this file has moved.')

    def add_files(self):
        files,_ = QFileDialog.getOpenFileNames(self,'Link project files',str(Path.home()),'All files (*)')
        for file in files:
            self.store.link(self.project_id,file)
        self.refresh()

    def add_folder(self):
        folder = QFileDialog.getExistingDirectory(self,'Link project folder',str(Path.home()))
        if folder:
            self.store.link(self.project_id,folder)
            self.refresh()

    def remove(self):
        item = self.files.currentItem()
        if item:
            self.store.unlink(self.project_id,item.text()); self.refresh()
