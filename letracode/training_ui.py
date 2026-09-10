"""A deliberate, local workflow for developing an assistant's model."""
from __future__ import annotations

import dataclasses
import json
from pathlib import Path
import uuid

from PySide6.QtCore import Qt, Signal, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog,
    QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea,
    QSpinBox, QSplitter, QTabWidget, QVBoxLayout, QWidget)

from .training import TrainingConfig, TrainingRepository
from .training_worker import ActivationWorker, TrainingWorker


class FineTuningPanel(QWidget):
    busy_changed = Signal(bool)

    def __init__(self, store, main_window):
        super().__init__(main_window)
        self.store = store
        self.main_window = main_window
        self.repository = TrainingRepository(store)
        self.repository.mark_interrupted()
        self.job = None
        self.chat_busy = False
        self.example_id = None
        self.example_source = ''
        self._editor_key = 'draft:' + uuid.uuid4().hex
        self._drafts = store.setting('training_editor_drafts', {})
        if not isinstance(self._drafts, dict):
            self._drafts = {}
        self.run_id = None
        self._loading = False
        layout = QVBoxLayout(self)
        heading = QLabel('Develop your assistant')
        font = heading.font(); font.setPointSizeF(font.pointSizeF() + 4); font.setBold(True)
        heading.setFont(font); layout.addWidget(heading)
        intro = QLabel('Review examples, train locally, then compare versions before choosing one. Your chat model changes only when you adopt a version.')
        intro.setWordWrap(True); layout.addWidget(intro)
        self.tabs = QTabWidget(); layout.addWidget(self.tabs, 1)
        self._examples_tab()
        self._training_tab()
        self._versions_tab()
        self.progress = QLabel('Ready. No training is running.')
        self.progress.setTextFormat(Qt.TextFormat.PlainText)
        self.progress.setWordWrap(True)
        bottom = QHBoxLayout(); bottom.addWidget(self.progress, 1)
        self.stop_button = QPushButton('Stop'); self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.stop)
        bottom.addWidget(self.stop_button); layout.addLayout(bottom)
        self.refresh_runs()
        draft = store.setting('training_editor', {})
        if isinstance(draft, dict) and draft:
            self.load_draft(draft, draft.get('key') or draft.get('id') or self._editor_key)
        self.refresh_examples()
        self.prompt.textChanged.connect(self.editor_changed)
        self.response.textChanged.connect(self.editor_changed)
        self.split.currentIndexChanged.connect(self.editor_changed)
        # Also invalidate approval for a recovered edit from an older app version.
        self.editor_changed()

    def _button(self, text, callback, layout):
        button = QPushButton(text)
        button.clicked.connect(callback); layout.addWidget(button)
        return button

    def _examples_tab(self):
        page = QWidget(); outer = QVBoxLayout(page)
        hint = QLabel('Examples teach a desired answer to a prompt. Keep evaluation prompts separate so they test behavior the model did not train on. Imported and chat examples start as drafts.')
        hint.setWordWrap(True); outer.addWidget(hint)
        body = QSplitter(); outer.addWidget(body, 1)
        self.examples_list = QListWidget(); self.examples_list.currentItemChanged.connect(self.select_example)
        body.addWidget(self.examples_list)
        editor = QWidget(); form = QVBoxLayout(editor)
        form.addWidget(QLabel('Prompt'))
        self.prompt = QPlainTextEdit(); self.prompt.setPlaceholderText('What should the user ask?')
        form.addWidget(self.prompt, 1)
        form.addWidget(QLabel('Desired response'))
        self.response = QPlainTextEdit(); self.response.setPlaceholderText('Write or correct the answer you want the assistant to learn.')
        form.addWidget(self.response, 1)
        self.split = QComboBox(); self.split.addItem('Training example', 'train'); self.split.addItem('Held-out evaluation', 'eval')
        form.addWidget(self.split)
        row = QHBoxLayout()
        self._button('New example', self.new_example, row)
        self._button('Save draft', self.save_example, row)
        self._button('Approve example', self.approve_example, row)
        self._button('Delete', self.delete_example, row)
        form.addLayout(row); body.addWidget(editor); body.setSizes([280, 650])
        actions = QHBoxLayout()
        self._button('Import JSONL…', self.import_examples, actions)
        self._button('Export examples…', self.export_examples, actions)
        actions.addStretch(); self.counts = QLabel(); actions.addWidget(self.counts)
        outer.addLayout(actions); self.tabs.addTab(page, 'Examples')

    def _training_tab(self):
        page = QWidget(); form = QVBoxLayout(page)
        help_text = QLabel('Train reviewed text responses with local Llama or Gemma 4 E2B/E4B original safetensors weights. 4-bit QLoRA needs an NVIDIA CUDA GPU and a separate training Python with PyTorch, Transformers, PEFT, Accelerate and bitsandbytes. A GGUF chat file alone cannot be trained. The model architecture is detected from the selected training folder.')
        help_text.setWordWrap(True); form.addWidget(help_text)
        paths = QFormLayout(); self.fields = {}
        saved = self.store.setting('training_config', {})
        if not isinstance(saved, dict):
            saved = {}
        defaults = dataclasses.asdict(TrainingConfig(python_executable='', base_model=''))
        if not saved:
            defaults.update(training_method='qlora', device='cuda',
                            gradient_accumulation_steps=4, gradient_checkpointing=True)
        training_python = Path.home() / '.local/share/letracode-training-qlora/bin/python'
        if not saved.get('python_executable') and training_python.is_file():
            defaults['python_executable'] = str(training_python)
        self.profile = QComboBox()
        self.profile.addItem('Llama / current custom settings', 'custom')
        self.profile.addItem('Gemma 4 E2B · first target for 8 GB', 'gemma4-e2b')
        self.profile.addItem('Gemma 4 E4B · experimental', 'gemma4-e4b')
        self.profile.setToolTip('Apply starting memory settings without changing model paths, epochs or learning rate. The selected model folder determines the architecture.')
        paths.addRow('Starting settings', self.profile)
        method = QComboBox()
        method.addItem('4-bit QLoRA (NVIDIA GPU)', 'qlora')
        method.addItem('LoRA (full precision)', 'lora')
        method.setCurrentIndex(method.findData(saved.get('training_method', defaults['training_method'])))
        self.fields['training_method'] = method
        paths.addRow('Training method', method)
        for name, label, kind in (
            ('python_executable', 'Training Python', 'python'),
            ('base_model', 'Training model folder', 'directory'),
            ('base_gguf', 'Matching chat GGUF (for adoption)', 'gguf'),
            ('llama_cpp_dir', 'llama.cpp source folder (for conversion)', 'directory')):
            value = saved.get(name, defaults.get(name, ''))
            if name == 'python_executable' and not value:
                value = defaults[name]
            field = QLineEdit(str(value)); self.fields[name] = field
            row = QWidget(); horizontal = QHBoxLayout(row); horizontal.setContentsMargins(0, 0, 0, 0)
            horizontal.addWidget(field)
            button = QPushButton('Browse…'); horizontal.addWidget(button)
            button.clicked.connect(lambda _=False, f=field, k=kind: self.browse(f, k))
            paths.addRow(label, row)
        form.addLayout(paths)
        advanced = QGroupBox('Advanced training settings'); settings = QFormLayout(advanced)
        for name, label, low, high in (('epochs', 'Passes through training data', 1, 100),
                                     ('rank', 'LoRA rank', 1, 256),
                                     ('max_length', 'Maximum example length (tokens)', 32, 8192),
                                     ('batch_size', 'Examples in memory at once', 1, 64),
                                     ('gradient_accumulation_steps', 'Batches per learning update', 1, 128),
                                     ('seed', 'Reproducibility seed', 0, 2147483647)):
            field = QSpinBox(); field.setRange(low, high); field.setValue(saved.get(name, defaults[name]))
            self.fields[name] = field; settings.addRow(label, field)
        rate = QDoubleSpinBox(); rate.setDecimals(6); rate.setRange(.000001, .1); rate.setSingleStep(.0001)
        rate.setValue(saved.get('learning_rate', defaults['learning_rate'])); self.fields['learning_rate'] = rate
        settings.addRow('Learning rate', rate)
        device = QComboBox(); device.addItems(['cpu', 'cuda']); device.setCurrentText(saved.get('device', defaults['device']))
        if method.currentData() == 'qlora':
            device.setCurrentText('cuda')
            device.setEnabled(False)
        self.fields['device'] = device; settings.addRow('Compute device', device)
        checkpointing = QCheckBox('Save memory by recomputing activations (slower)')
        checkpointing.setChecked(saved.get('gradient_checkpointing', defaults['gradient_checkpointing']))
        self.fields['gradient_checkpointing'] = checkpointing
        settings.addRow('Gradient checkpointing', checkpointing)
        self.effective_batch = QLabel(); self.effective_batch.setWordWrap(True)
        settings.addRow(self.effective_batch)
        self.update_effective_batch()
        method.currentIndexChanged.connect(self.training_method_changed)
        for field in self.fields.values():
            signal = (field.textChanged if isinstance(field, QLineEdit) else
                      field.currentIndexChanged if isinstance(field, QComboBox) else
                      field.toggled if isinstance(field, QCheckBox) else field.valueChanged)
            signal.connect(self.persist_configuration)
        self.fields['batch_size'].valueChanged.connect(self.update_effective_batch)
        self.fields['gradient_accumulation_steps'].valueChanged.connect(self.update_effective_batch)
        self.profile.currentIndexChanged.connect(self.apply_profile)
        form.addWidget(advanced)
        gemma_hint = QLabel('Gemma 4: E2B has more room on an 8 GB GPU. E4B uses frozen embeddings in system RAM to fit short examples; its usual QLoRA baseline is 10 GB VRAM. On the tested RTX 2060 SUPER, a 250-token update fit but 509 tokens ran out of memory. Start at 256 tokens or less, batch 1 and rank 4. Only text responses are trained. Adoption requires a matching GGUF conversion manifest. Compare held-out answers before adoption.')
        gemma_hint.setWordWrap(True); form.addWidget(gemma_hint)
        warning = QLabel('Training reads only approved examples and local weights. Larger models and longer examples still need more memory. QLoRA does not guarantee better answers: compare held-out results before adoption. Training runs do not download models or packages. Conversion also needs the selected llama.cpp checkout’s Python dependencies in the training environment.')
        warning.setWordWrap(True); form.addWidget(warning)
        self.review_check = QCheckBox('I reviewed the approved examples and selected the matching model files.')
        form.addWidget(self.review_check)
        self.start_button = QPushButton('Start local fine-tuning'); self.start_button.clicked.connect(self.start_training)
        form.addWidget(self.start_button); form.addStretch()
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setWidget(page)
        self.training_form = page; self.tabs.addTab(scroll, 'Train')

    def _versions_tab(self):
        page = QWidget(); layout = QVBoxLayout(page)
        split = QSplitter()
        self.runs_list = QListWidget(); self.runs_list.currentItemChanged.connect(self.select_run)
        self.results = QPlainTextEdit(); self.results.setReadOnly(True)
        split.addWidget(self.runs_list); split.addWidget(self.results); split.setSizes([270, 700])
        layout.addWidget(split, 1)
        row = QHBoxLayout()
        self.adopt_button = self._button('Adopt selected version', self.adopt, row)
        self.rollback_button = self._button('Roll back to previous model', self.rollback, row)
        self._button('Open run folder', self.open_run, row)
        layout.addLayout(row)
        note = QLabel('Held-out loss measures fit to these expected answers; lower is better on this set. Review sample answers and test real tasks before deciding the assistant improved. Previous versions and base weights are kept.')
        note.setWordWrap(True); layout.addWidget(note)
        self.tabs.addTab(page, 'Versions & evaluation')

    def browse(self, field, kind):
        if kind == 'directory':
            value = QFileDialog.getExistingDirectory(self, 'Choose local folder', field.text())
        else:
            value, _ = QFileDialog.getOpenFileName(self, 'Choose local file', field.text(),
                                                   'GGUF (*.gguf)' if kind == 'gguf' else 'All files (*)')
        if value:
            field.setText(value)

    def error(self, error):
        self.progress.setText(str(error))
        QMessageBox.warning(self, 'Fine-Tuning', str(error))

    def persist_draft(self):
        draft = {'id': self.example_id, 'key': self._editor_key,
            'prompt': self.prompt.toPlainText(), 'response': self.response.toPlainText(),
            'split': self.split.currentData(), 'source': self.example_source}
        if self.example_id or draft['prompt'] or draft['response']:
            self._drafts[self._editor_key] = draft
        else:
            self._drafts.pop(self._editor_key, None)
        self.store.set_setting('training_editor_drafts', self._drafts)
        self.store.set_setting('training_editor', draft)

    def load_draft(self, draft, key):
        self._loading = True
        try:
            self._editor_key = key
            self.example_id = draft.get('id')
            self.example_source = draft.get('source', '')
            self.prompt.setPlainText(draft.get('prompt', ''))
            self.response.setPlainText(draft.get('response', ''))
            self.split.setCurrentIndex(1 if draft.get('split') == 'eval' else 0)
        finally:
            self._loading = False

    def editor_changed(self):
        if self._loading:
            return
        if self.example_id:
            row = next((r for r in self.repository.examples() if r['id'] == self.example_id), None)
            if row and (self.prompt.toPlainText().strip(), self.response.toPlainText().strip(), self.split.currentData()) != (row['prompt'], row['response'], row['split']):
                if row['approved']:
                    self.repository.save_example(row['prompt'], row['response'], split=row['split'],
                        source=row['source'], example_id=row['id'])
                self.progress.setText('Unsaved draft retained. Save and approve these edits before training.')
        self.persist_draft()
        self.refresh_examples()

    def refresh_examples(self):
        self.examples_list.blockSignals(True); self.examples_list.clear()
        rows = self.repository.examples()
        for row in rows:
            label = ('Approved' if row['approved'] else 'Draft') + ' · ' + ('Eval' if row['split'] == 'eval' else 'Train')
            item = QListWidgetItem(label + '\n' + row['prompt'][:100].replace('\n', ' '))
            item.setData(Qt.ItemDataRole.UserRole, row['id']); self.examples_list.addItem(item)
            if row['id'] == self.example_id:
                self.examples_list.setCurrentItem(item)
        for key, draft in self._drafts.items():
            if draft.get('id') is not None:
                continue
            item = QListWidgetItem('Unsaved draft\n' + draft.get('prompt', '')[:100].replace('\n', ' '))
            item.setData(Qt.ItemDataRole.UserRole, key); self.examples_list.addItem(item)
            if key == self._editor_key:
                self.examples_list.setCurrentItem(item)
        self.examples_list.blockSignals(False)
        training = sum(row['approved'] and row['split'] == 'train' for row in rows)
        evaluation = sum(row['approved'] and row['split'] == 'eval' for row in rows)
        self.counts.setText(f'Approved: {training} training · {evaluation} evaluation')

    def select_example(self, current, previous=None):
        if current is None:
            return
        # Retain editor text as a draft before switching, including unapproved edits.
        self.persist_draft()
        key = current.data(Qt.ItemDataRole.UserRole)
        row = self._drafts.get(key) or next((r for r in self.repository.examples() if r['id'] == key), None)
        if row:
            self.load_draft(row, key)
            self.persist_draft()

    def new_example(self):
        self.persist_draft()
        self.load_draft({}, 'draft:' + uuid.uuid4().hex)
        self.persist_draft(); self.refresh_examples()

    def save_example(self, checked=False):
        try:
            row = self.repository.save_example(self.prompt.toPlainText(), self.response.toPlainText(),
                split=self.split.currentData(), source=self.example_source, example_id=self.example_id)
            self._drafts.pop(self._editor_key, None)
            self.example_id = row['id']; self._editor_key = row['id']
            self.persist_draft(); self.refresh_examples()
            self.progress.setText('Example saved. Review and approve it before training.')
            return row
        except (ValueError, OSError) as error:
            self.error(error)
            return None

    def approve_example(self):
        row = self.save_example()
        if row:
            try:
                self.repository.save_example(row['prompt'], row['response'], split=row['split'],
                    approved=True, source=row['source'], example_id=row['id'])
                self.refresh_examples(); self.progress.setText('Example approved for its selected use.')
            except ValueError as error:
                self.error(error)

    def delete_example(self):
        if self.example_id:
            self.repository.delete_example(self.example_id)
        self._drafts.pop(self._editor_key, None)
        self.load_draft({}, 'draft:' + uuid.uuid4().hex)
        self.persist_draft(); self.refresh_examples()

    def import_examples(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Import draft examples', '', 'JSON Lines (*.jsonl)')
        if path:
            try:
                self.repository.import_jsonl(Path(path)); self.refresh_examples()
                self.progress.setText('Imported as drafts. Review each example before approval.')
            except (OSError, ValueError) as error:
                self.error(error)

    def export_examples(self):
        path, _ = QFileDialog.getSaveFileName(self, 'Export local examples', 'training-examples.jsonl', 'JSON Lines (*.jsonl)')
        if path:
            try:
                self.repository.export_jsonl(Path(path))
                self.progress.setText('Examples exported. This file contains the full example text.')
            except (OSError, ValueError) as error:
                self.error(error)

    def configuration(self):
        values = {}
        for name, field in self.fields.items():
            if isinstance(field, QLineEdit):
                values[name] = field.text().strip()
            elif isinstance(field, QCheckBox):
                values[name] = field.isChecked()
            elif isinstance(field, QComboBox):
                values[name] = field.currentData() if name == 'training_method' else field.currentText()
            else:
                values[name] = field.value()
        return TrainingConfig(**values)

    def persist_configuration(self, *_):
        # Keep incomplete setup too; paths are validated only when a run starts.
        self.store.set_setting('training_config', dataclasses.asdict(self.configuration()))

    def training_method_changed(self, *_):
        qlora = self.fields['training_method'].currentData() == 'qlora'
        device = self.fields['device']
        if qlora:
            device.setCurrentText('cuda')
            self.fields['gradient_checkpointing'].setChecked(True)
        device.setEnabled(not qlora)

    def apply_profile(self, *_):
        if self.profile.currentData() not in ('gemma4-e2b', 'gemma4-e4b'):
            return
        self.fields['training_method'].setCurrentIndex(self.fields['training_method'].findData('qlora'))
        self.fields['device'].setCurrentText('cuda')
        for key, value in (('batch_size', 1), ('rank', 4), ('max_length', 256),
                           ('gradient_accumulation_steps', 4)):
            self.fields[key].setValue(value)
        self.fields['gradient_checkpointing'].setChecked(True)
        self.persist_configuration()

    def update_effective_batch(self, *_):
        size = self.fields['batch_size'].value() * self.fields['gradient_accumulation_steps'].value()
        self.effective_batch.setText(f'Effective batch: up to {size} examples per learning update. '
                                    'Accumulation increases the batch without keeping every example in memory; '
                                    'the last update can contain fewer examples.')

    def start_training(self):
        if self.job is not None or self.chat_busy:
            return
        if not self.review_check.isChecked():
            self.error('Review the approved examples and matching model files, then select the checkbox to start.')
            return
        try:
            config = self.configuration()
            run = self.repository.create_run(config)
            self.store.set_setting('training_config', dataclasses.asdict(config))
        except (OSError, ValueError) as error:
            self.error(error); return
        self.persist_draft()
        self.main_window.engine.stop()
        self.run_id = run['id']
        self.job = TrainingWorker(self.repository, run['id'], self)
        self.job.status.connect(self.progress.setText)
        self.job.finished.connect(self.job_finished)
        self.busy_changed.emit(True); self.set_chat_busy(True)
        self.refresh_runs(); self.tabs.setCurrentIndex(2)
        self.progress.setText('Preparing local training…'); self.job.start()

    def refresh_runs(self):
        self.runs_list.blockSignals(True); self.runs_list.clear()
        for row in self.repository.runs():
            report = row.get('report') or {}
            details = report.get('training_details') or {}
            name = f"Gemma 4 {details.get('variant', '')}" if details.get('model_family') == 'gemma4' else row['id'][:12]
            if report.get('verification_only'):
                name += ' · synthetic verification'
            item = QListWidgetItem(f"{row['created']} · {row['status']}\n{name}")
            item.setData(Qt.ItemDataRole.UserRole, row['id']); self.runs_list.addItem(item)
            if row['id'] == self.run_id:
                self.runs_list.setCurrentItem(item)
        self.runs_list.blockSignals(False)
        if self.run_id:
            self.show_run(self.repository.run(self.run_id))
        self.update_controls()

    def select_run(self, current, previous=None):
        if current:
            self.run_id = current.data(Qt.ItemDataRole.UserRole)
            self.show_run(self.repository.run(self.run_id)); self.update_controls()

    def show_run(self, run):
        report = run.get('report') or {}
        text = f"Version {run['id']}\nStatus: {run['status']}\nCreated: {run['created']}\n"
        if report.get('verification_only'):
            text += 'Synthetic workflow verification. This version has not been validated for answer quality.\n'
        if self.store.setting('training_active_version') == run['id']:
            text += 'Active in Chat\n'
        details = report.get('training_details') or {}
        config = run['config']
        if details.get('model_family') == 'gemma4':
            text += '\nModel family: Gemma 4\nTraining scope: text responses\n'
            if details.get('frozen_ple_cpu') is True:
                text += 'Frozen per-layer embeddings: CPU\n'
            if report.get('gemma_pair'):
                text += 'Matching base/GGUF conversion: verified for this run\n'
        method = details.get('training_method', config.get('training_method', 'lora'))
        method_label = '4-bit QLoRA (NVIDIA GPU)' if method == 'qlora' else 'LoRA (full precision)'
        text += '\nTraining method: ' + method_label + '\n'
        quantization = details.get('quantization', 'nf4-double' if method == 'qlora' else 'none')
        text += 'Base weight precision: ' + ('4-bit NF4 with double quantization' if quantization == 'nf4-double' else 'full precision') + '\n'
        dtype = details.get('compute_dtype', 'float32' if method == 'lora' else 'selected when training starts')
        text += 'Compute precision: ' + str(dtype) + '\n'
        batch = details.get('effective_batch_size', config.get('batch_size', 1) * config.get('gradient_accumulation_steps', 1))
        text += f'Effective batch: up to {batch} examples per learning update\n'
        checkpointing = details.get('gradient_checkpointing', config.get('gradient_checkpointing', False))
        text += 'Gradient checkpointing: ' + ('on' if checkpointing else 'off') + '\n'
        memory = report.get('memory') or {}
        if memory:
            for key, label in (('base_model_bytes', 'Base model footprint'),
                               ('peak_allocated_bytes', 'Peak GPU allocation'),
                               ('peak_reserved_bytes', 'Peak GPU reservation'),
                               ('frozen_ple_cpu_bytes', 'Frozen embeddings in system RAM'),
                               ('peak_process_rss_bytes', 'Peak training process RAM')):
                value = memory.get(key)
                if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
                    unit, scale = ('GiB', 1024 ** 3) if value >= 1024 ** 3 else ('MiB', 1024 ** 2)
                    text += f'{label}: {value / scale:.2f} {unit}\n'
        elif report:
            text += 'Memory measurements were not recorded for this version.\n'
        if report:
            if details.get('model_family') == 'gemma4':
                text += 'Generated comparisons: greedy, thinking off, up to 64 new tokens.\n'
                if details.get('max_train_example_tokens'):
                    text += f"Longest training example: {details['max_train_example_tokens']} tokens\n"
            text += f"\nHeld-out response loss\nBase: {report['base_loss']:.6f}\nCandidate: {report['candidate_loss']:.6f}\n"
            if method == 'qlora':
                text += 'Both evaluations use the same 4-bit base; the candidate adds the trained adapter.\n'
            text += '\nThis comparison does not establish overall task quality.\n'
            if report.get('conversion_error'):
                text += '\nAdapter conversion: ' + report['conversion_error'] + '\n'
            for example in report.get('eval_examples', []):
                text += '\nPrompt: ' + example.get('prompt', '') + '\nExpected: ' + example.get('response', example.get('expected', ''))
                text += '\nBase: ' + example.get('base_output', '') + '\nCandidate: ' + example.get('candidate_output', '') + '\n'
        if run.get('error'):
            text += '\n' + run['error'] + '\n'
        text += '\nConfiguration\n' + json.dumps(run['config'], ensure_ascii=False, indent=2)
        if report:
            text += '\n\nProvenance and report\n' + json.dumps(report, ensure_ascii=False, indent=2)
        self.results.setPlainText(text)

    def open_run(self):
        if self.run_id:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.repository.run_directory(self.run_id))))

    def adopt(self):
        if self.job is not None or self.chat_busy or not self.run_id:
            return
        if self.run_id == self.store.setting('training_active_version'):
            self.progress.setText('This version is already active. The previous model is still available for rollback.')
            return
        run = self.repository.run(self.run_id)
        report = run.get('report') or {}
        if run['status'] != 'succeeded' or not report.get('adapter_gguf_sha256'):
            self.error('Select a version with completed evaluation and a converted GGUF adapter.'); return
        answer = QMessageBox.question(self, 'Adopt this version?',
            'Load this adapter and its selected base GGUF as Model A for Chat? Model B stays as configured. The previous model configuration will be saved for rollback. Review the evaluation results before proceeding.',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if answer == QMessageBox.StandardButton.Yes:
            self.start_activation(run_id=self.run_id)

    def rollback(self):
        if self.job is not None or self.chat_busy:
            return
        previous = self.store.setting('training_previous_engine')
        if previous:
            self.start_activation(rollback=previous)

    def start_activation(self, run_id=None, rollback=None):
        if self.job is not None or self.chat_busy:
            return
        if rollback is None and run_id == self.store.setting('training_active_version'):
            return
        self.main_window.engine.stop()
        self.job = ActivationWorker(self.repository, self.main_window.engine_config, run_id, rollback, self,
                                    secondary_enabled=self.main_window.conversation_mode.currentIndex() == 1)
        self.job.status.connect(self.progress.setText)
        self.job.failed.connect(self.progress.setText)
        self.job.ready.connect(self.activation_ready)
        self.job.finished.connect(self.job_finished)
        self.busy_changed.emit(True); self.set_chat_busy(True)
        self.job.start()

    def activation_ready(self, engine):
        job = self.job
        if job is None or job.cancelled.is_set() or self.main_window.closing_when_stopped:
            engine.stop(); return
        selected_config = job.selected_config
        previous = dataclasses.asdict(self.main_window.engine_config)
        previous_version = self.store.setting('training_active_version')
        new_version = job.run_id if job.rollback is None else self.store.setting('training_previous_version')
        try:
            with self.store.connection() as db:
                for key, value in [('engine', dataclasses.asdict(selected_config)),
                    ('training_previous_engine', previous), ('training_active_version', new_version),
                    ('training_previous_version', previous_version)]:
                    db.execute('INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                               (key, json.dumps(value)))
        except Exception as error:
            engine.stop(); self.progress.setText(str(error)); return
        self.main_window.engine = engine
        self.main_window.engine_config = selected_config
        self.main_window.render_chat()
        self.progress.setText('Selected model loaded and saved. Chat will use this configuration after restart.')

    def job_finished(self):
        completed = self.job
        self.job = None
        if completed is not None:
            completed.deleteLater()
        self.busy_changed.emit(False); self.set_chat_busy(False)
        self.refresh_runs()
        if self.main_window.closing_when_stopped:
            self.main_window.close()

    def stop(self):
        if self.job is not None:
            self.progress.setText('Stopping the local process…')
            self.job.cancel()

    def set_chat_busy(self, busy):
        self.chat_busy = busy
        self.update_controls()

    def update_controls(self):
        busy = self.chat_busy or self.job is not None
        self.start_button.setEnabled(not busy)
        self.training_form.setEnabled(not busy)
        self.stop_button.setEnabled(self.job is not None)
        run = self.repository.run(self.run_id) if self.run_id else None
        can_adopt = run and run['id'] != self.store.setting('training_active_version') and run['status'] == 'succeeded' and (run.get('report') or {}).get('adapter_gguf_sha256') and run['config'].get('base_gguf')
        self.adopt_button.setEnabled(not busy and bool(can_adopt))
        self.rollback_button.setEnabled(not busy and bool(self.store.setting('training_previous_engine')))
