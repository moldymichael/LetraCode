"""Preparation and comparison describe what actually happened to Strand."""
import dataclasses
import hashlib
import json
from pathlib import Path
import sys
import threading

import pytest

from letracode.engine import EngineConfig
from letracode.store import Store
from letracode.training import TrainingConfig, TrainingRepository
from test_training_models import model as gemma_model, converter as gemma_converter


def experience():
    try:
        from letracode import training_experience
    except ImportError:
        pytest.fail('The training preparation and comparison service is missing')
    return training_experience


def completed(tmp_path, evaluation=5):
    base = tmp_path / 'source'; base.mkdir()
    (base / 'config.json').write_text('{"model_type":"llama"}')
    (base / 'model.safetensors').write_bytes(b'original weights')
    gguf = tmp_path / 'base.gguf'; gguf.write_bytes(b'GGUF original model')
    repo = TrainingRepository(Store(tmp_path / 'data'))
    repo.save_example('Training only', 'Desired answer', approved=True)
    for n in range(evaluation):
        repo.save_example(f'Unseen question {n}', f'Expected {n}', 'eval', approved=True)
    run = repo.create_run(TrainingConfig(sys.executable, base, gguf))
    directory = repo.run_directory(run['id'])
    adapter = directory / 'adapter'; adapter.mkdir()
    (adapter / 'adapter_config.json').write_text('{}')
    (adapter / 'adapter_model.safetensors').write_bytes(b'trained weights')
    (directory / 'adapter.gguf').write_bytes(b'GGUF candidate')
    report = {'base_loss': 2.0, 'candidate_loss': 1.9,
              'base_gguf_sha256': hashlib.sha256(gguf.read_bytes()).hexdigest(),
              'adapter_gguf_sha256': hashlib.sha256((directory / 'adapter.gguf').read_bytes()).hexdigest(),
              'adapter_sha256': hashlib.sha256((adapter / 'adapter_model.safetensors').read_bytes()).hexdigest()}
    report['provenance'] = {'model_config_sha256': hashlib.sha256((base / 'config.json').read_bytes()).hexdigest(),
        'model_weight_manifest': {'model.safetensors': {'sha256': hashlib.sha256((base / 'model.safetensors').read_bytes()).hexdigest(), 'bytes': (base / 'model.safetensors').stat().st_size}},
        'tokenizer_manifest': {},
        'adapter_manifest': {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in adapter.iterdir()}}
    repo.update_run(run['id'], 'running')
    repo.update_run(run['id'], 'succeeded', report=report)
    original = EngineConfig(executable=sys.executable, model_path=str(gguf), max_tokens=777)
    repo.store.set_setting('engine', dataclasses.asdict(original))
    return repo, repo.run(run['id']), original


def test_readiness_failure_does_not_create_a_training_run(tmp_path):
    module = experience()
    repo = TrainingRepository(Store(tmp_path / 'data'))
    result = module.check_readiness(repo, TrainingConfig('/missing/python', '/missing/model'), threading.Event())
    assert result['ready'] is False
    assert result['checks'] and any('Python' in item['detail'] for item in result['checks'])
    assert repo.runs() == []
    assert repo.store.setting('engine') is None


def test_comparison_generates_every_held_out_answer_with_chat_budget_and_no_adoption(tmp_path, monkeypatch):
    module = experience()
    repo, run, original = completed(tmp_path)
    class Peer:
        def __init__(self, config, directory):
            self.config = config
        def start(self, cancel, status): pass
        def stop(self): pass
        def complete(self, messages, tools, cancel, delta, thinking=False):
            assert tools is None
            assert self.config.secondary_model_path == ''
            assert self.config.max_tokens == 777
            return {'role': 'assistant', 'content': ('candidate: ' if self.config.lora_path else 'current: ') + messages[-1]['content']}
    monkeypatch.setattr(module, 'LocalEngine', Peer)
    result = module.compare_version(repo, run['id'], original, threading.Event())
    assert result['status'] == 'complete'
    assert len(result['examples']) == 5
    assert result['examples'][4]['candidate_output'] == 'candidate: Unseen question 4'
    assert result['examples'][4]['current_output'] == 'current: Unseen question 4'
    assert result['examples'][4]['response'] == 'Expected 4'
    assert repo.store.setting('engine') == dataclasses.asdict(original)
    assert repo.store.setting('training_active_version') is None
    assert module.version_review(repo, run['id'])['comparison']['examples'] == result['examples']


def test_comparison_failure_preserves_partial_answers_and_cannot_claim_complete(tmp_path, monkeypatch):
    module = experience()
    repo, run, original = completed(tmp_path, 2)
    class Peer:
        def __init__(self, config, directory): self.config = config
        def start(self, cancel, status): pass
        def stop(self): pass
        def complete(self, messages, tools, cancel, delta, thinking=False):
            delta('partial response')
            if self.config.lora_path:
                raise RuntimeError('output token limit')
            return {'content': 'Complete current response'}
    monkeypatch.setattr(module, 'LocalEngine', Peer)
    result = module.compare_version(repo, run['id'], original, threading.Event())
    assert result['status'] == 'incomplete'
    assert result['examples'][0]['candidate_output'] == 'partial response'
    assert 'token limit' in result['examples'][0]['candidate_error']
    assert not module.comparison_is_current(repo, run['id'], original)


def test_changed_model_or_settings_invalidates_comparison(tmp_path, monkeypatch):
    module = experience()
    repo, run, original = completed(tmp_path, 1)
    class Peer:
        def __init__(self, config, directory): self.config = config
        def start(self, cancel, status): pass
        def stop(self): pass
        def complete(self, *args, **kwargs): return {'content': 'Response'}
    monkeypatch.setattr(module, 'LocalEngine', Peer)
    module.compare_version(repo, run['id'], original, threading.Event())
    assert module.comparison_is_current(repo, run['id'], original)
    assert not module.comparison_is_current(repo, run['id'], dataclasses.replace(original, max_tokens=900))
    Path(original.model_path).write_bytes(b'GGUF other weights')
    assert not module.comparison_is_current(repo, run['id'], original)


def test_conversion_retry_preserves_training_snapshot_and_verifies_output(tmp_path):
    module = experience()
    repo, run, original = completed(tmp_path, 1)
    directory = repo.run_directory(run['id'])
    (directory / 'adapter.gguf').unlink()
    checkout = tmp_path / 'converter'; checkout.mkdir()
    (checkout / 'convert_lora_to_gguf.py').write_text(
        'import pathlib,sys\np=pathlib.Path(sys.argv[sys.argv.index("--outfile")+1]);p.write_bytes(b"GGUF converted adapter")\n')
    before = (directory / 'config.json').read_bytes()
    result = module.retry_conversion(repo, run['id'], str(checkout), str(sys.executable), threading.Event())
    assert result['status'] == 'complete'
    assert result['adapter_gguf_sha256']
    assert (directory / 'config.json').read_bytes() == before
    assert repo.run(run['id'])['report'] == run['report']
    assert module.effective_report(repo, run['id'])['adapter_gguf_sha256'] == result['adapter_gguf_sha256']
    with pytest.raises(ValueError, match='already|preserved'):
        module.retry_conversion(repo, run['id'], str(checkout), str(sys.executable), threading.Event())


def test_review_and_stage_survive_reopening_without_altering_optimizer_report(tmp_path):
    module = experience()
    repo, run, _ = completed(tmp_path, 1)
    module.save_version_review(repo, run['id'], name='Clearer explanations', judgment='worse', notes='Lost key details.')
    reopened = TrainingRepository(Store(repo.store.directory))
    review = module.version_review(reopened, run['id'])
    assert review['name'] == 'Clearer explanations'
    assert review['judgment'] == 'worse'
    assert 'Compare' in module.version_stage(reopened, run['id'])
    assert reopened.run(run['id'])['report'] == run['report']


def test_backend_preflight_rejects_missing_runtime_without_optimization(tmp_path, monkeypatch):
    from letracode import training_backend
    import builtins
    repo, run, _ = completed(tmp_path, 1)
    directory = repo.run_directory(run['id'])
    assert callable(getattr(training_backend, 'check_training', None)), 'A tokenizer/runtime preflight is needed before starting optimization'
    real_import = builtins.__import__
    def without_torch(name, *args, **kwargs):
        if name == 'torch':
            raise ImportError('torch unavailable')
        return real_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', without_torch)
    before = (directory / 'adapter' / 'adapter_model.safetensors').read_bytes()
    with pytest.raises(RuntimeError, match='training Python'):
        training_backend.check_training(directory)
    assert (directory / 'adapter' / 'adapter_model.safetensors').read_bytes() == before
    assert not (directory / 'readiness.json').exists()


def test_improve_defaults_hide_infrastructure_and_failed_preflight_keeps_chat_loaded(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication
    from letracode.ui import MainWindow
    app = QApplication.instance() or QApplication([])
    window = MainWindow(Store(tmp_path / 'data'))
    panel = window.training_panel
    assert hasattr(panel, 'readiness'), 'Improve must show preparation status'
    assert not panel.advanced.isVisible()
    stopped = []
    monkeypatch.setattr(window.engine, 'stop', lambda: stopped.append(True))
    panel.review_check.setChecked(True)
    panel.start_training()
    import time
    deadline = time.monotonic() + 3
    while panel.job is not None and time.monotonic() < deadline:
        app.processEvents(); time.sleep(.01)
    assert panel.job is None
    assert stopped == []
    assert panel.repository.runs() == []
    assert 'Ready' not in panel.readiness.text().splitlines()[0]
    window.close()


def test_contextual_capture_keeps_background_in_reviewed_learning_input(tmp_path):
    from PySide6.QtWidgets import QApplication
    from letracode.ui import MainWindow
    app = QApplication.instance() or QApplication([])
    window = MainWindow(Store(tmp_path / 'data'))
    panel = window.training_panel
    assert callable(getattr(panel, 'capture_example', None)), 'Capture must retain the reviewed context'
    panel.capture_example('Make that clearer', 'A clear answer.', source='chat:fixture', context='Earlier request: explain the revision process.')
    assert 'Earlier request: explain the revision process.' in panel.prompt.toPlainText()
    assert 'Make that clearer' in panel.prompt.toPlainText()
    assert panel.repository.examples() == []
    panel.save_example()
    assert 'Earlier request' in panel.repository.examples()[0]['prompt']
    assert not panel.repository.examples()[0]['approved']
    window.close()


@pytest.mark.parametrize('changed', ['adapter_config', 'model_config', 'model_weights'])
def test_retry_conversion_rejects_changed_training_source_or_adapter_configuration(tmp_path, changed):
    module = experience()
    repo, run, _ = completed(tmp_path, 1)
    directory = repo.run_directory(run['id'])
    (directory / 'adapter.gguf').unlink()
    paths = {'adapter_config': directory / 'adapter' / 'adapter_config.json',
             'model_config': Path(run['config']['base_model']) / 'config.json',
             'model_weights': Path(run['config']['base_model']) / 'model.safetensors'}
    paths[changed].write_bytes(b'changed')
    checkout = tmp_path / 'converter'; checkout.mkdir()
    (checkout / 'convert_lora_to_gguf.py').write_text('raise AssertionError("must not launch")')
    with pytest.raises(ValueError, match='changed|provenance'):
        module.retry_conversion(repo, run['id'], str(checkout), sys.executable, threading.Event())
    assert not (directory / 'adapter.gguf').exists()


def test_preparation_stop_reaps_its_owned_process(tmp_path):
    module = experience()
    import time
    cancel = threading.Event()
    marker = tmp_path / 'started'
    script = tmp_path / 'wait.py'
    script.write_text('import pathlib,time\npathlib.Path("started").write_text("started")\ntime.sleep(60)\n')
    errors = []
    def run():
        try:
            module.run_local([sys.executable, str(script)], tmp_path, tmp_path / 'log', cancel)
        except Exception as error:
            errors.append(error)
    thread = threading.Thread(target=run); thread.start()
    try:
        deadline = time.monotonic() + 3
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(.01)
        assert marker.exists()
        cancel.set(); thread.join(3)
        assert not thread.is_alive()
        assert errors and isinstance(errors[0], module.Cancelled)
    finally:
        cancel.set(); thread.join(3)


@pytest.mark.usefixtures('python_engine_peer')
def test_compare_uses_real_managed_chat_transport_for_every_question(tmp_path):
    module = experience()
    import importlib.util
    spec = importlib.util.spec_from_file_location('training_comparison_peer', Path(__file__).with_name('test_engine.py'))
    peer = importlib.util.module_from_spec(spec); spec.loader.exec_module(peer)
    repo, run, original = completed(tmp_path, 4)
    binary = tmp_path / 'llama-server'
    binary.write_text(peer.PEER_SOURCE); binary.chmod(0o700)
    original.executable = str(binary)
    result = module.compare_version(repo, run['id'], original, threading.Event())
    assert result['status'] == 'complete'
    records = json.loads(binary.with_suffix('.requests.json').read_text())
    completions = [r for r in records if r['path'] == '/v1/chat/completions']
    assert len(completions) == 8
    assert all(r['body']['max_tokens'] == 777 for r in completions)
    assert all('tools' not in r['body'] for r in completions)
    assert all(row['current_output'] == 'ok' and row['candidate_output'] == 'ok' for row in result['examples'])


def test_managed_preparation_creates_verified_pair_and_discovers_it_without_changing_strand(tmp_path, gemma_model, gemma_converter):
    module = experience()
    repo = TrainingRepository(Store(tmp_path / 'data'))
    original = {'model_path': '/existing/strand.gguf'}
    repo.store.set_setting('engine', original)
    config = TrainingConfig(sys.executable, gemma_model, llama_cpp_dir=gemma_converter)
    result = module.prepare_matching_model(repo, config, threading.Event())
    output = Path(result['base_gguf'])
    assert output.is_file() and output.is_relative_to(repo.store.directory / 'training' / 'models')
    manifest = module.verify_gemma_pair(gemma_model, output)
    assert manifest['source']['base_model'] == str(gemma_model.resolve())
    discovered = module.discover_configuration(repo.store, EngineConfig(model_path=str(output)))
    assert discovered['base_model'] == str(gemma_model.resolve())
    assert discovered['llama_cpp_dir'] == str(gemma_converter.resolve())
    assert repo.store.setting('engine') == original
    assert repo.runs() == []


def test_preflight_uses_reviewed_snapshot_without_creating_run_or_outputs(tmp_path, monkeypatch):
    module = experience()
    repo, run, _ = completed(tmp_path, 2)
    script = tmp_path / 'preflight-peer.py'
    script.write_text('''import json,pathlib,sys,os
assert '--check-only' in sys.argv
assert os.environ['HF_HUB_OFFLINE'] == '1'
assert 'HF_TOKEN' not in os.environ
p = pathlib.Path(sys.argv[-1])
assert len((p/'eval.jsonl').read_text().splitlines()) == 2
assert len((p/'train.jsonl').read_text().splitlines()) == 1
assert not (p/'adapter').exists()
(p/'readiness.json').write_text(json.dumps({'ready': True, 'summary': 'Every example fits'}))
''')
    monkeypatch.setattr(module, 'BACKEND_SCRIPT', script)
    monkeypatch.setenv('HF_TOKEN', 'never-forward-this-test-token')
    result = module.check_readiness(repo, TrainingConfig(**run['config']), threading.Event())
    assert result['ready']
    assert result['checks'][-1]['detail'] == 'Every example fits'
    assert len(repo.runs()) == 1
    assert not list(repo.store.directory.glob('.training-check-*'))


def test_examples_changed_after_preflight_require_recheck_before_unloading(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication
    from letracode.ui import MainWindow
    module = experience()
    app = QApplication.instance() or QApplication([])
    repo, run, _ = completed(tmp_path, 1)
    repo.store.set_setting('training_config', run['config'])
    window = MainWindow(repo.store)
    panel = window.training_panel
    assert hasattr(module, 'examples_fingerprint'), 'Readiness must be tied to the reviewed dataset'
    panel._readiness_result = {'ready': True, 'configuration': dataclasses.asdict(panel.configuration()),
                              'examples_fingerprint': module.examples_fingerprint(repo)}
    repo.save_example('Changed dataset', 'Another answer', approved=True)
    stopped = []
    monkeypatch.setattr(window.engine, 'stop', lambda: stopped.append(True))
    monkeypatch.setattr(panel, 'error', lambda error: panel.progress.setText(str(error)))
    panel.begin_training()
    assert stopped == []
    assert len(repo.runs()) == 1
    assert 'changed' in panel.progress.text().lower()
    window.close()


def test_missing_converted_file_exposes_retry_and_hides_adoption_until_recovered(tmp_path):
    from PySide6.QtWidgets import QApplication
    from letracode.ui import MainWindow
    module = experience()
    app = QApplication.instance() or QApplication([])
    repo, run, _ = completed(tmp_path, 1)
    window = MainWindow(repo.store)
    panel = window.training_panel
    panel.run_id = run['id']
    panel.refresh_runs()
    assert not panel.convert_button.isEnabled()
    assert panel.compare_button.isEnabled()
    (repo.run_directory(run['id']) / 'adapter.gguf').unlink()
    panel.refresh_runs()
    assert panel.convert_button.isEnabled()
    assert not panel.compare_button.isEnabled()
    assert not panel.adopt_button.isEnabled()
    assert 'missing' in module.version_stage(repo, run['id']).lower()
    assert 'Retry conversion' in panel.results.toPlainText()
    repo.store.set_setting('training_active_version', run['id'])
    panel.refresh_runs()
    assert not panel.convert_button.isEnabled()
    assert 'restore' in panel.stage.text().lower()
    window.close()
