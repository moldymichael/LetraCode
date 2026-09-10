"""Gemma setup and acceptance preserve the reviewed local training boundary."""
import dataclasses
import hashlib
import json
import os
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtWidgets import QApplication

from letracode import training_worker
from letracode.store import Store
from letracode.training import TrainingConfig
from letracode.ui import MainWindow
from test_training_worker import (SUCCESS_BACKEND, backend_peer, prepared_run,
                                  qlora_config, qlora_report)
from test_training_adoption import completed_version, model_files

pytestmark = pytest.mark.usefixtures('python_engine_peer')


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize('profile', ['gemma4-e2b', 'gemma4-e4b'])
def test_gemma_profile_changes_memory_settings_without_replacing_paths(tmp_path, app, profile):
    store = Store(tmp_path / 'data')
    old = dataclasses.asdict(TrainingConfig(python_executable='/saved/python',
        base_model='/saved/base', base_gguf='/saved/chat.gguf', llama_cpp_dir='/saved/converter',
        epochs=3, learning_rate=.001, rank=16, max_length=1024, batch_size=2))
    store.set_setting('training_config', old)
    window = MainWindow(store)
    try:
        panel = window.training_panel
        assert dataclasses.asdict(panel.configuration()) == old
        panel.profile.setCurrentIndex(panel.profile.findData(profile))
        saved = store.setting('training_config')
        assert {key: saved[key] for key in ('training_method', 'device', 'rank', 'max_length',
                'batch_size', 'gradient_accumulation_steps', 'gradient_checkpointing')} == {
            'training_method': 'qlora', 'device': 'cuda', 'rank': 4, 'max_length': 256,
            'batch_size': 1, 'gradient_accumulation_steps': 4, 'gradient_checkpointing': True}
        assert all(saved[key] == old[key] for key in ('python_executable', 'base_model',
                   'base_gguf', 'llama_cpp_dir', 'epochs', 'learning_rate'))
        panel.profile.setCurrentIndex(0)
        assert store.setting('training_config') == saved
        assert not panel.repository.runs() and not window.engine.running
    finally:
        window.close()


def gemma_report_config(tmp_path):
    (tmp_path / 'config.json').write_text(json.dumps({'model_type': 'gemma4',
        'text_config': {'model_type': 'gemma4_text'}}))
    config = dict(qlora_config(), base_model=str(tmp_path))
    report = qlora_report()
    report['training_details'].update(model_family='gemma4', text_only=True,
        frozen_ple_cpu=True, target_modules=['q_proj', 'k_proj', 'v_proj', 'o_proj',
                                            'gate_proj', 'up_proj', 'down_proj'])
    return report, config


def test_gemma_report_accepts_language_adapters_and_frozen_cpu_embeddings(tmp_path):
    report, config = gemma_report_config(tmp_path)
    training_worker.validate_training_report(report, config)


@pytest.mark.parametrize('change', [
    lambda report: report['training_details'].update(model_family='llama'),
    lambda report: report['training_details'].update(text_only=False),
    lambda report: report['training_details'].pop('frozen_ple_cpu'),
    lambda report: report['training_details'].update(target_modules='all-linear'),
])
def test_gemma_report_requires_architecture_specific_runtime_evidence(tmp_path, change):
    report, config = gemma_report_config(tmp_path)
    change(report)
    with pytest.raises(ValueError):
        training_worker.validate_training_report(report, config)


def test_report_cannot_select_gemma_policy_for_a_llama_folder(tmp_path):
    report, config = gemma_report_config(tmp_path)
    (tmp_path / 'config.json').write_text('{"model_type":"llama"}')
    report['training_details']['target_modules'] = 'all-linear'
    with pytest.raises(ValueError, match='family|architecture'):
        training_worker.validate_training_report(report, config)


def test_gemma_cannot_use_legacy_report_exemption(tmp_path):
    _, config = gemma_report_config(tmp_path)
    config.update(training_method='lora', gradient_accumulation_steps=1,
                  gradient_checkpointing=False)
    with pytest.raises(ValueError):
        training_worker.validate_training_report({}, config)


def test_gemma_training_pair_failure_preserves_run_without_launching(tmp_path, monkeypatch):
    repo, run = prepared_run(tmp_path)
    (Path(run['config']['base_model']) / 'config.json').write_text('{"model_type":"gemma4"}')
    backend_peer(tmp_path, monkeypatch, 'from pathlib import Path\nPath("launched").touch()')
    def reject(*args, **kwargs):
        raise ValueError('Gemma model pair has no matching manifest')
    monkeypatch.setattr(training_worker, 'verify_gemma_pair', reject, raising=False)
    training_worker.TrainingWorker(repo, run['id']).run()
    saved = repo.run(run['id'])
    assert saved['status'] == 'failed' and 'matching manifest' in saved['error']
    assert not (repo.run_directory(run['id']) / 'launched').exists()


def prepared_gemma_peer(tmp_path, monkeypatch, report_change='', source_change=''):
    repo, original = prepared_run(tmp_path)
    base = Path(original['config']['base_model'])
    details, _ = gemma_report_config(base)
    for name in ('tokenizer.json', 'tokenizer_config.json', 'special_tokens_map.json',
                 'added_tokens.json', 'chat_template.jinja', 'generation_config.json'):
        (base / name).write_text('original ' + name)
    config = TrainingConfig(**dict(original['config'], training_method='qlora', device='cuda',
        batch_size=2, gradient_accumulation_steps=4, gradient_checkpointing=True))
    run = repo.create_run(config)
    record = lambda path: {'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'bytes': path.stat().st_size}
    manifest = {'source': {'base_model': str(base), 'model_type': 'gemma4',
        'weights': {'model.safetensors': record(base / 'model.safetensors')},
        'files': {path.name: record(path) for path in base.iterdir() if path.suffix != '.safetensors'}}}
    source = SUCCESS_BACKEND + '\nreport.update(' + repr(details) + ')\n'
    source += 'base = pathlib.Path(' + repr(str(base)) + ')\n'
    source += '''
report['provenance'].update(
 base_model=str(base), model_config_sha256=hashlib.sha256((base/'config.json').read_bytes()).hexdigest(),
 model_weight_manifest={f.name: {'sha256': hashlib.sha256(f.read_bytes()).hexdigest(), 'bytes': f.stat().st_size} for f in base.glob('*.safetensors')},
 tokenizer_manifest={f.name: hashlib.sha256(f.read_bytes()).hexdigest() for f in base.iterdir() if f.is_file() and (f.name.startswith('tokenizer') or f.name in ('special_tokens_map.json', 'added_tokens.json', 'chat_template.jinja'))})
'''
    source += report_change + '\n(p/"report.json").write_text(json.dumps(report))\n' + source_change + '\n'
    backend_peer(tmp_path, monkeypatch, source)
    return repo, run, manifest


def test_gemma_training_saves_verified_pair_provenance(tmp_path, monkeypatch):
    repo, run, manifest = prepared_gemma_peer(tmp_path, monkeypatch)
    monkeypatch.setattr(training_worker, 'verify_gemma_pair', lambda *args, **kwargs: manifest)
    training_worker.TrainingWorker(repo, run['id']).run()
    saved = repo.run(run['id'])
    assert saved['status'] == 'succeeded', saved['error']
    assert saved['report']['gemma_pair'] == manifest
    assert repo.store.setting('engine') is None


@pytest.mark.parametrize('change', [
    "report['provenance'].pop('model_config_sha256')",
    "report['provenance']['model_config_sha256'] = 'other config'",
    "report['provenance']['model_weight_manifest'] = {}",
    "report['provenance']['model_weight_manifest']['model.safetensors']['sha256'] = 'other weights'",
    "report['provenance']['model_weight_manifest']['model.safetensors']['bytes'] += 1",
    "report['provenance']['tokenizer_manifest'].pop('chat_template.jinja')",
    "report['provenance']['tokenizer_manifest']['tokenizer.json'] = 'other tokenizer'",
    "report['provenance']['tokenizer_manifest']['extra.json'] = 'unexpected file'",
])
def test_gemma_report_source_must_match_the_preverified_pair(tmp_path, monkeypatch, change):
    repo, run, manifest = prepared_gemma_peer(tmp_path, monkeypatch, report_change=change)
    monkeypatch.setattr(training_worker, 'verify_gemma_pair', lambda *args, **kwargs: manifest)
    training_worker.TrainingWorker(repo, run['id']).run()
    saved = repo.run(run['id'])
    assert saved['status'] == 'failed'
    assert 'source' in saved['error'] or 'pair' in saved['error']
    assert (repo.run_directory(run['id']) / 'adapter' / 'adapter_model.safetensors').is_file()
    assert repo.store.setting('engine') is None


@pytest.mark.parametrize('change_pair', [False, True])
def test_gemma_pair_is_reverified_after_training_before_acceptance(tmp_path, monkeypatch, change_pair):
    repo, run, manifest = prepared_gemma_peer(tmp_path, monkeypatch,
        source_change="(base/'generation_config.json').write_text('changed after evaluation')")
    def verify(base, gguf, cancel=None):
        if (Path(base) / 'generation_config.json').read_text().startswith('changed'):
            if change_pair:
                return dict(manifest, gguf={'sha256': 'changed pair'})
            raise ValueError('Gemma source files changed after training')
        return manifest
    monkeypatch.setattr(training_worker, 'verify_gemma_pair', verify)
    training_worker.TrainingWorker(repo, run['id']).run()
    saved = repo.run(run['id'])
    assert saved['status'] == 'failed' and 'changed' in saved['error']
    assert (repo.run_directory(run['id']) / 'report.json').is_file()


@pytest.mark.parametrize('saved_manifest', [None, {'original': 'different'}])
def test_gemma_adoption_refuses_unverified_or_changed_pair_before_engine_load(
        completed_version, monkeypatch, saved_manifest):
    repo, run_id, original, _ = completed_version
    run = repo.run(run_id)
    (Path(run['config']['base_model']) / 'config.json').write_text('{"model_type":"gemma4"}')
    report = run['report']
    if saved_manifest is not None:
        report['gemma_pair'] = saved_manifest
    with repo.store.connection() as db:
        db.execute('UPDATE training_runs SET report=? WHERE id=?', (json.dumps(report), run_id))
    monkeypatch.setattr(training_worker, 'verify_gemma_pair', lambda *args, **kwargs: {'original': 'current'}, raising=False)
    worker = training_worker.ActivationWorker(repo, original, run_id)
    failed, ready = [], []
    worker.failed.connect(failed.append)
    worker.ready.connect(ready.append)
    try:
        worker.run()
        assert failed and ('pair' in failed[0] or 'manifest' in failed[0])
        assert not ready and worker.engine is None
        assert repo.store.setting('engine') == dataclasses.asdict(original)
    finally:
        if worker.engine is not None:
            worker.engine.stop()


def test_gemma_matching_pair_adopts_and_rolls_back_without_losing_original_config(
        completed_version, monkeypatch):
    repo, run_id, original, adapter = completed_version
    run = repo.run(run_id)
    (Path(run['config']['base_model']) / 'config.json').write_text('{"model_type":"gemma4"}')
    report = dict(run['report'], gemma_pair={'original': 'verified'})
    with repo.store.connection() as db:
        db.execute('UPDATE training_runs SET report=? WHERE id=?', (json.dumps(report), run_id))
    monkeypatch.setattr(training_worker, 'verify_gemma_pair',
                        lambda *args, **kwargs: {'original': 'verified'}, raising=False)
    worker = training_worker.ActivationWorker(repo, original, run_id)
    failed, ready = [], []
    worker.failed.connect(failed.append)
    worker.ready.connect(ready.append)
    try:
        worker.run()
        assert not failed and len(ready) == 1
        assert ready[0].running and ready[0].config.lora_path == str(adapter)
        assert repo.store.setting('engine') == dataclasses.asdict(original)
    finally:
        if worker.engine is not None:
            worker.engine.stop()
    rollback = training_worker.ActivationWorker(repo, original,
                                                rollback=dataclasses.asdict(original))
    try:
        rollback.run()
        assert rollback.engine.running
        assert dataclasses.asdict(rollback.engine.config) == dataclasses.asdict(original)
        assert rollback.engine.config.lora_path == ''
    finally:
        if rollback.engine is not None:
            rollback.engine.stop()
