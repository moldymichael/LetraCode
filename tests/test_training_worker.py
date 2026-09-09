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
