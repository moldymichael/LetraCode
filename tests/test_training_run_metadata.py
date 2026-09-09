"""Saved evaluations identify the adapter used without exporting its location."""
from dataclasses import asdict
import json
import zipfile

import pytest

from letracode.engine import EngineConfig
from letracode.store import Store
from letracode.worker import ConversationWorker
from test_worker import ScriptedEngine


class AnswerEngine(ScriptedEngine):
    def complete(self, messages, tools, cancel, on_delta, thinking=False):
        on_delta('Saved answer.')
        return {'role': 'assistant', 'content': 'Saved answer.'}


@pytest.mark.parametrize(('adapted', 'matching', 'version'), [
    (True, True, 'run-abc123'), (True, False, None), (False, True, None),
])
def test_saved_chat_and_export_identify_the_actual_adapter(tmp_path, adapted, matching, version):
    store = Store(tmp_path / 'data')
    chat = store.create_chat('Compare model versions')
    store.add_message(chat, 'user', 'Answer briefly')
    config = EngineConfig(model_path=str(tmp_path / 'private' / 'base.gguf'),
                          lora_path=str(tmp_path / 'private' / 'adapter.gguf') if adapted else '')
    saved_config = asdict(config)
    if not matching:
        saved_config['lora_path'] = str(tmp_path / 'other.gguf')
    store.set_setting('engine', saved_config)
    store.set_setting('training_active_version', 'run-abc123')
    engine = AnswerEngine()
    engine.config = config
    worker = ConversationWorker(store, chat, engine, use_tools=False,
                                web_enabled=False, computer_enabled=False)
    worker.run()
    row = next(row for row in store.messages(chat) if row['role'] == 'assistant')
    assert row['status'] == 'complete'
    recorded = json.loads(row['payload'])['run_configuration']
    if adapted:
        assert recorded['adapter_name'] == 'adapter.gguf'
    else:
        assert 'adapter_name' not in recorded
    assert recorded.get('training_version') == version
    destination = tmp_path / 'evaluation.zip'
    store.export_evaluation(chat, destination)
    with zipfile.ZipFile(destination) as archive:
        metadata = json.loads(archive.read('metadata.json'))
        text = archive.read('conversation.json').decode()
    exported = metadata['run_configurations'][0]['configuration']
    assert exported == recorded
    assert str(tmp_path / 'private') not in text


@pytest.mark.parametrize('adapter', ['/home/person/private/adapter.gguf', r'C:\Users\person\private\adapter.gguf'])
def test_evaluation_scrubs_adapter_paths_in_older_saved_configuration(tmp_path, adapter):
    store = Store(tmp_path / 'data')
    chat = store.create_chat('Older adapter run')
    store.add_message(chat, 'assistant', 'Answer', payload={'run_configuration': {
        'adapter_name': adapter, 'training_version': 'version-42'}})
    destination = tmp_path / 'evaluation.zip'
    store.export_evaluation(chat, destination)
    with zipfile.ZipFile(destination) as archive:
        metadata = json.loads(archive.read('metadata.json'))
    assert metadata['run_configurations'][0]['configuration'] == {
        'adapter_name': 'adapter.gguf', 'training_version': 'version-42'}
