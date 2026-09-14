"""Guided setup uses the real editor, saved approvals and preparation worker."""
import os
import time

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtWidgets import QApplication

from letracode.store import Store
from letracode.ui import MainWindow
from test_training_experience import completed


@pytest.fixture
def window(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(Store(tmp_path / 'data'))
    window.show()
    window.workspaces.setCurrentWidget(window.training_panel)
    yield window
    window.close()


def wait_for_check(panel):
    deadline = time.monotonic() + 5
    while panel.job is not None and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(.01)
    assert panel.job is None


def test_first_setup_exposes_required_files_without_exposing_tuning(window):
    panel = window.training_panel
    panel.tabs.setCurrentIndex(1)
    QApplication.processEvents()
    assert panel.fields['base_model'].isVisible()
    assert panel.fields['python_executable'].isVisible()
    assert not panel.fields['rank'].isVisible()
    assert not panel.start_button.isEnabled()
    assert panel.check_button.isEnabled()
    panel.review_examples_button.click()
    assert panel.tabs.currentIndex() == 0


def test_save_and_approve_records_current_example_and_explains_its_use(window):
    panel = window.training_panel
    panel.prompt.setPlainText('Give me a short greeting.')
    panel.response.setPlainText('Hello!')
    panel.approve_example_button.click()
    row = panel.repository.examples()[0]
    assert row['approved'] and row['split'] == 'train'
    assert row['response'] == 'Hello!'
    assert 'Approved' in panel.example_status.text()
    assert '1 teaching' in panel.setup_examples.text()
    panel.split.setCurrentIndex(1)
    assert not panel.repository.examples()[0]['approved']
    assert 'not trained on' in panel.example_use_hint.text()
    panel.approve_example_button.click()
    assert panel.repository.examples()[0]['split'] == 'eval'
    assert '1 comparison' in panel.setup_examples.text()
    assert panel.repository.runs() == []


def test_failed_setup_displays_real_missing_item_without_creating_a_run(window):
    panel = window.training_panel
    panel.fields['python_executable'].setText('/missing/training-python')
    panel.tabs.setCurrentIndex(1)
    panel.check_button.click()
    wait_for_check(panel)
    failure = next(check['detail'] for check in panel._readiness_result['checks'] if not check['ready'])
    assert failure in panel.readiness.text()
    assert panel.setup_locations.isVisible()
    assert not panel.start_button.isEnabled()
    assert panel.repository.runs() == []
    assert not window.engine.running


def test_checked_examples_or_settings_changed_require_another_check(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    repo, run, _ = completed(tmp_path, evaluation=1)
    repo.store.set_setting('training_config', run['config'])
    from letracode import training_experience
    script = tmp_path / 'check-peer.py'
    script.write_text('''
import argparse, json, pathlib
p = argparse.ArgumentParser()
p.add_argument('--check-only', action='store_true')
p.add_argument('--run-dir')
a = p.parse_args()
assert a.check_only
(pathlib.Path(a.run_dir) / 'readiness.json').write_text(json.dumps({'ready': True, 'summary': 'Every example fits'}))
''')
    monkeypatch.setattr(training_experience, 'BACKEND_SCRIPT', script)
    window = MainWindow(repo.store)
    panel = window.training_panel
    try:
        panel.check_button.click()
        wait_for_check(panel)
        assert panel._readiness_result['ready']
        assert not panel.start_button.isEnabled()
        panel.review_check.setChecked(True)
        assert panel.start_button.isEnabled()
        repo.save_example('Another approved lesson', 'Another answer', approved=True)
        panel.refresh_examples()
        assert not panel.start_button.isEnabled()
        assert not panel.review_check.isChecked()
        panel.check_button.click()
        wait_for_check(panel)
        panel.review_check.setChecked(True)
        assert panel.start_button.isEnabled()
        panel.fields['epochs'].setValue(panel.fields['epochs'].value() + 1)
        assert not panel.start_button.isEnabled()
        assert not panel.review_check.isChecked()
        assert len(repo.runs()) == 1
        assert not window.engine.running
    finally:
        window.close()
