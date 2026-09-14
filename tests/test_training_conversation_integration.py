"""Cross-layer conversation checks exercise storage, preparation and review boundaries."""
import copy
import json
import threading

import pytest

from letracode.training import TrainingRepository
from letracode.store import Store
from test_training_experience import completed
from test_training_models import model as gemma_model
from test_training_worker import prepared_run, backend_peer, SUCCESS_BACKEND


def conversation(question='Check the file'):
    return {
        'schema_version': 2,
        'tools': [{'type': 'function', 'function': {'name': 'read_file',
            'description': 'Read a saved file', 'parameters': {'type': 'object',
            'properties': {'path': {'type': 'string'}}, 'required': ['path']}}}],
        'messages': [
            {'role': 'user', 'content': question},
            {'role': 'assistant', 'content': '', 'train': False, 'tool_calls': [
                {'id': 'call_a', 'type': 'function', 'function': {'name': 'read_file',
                    'arguments': {'path': 'example.txt'}}}]},
            {'role': 'tool', 'content': 'Recorded file text', 'tool_call_id': 'call_a'},
            {'role': 'assistant', 'content': 'The recorded text is available.', 'train': True},
            {'role': 'user', 'content': 'What comes next?'},
            {'role': 'assistant', 'content': 'Ask a follow-up question.', 'train': True},
        ],
    }


def structured_run(repo, run):
    example = conversation()
    repo.save_example(messages=example['messages'], tools=example['tools'], split='eval', approved=True)
    # Use the production snapshot path with an existing valid local configuration.
    from letracode.training import TrainingConfig
    new = repo.create_run(TrainingConfig(**run['config']))
    directory = repo.run_directory(new['id'])
    for path in (repo.run_directory(run['id']) / 'adapter').iterdir():
        (directory / 'adapter').mkdir(exist_ok=True)
        (directory / 'adapter' / path.name).write_bytes(path.read_bytes())
    (directory / 'adapter.gguf').write_bytes((repo.run_directory(run['id']) / 'adapter.gguf').read_bytes())
    repo.update_run(new['id'], 'running')
    repo.update_run(new['id'], 'succeeded', report=run['report'])
    return repo.run(new['id'])


def test_comparison_preserves_recorded_context_and_accepts_calls_without_execution(tmp_path, monkeypatch):
    from letracode import training_experience as module
    repo, old, original = completed(tmp_path, evaluation=1)
    run = structured_run(repo, old)
    requests = []
    class Peer:
        def __init__(self, config, directory): self.config = config
        def start(self, cancel, status): pass
        def stop(self): pass
        def complete(self, messages, tools, cancel, delta, **kwargs):
            requests.append((copy.deepcopy(messages), copy.deepcopy(tools)))
            if tools:
                assert messages[-1] == {'role': 'tool', 'content': 'Recorded file text', 'tool_call_id': 'call_a'}
                assert messages[0]['content'] == 'Check the file'
                assert messages[1]['tool_calls'][0]['function']['arguments'] == '{"path": "example.txt"}'
                return {'role': 'assistant', 'content': '', 'tool_calls': [
                    {'id': 'new_call', 'type': 'function', 'function': {'name': 'read_file', 'arguments': '{"path":"next.txt"}'}}]}
            return {'role': 'assistant', 'content': 'A normal answer'}
    monkeypatch.setattr(module, 'LocalEngine', Peer)
    result = module.compare_version(repo, run['id'], original, threading.Event())
    assert result['status'] == 'complete'
    assert len(requests) == 4  # One generation per target/version; no tool dispatch/continuation.
    row = result['examples'][1]
    assert row['messages'][-1]['role'] == 'tool'
    assert row['tools'] == conversation()['tools']
    assert row['response'] == 'The recorded text is available.'
    assert row['candidate_tool_calls'][0]['id'] == 'new_call'
    assert 'read_file' in row['candidate_output']
    assert module.comparison_is_current(repo, run['id'], original)
    tampered = copy.deepcopy(result)
    tampered['examples'][1]['messages'][-1]['content'] = 'Changed context'
    module.save_version_review(repo, run['id'], comparison=tampered)
    assert not module.comparison_is_current(repo, run['id'], original)
    tampered.pop('schema_version')
    tampered['examples'][1]['candidate_status'] = 'incomplete'
    module.save_version_review(repo, run['id'], comparison=tampered)
    assert not module.comparison_is_current(repo, run['id'], original)


def test_preparation_allows_shared_opening_with_distinct_recorded_context(tmp_path):
    from letracode.training_experience import approved_examples
    repo = TrainingRepository(Store(tmp_path / 'data'))
    one = conversation()
    two = copy.deepcopy(one)
    two['messages'][2]['content'] = 'Another recorded result'
    repo.save_example(messages=one['messages'], tools=one['tools'], approved=True)
    repo.save_example(messages=two['messages'], tools=two['tools'], split='eval', approved=True)
    assert len(approved_examples(repo)['eval']) == 1


@pytest.mark.parametrize('tamper', [False, True])
def test_worker_checks_complete_structured_report_against_frozen_example(tmp_path, monkeypatch, tamper):
    from letracode.training_worker import TrainingWorker
    repo, old = prepared_run(tmp_path)
    # Replace legacy evaluation with a structured one before creating the run.
    for row in repo.examples('eval'):
        repo.delete_example(row['id'])
    sample = conversation()
    repo.save_example(messages=sample['messages'], tools=sample['tools'], split='eval', approved=True)
    from letracode.training import TrainingConfig
    run = repo.create_run(TrainingConfig(**old['config']))
    extension = '''
row = json.loads((p/'eval.jsonl').read_text())
report['eval_examples'] = [dict(row, prompt='Check the file', response='The recorded text is available.', base_output='old', candidate_output='new')]
'''
    if tamper:
        extension += "report['eval_examples'][0]['messages'][2]['content'] = 'Unreviewed tool result'\n"
    extension += "(p/'report.json').write_text(json.dumps(report))\n"
    backend_peer(tmp_path, monkeypatch, SUCCESS_BACKEND + extension)
    TrainingWorker(repo, run['id']).run()
    saved = repo.run(run['id'])
    assert saved['status'] == ('failed' if tamper else 'succeeded'), saved['error']
    if tamper:
        assert 'held-out' in saved['error']


@pytest.mark.parametrize("folder", ["chat_templates", "additional_chat_templates"])
def test_conversion_retry_verifies_named_native_tool_template(tmp_path, folder):
    from letracode.training_experience import verify_conversion_source
    from letracode.training_worker import file_hash
    repo, run, _ = completed(tmp_path, evaluation=1)
    from pathlib import Path
    directory = Path(run['config']['base_model']) / folder
    directory.mkdir()
    template = directory / 'tool_use.jinja'
    template.write_text('Original native tool template')
    run['report']['provenance']['tokenizer_manifest'][folder + '/tool_use.jinja'] = file_hash(template)
    verify_conversion_source(run, repo.run_directory(run['id']), threading.Event())
    template.write_text('Changed native tool template')
    with pytest.raises(ValueError, match='changed'):
        verify_conversion_source(run, repo.run_directory(run['id']), threading.Event())


@pytest.mark.parametrize('folder', ['chat_templates', 'additional_chat_templates'])
def test_gemma_pair_and_backend_agree_on_named_template_provenance(gemma_model, folder):
    from letracode.training_worker import validate_gemma_source
    from letracode.training_models import _source_snapshot
    base = gemma_model
    (base / folder).mkdir()
    (base / folder / 'tool_use.jinja').write_text('Named native tool template')
    source = _source_snapshot(base)
    source['files'] = {key.replace('\\', '/'): value for key, value in source['files'].items()}
    name = folder + '/tool_use.jinja'
    assert name in source['files']
    selected = {key: value['sha256'] for key, value in source['files'].items()
                if key.startswith('tokenizer') or key == 'chat_template.jinja' or key == name}
    report = {'provenance': {'model_config_sha256': source['files']['config.json']['sha256'],
                            'model_weight_manifest': source['weights'], 'tokenizer_manifest': selected}}
    validate_gemma_source(report, {'source': source})
    source['files'] = {key.replace('/', '\\'): value for key, value in source['files'].items()}
    validate_gemma_source(report, {'source': source})
