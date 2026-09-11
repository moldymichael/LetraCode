"""Exercise the actual entry point and review window using disposable runs."""
import time

from PySide6.QtWidgets import QApplication

from letracode.store import Store
from letracode.ui import MainWindow
from letracode.training_experience import version_review
from test_training_experience import completed


def open_comparison(tmp_path):
    app = QApplication.instance() or QApplication([])
    repo, run, original = completed(tmp_path, 2)
    window = MainWindow(repo.store)
    panel = window.training_panel
    panel.run_id = run['id']
    panel.refresh_runs()
    panel.compare_button.click()
    dialog = getattr(panel, 'comparison_dialog', None)
    assert dialog is not None, 'Compare must open an obvious review window immediately'
    return app, repo, run, window, dialog


def test_compare_click_opens_prompt_workspace_without_starting_inference(tmp_path):
    app, repo, run, window, dialog = open_comparison(tmp_path)
    try:
        assert dialog.isVisible()
        assert window.training_panel.job is None
        assert dialog.questions.count() == 2
        dialog.fresh_prompt.setPlainText('A fresh question\nwith useful context')
        dialog.add_button.click()
        assert dialog.questions.count() == 3
        assert version_review(repo, run['id'])['comparison_prompts'][0]['prompt'].endswith('with useful context')
        assert len(repo.run(run['id'])['examples']['eval']) == 2
        assert len(repo.examples()) == 3
    finally:
        dialog.close(); window.close()
    reopened = MainWindow(Store(repo.store.directory))
    reopened.training_panel.run_id = run['id']
    reopened.training_panel.refresh_runs()
    reopened.training_panel.compare_button.click()
    try:
        assert reopened.training_panel.comparison_dialog.questions.count() == 3
    finally:
        reopened.training_panel.comparison_dialog.close(); reopened.close()


def test_full_responses_review_and_progress_survive_real_worker(tmp_path, monkeypatch):
    from letracode import training_experience
    class Peer:
        def __init__(self, config, directory): self.config = config
        def start(self, cancel, status): status('Loading comparison model')
        def stop(self): pass
        def complete(self, messages, tools, cancel, delta, thinking=False, **kwargs):
            text = ('Candidate' if self.config.lora_path else 'Strand now') + '\n' + 'Full response line\n' * 500 + 'THE END'
            delta(text)
            return {'content': text}
    monkeypatch.setattr(training_experience, 'LocalEngine', Peer)
    app, repo, run, window, dialog = open_comparison(tmp_path)
    try:
        dialog.fresh_prompt.setPlainText('Fresh test')
        dialog.add_button.click()
        dialog.run_button.click()
        assert window.training_panel.job is not None
        assert not dialog.run_button.isEnabled()
        assert dialog.progress.text()
        deadline = time.monotonic() + 8
        while window.training_panel.job is not None and time.monotonic() < deadline:
            app.processEvents(); time.sleep(.01)
        app.processEvents()
        assert window.training_panel.job is None
        assert 'complete' in dialog.progress.text().lower()
        dialog.questions.setCurrentIndex(2)
        assert dialog.current_answer.toPlainText().endswith('THE END')
        assert dialog.candidate_answer.toPlainText().endswith('THE END')
        assert dialog.current_answer.verticalScrollBar().value() == 0
        assert len(dialog.candidate_answer.toPlainText()) > 8000
        dialog.question_judgment.setCurrentIndex(dialog.question_judgment.findData('better'))
        dialog.judgment.setCurrentIndex(dialog.judgment.findData('mixed'))
        dialog.notes.setText('Longer, but check factual claims')
        dialog.save_button.click()
        review = version_review(repo, run['id'])
        assert review['judgment'] == 'mixed'
        assert review['comparison']['examples'][2]['judgment'] == 'better'
        assert repo.store.setting('training_active_version') is None
        assert dialog.adopt_button.isEnabled()
    finally:
        if window.training_panel.job is not None:
            window.training_panel.job.cancel(); window.training_panel.job.wait(5000)
            app.processEvents()
        dialog.close(); window.close()


def test_fresh_teaching_duplicate_is_visible_and_never_started(tmp_path):
    app, repo, run, window, dialog = open_comparison(tmp_path)
    try:
        dialog.fresh_prompt.setPlainText(' TRAINING   only ')
        dialog.add_button.click()
        assert dialog.questions.count() == 2
        assert dialog.fresh_prompt.toPlainText()
        assert 'teach' in dialog.progress.text().lower() or 'train' in dialog.progress.text().lower()
        assert window.training_panel.job is None
    finally:
        dialog.close(); window.close()


def test_stopped_comparison_can_be_reopened_and_exported_with_partial_answers(tmp_path, monkeypatch):
    import json
    import threading
    from PySide6.QtWidgets import QFileDialog
    from letracode import training_experience
    started = threading.Event()
    class Peer:
        def __init__(self, config, directory): pass
        def start(self, cancel, status): pass
        def stop(self): pass
        def complete(self, messages, tools, cancel, delta, **kwargs):
            delta('Saved partial answer'); started.set()
            assert cancel.wait(5), 'Test failed to stop the comparison'
            raise training_experience.Cancelled('Stopped during this answer')
    monkeypatch.setattr(training_experience, 'LocalEngine', Peer)
    app, repo, run, window, dialog = open_comparison(tmp_path)
    try:
        dialog.run_button.click()
        deadline = time.monotonic() + 5
        while not started.is_set() and time.monotonic() < deadline:
            app.processEvents(); time.sleep(.01)
        assert started.is_set()
        dialog.close()
        assert window.training_panel.job is not None
        window.training_panel.compare_button.click()
        assert dialog.isVisible()
        dialog.stop_button.click()
        while window.training_panel.job is not None and time.monotonic() < deadline:
            app.processEvents(); time.sleep(.01)
        assert window.training_panel.job is None
        assert 'stopped' in dialog.progress.text().lower()
        assert dialog.current_answer.toPlainText() == 'Saved partial answer'
        assert not dialog.adopt_button.isEnabled()
        destination = tmp_path / 'saved-comparison.json'
        monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *args: (str(destination), 'JSON (*.json)'))
        dialog.export_button.click()
        exported = json.loads(destination.read_text())
        assert exported['review']['comparison']['status'] == 'cancelled'
        assert exported['review']['comparison']['examples'][0]['current_output'] == 'Saved partial answer'
    finally:
        if window.training_panel.job is not None:
            window.training_panel.job.cancel(); window.training_panel.job.wait(6000)
            app.processEvents()
        dialog.close(); window.close()


def test_saved_answers_remain_accessible_when_next_questions_change(tmp_path):
    from letracode.training_experience import save_version_review
    app, repo, run, window, dialog = open_comparison(tmp_path)
    try:
        save_version_review(repo, run['id'], comparison_prompts=[{'prompt': 'Fresh answered question', 'response': ''}],
            comparison={'status': 'complete', 'examples': [
                {'prompt': 'Fresh answered question', 'response': '', 'source': 'fresh',
                 'current_output': 'Keep this current answer', 'candidate_output': 'Keep this candidate answer'}]})
        dialog.reload()
        dialog.questions.setCurrentIndex(2)
        dialog.remove_button.click()
        assert dialog.attempts.count() == 2, 'Previous saved suite must remain viewable before rerunning'
        dialog.attempts.setCurrentIndex(1)
        assert dialog.current_answer.toPlainText() == 'Keep this current answer'
        assert dialog.candidate_answer.toPlainText() == 'Keep this candidate answer'
        assert not dialog.save_button.isEnabled()
    finally:
        dialog.close(); window.close()


def test_reopen_reflects_review_saved_in_parent_without_losing_question_draft(tmp_path):
    app, repo, run, window, dialog = open_comparison(tmp_path)
    try:
        dialog.fresh_prompt.setPlainText('Unsubmitted draft')
        dialog.close()
        panel = window.training_panel
        panel.judgment.setCurrentIndex(panel.judgment.findData('mixed'))
        panel.review_notes.setText('Newly saved parent review')
        panel.save_review()
        panel.compare_button.click()
        assert dialog.notes.text() == 'Newly saved parent review'
        assert dialog.judgment.currentData() == 'mixed'
        assert dialog.fresh_prompt.toPlainText() == 'Unsubmitted draft'
    finally:
        dialog.close(); window.close()
