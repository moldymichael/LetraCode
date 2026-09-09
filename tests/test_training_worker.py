import dataclasses
import json
from pathlib import Path
import sys
import threading
import time

import pytest

from letracode.store import Store


def prepared_run(tmp_path):
    from letracode.training import TrainingConfig, TrainingRepository
    base = tmp_path / 'base'
    base.mkdir()
    (base / 'config.json').write_text('{"model_type":"llama"}')
    (base / 'model.safetensors').write_bytes(b'fixture')
    repo = TrainingRepository(Store(tmp_path / 'data'))
    repo.save_example('Training question', 'Reviewed answer', approved=True)
    repo.save_example('Held out question', 'Expected answer', split='eval', approved=True)
    config = TrainingConfig(python_executable=sys.executable, base_model=str(base))
    return repo, repo.create_run(config)


def backend_peer(tmp_path, monkeypatch, source):
    from letracode import training_worker
    script = tmp_path / 'backend peer.py'
    script.write_text(source)
    monkeypatch.setattr(training_worker, 'BACKEND_SCRIPT', script)


SUCCESS_BACKEND = '''
import hashlib, json, os, pathlib, sys
p = pathlib.Path(sys.argv[-1])
assert os.environ['HF_HUB_OFFLINE'] == '1'
assert os.environ['TRANSFORMERS_OFFLINE'] == '1'
assert 'HF_TOKEN' not in os.environ
print(json.dumps({'message': 'Training fixture completed'}), flush=True)
(p/'adapter').mkdir()
(p/'adapter'/'adapter_model.safetensors').write_bytes(b'updated weights')
(p/'adapter'/'adapter_config.json').write_text('{}')
report = {'base_loss': 2.0, 'candidate_loss': 1.5,
 'eval_examples': [{'prompt':'Held out question','response':'Expected answer', 'base_output':'old', 'candidate_output':'new'}],
 'eval_response_tokens': 3, 'optimization_steps': 1,
 'adapter_path': str(p/'adapter'), 'adapter_gguf': '', 'conversion_error': 'Not configured',
 'package_versions': {'torch':'fixture', 'transformers':'fixture', 'peft':'fixture', 'safetensors':'fixture'},
 'provenance': {key: hashlib.sha256((p/name).read_bytes()).hexdigest() for key,name in
 [('config_sha256','config.json'),('train_sha256','train.jsonl'),('eval_sha256','eval.jsonl')]}}
(p/'report.json').write_text(json.dumps(report))
'''


def test_training_process_saves_verified_report_and_offline_environment(tmp_path, monkeypatch):
    from letracode.training_worker import TrainingWorker
    repo, run = prepared_run(tmp_path)
    backend_peer(tmp_path, monkeypatch, SUCCESS_BACKEND)
    monkeypatch.setenv('HF_TOKEN', 'private-test-token')
    worker = TrainingWorker(repo, run['id'])
    worker.run()
    saved = repo.run(run['id'])
    assert saved['status'] == 'succeeded'
    assert saved['report']['candidate_loss'] == 1.5
    assert saved['report']['adapter_sha256']
    assert repo.store.setting('engine') is None


@pytest.mark.parametrize('filename', ['config.json', 'train.jsonl', 'eval.jsonl'])
def test_changed_snapshot_is_refused_before_trainer_launch(tmp_path, monkeypatch, filename):
    from letracode.training_worker import TrainingWorker
    repo, run = prepared_run(tmp_path)
    directory = repo.run_directory(run['id'])
    (directory / filename).write_text('{}')
    backend_peer(tmp_path, monkeypatch, 'from pathlib import Path\nPath("launched").touch()')
    TrainingWorker(repo, run['id']).run()
    assert repo.run(run['id'])['status'] == 'failed'
    assert 'snapshot' in repo.run(run['id'])['error']
    assert not (directory / 'launched').exists()


def test_snapshot_changed_during_training_cannot_be_accepted(tmp_path, monkeypatch):
    from letracode.training_worker import TrainingWorker
    repo, run = prepared_run(tmp_path)
    backend_peer(tmp_path, monkeypatch, SUCCESS_BACKEND + '\n(p/"train.jsonl").write_text("changed")\n')
    TrainingWorker(repo, run['id']).run()
    saved = repo.run(run['id'])
    assert saved['status'] == 'failed'
    assert 'snapshot' in saved['error']


@pytest.mark.parametrize('change', [
    "report['optimization_steps'] = 0",
    "report['eval_response_tokens'] = 0",
    "report['eval_examples'][0].pop('candidate_output')",
    "report['eval_examples'][0]['response'] = 'unapproved answer'",
    "report['provenance']['train_sha256'] = 'wrong dataset'",
    "report['package_versions'] = {}",
    "report['conversion_error'] = ''",
])
def test_incomplete_or_unrelated_report_is_not_accepted(tmp_path, monkeypatch, change):
    from letracode.training_worker import TrainingWorker
    repo, run = prepared_run(tmp_path)
    backend_peer(tmp_path, monkeypatch, SUCCESS_BACKEND + '\n' + change + '\n(p/"report.json").write_text(json.dumps(report))\n')
    TrainingWorker(repo, run['id']).run()
    saved = repo.run(run['id'])
    assert saved['status'] == 'failed'
    assert saved['error']


@pytest.mark.parametrize('source', [
    'raise SystemExit(7)',
    'print("process exited but produced no model")',
])
def test_failed_or_incomplete_backend_is_not_success(tmp_path, monkeypatch, source):
    from letracode.training_worker import TrainingWorker
    repo, run = prepared_run(tmp_path)
    backend_peer(tmp_path, monkeypatch, source)
    TrainingWorker(repo, run['id']).run()
    saved = repo.run(run['id'])
    assert saved['status'] == 'failed'
    assert saved['error']


def test_cancel_stops_owned_training_process_and_keeps_run(tmp_path, monkeypatch):
    from letracode.training_worker import TrainingWorker
    repo, run = prepared_run(tmp_path)
    backend_peer(tmp_path, monkeypatch, 'import time\nprint("started", flush=True)\ntime.sleep(60)')
    worker = TrainingWorker(repo, run['id'])
    thread = threading.Thread(target=worker.run)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while worker.process is None and time.monotonic() < deadline:
            time.sleep(.01)
        assert worker.process is not None
        worker.cancel()
        thread.join(5)
        assert not thread.is_alive()
        assert worker.process is None
        assert repo.run(run['id'])['status'] == 'cancelled'
        assert (repo.run_directory(run['id']) / 'train.jsonl').is_file()
    finally:
        worker.cancel()
        thread.join(5)


def test_exited_trainer_cannot_leave_worker_waiting_for_descendant_output(tmp_path, monkeypatch):
    from letracode.training_worker import TrainingWorker
    repo, run = prepared_run(tmp_path)
    backend_peer(tmp_path, monkeypatch, '''
import subprocess, sys
subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
print('trainer failed after starting helper', flush=True)
raise SystemExit(7)
''')
    worker = TrainingWorker(repo, run['id'])
    thread = threading.Thread(target=worker.run)
    thread.start()
    try:
        thread.join(3)
        assert not thread.is_alive(), 'worker must reap descendants after the trainer exits'
        assert repo.run(run['id'])['status'] == 'failed'
        assert 'code 7' in repo.run(run['id'])['error']
    finally:
        worker.cancel()
        thread.join(5)


def test_training_records_are_included_in_readable_backup(tmp_path):
    import zipfile
    repo, run = prepared_run(tmp_path)
    target = tmp_path / 'backup.zip'
    repo.store.backup(target)
    with zipfile.ZipFile(target) as archive:
        data = json.loads(archive.read('letracode.json'))
        assert len(data['training_examples']) == 2
        assert data['training_runs'][0]['id'] == run['id']
        assert 'training' in archive.read('RESTORE.txt').decode().lower()


def qlora_report():
    return {
        'training_details': {'training_method': 'qlora', 'quantization': 'nf4-double',
                             'compute_dtype': 'float16', 'target_modules': 'all-linear',
                             'gradient_checkpointing': True, 'gradient_accumulation_steps': 4,
                             'effective_batch_size': 8, 'device': 'cuda',
                             'quantized_layer_count': 7, 'device_name': 'Test GPU',
                             'base_frozen': True, 'trainable_parameter_count': 100,
                             'total_parameter_count': 1000},
        'memory': {'base_model_bytes': 1000, 'peak_allocated_bytes': 2000,
                   'peak_reserved_bytes': 3000},
        'package_versions': {'torch': '2', 'transformers': '5', 'peft': '1',
                             'safetensors': '1', 'bitsandbytes': '1', 'accelerate': '1'},
    }


def qlora_config():
    return {'training_method': 'qlora', 'device': 'cuda', 'batch_size': 2,
            'gradient_accumulation_steps': 4, 'gradient_checkpointing': True}


def test_report_validation_accepts_quantized_training_and_legacy_reports():
    from letracode.training_worker import validate_training_report
    validate_training_report(qlora_report(), qlora_config())
    validate_training_report({'package_versions': {}}, {'batch_size': 1, 'device': 'cpu'})


@pytest.mark.parametrize('change', [
    lambda r: r.pop('training_details'),
    lambda r: r['training_details'].update(training_method='lora'),
    lambda r: r['training_details'].update(quantization='none'),
    lambda r: r['training_details'].update(compute_dtype='float32'),
    lambda r: r['training_details'].update(target_modules=['q_proj', 'v_proj']),
    lambda r: r['training_details'].update(gradient_checkpointing=False),
    lambda r: r['training_details'].update(gradient_accumulation_steps=1),
    lambda r: r['training_details'].update(effective_batch_size=2),
    lambda r: r['training_details'].update(device='cpu'),
    lambda r: r['training_details'].update(quantized_layer_count=0),
    lambda r: r['training_details'].update(base_frozen=False),
    lambda r: r['training_details'].update(trainable_parameter_count=1000),
    lambda r: r['memory'].update(peak_allocated_bytes=-1),
    lambda r: r['memory'].update(peak_reserved_bytes=1000),
    lambda r: r['package_versions'].pop('bitsandbytes'),
    lambda r: r['package_versions'].pop('accelerate'),
])
def test_report_cannot_claim_qlora_without_matching_runtime_evidence(change):
    from letracode.training_worker import validate_training_report
    report = qlora_report()
    change(report)
    with pytest.raises(ValueError):
        validate_training_report(report, qlora_config())


@pytest.mark.parametrize('reported_steps,expected_status', [(2, 'succeeded'), (3, 'failed')])
def test_worker_checks_optimizer_steps_after_accumulation_and_last_partial_group(tmp_path, monkeypatch, reported_steps, expected_status):
    from letracode.training import TrainingConfig
    from letracode.training_worker import TrainingWorker
    repo, original = prepared_run(tmp_path)
    repo.save_example('Second training question', 'Reviewed answer', approved=True)
    repo.save_example('Third training question', 'Reviewed answer', approved=True)
    config = TrainingConfig(**dict(original['config'], training_method='qlora', device='cuda',
                                  gradient_accumulation_steps=2, gradient_checkpointing=True))
    run = repo.create_run(config)
    details = qlora_report()
    details['training_details'].update(gradient_accumulation_steps=2, effective_batch_size=2)
    details['optimization_steps'] = reported_steps
    source = SUCCESS_BACKEND + '\nreport.update(' + repr(details) + ')\n(p/"report.json").write_text(json.dumps(report))\n'
    backend_peer(tmp_path, monkeypatch, source)
    TrainingWorker(repo, run['id']).run()
    assert repo.run(run['id'])['status'] == expected_status
