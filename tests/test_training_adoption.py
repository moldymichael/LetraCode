"""Adapter selection must reach the engine without losing ordinary model settings."""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from threading import Event
import time

import pytest
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox, QPushButton

from letracode.dialogs import ModelDialog
from letracode.engine import EngineConfig, EngineError, LocalEngine
from letracode.store import Store


pytestmark = pytest.mark.usefixtures('python_engine_peer')


@pytest.fixture
def model_files(tmp_path):
    spec = importlib.util.spec_from_file_location(
        'adapter_engine_peer', Path(__file__).with_name('test_engine.py'))
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    executable = tmp_path / 'llama-server-peer.exe'
    executable.write_text(fixture.PEER_SOURCE, encoding='utf-8')
    executable.chmod(0o700)
    model = tmp_path / 'base.gguf'
    model.write_bytes(b'GGUF' + bytes(64))
    adapter = tmp_path / 'candidate café with spaces.gguf'
    adapter.write_bytes(b'GGUF' + bytes(64))
    return executable, model, adapter


def adapter_config(model_files):
    executable, model, adapter = model_files
    return EngineConfig(executable=str(executable), model_path=str(model), lora_path=str(adapter))


def test_selected_adapter_reaches_started_engine_as_one_absolute_argument(model_files, tmp_path, monkeypatch):
    executable, model, adapter = model_files
    config = adapter_config(model_files)
    monkeypatch.chdir(tmp_path)
    config.lora_path = adapter.name
    engine = LocalEngine(config, tmp_path / 'data')
    try:
        engine.start(Event())
        argv = json.loads(executable.with_suffix('.args.json').read_text())
        assert '--lora' in argv
        assert argv[argv.index('--lora') + 1] == str(adapter.resolve())
        assert argv[argv.index('--model') + 1] == str(model.resolve())
    finally:
        engine.stop()


@pytest.mark.parametrize('invalid', ['missing', 'directory', 'bad_header', 'comma'])
def test_invalid_adapter_is_refused_before_starting_a_process(model_files, tmp_path, invalid):
    executable, _, adapter = model_files
    config = adapter_config(model_files)
    if invalid == 'missing':
        adapter.unlink()
    elif invalid == 'directory':
        adapter.unlink()
        adapter.mkdir()
    elif invalid == 'bad_header':
        adapter.write_bytes(b'not GGUF')
    else:
        renamed = adapter.with_name('candidate,another.gguf')
        adapter.rename(renamed)
        config.lora_path = str(renamed)
    engine = LocalEngine(config, tmp_path / 'data')
    try:
        with pytest.raises(EngineError, match='[Aa]dapter|LoRA'):
            engine.start(Event())
        assert not executable.with_suffix('.args.json').exists()
        assert not engine.running
    finally:
        engine.stop()


def test_model_dialog_keeps_adapter_when_other_settings_change_and_can_clear_it(model_files):
    app = QApplication.instance() or QApplication([])
    dialog = ModelDialog(adapter_config(model_files))
    try:
        dialog.temperature.setValue(0.4)
        saved = asdict(dialog.config())
        assert saved.get('lora_path') == str(model_files[2])
        assert saved['temperature'] == 0.4
        clear = next(button for button in dialog.findChildren(QPushButton)
                     if button.text() == 'Clear adapter')
        clear.click()
        assert asdict(dialog.config()).get('lora_path') == ''
        assert dialog.config().model_path == str(model_files[1])
    finally:
        dialog.close()


def test_model_dialog_refuses_invalid_adapter_without_saving(model_files, monkeypatch):
    app = QApplication.instance() or QApplication([])
    model_files[2].write_bytes(b'not GGUF')
    warnings = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda parent, title, text: warnings.append(text))
    dialog = ModelDialog(adapter_config(model_files))
    try:
        dialog.validate()
        assert dialog.result() != QDialog.DialogCode.Accepted
        assert warnings and 'GGUF' in warnings[0]
    finally:
        dialog.close()


@pytest.fixture
def completed_version(model_files, tmp_path):
    from letracode.training import TrainingConfig, TrainingRepository

    executable, base, sample_adapter = model_files
    model = tmp_path / 'training weights'
    model.mkdir()
    (model / 'config.json').write_text('{"model_type":"llama"}', encoding='utf-8')
    (model / 'model.safetensors').write_bytes(b'training fixture weights')
    repo = TrainingRepository(Store(tmp_path / 'data'))
    repo.save_example('Training question', 'Reviewed answer', approved=True)
    repo.save_example('Held-out question', 'Expected answer', split='eval', approved=True)
    run = repo.create_run(TrainingConfig(python_executable=sys.executable,
        base_model=str(model), base_gguf=str(base)))
    adapter = repo.run_directory(run['id']) / 'adapter.gguf'
    adapter.write_bytes(sample_adapter.read_bytes())
    report = {'base_loss': 2.0, 'candidate_loss': 1.5,
              'eval_examples': [{'prompt': 'Held-out question', 'expected': 'Expected answer'}],
              'base_gguf_sha256': hashlib.sha256(base.read_bytes()).hexdigest(),
              'adapter_gguf_sha256': hashlib.sha256(adapter.read_bytes()).hexdigest()}
    repo.update_run(run['id'], 'running')
    repo.update_run(run['id'], 'succeeded', report=report)
    original = EngineConfig(executable=str(executable), model_path=str(base), temperature=0.3)
    repo.store.set_setting('engine', asdict(original))
    return repo, run['id'], original, adapter


def observed_activation(completed_version, **kwargs):
    from letracode.training_worker import ActivationWorker

    repo, run_id, original, _ = completed_version
    worker = ActivationWorker(repo, original, run_id=run_id, **kwargs)
    ready, failed = [], []
    worker.ready.connect(ready.append)
    worker.failed.connect(failed.append)
    return worker, ready, failed


def test_activation_hands_off_loaded_adapter_without_persisting_adoption(completed_version):
    repo, _, original, adapter = completed_version
    worker, ready, failed = observed_activation(completed_version)
    try:
        worker.run()
        assert not failed
        assert len(ready) == 1 and ready[0].running
        argv = json.loads(Path(original.executable).with_suffix('.args.json').read_text())
        assert argv[argv.index('--lora') + 1] == str(adapter)
        assert ready[0].config.temperature == 0.3
        assert ready[0].config.lora_path == str(adapter)
        assert original.lora_path == ''
        assert repo.store.setting('engine') == asdict(original)
    finally:
        if worker.engine is not None:
            worker.engine.stop()


def test_activation_failed_load_preserves_settings_and_version(completed_version):
    repo, run_id, original, adapter = completed_version
    Path(original.executable).write_text(
        '#!/usr/bin/env python3\nprint("adapter is incompatible", flush=True)\nraise SystemExit(7)\n',
        encoding='utf-8')
    worker, ready, failed = observed_activation(completed_version)
    worker.run()
    assert not ready
    assert failed and 'adapter is incompatible' in failed[0]
    assert not worker.engine.running
    assert repo.store.setting('engine') == asdict(original)
    assert repo.run(run_id)['status'] == 'succeeded'
    assert adapter.is_file()


@pytest.mark.parametrize('changed', ['base', 'adapter'])
def test_activation_rejects_changed_evaluated_files_before_launch(completed_version, changed):
    repo, _, original, adapter = completed_version
    path = Path(original.model_path) if changed == 'base' else adapter
    path.write_bytes(path.read_bytes() + b'changed')
    worker, ready, failed = observed_activation(completed_version)
    worker.run()
    assert not ready and failed and 'changed' in failed[0]
    assert worker.engine is None
    assert not Path(original.executable).with_suffix('.args.json').exists()
    assert repo.store.setting('engine') == asdict(original)


def test_cancelled_activation_stops_owned_engine_and_keeps_saved_selection(completed_version):
    app = QApplication.instance() or QApplication([])
    repo, _, original, _ = completed_version
    marker = Path(original.executable).with_suffix('.loading')
    Path(original.executable).write_text(
        '#!/usr/bin/env python3\nfrom pathlib import Path\nimport sys,time\n'
        'Path(sys.argv[0]).with_suffix(".loading").touch()\ntime.sleep(30)\n', encoding='utf-8')
    worker, ready, failed = observed_activation(completed_version)
    worker.start()
    try:
        deadline = time.monotonic() + 5
        while not marker.exists() and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        assert marker.exists()
        worker.cancel()
        assert worker.wait(5000)
        app.processEvents()
        assert not ready and failed
        assert worker.engine is not None and not worker.engine.running
        assert repo.store.setting('engine') == asdict(original)
    finally:
        worker.cancel()
        worker.wait(5000)


@pytest.mark.parametrize('changed', ['base', 'adapter'])
def test_activation_rechecks_files_changed_during_model_loading(completed_version, changed):
    repo, _, original, adapter = completed_version
    worker, ready, failed = observed_activation(completed_version)

    def replace_while_loading(message):
        if message == 'Local model ready':
            path = Path(original.model_path) if changed == 'base' else adapter
            path.write_bytes(b'GGUF' + b'changed during engine startup')

    worker.status.connect(replace_while_loading)
    try:
        worker.run()
        assert not ready
        assert failed and 'changed' in failed[0]
        assert not worker.engine.running
        assert repo.store.setting('engine') == asdict(original)
    finally:
        if worker.engine is not None:
            worker.engine.stop()


def test_rollback_loads_prior_full_config_without_adapter_or_implicit_save(completed_version):
    repo, _, original, _ = completed_version
    previous = asdict(original)
    previous.update(max_tokens=1024, threads=2, temperature=0.8)
    worker, ready, failed = observed_activation(completed_version, rollback=previous)
    try:
        worker.run()
        assert not failed
        assert len(ready) == 1 and ready[0].running
        assert asdict(ready[0].config) == previous
        argv = json.loads(Path(original.executable).with_suffix('.args.json').read_text())
        assert '--lora' not in argv
        assert repo.store.setting('engine') == asdict(original)
    finally:
        if worker.engine is not None:
            worker.engine.stop()


def test_readopting_active_version_preserves_rollback(completed_version, monkeypatch):
    from letracode.ui import MainWindow
    from PySide6.QtWidgets import QMessageBox
    repo, run_id, original, adapter = completed_version
    previous = asdict(original)
    repo.store.set_setting('training_active_version', run_id)
    repo.store.set_setting('training_previous_engine', previous)
    window = MainWindow(repo.store)
    panel = window.training_panel
    panel.run_id = run_id
    panel.refresh_runs()
    assert not panel.adopt_button.isEnabled()
    monkeypatch.setattr(QMessageBox, 'question', lambda *args: pytest.fail('Active version must not prompt for adoption'))
    panel.adopt()
    panel.start_activation(run_id=run_id)
    assert panel.job is None
    assert repo.store.setting('training_previous_engine') == previous
    window.close()
