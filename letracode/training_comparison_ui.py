"""A per-candidate workspace for standalone Chat-runtime comparisons."""
from __future__ import annotations

import copy
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QDialog, QFileDialog, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton, QSplitter,
    QTabWidget, QVBoxLayout, QWidget)

from .training_experience import save_version_review, validate_comparison_prompts, version_review


JUDGMENTS = (('Not reviewed', 'unreviewed'), ('Candidate is better', 'better'),
             ('About the same', 'same'), ('Strand now is better', 'worse'), ('Mixed / uncertain', 'mixed'))


def judgment_box():
    box = QComboBox()
    for label, value in JUDGMENTS:
        box.addItem(label, value)
    return box


def prompts_match(review):
    report = review.get('comparison') or {}
    fresh = [{'prompt': row['prompt'], 'response': row.get('response', '')}
             for row in report.get('examples', []) if row.get('source') == 'fresh']
    return fresh == review.get('comparison_prompts', [])


class ComparisonDialog(QDialog):
    def __init__(self, panel, ident):
        super().__init__(panel)
        self.panel, self.repo, self.ident = panel, panel.repository, ident
        self.report = {}
        self.display_rows = []
        self._loading = False
        self._shown_question = None
        self.review_dirty = False
        self._review_draft = None
        self.setWindowTitle('Compare with Strand')
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        self.resize(1140, 850)
        layout = QVBoxLayout(self)
        self.heading = QLabel(); self.heading.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.heading)
        hint = QLabel('Both versions answer each question independently with your Chat settings. '
                      'Shared notes, project files and tools are excluded. Add fresh questions to test useful behavior beyond the training evaluation.')
        hint.setWordWrap(True); layout.addWidget(hint)
        self.attempts = QComboBox(); self.attempts.currentIndexChanged.connect(self.select_attempt)
        layout.addWidget(self.attempts)

        self.composer_toggle = QPushButton('Add fresh questions')
        self.composer_toggle.setCheckable(True); self.composer_toggle.setChecked(True)
        layout.addWidget(self.composer_toggle)
        self.composer = QWidget(); form = QFormLayout(self.composer); form.setContentsMargins(0, 0, 0, 0)
        self.composer_toggle.toggled.connect(self.composer.setVisible)
        self.fresh_prompt = QPlainTextEdit(); self.fresh_prompt.setMaximumHeight(80)
        self.fresh_prompt.setPlaceholderText('Write a new, self-contained question. These prompts are never added to teaching examples.')
        self.reference = QLineEdit(); self.reference.setPlaceholderText('Optional: what a good answer should cover')
        form.addRow('Fresh question', self.fresh_prompt); form.addRow('Reference', self.reference)
        self.add_button = QPushButton('Add question'); self.add_button.clicked.connect(self.add_prompt)
        form.addRow('', self.add_button); layout.addWidget(self.composer)

        selector = QHBoxLayout()
        self.questions = QComboBox(); self.questions.setMinimumContentsLength(25)
        self.questions.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.questions.currentIndexChanged.connect(self.show_question)
        selector.addWidget(self.questions, 1)
        self.remove_button = QPushButton('Remove fresh question'); self.remove_button.clicked.connect(self.remove_prompt)
        selector.addWidget(self.remove_button); layout.addLayout(selector)
        self.question_text = QPlainTextEdit(); self.question_text.setReadOnly(True); self.question_text.setMaximumHeight(90)
        layout.addWidget(self.question_text)

        split = QSplitter()
        for key, title in (('current', 'Strand now'), ('candidate', 'Trained candidate')):
            pane = QWidget(); column = QVBoxLayout(pane); column.setContentsMargins(0, 0, 0, 0)
            label = QLabel(title); label.setTextFormat(Qt.TextFormat.PlainText); label.setWordWrap(True)
            setattr(self, key + '_status', label); column.addWidget(label)
            tabs = QTabWidget()
            answer = QPlainTextEdit(); answer.setReadOnly(True)
            answer.setPlaceholderText('The complete saved response will appear here.')
            reasoning = QPlainTextEdit(); reasoning.setReadOnly(True)
            reasoning.setPlaceholderText('No thinking text recorded.')
            setattr(self, key + '_answer', answer); setattr(self, key + '_reasoning', reasoning)
            tabs.addTab(answer, 'Answer'); tabs.addTab(reasoning, 'Thinking')
            column.addWidget(tabs); split.addWidget(pane)
        split.setSizes([550, 550]); layout.addWidget(split, 1)

        review = QFormLayout()
        self.question_judgment = judgment_box()
        self.question_judgment.currentIndexChanged.connect(self.question_review_changed)
        review.addRow('This question', self.question_judgment)
        self.judgment = judgment_box(); review.addRow('Overall judgment', self.judgment)
        self.notes = QLineEdit(); self.notes.setPlaceholderText('Accuracy, useful detail, style, regressions and uncertainty')
        self.judgment.currentIndexChanged.connect(self.review_changed)
        self.notes.textChanged.connect(self.review_changed)
        review.addRow('Review notes', self.notes); layout.addLayout(review)
        self.progress = QLabel(); self.progress.setTextFormat(Qt.TextFormat.PlainText); self.progress.setWordWrap(True)
        layout.addWidget(self.progress)
        buttons = QHBoxLayout()
        for attr, label, action in (
                ('run_button', 'Run comparison', self.start), ('stop_button', 'Stop', panel.stop),
                ('save_button', 'Save my review', self.save_review),
                ('adopt_button', 'Use this version…', self.adopt),
                ('export_button', 'Export comparison…', self.export),
                ('close_button', 'Close', self.close)):
            button = QPushButton(label); button.clicked.connect(action)
            setattr(self, attr, button); buttons.addWidget(button)
        layout.addLayout(buttons)
        panel.comparison_updated.connect(self.receive_report)
        self.reload()

    def reload(self, *, preserve_edits=False):
        draft = self._review_draft if preserve_edits else None
        review = version_review(self.repo, self.ident)
        self.heading.setText((review.get('name') or 'Candidate ' + self.ident[:12]) + ' · comparison and review')
        self.attempts.blockSignals(True); self.attempts.clear()
        self.attempts.addItem('Latest comparison / questions for the next run', None)
        if review.get('comparison') and not prompts_match(review):
            self.attempts.addItem('Saved answers before question changes', review)
        for record in reversed(review.get('comparison_history', [])):
            report = record.get('comparison') or {}
            self.attempts.addItem(f"Previous: {report.get('created', '?')} · {report.get('status', 'unknown')} · {record.get('judgment', 'unreviewed')}", record)
        self.attempts.blockSignals(False)
        self._review_draft = draft
        self.review_dirty = draft is not None
        self.select_attempt()
        if not preserve_edits:
            self.composer_toggle.setChecked(not bool(review.get('comparison')) or bool(self.fresh_prompt.toPlainText()))

    def select_attempt(self, _=None):
        review = self.attempts.currentData() or self._review_draft or version_review(self.repo, self.ident)
        self.report = copy.deepcopy(review.get('comparison') or {})
        self._loading = True
        self.judgment.setCurrentIndex(max(0, self.judgment.findData(review.get('judgment', 'unreviewed'))))
        self.notes.setText(review.get('notes', ''))
        self._loading = False
        self.render_rows()
        self.describe_status()
        self.update_controls()

    def render_rows(self):
        selected = self.questions.currentIndex()
        if self.attempts.currentData() is not None or self.report.get('status') == 'running':
            self.display_rows = self.report.get('examples', [])
        else:
            review = version_review(self.repo, self.ident)
            rows = [dict(prompt=r['prompt'], response=r['response'], source='held_out')
                    for r in self.repo.run(self.ident)['examples']['eval']]
            rows.extend(dict(r, source='fresh') for r in review.get('comparison_prompts', []))
            previous = {(r['prompt'], r.get('response', '')): r for r in self.report.get('examples', [])}
            self.display_rows = [previous.get((r['prompt'], r.get('response', '')), r) for r in rows]
        self.questions.blockSignals(True); self.questions.clear()
        for index, row in enumerate(self.display_rows):
            source = 'Fresh' if row.get('source') == 'fresh' else 'Held out'
            self.questions.addItem(f"{index + 1}. {source} · {row['prompt'][:100].replace(chr(10), ' ')}")
        self.questions.setCurrentIndex(max(0, min(selected, len(self.display_rows) - 1)))
        self.questions.blockSignals(False)
        self.show_question()

    def show_question(self, _=None):
        index = self.questions.currentIndex()
        if not 0 <= index < len(self.display_rows):
            return
        row = self.display_rows[index]
        selection = (self.report.get('id'), index, row['prompt'], self.report.get('status') != 'running')
        changed = selection != self._shown_question
        self._shown_question = selection
        self.question_text.setPlainText(row['prompt'] + ('\n\nReference: ' + row['response'] if row.get('response') else ''))
        for key, label in (('current', 'Strand now'), ('candidate', 'Trained candidate')):
            state = row.get(key + '_status') or ('incomplete' if row.get(key + '_error') else 'saved' if key + '_output' in row else 'pending')
            error = row.get(key + '_error', '')
            config = self.report.get(key + '_config') or {}
            model = Path(config.get('model_path') or '').name
            adapter = Path(config.get('lora_path') or '').name
            identity = model + (' + ' + adapter if adapter else '')
            getattr(self, key + '_status').setText(label + ' · ' + state + ('\n' + identity if identity else '') + ('\n' + error if error else ''))
            getattr(self, key + '_status').setToolTip(config.get('model_path', '') + '\n' + config.get('lora_path', ''))
            for suffix, field in (('_output', '_answer'), ('_reasoning', '_reasoning')):
                widget = getattr(self, key + field)
                value = row.get(key + suffix, '')
                if widget.toPlainText() != value:
                    scroll = widget.verticalScrollBar()
                    position, at_end = scroll.value(), scroll.value() == scroll.maximum()
                    widget.setPlainText(value)
                    scroll.setValue(scroll.maximum() if at_end else position)
                if changed:
                    widget.verticalScrollBar().setValue(0)
        self._loading = True
        self.question_judgment.setCurrentIndex(max(0, self.question_judgment.findData(row.get('judgment', 'unreviewed'))))
        self._loading = False
        self.update_controls()

    def describe_status(self):
        status = self.report.get('status')
        live = self.panel.job is not None and getattr(self.panel, '_comparison_run_id', None) == self.ident
        text = {'complete': 'Comparison complete. Read both answers and save your judgment.',
                'incomplete': 'Comparison incomplete. Saved partial answers and errors remain available. Try again after resolving the error.',
                'cancelled': 'Comparison stopped. Saved partial answers remain available.',
                'running': 'Comparison is running…' if live else 'Comparison was interrupted. Saved partial answers remain available; run it again.'}.get(status,
                    'Ready. Add fresh questions or run the held-out questions below. Neither version is adopted by comparing.')
        if self.report.get('error'):
            text += '\n' + self.report['error']
        if self.attempts.currentData() is None and self.report and not prompts_match(version_review(self.repo, self.ident)):
            text += '\nQuestions changed. Run comparison again before choosing this version.'
        if self.report.get('base_changed'):
            text += '\nThe candidate uses a different base model; this comparison includes that change as well as training.'
        config = self.report.get('current_config') or {}
        if config:
            text += f"\nChat output budget: {config.get('max_tokens', '?')} tokens per reply. Cut-off responses are incomplete."
        self.progress.setText(text)

    def add_prompt(self):
        review = version_review(self.repo, self.ident)
        prompts = review.get('comparison_prompts', []) + [{'prompt': self.fresh_prompt.toPlainText(), 'response': self.reference.text()}]
        try:
            prompts = validate_comparison_prompts(self.repo, self.ident, prompts)
            save_version_review(self.repo, self.ident, comparison_prompts=prompts)
        except (OSError, ValueError) as error:
            self.progress.setText(str(error)); return
        self.fresh_prompt.clear(); self.reference.clear(); self.reload(preserve_edits=True)
        self.questions.setCurrentIndex(self.questions.count() - 1)
        self.panel.refresh_runs()

    def remove_prompt(self):
        index = self.questions.currentIndex()
        if index < 0 or self.display_rows[index].get('source') != 'fresh':
            return
        row = self.display_rows[index]
        prompts = [r for r in version_review(self.repo, self.ident).get('comparison_prompts', []) if r['prompt'] != row['prompt']]
        save_version_review(self.repo, self.ident, comparison_prompts=prompts)
        self.reload(preserve_edits=True); self.panel.refresh_runs()

    def start(self):
        if self.panel.job is not None or self.panel.chat_busy:
            self.progress.setText('Wait for the current local model job to finish, or stop it first.'); return
        if self.fresh_prompt.toPlainText().strip():
            self.add_prompt()
            if self.fresh_prompt.toPlainText().strip():
                return
        prompts = version_review(self.repo, self.ident).get('comparison_prompts', [])
        try:
            validate_comparison_prompts(self.repo, self.ident, prompts)
        except (OSError, ValueError) as error:
            self.progress.setText(str(error)); return
        self.attempts.setCurrentIndex(0)
        self.progress.setText('Starting comparison. Verifying model files; large models can take a while…')
        self.panel.run_comparison(self.ident, prompts)
        self.update_controls()

    def receive_report(self, ident, report):
        if ident != self.ident or self.attempts.currentData() is not None:
            return
        self.report = report
        self.render_rows(); self.describe_status()

    def question_review_changed(self, _):
        if not self._loading and 0 <= self.questions.currentIndex() < len(self.display_rows):
            self.display_rows[self.questions.currentIndex()]['judgment'] = self.question_judgment.currentData()
            self.review_changed()

    def review_changed(self, _=None):
        if not self._loading and self.attempts.currentData() is None:
            self.review_dirty = True
            self._review_draft = dict(comparison=copy.deepcopy(self.report),
                                      judgment=self.judgment.currentData(), notes=self.notes.text())

    def save_review(self):
        if self.panel.job is not None or self.attempts.currentData() is not None:
            return False
        review = version_review(self.repo, self.ident)
        fields = dict(judgment=self.judgment.currentData(), notes=self.notes.text())
        if self.report:
            fields['comparison'] = self.report
        try:
            save_version_review(self.repo, self.ident, **fields)
        except ValueError as error:
            self.progress.setText(str(error)); return False
        self.review_dirty = False; self._review_draft = None
        self.panel.refresh_runs(); self.update_controls()
        self.progress.setText('Review saved. Choosing this version will recheck the model files and comparison settings.')
        return True

    def adopt(self):
        if not self.save_review():
            return
        self.panel.run_id = self.ident
        self.panel.refresh_runs()
        self.panel.adopt()
        self.hide()

    def export(self):
        from .training_export import export_version_review
        path, _ = QFileDialog.getSaveFileName(self, 'Export saved comparison — includes full prompts, answers, notes and local paths',
                                             str(Path.home() / ('strand-comparison-' + self.ident[:12] + '.json')), 'JSON (*.json)')
        if path:
            try:
                export_version_review(self.repo, self.ident, Path(path))
                self.progress.setText('Saved comparison exported. Unsaved review edits are not included. Inspect the full text and local paths before sharing.')
            except (OSError, ValueError) as error:
                self.progress.setText(str(error))

    def update_controls(self):
        busy = self.panel.chat_busy or self.panel.job is not None
        latest = self.attempts.currentData() is None
        editable = not busy and latest
        self.composer.setEnabled(editable)
        self.run_button.setEnabled(editable)
        self.run_button.setText(f'Run comparison ({len(self.display_rows)} questions)')
        self.stop_button.setEnabled(self.panel.job is not None and getattr(self.panel, '_comparison_run_id', None) == self.ident)
        self.save_button.setEnabled(editable)
        self.judgment.setEnabled(editable); self.notes.setEnabled(editable)
        row = self.display_rows[self.questions.currentIndex()] if 0 <= self.questions.currentIndex() < len(self.display_rows) else {}
        self.question_judgment.setEnabled(editable and any(row is saved for saved in self.report.get('examples', [])))
        self.remove_button.setEnabled(editable and row.get('source') == 'fresh')
        review = version_review(self.repo, self.ident)
        self.adopt_button.setEnabled(editable and review.get('comparison', {}).get('status') == 'complete'
                                     and prompts_match(review) and review.get('judgment', 'unreviewed') != 'unreviewed'
                                     and self.repo.store.setting('training_active_version') != self.ident)
        self.close_button.setText('Close (keeps running)' if self.stop_button.isEnabled() else 'Close')
