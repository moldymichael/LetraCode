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
from .training_experience import (ExperienceWorker, check_readiness, compare_version,
    can_retry_conversion, converted_artifact_state,
    discover_configuration, effective_report, examples_fingerprint, prepare_matching_model, retry_conversion,
    save_version_review, version_review, version_stage)


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
        self._train_after_check = False
        self._readiness_result = None
        heading = QLabel('Help Strand improve')
        font = heading.font(); font.setPointSizeF(font.pointSizeF() + 4); font.setBold(True)
        heading.setFont(font); layout.addWidget(heading)
        intro = QLabel('Show Strand a good answer. Build a candidate from your reviewed examples, compare it with Strand, then choose which version to use. Saved examples and training never change your current assistant by themselves.')
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
        hint = QLabel('A good example stands on its own: include what the question means and the answer you want. Keep a few different questions for comparison; Strand will not train on those. New examples stay drafts until you approve them.')
        hint.setWordWrap(True); outer.addWidget(hint)
        body = QSplitter(); outer.addWidget(body, 1)
        self.examples_list = QListWidget(); self.examples_list.currentItemChanged.connect(self.select_example)
        body.addWidget(self.examples_list)
        editor = QWidget(); form = QVBoxLayout(editor)
        form.addWidget(QLabel('Question, with any background needed'))
        self.prompt = QPlainTextEdit(); self.prompt.setPlaceholderText('For example: Explain a metaphor to a beginner using one everyday example.')
        self.prompt.setAccessibleName('Training question and background')
        form.addWidget(self.prompt, 1)
        form.addWidget(QLabel('Desired response'))
        self.response = QPlainTextEdit(); self.response.setPlaceholderText('Write or correct the answer Strand should learn. Include enough detail to show what makes it useful.')
        self.response.setAccessibleName('Desired answer for Strand')
        form.addWidget(self.response, 1)
        self.split = QComboBox(); self.split.addItem('Teach this answer', 'train'); self.split.addItem('Keep for comparison', 'eval')
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
        help_text = QLabel('Start with Check preparation. It checks whether your model and training tools are ready, and tells you how to fix anything missing. Strand keeps your current version until you compare answers and choose a candidate.')
        help_text.setWordWrap(True); form.addWidget(help_text)
        self.readiness = QLabel('Not checked yet. You can save and review examples at any time.')
        self.readiness.setTextFormat(Qt.TextFormat.PlainText)
        self.readiness.setWordWrap(True); form.addWidget(self.readiness)
        preparation = QHBoxLayout()
        self.check_button = self._button('Check preparation', self.check_preparation, preparation)
        self.prepare_button = self._button('Prepare matching model', self.prepare_model, preparation)
        form.addLayout(preparation)
        self.details_toggle = QCheckBox('Show preparation details and advanced settings')
        form.addWidget(self.details_toggle)
        self.advanced = QWidget(); advanced_layout = QVBoxLayout(self.advanced)
        advanced_layout.setContentsMargins(0, 0, 0, 0)
        self.details_toggle.toggled.connect(self.advanced.setVisible)
        self.advanced.hide()
        paths = QFormLayout(); self.fields = {}
        saved = discover_configuration(self.store, self.main_window.engine_config)
        if not isinstance(saved, dict):
            saved = {}
        defaults = dataclasses.asdict(TrainingConfig(python_executable='', base_model=''))
        if not self.store.setting('training_config'):
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
        advanced_layout.addLayout(paths)
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
        advanced_layout.addWidget(advanced)
        gemma_hint = QLabel('Supported training: original text-only Llama and Gemma 4 E2B/E4B weights. Gemma uses CUDA QLoRA with frozen per-layer embeddings in system RAM. Example length and available memory determine capacity; the preparation check counts every example without truncating it. Start conservatively. Historical hardware measurements are in Help → Training guide.')
        gemma_hint.setWordWrap(True); advanced_layout.addWidget(gemma_hint)
        form.addWidget(self.advanced)
        technical_note = QLabel('Conversion needs the selected llama.cpp checkout’s Python dependencies in the training environment. QLoRA is an optimization method; held-out loss is supporting evidence rather than a guarantee of better answers.')
        technical_note.setWordWrap(True); advanced_layout.addWidget(technical_note)
        self.preparation_diagnostics = QLabel()
        self.preparation_diagnostics.setTextFormat(Qt.TextFormat.PlainText)
        self.preparation_diagnostics.setWordWrap(True); advanced_layout.addWidget(self.preparation_diagnostics)
        warning = QLabel('Only your approved examples are used. Larger models and longer examples need more memory. Training may improve some answers and worsen others, so compare them before choosing a version. Nothing is downloaded during preparation or training.')
        warning.setWordWrap(True); form.addWidget(warning)
        self.review_check = QCheckBox('Use my approved teaching and comparison examples for this candidate')
        form.addWidget(self.review_check)
        self.start_button = QPushButton('Check preparation and train'); self.start_button.clicked.connect(self.start_training)
        self.start_button.setToolTip('Checks preparation first. Training starts only after those checks pass and uses your approved examples.')
        form.addWidget(self.start_button); form.addStretch()
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setWidget(page)
        self.training_form = page; self.tabs.addTab(scroll, 'Prepare and train')

    def _versions_tab(self):
        page = QWidget(); layout = QVBoxLayout(page)
        self.stage = QLabel('No candidates yet. Start in Examples, then use Prepare and train to build one.'); self.stage.setWordWrap(True)
        layout.addWidget(self.stage)
        split = QSplitter()
        self.runs_list = QListWidget(); self.runs_list.currentItemChanged.connect(self.select_run)
        self.results = QPlainTextEdit(); self.results.setReadOnly(True)
        self.results.setPlaceholderText('Candidates and their comparisons will appear here. Your current Strand stays in use while you prepare examples.')
        split.addWidget(self.runs_list); split.addWidget(self.results); split.setSizes([270, 700])
        layout.addWidget(split, 1)
        row = QHBoxLayout()
        self.compare_button = self._button('Compare with Strand', self.compare, row)
        self.convert_button = self._button('Retry conversion', self.retry_conversion, row)
        self.adopt_button = self._button('Use this version', self.adopt, row)
        self.rollback_button = self._button('Restore previous version', self.rollback, row)
        self.open_run_button = self._button('Open run folder', self.open_run, row)
        layout.addLayout(row)
        review = QFormLayout()
        self.version_name = QLineEdit(); self.version_name.setPlaceholderText('For example: Clearer explanations')
        self.judgment = QComboBox()
        for label, value in (('Not reviewed yet', 'unreviewed'), ('Candidate is better on these examples', 'better'),
                             ('About the same', 'same'), ('Candidate lost useful behavior', 'worse'), ('Mixed results', 'mixed')):
            self.judgment.addItem(label, value)
        self.review_notes = QLineEdit(); self.review_notes.setPlaceholderText('Accuracy, missing details, style, and any regressions')
        review.addRow('Version name', self.version_name); review.addRow('Your judgment', self.judgment)
        review.addRow('Review notes', self.review_notes)
        layout.addLayout(review)
        self.review_button = QPushButton('Save my review'); self.review_button.clicked.connect(self.save_review)
        layout.addWidget(self.review_button)
        self.report_toggle = QCheckBox('Show technical training report')
        self.report_toggle.toggled.connect(lambda _: self.show_run(self.repository.run(self.run_id)) if self.run_id else None)
        layout.addWidget(self.report_toggle)
        note = QLabel('Compare answers for correctness, useful detail and style. Keep worse or mixed results in your review too. Scores in the technical report are supporting evidence. Previous versions are kept so you can restore one.')
        note.setWordWrap(True); layout.addWidget(note)
        self.tabs.addTab(page, 'Compare and choose')

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
        QMessageBox.warning(self, 'Improve Strand', str(error))

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
            label = ('Approved' if row['approved'] else 'Draft') + ' · ' + ('Comparison' if row['split'] == 'eval' else 'Teaching')
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
        self.counts.setText(f'Approved: {training} teaching · {evaluation} comparison')

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

    def capture_example(self, prompt, response, source='', context=''):
        self.new_example()
        if context.strip():
            prompt = 'Background for this request:\n' + context.strip() + '\n\nRequest:\n' + prompt
        self.prompt.setPlainText(prompt)
        self.response.setPlainText(response)
        self.example_source = source
        self.persist_draft(); self.tabs.setCurrentIndex(0)
        self.progress.setText('Review this example as a standalone question and answer. Earlier context is editable above; source files and tool results are not copied automatically. Add any facts the answer needs, then approve it. Saving does not train Strand.')

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
        self._readiness_result = None
        if hasattr(self, 'readiness'):
            self.readiness.setText('Preparation changed. Check it before training; your current Strand is unchanged.')

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
            self.error('Review your approved examples, then select the checkbox to use them for this candidate.')
            return
        self.check_preparation(train_after=True)

    def check_preparation(self, checked=False, *, train_after=False):
        if self.job is not None or self.chat_busy:
            return
        config = self.configuration()
        self._train_after_check = train_after
        self.readiness.setText('Checking local files, reviewed examples, runtime, token lengths and conversion…')
        self.start_experience(lambda cancel, status: check_readiness(self.repository, config, cancel, status),
                              self.preparation_checked)

    def preparation_checked(self, result):
        self._readiness_result = result
        self.preparation_diagnostics.setText('\n'.join(check['name'] + ': ' + check['detail'] for check in result['checks']))
        summary = []
        for check in result['checks']:
            detail = check['detail']
            if not check['ready'] and check['name'] == 'Local training files':
                detail = 'Finish model setup in preparation details. Help → Training guide explains the required files.'
            elif not check['ready'] and check['name'] == 'Runtime, tokenizer and conversion tools':
                detail = 'Training tools or example sizes need attention. Open preparation details for the exact check result.'
            summary.append(('✓ ' if check['ready'] else '• ') + detail)
        self.readiness.setText(('Ready for local training' if result['ready'] else 'Preparation needs attention') + '\n' +
            '\n'.join(summary))
        if not result['ready']:
            self._train_after_check = False
            self.progress.setText('Finish the listed preparation step, then check again. Your current Strand is unchanged.')
        else:
            self.progress.setText('Preparation passed. Memory use is still confirmed during training; your current version stays saved.')

    def prepare_model(self):
        if self.job is not None or self.chat_busy:
            return
        config = self.configuration()
        self.start_experience(lambda cancel, status: prepare_matching_model(self.repository, config, cancel, status),
                              self.model_prepared)

    def model_prepared(self, result):
        self.fields['base_gguf'].setText(result['base_gguf'])
        self.readiness.setText('Matching local model prepared. Check preparation to validate your runtime and examples.')
        self.progress.setText('Matching Chat model and provenance saved. The active Strand model is unchanged.')

    def start_experience(self, operation, callback, *, unload=False):
        if self.job is not None or self.chat_busy:
            return
        if unload:
            self.main_window.engine.stop()
        self.job = ExperienceWorker(operation, self)
        self.job.status.connect(self.progress.setText)
        self.job.failed.connect(self.experience_failed)
        self.job.ready.connect(callback)
        self.job.finished.connect(self.job_finished)
        self.busy_changed.emit(True); self.set_chat_busy(True)
        self.job.start()

    def experience_failed(self, error):
        self._train_after_check = False
        self.progress.setText(error)
        if self._readiness_result is None:
            self.readiness.setText('Preparation needs attention\n' + error)

    def begin_training(self):
        try:
            config = self.configuration()
            if (not self._readiness_result or not self._readiness_result.get('ready')
                    or self._readiness_result.get('configuration') != dataclasses.asdict(config)
                    or self._readiness_result.get('examples_fingerprint') != examples_fingerprint(self.repository)):
                raise ValueError('Examples or preparation changed after the check. Check preparation again before training.')
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

    def compare(self):
        if self.job is not None or self.chat_busy or not self.run_id:
            return
        ident = self.run_id
        config = dataclasses.replace(self.main_window.engine_config)
        thinking = self.main_window.mode.currentText() == 'Thinking'
        self.start_experience(lambda cancel, status: compare_version(self.repository, ident, config, cancel, status, thinking),
                              lambda result: self.progress.setText('Comparison complete. Read every answer and save your judgment.' if result['status'] == 'complete' else 'Comparison is incomplete. Saved answers and errors are shown; you can try again.'), unload=True)

    def retry_conversion(self):
        if self.job is not None or self.chat_busy or not self.run_id:
            return
        ident = self.run_id
        config = self.configuration()
        self.start_experience(lambda cancel, status: retry_conversion(self.repository, ident, config.llama_cpp_dir,
                              config.python_executable, cancel, status),
                              lambda result: self.progress.setText('Conversion completed. Compare this candidate with Strand next.'))

    def save_review(self):
        if not self.run_id or self.job is not None:
            return
        try:
            save_version_review(self.repository, self.run_id, name=self.version_name.text().strip(),
                                judgment=self.judgment.currentData(), notes=self.review_notes.text())
            self.refresh_runs()
            self.progress.setText('Your judgment is saved separately from the measured training results.')
        except ValueError as error:
            self.error(error)

    def refresh_runs(self):
        self.runs_list.blockSignals(True); self.runs_list.clear()
        runs = self.repository.runs()
        for row in runs:
            report = row.get('report') or {}
            details = report.get('training_details') or {}
            name = f"Gemma 4 {details.get('variant', '')}" if details.get('model_family') == 'gemma4' else row['id'][:12]
            name = version_review(self.repository, row['id']).get('name') or name
            if report.get('verification_only'):
                name += ' · synthetic verification'
            item = QListWidgetItem(f"{name}\n{version_stage(self.repository, row['id'])}")
            item.setData(Qt.ItemDataRole.UserRole, row['id']); self.runs_list.addItem(item)
            if row['id'] == self.run_id:
                self.runs_list.setCurrentItem(item)
        self.runs_list.blockSignals(False)
        if self.run_id:
            self.show_run(self.repository.run(self.run_id))
        else:
            self.stage.setText('Choose a candidate to see its next step.' if runs else
                'No candidates yet. Start in Examples, then use Prepare and train to build one.')
        self.update_controls()

    def select_run(self, current, previous=None):
        if current:
            self.run_id = current.data(Qt.ItemDataRole.UserRole)
            self.show_run(self.repository.run(self.run_id)); self.update_controls()

    def show_run(self, run):
        if run is None:
            return
        stored = self.repository.run(run['id'])
        report = effective_report(self.repository, run['id']) if stored else run.get('report') or {}
        review = version_review(self.repository, run['id'])
        self.version_name.setText(review.get('name', ''))
        self.judgment.setCurrentIndex(max(0, self.judgment.findData(review.get('judgment', 'unreviewed'))))
        self.review_notes.setText(review.get('notes', ''))
        self.stage.setText(version_stage(self.repository, run['id']) if stored else 'Historical training record')
        comparison = review.get('comparison') or {}
        if stored and not self.report_toggle.isChecked():
            text = (review.get('name') or 'Candidate ' + run['id'][:8]) + '\n' + version_stage(self.repository, run['id'])
            text += '\n\nTeaching, conversion and comparison are separate steps. A lower training loss does not prove better answers.'
            text += '\nYour judgment: ' + review.get('judgment', 'unreviewed')
            if comparison:
                text += '\n\nChat comparison: ' + comparison.get('status', 'unknown')
                text += '\n' + comparison.get('runtime', '')
                text += '\nThis tests standalone answers. Shared notes, project retrieval, file edits and action tools are not exercised.'
                text += '\nEach reply uses the Chat output budget (' + str(comparison.get('current_config', {}).get('max_tokens', '?')) + ' tokens). Errors and cut-off answers remain incomplete.'
                if comparison.get('base_changed'):
                    text += '\nThe candidate uses a different base model. This comparison includes that model change as well as training.'
                if comparison.get('error'):
                    text += '\n' + comparison['error']
                for index, row in enumerate(comparison.get('examples', []), 1):
                    text += f'\n\n── Comparison {index} ──\nQuestion: ' + row['prompt'] + '\nDesired answer: ' + row['response']
                    for key, label in (('current', 'Strand now'), ('candidate', 'Candidate')):
                        text += '\n\n' + label + ':\n' + row.get(key + '_output', 'No answer saved')
                        if row.get(key + '_error'):
                            text += '\nIncomplete: ' + row[key + '_error']
            elif converted_artifact_state(self.repository, run['id']) == 'present':
                text += '\n\nNext: Compare with Strand. Every saved comparison question will be answered by both versions independently in the Chat runtime.'
            elif run['status'] == 'succeeded':
                text += '\n\nThe trained adapter is preserved. Retry conversion after resolving preparation details; optimization will not repeat.'
                text += '\n' + report.get('conversion_error', '')
            if run.get('error'):
                text += '\n\n' + run['error']
            if review.get('notes'):
                text += '\n\nYour notes: ' + review['notes']
            self.results.setPlainText(text)
            return
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
        report = effective_report(self.repository, self.run_id)
        if run['status'] != 'succeeded' or not report.get('adapter_gguf_sha256'):
            self.error('Select a version with completed evaluation and a converted GGUF adapter.'); return
        review = version_review(self.repository, self.run_id)
        if (review.get('comparison', {}).get('status') != 'complete'
                or review.get('judgment', 'unreviewed') == 'unreviewed'):
            self.error('Compare this candidate with Strand, then save your judgment before choosing it.')
            return
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
                                    secondary_enabled=self.main_window.conversation_mode.currentIndex() == 1,
                                    require_comparison=rollback is None,
                                    thinking=self.main_window.mode.currentText() == 'Thinking')
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
        if hasattr(self.main_window, 'settings_panel'):
            self.main_window.settings_panel.refresh()
        self.progress.setText('Selected model loaded and saved. Chat will use this configuration after restart.')

    def job_finished(self):
        completed = self.job
        train_after = self._train_after_check and self._readiness_result and self._readiness_result.get('ready')
        self._train_after_check = False
        self.job = None
        if completed is not None:
            completed.deleteLater()
        self.busy_changed.emit(False); self.set_chat_busy(False)
        self.refresh_runs()
        if self.main_window.closing_when_stopped:
            self.main_window.close()
        elif train_after and completed is not None and not completed.cancelled.is_set():
            self.begin_training()

    def stop(self):
        self._train_after_check = False
        if self.job is not None:
            self.progress.setText('Stopping the local process…')
            self.job.cancel()

    def set_chat_busy(self, busy):
        self.chat_busy = busy
        self.update_controls()

    def update_controls(self):
        busy = self.chat_busy or self.job is not None
        self.start_button.setEnabled(not busy)
        self.start_button.setText('Train a candidate' if self._readiness_result and self._readiness_result.get('ready') else 'Check preparation and train')
        self.training_form.setEnabled(not busy)
        self.stop_button.setEnabled(self.job is not None)
        run = self.repository.run(self.run_id) if self.run_id else None
        review = version_review(self.repository, run['id']) if run else {}
        report = effective_report(self.repository, run['id']) if run else {}
        converted = run and run['status'] == 'succeeded' and converted_artifact_state(self.repository, run['id']) == 'present' and run['config'].get('base_gguf')
        can_adopt = converted and run['id'] != self.store.setting('training_active_version') and review.get('comparison', {}).get('status') == 'complete' and review.get('judgment', 'unreviewed') != 'unreviewed'
        self.adopt_button.setEnabled(not busy and bool(can_adopt))
        self.compare_button.setEnabled(not busy and bool(converted) and bool(self.main_window.engine_config.model_path))
        self.convert_button.setEnabled(not busy and bool(run and can_retry_conversion(self.repository, run['id'])))
        self.review_button.setEnabled(not busy and bool(run))
        self.open_run_button.setEnabled(bool(run))
        self.version_name.setEnabled(not busy and bool(run))
        self.judgment.setEnabled(not busy and bool(run))
        self.review_notes.setEnabled(not busy and bool(run))
        self.rollback_button.setEnabled(not busy and bool(self.store.setting('training_previous_engine')))
