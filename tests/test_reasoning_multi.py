"""Preserve adapters and thinking when fine tuning meets two-model exchanges."""
import configparser
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import sys
import threading

import pytest

from letracode.dialogue import DialogueWorker
from letracode.engine import Cancelled, EngineError
from letracode.store import Store
from letracode.training import TrainingConfig, TrainingRepository
from letracode.training_worker import ActivationWorker
from test_dialogue import DialogueEngine, chat
from test_multimodel_engine import router

pytestmark = pytest.mark.usefixtures('python_engine_peer')


def read_preset(peer):
    ini = configparser.ConfigParser(interpolation=None, inline_comment_prefixes=('#', ';'))
    ini.read_string(peer.with_suffix('.preset').read_text().removeprefix('version = 1\n'))
    return ini


def test_router_applies_adapter_only_to_primary_model(router, tmp_path):
    engine, peer, first, second = router
    adapter = tmp_path / 'adapter café.gguf'
    adapter.write_bytes(b'GGUF' + bytes(64))
    engine.config.lora_path = str(adapter)
    engine.start(threading.Event())
    argv = json.loads(peer.with_suffix('.args.json').read_text())
    assert '--lora' not in argv, 'A global adapter would also alter Model B'
    ini = read_preset(peer)
    assert ini['local']['model'] == str(first)
    assert ini['local'].get('lora') == str(adapter)
    assert ini['local-b']['model'] == str(second)
    assert 'lora' not in ini['local-b']
    assert engine.loaded_models == ('local', 'local-b')


@pytest.mark.parametrize('filename', ['adapter#extra.gguf', 'adapter;extra.gguf', 'adapter\nextra.gguf', 'adapter.gguf '])
def test_router_rejects_adapter_paths_that_preset_would_change(router, tmp_path, filename):
    if os.name == 'nt' and ('\n' in filename or filename.endswith(' ')):
        pytest.skip('This path is not representable on Windows')
    engine, peer, _, _ = router
    adapter = tmp_path / filename
    adapter.write_bytes(b'GGUF')
    engine.config.lora_path = str(adapter)
    with pytest.raises(EngineError, match='preset|path'):
        engine.start(threading.Event())
    assert not peer.with_suffix('.args.json').exists()
    assert not engine.running


class ThinkingDialogueEngine(DialogueEngine):
    def __init__(self, outcome='complete'):
        super().__init__()
        self.outcome = outcome
        self.config.lora_path = '/models/adapter.gguf'

    def complete(self, messages, tools, cancel, on_delta, thinking=False, model='local', *, on_reasoning=None):
        self.requests.append((model, messages, tools))
        if on_reasoning:
            on_reasoning(model + ' first thought. ')
            on_reasoning('Last thought.')
        if self.outcome == 'interrupted':
            cancel.set()
            raise Cancelled()
        if self.outcome == 'error':
            raise EngineError('Failed after thinking')
        on_delta(model + ' answer')
        return {'role': 'assistant', 'content': model + ' answer',
                'reasoning_content': model + ' first thought. Last thought.'}


@pytest.mark.parametrize('outcome', ['complete', 'interrupted', 'error'])
def test_dialogue_retains_each_speakers_thinking_and_primary_adapter_metadata(tmp_path, outcome):
    store, ident = chat(tmp_path)
    engine = ThinkingDialogueEngine(outcome)
    store.set_setting('engine', asdict(engine.config))
    store.set_setting('training_active_version', 'trained-v1')
    worker = DialogueWorker(store, ident, engine, thinking=True, reply_count=2,
                            computer_enabled=False)
    snapshots = []
    worker.changed.connect(lambda: snapshots.extend(store.messages(ident)))
    worker.run()
    rows = [r for r in Store(tmp_path / 'data').messages(ident) if r['role'] == 'assistant']
    assert len(rows) == (2 if outcome == 'complete' else 1)
    for row, model in zip(rows, ['local', 'local-b']):
        payload = json.loads(row['payload'])
        assert payload.get('reasoning') == model + ' first thought. Last thought.'
        assert payload['speaker']['model'] == model
        assert row['status'] == outcome
        assert 'thought' not in row['content']
        config = payload['run_configuration']
        assert config['thinking'] is True
        assert config['conversation_mode'] == 'two_models'
        if model == 'local':
            assert config['adapter_name'] == 'adapter.gguf'
            assert config['training_version'] == 'trained-v1'
        else:
            assert 'adapter_name' not in config
            assert 'training_version' not in config
        if outcome == 'complete':
            assert payload['message'] == {'role': 'assistant', 'content': model + ' answer'}
    assert any(json.loads(r['payload']).get('reasoning') == 'local first thought. '
               for r in snapshots if r['role'] == 'assistant' and r['status'] == 'streaming')
    if outcome == 'complete':
        assert 'thought' not in json.dumps(engine.requests[1][1])


def test_router_streams_reasoning_from_selected_model(router):
    engine, peer, _, _ = router
    peer.write_text(peer.read_text().replace("{'content': body['model'] + ' response'}",
        "{'content': body['model'] + ' response', 'reasoning_content': body['model'] + ' thought'}"))
    answers, thoughts = [], []
    reply = engine.complete([{'role': 'user', 'content': 'Hello'}], None, threading.Event(),
                            answers.append, True, model='local-b', on_reasoning=thoughts.append)
    assert ''.join(answers) == 'local-b response'
    assert ''.join(thoughts) == 'local-b thought'
    assert reply['reasoning_content'] == 'local-b thought'


def test_adoption_keeps_secondary_model_and_loads_primary_adapter(router, tmp_path):
    initial, peer, primary, secondary = router
    weights = tmp_path / 'training weights'
    weights.mkdir()
    (weights / 'config.json').write_text('{"model_type":"llama"}')
    (weights / 'model.safetensors').write_bytes(b'training fixture')
    repo = TrainingRepository(Store(tmp_path / 'training-data'))
    repo.save_example('Question', 'Reviewed answer', approved=True)
    repo.save_example('Held-out question', 'Expected answer', split='eval', approved=True)
    run = repo.create_run(TrainingConfig(python_executable=sys.executable,
                                       base_model=str(weights), base_gguf=str(primary)))
    adapter = repo.run_directory(run['id']) / 'adapter.gguf'
    adapter.write_bytes(b'GGUF' + bytes(64))
    repo.update_run(run['id'], 'running')
    repo.update_run(run['id'], 'succeeded', report={
        'base_gguf_sha256': hashlib.sha256(primary.read_bytes()).hexdigest(),
        'adapter_gguf_sha256': hashlib.sha256(adapter.read_bytes()).hexdigest()})
    worker = ActivationWorker(repo, initial.config, run_id=run['id'])
    ready, failed = [], []
    worker.ready.connect(ready.append)
    worker.failed.connect(failed.append)
    try:
        worker.run()
        assert not failed
        assert len(ready) == 1 and ready[0].loaded_models == ('local', 'local-b')
        assert ready[0].config.secondary_model_path == str(secondary)
        assert ready[0].config.lora_path == str(adapter)
        ini = read_preset(peer)
        assert ini['local'].get('lora') == str(adapter)
        assert 'lora' not in ini['local-b']
        assert initial.config.lora_path == ''
    finally:
        if worker.engine is not None:
            worker.engine.stop()
