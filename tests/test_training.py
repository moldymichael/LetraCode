import json
from pathlib import Path

import pytest

from letracode.store import Store
from letracode.training import TrainingConfig, TrainingRepository


def config(tmp_path, **changes):
    python = tmp_path / 'python'
    python.touch()
    model = tmp_path / 'model'
    model.mkdir(exist_ok=True)
    (model / 'model.safetensors').touch()
    gguf = tmp_path / 'base.gguf'
    gguf.touch()
    values = {'python_executable': python, 'base_model': model, 'base_gguf': gguf}
    values.update(changes)
    return TrainingConfig(**values)


def repository(tmp_path):
    return TrainingRepository(Store(tmp_path / 'data'))


def approved_splits(repo):
    repo.save_example('Train prompt', 'Train response', approved=True)
    repo.save_example('Eval prompt', 'Eval response', split='eval', approved=True)


def test_config_requires_local_files_and_bounded_training_values(tmp_path):
    assert config(tmp_path).validate() is None
    for changes in ({'epochs': 0}, {'learning_rate': 0}, {'rank': 0}, {'max_length': 0},
                    {'batch_size': 0}, {'seed': -1}, {'device': 'remote'}):
        with pytest.raises(ValueError):
            config(tmp_path, **changes).validate()
    with pytest.raises(ValueError, match='safetensors'):
        empty = tmp_path / 'empty'
        empty.mkdir()
        config(tmp_path, base_model=empty).validate()
    with pytest.raises(ValueError, match='Python'):
        config(tmp_path, python_executable=tmp_path / 'missing-python').validate()


def test_config_snapshot_canonicalizes_paths_without_resolving_python_symlink(tmp_path, monkeypatch):
    real_python = tmp_path / 'real-python'
    real_python.touch()
    selected = tmp_path / 'venv-python'
    selected.symlink_to(real_python)
    model = tmp_path / 'model'
    model.mkdir()
    (model / 'model.safetensors').touch()
    alias = tmp_path / 'model-alias'
    alias.symlink_to(model, target_is_directory=True)
    monkeypatch.chdir(tmp_path)
    saved = TrainingConfig(python_executable='venv-python', base_model='model-alias').to_dict()
    assert saved['python_executable'] == str(selected.absolute())
    assert saved['base_model'] == str(model.resolve())
    with pytest.raises(ValueError, match='max_length'):
        TrainingConfig(python_executable=selected, base_model=model, max_length=7).validate()
    with pytest.raises(ValueError, match='learning_rate'):
        TrainingConfig(python_executable=selected, base_model=model, learning_rate=.11).validate()
    with pytest.raises(ValueError, match='device'):
        TrainingConfig(python_executable=selected, base_model=model, device='cuda:1').validate()


def test_save_reopen_and_review_invalidation(tmp_path):
    store = Store(tmp_path / 'data')
    repo = TrainingRepository(store)
    example = repo.save_example('  prompt  ', ' response ', approved=True, source='chat')
    assert example['prompt'] == 'prompt'
    assert example['response'] == 'response'
    assert example['approved'] is True
    unchanged = repo.save_example('prompt', 'response', approved=True, source='edited metadata',
                                  example_id=example['id'])
    assert unchanged['approved'] is True
    edited = repo.save_example('changed', 'response', approved=True, example_id=example['id'])
    assert edited['approved'] is False
    assert TrainingRepository(store).examples()[0]['prompt'] == 'changed'


def test_import_is_atomic_supports_messages_and_always_creates_drafts(tmp_path):
    repo = repository(tmp_path)
    incoming = tmp_path / 'examples.jsonl'
    incoming.write_text('\n'.join((
        json.dumps({'prompt': 'p1', 'response': 'r1', 'approved': True, 'split': 'eval'}),
        json.dumps({'messages': [
            {'role': 'user', 'content': 'p2'}, {'role': 'assistant', 'content': 'r2'}],
            'approved': True}),
    )), encoding='utf-8')
    imported = repo.import_jsonl(incoming)
    assert len(imported) == 2
    assert all(row['approved'] is False for row in imported)
    assert [row['split'] for row in imported] == ['eval', 'train']

    bad = tmp_path / 'bad.jsonl'
    bad.write_text('{"prompt":"kept","response":"no"}\n{"messages": []}\n', encoding='utf-8')
    before = repo.examples()
    with pytest.raises(ValueError, match='line 2'):
        repo.import_jsonl(bad)
    assert repo.examples() == before

    unknown = tmp_path / 'unknown.jsonl'
    unknown.write_text('{"prompt":"p","response":"r","unexpected":1}\n', encoding='utf-8')
    with pytest.raises(ValueError, match='unsupported fields'):
        repo.import_jsonl(unknown)


def test_export_retains_review_metadata(tmp_path):
    repo = repository(tmp_path)
    repo.save_example('p', 'r', split='eval', approved=True, source='manual')
    destination = tmp_path / 'out.jsonl'
    repo.export_jsonl(destination)
    row = json.loads(destination.read_text(encoding='utf-8'))
    assert row == {'prompt': 'p', 'response': 'r', 'split': 'eval',
                   'approved': True, 'source': 'manual'}


def test_create_run_rejects_missing_approval_and_normalized_prompt_leakage(tmp_path):
    repo = repository(tmp_path)
    repo.save_example('only train', 'r', approved=True)
    with pytest.raises(ValueError, match='evaluation'):
        repo.create_run(config(tmp_path))
    repo.save_example(' Same   prompt ', 'one', approved=True)
    repo.save_example('same prompt', 'two', split='eval', approved=True)
    with pytest.raises(ValueError, match='Duplicate prompt'):
        repo.create_run(config(tmp_path))


def test_create_run_rejects_same_split_duplicates_for_review(tmp_path):
    repo = repository(tmp_path)
    repo.save_example('Repeated prompt', 'same answer', approved=True)
    repo.save_example(' repeated   PROMPT ', 'same answer', approved=True)
    repo.save_example('eval', 'answer', split='eval', approved=True)
    with pytest.raises(ValueError, match='Duplicate prompt.*review'):
        repo.create_run(config(tmp_path))


def test_run_snapshots_are_immutable_and_persisted(tmp_path):
    store = Store(tmp_path / 'data')
    repo = TrainingRepository(store)
    approved_splits(repo)
    run = repo.create_run(config(tmp_path, epochs=2))
    directory = repo.run_directory(run['id'])
    assert directory.parent == store.directory / 'training' / 'runs'
    assert json.loads((directory / 'config.json').read_text())['epochs'] == 2
    assert json.loads((directory / 'train.jsonl').read_text())['prompt'] == 'Train prompt'
    repo.save_example('changed later', 'new response', approved=True,
                      example_id=repo.examples('train')[0]['id'])
    reopened = TrainingRepository(store).run(run['id'])
    assert reopened['examples']['train'][0]['prompt'] == 'Train prompt'
    assert reopened == repo.verify_run_snapshot(run['id'])


@pytest.mark.parametrize('filename,replacement', [
    ('config.json', '{}'),
    ('train.jsonl', '{"prompt":"Eval prompt","response":"Eval response"}\n'),
    ('eval.jsonl', '{"prompt":"draft","response":"not approved"}\n'),
])
def test_verify_run_snapshot_rejects_changed_files(tmp_path, filename, replacement):
    repo = repository(tmp_path)
    approved_splits(repo)
    run = repo.create_run(config(tmp_path))
    (repo.run_directory(run['id']) / filename).write_text(replacement, encoding='utf-8')
    with pytest.raises(ValueError, match='snapshot'):
        repo.verify_run_snapshot(run['id'])


def test_run_lifecycle_is_terminal_and_recovery_is_explicit(tmp_path):
    store = Store(tmp_path / 'data')
    repo = TrainingRepository(store)
    approved_splits(repo)
    queued = repo.create_run(config(tmp_path))
    first = repo.create_run(config(tmp_path))
    repo.update_run(first['id'], 'running')
    assert TrainingRepository(store).run(first['id'])['status'] == 'running'
    assert repo.mark_interrupted() == 2
    assert repo.run(queued['id'])['status'] == 'interrupted'
    assert repo.run(first['id'])['status'] == 'interrupted'
    with pytest.raises(ValueError, match='terminal'):
        repo.update_run(first['id'], 'running')

    second = repo.create_run(config(tmp_path))
    repo.update_run(second['id'], 'running')
    finished = repo.update_run(second['id'], 'succeeded', report={'candidate_loss': 1.2})
    assert finished['report']['candidate_loss'] == 1.2
    with pytest.raises(ValueError, match='terminal'):
        repo.update_run(second['id'], 'failed', error='late')


def test_qlora_configuration_requires_cuda_and_preserves_accumulation_snapshot(tmp_path):
    with pytest.raises(ValueError, match='QLoRA.*CUDA'):
        config(tmp_path, training_method='qlora').validate()
    selected = config(tmp_path, training_method='qlora', device='cuda',
                      gradient_accumulation_steps=4, gradient_checkpointing=True)
    snapshot = selected.to_dict()
    assert snapshot['training_method'] == 'qlora'
    assert snapshot['gradient_accumulation_steps'] == 4
    assert snapshot['gradient_checkpointing'] is True
    repo = repository(tmp_path)
    approved_splits(repo)
    run = repo.create_run(selected)
    assert repo.verify_run_snapshot(run['id'])['config'] == snapshot


@pytest.mark.parametrize('changes,field', [
    ({'training_method': 'int8'}, 'training_method'),
    ({'training_method': []}, 'training_method'),
    ({'gradient_accumulation_steps': 0}, 'gradient_accumulation_steps'),
    ({'gradient_accumulation_steps': 129}, 'gradient_accumulation_steps'),
    ({'gradient_accumulation_steps': True}, 'gradient_accumulation_steps'),
    ({'gradient_checkpointing': 'true'}, 'gradient_checkpointing'),
])
def test_new_training_options_reject_invalid_values(tmp_path, changes, field):
    with pytest.raises(ValueError, match=field):
        config(tmp_path, **changes).validate()


def test_legacy_training_configuration_keeps_full_precision_cpu_defaults(tmp_path):
    saved = config(tmp_path).to_dict()
    for key in ('training_method', 'gradient_accumulation_steps', 'gradient_checkpointing'):
        saved.pop(key, None)
    restored = TrainingConfig(**saved)
    assert restored.training_method == 'lora'
    assert restored.device == 'cpu'
    assert restored.gradient_accumulation_steps == 1
    assert restored.gradient_checkpointing is False


def structured_conversation(question='Question'):
    return [
        {'role': 'system', 'content': 'Be precise.'},
        {'role': 'user', 'content': question},
        {'role': 'assistant', 'content': 'Earlier context.', 'train': False},
        {'role': 'user', 'content': 'Use the earlier context.'},
        {'role': 'assistant', 'content': 'Reviewed answer.'},
    ]


def test_structured_save_roundtrip_and_content_split_target_tool_approval_invalidation(tmp_path):
    repo = repository(tmp_path)
    messages = structured_conversation()
    saved = repo.save_example(messages=messages, approved=True)
    assert saved['schema_version'] == 2
    assert saved['prompt'] == 'Question'
    assert saved['response'] == 'Reviewed answer.'
    assert TrainingRepository(repo.store).examples()[0]['messages'][2]['train'] is False
    unchanged = repo.save_example(messages=messages, approved=True, source='Review metadata',
                                  example_id=saved['id'])
    assert unchanged['approved'] is True
    messages[2]['train'] = True
    target_edit = repo.save_example(messages=messages, approved=True, example_id=saved['id'])
    assert target_edit['approved'] is False
    repo.save_example(messages=messages, approved=True, example_id=saved['id'])
    changed_split = repo.save_example(messages=messages, split='eval', approved=True, example_id=saved['id'])
    assert changed_split['approved'] is False
    repo.save_example(messages=messages, split='eval', approved=True, example_id=saved['id'])
    tools = [{'type': 'function', 'function': {'name': 'read', 'parameters': {'type': 'object'}}}]
    changed_tools = repo.save_example(messages=messages, tools=tools, split='eval', approved=True,
                                      example_id=saved['id'])
    assert changed_tools['approved'] is False
    messages[2]['content'] = 'Edited earlier context.'
    changed_context = repo.save_example(messages=messages, tools=tools, split='eval', approved=True,
                                        example_id=saved['id'])
    assert changed_context['approved'] is False


def test_structured_export_import_preserves_all_messages_and_revokes_approval(tmp_path):
    repo = repository(tmp_path)
    repo.save_example(messages=structured_conversation(), split='eval', approved=True)
    path = tmp_path / 'conversation.jsonl'
    repo.export_jsonl(path)
    exported = json.loads(path.read_text())
    assert exported['schema_version'] == 2
    assert len(exported['messages']) == 5
    assert exported['messages'][2]['train'] is False
    assert 'prompt' not in exported and 'response' not in exported
    imported = repository(tmp_path / 'copy').import_jsonl(path)[0]
    assert imported['messages'] == exported['messages']
    assert imported['tools'] == []
    assert imported['approved'] is False
    assert imported['split'] == 'eval'


def test_import_rejects_unknown_nested_fields_and_invalid_metadata_atomically(tmp_path):
    repo = repository(tmp_path)
    path = tmp_path / 'conversation.jsonl'
    for bad in ({'messages': structured_conversation(), 'approved': 'yes'},
                {'messages': structured_conversation(), 'split': []},
                {'messages': structured_conversation(), 'prompt': 'mixed'},
                {'messages': [{'role': 'user', 'content': 'p', 'weight': 1},
                              {'role': 'assistant', 'content': 'r'}]}):
        path.write_text(json.dumps({'prompt': 'good', 'response': 'valid'}) + '\n' + json.dumps(bad))
        with pytest.raises(ValueError, match='line 2'):
            repo.import_jsonl(path)
        assert repo.examples() == []


def test_existing_database_migrates_without_changing_legacy_frozen_run_bytes(tmp_path):
    store = Store(tmp_path / 'data')
    with store.connection() as db:
        db.execute('''CREATE TABLE training_examples (
            id TEXT PRIMARY KEY, prompt TEXT NOT NULL, response TEXT NOT NULL,
            split TEXT NOT NULL, approved INTEGER NOT NULL, source TEXT NOT NULL,
            created TEXT NOT NULL, updated TEXT NOT NULL)''')
        db.execute("INSERT INTO training_examples VALUES ('old','Old prompt','Old response','train',1,'chat','before','before')")
    repo = TrainingRepository(store)
    row = repo.examples()[0]
    assert row['messages'] == [{'role': 'user', 'content': 'Old prompt'},
                               {'role': 'assistant', 'content': 'Old response', 'train': True}]
    assert row['approved'] is True and row['created'] == 'before'
    repo.save_example('eval', 'answer', split='eval', approved=True)
    run = repo.create_run(config(tmp_path))
    directory = repo.run_directory(run['id'])
    before = {p.name: p.read_bytes() for p in directory.iterdir() if p.is_file()}
    assert before['train.jsonl'] == b'{"prompt": "Old prompt", "response": "Old response"}\n'
    assert 'messages' not in run['examples']['train'][0]
    reopened = TrainingRepository(store).verify_run_snapshot(run['id'])
    assert reopened['examples'] == run['examples']
    assert {p.name: p.read_bytes() for p in directory.iterdir() if p.is_file()} == before


def test_structured_snapshots_preserve_context_and_conditioning_deduplication(tmp_path):
    repo = repository(tmp_path)
    repo.save_example(messages=structured_conversation(), approved=True)
    different_context = structured_conversation()
    different_context[2]['content'] = 'Distinct earlier context.'
    repo.save_example(messages=different_context, split='eval', approved=True)
    run = repo.create_run(config(tmp_path))
    saved = json.loads((repo.run_directory(run['id']) / 'train.jsonl').read_text())
    assert saved['schema_version'] == 2 and len(saved['messages']) == 5
    assert saved['messages'][2]['train'] is False
    assert repo.verify_run_snapshot(run['id']) == run
    duplicate_context = structured_conversation()
    duplicate_context[-1]['content'] = 'Different target, same conditioning.'
    repo.save_example(messages=duplicate_context, split='eval', approved=True)
    with pytest.raises(ValueError, match='Duplicate'):
        repo.create_run(config(tmp_path))
