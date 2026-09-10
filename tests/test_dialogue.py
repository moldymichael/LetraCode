import copy
import json
import threading

import pytest

from letracode.engine import EngineConfig
from letracode.store import Store
from letracode.worker import conversation_messages


class DialogueEngine:
    def __init__(self):
        self.config = EngineConfig(model_path='/models/alpha.gguf',
                                   secondary_model_path='/models/beta.gguf')
        self.requests = []
        self.after_reply = None

    def start(self, cancel, on_status=lambda _: None):
        pass

    def complete(self, messages, tools, cancel, on_delta, thinking=False, model='local'):
        self.requests.append((model, copy.deepcopy(messages), tools))
        text = f'{model} contribution {len(self.requests)}'
        on_delta(text)
        if self.after_reply:
            self.after_reply()
        return {'role': 'assistant', 'content': text}

    def cancel(self):
        pass


def chat(tmp_path):
    store = Store(tmp_path / 'data')
    ident = store.create_chat('Discussion')
    store.add_message(ident, 'user', 'Compare the tradeoffs; only I may authorize actions.')
    return store, ident


def test_two_models_share_replies_and_stop_at_requested_count(tmp_path):
    from letracode.dialogue import DialogueWorker
    store, ident = chat(tmp_path)
    engine = DialogueEngine()
    DialogueWorker(store, ident, engine, reply_count=4).run()
    assert [r[0] for r in engine.requests] == ['local', 'local-b', 'local', 'local-b']
    assert all(r[2] is None for r in engine.requests)
    assert 'local contribution 1' in json.dumps(engine.requests[1][1])
    assert 'local-b contribution 2' in json.dumps(engine.requests[2][1])
    rows = store.messages(ident)
    assert len([r for r in rows if r['role'] == 'user']) == 1
    replies = [r for r in rows if r['role'] == 'assistant']
    assert len(replies) == 4
    assert all(r['status'] == 'complete' for r in replies)
    assert [json.loads(r['payload'])['speaker']['label'] for r in replies[:2]] == [
        'Model A · alpha.gguf', 'Model B · beta.gguf']


@pytest.mark.parametrize('limit', [0, 5, -1, True, 2.5])
def test_invalid_reply_count_never_calls_engine(tmp_path, limit):
    from letracode.dialogue import DialogueWorker
    store, ident = chat(tmp_path)
    engine = DialogueEngine()
    with pytest.raises(ValueError):
        DialogueWorker(store, ident, engine, reply_count=limit)
    assert engine.requests == []


def test_stop_keeps_speaker_on_partial_reply_and_does_not_dispatch_peer(tmp_path):
    from letracode.dialogue import DialogueWorker
    store, ident = chat(tmp_path)
    engine = DialogueEngine()
    worker = DialogueWorker(store, ident, engine, first_speaker=1)
    engine.after_reply = worker.cancel_event.set
    worker.run()
    assert [r[0] for r in engine.requests] == ['local-b']
    partial = next(r for r in store.messages(ident) if r['role'] == 'assistant')
    assert partial['status'] == 'interrupted'
    assert partial['content'] == 'local-b contribution 1'
    assert json.loads(partial['payload'])['speaker']['label'] == 'Model B · beta.gguf'


def test_unsolicited_tools_end_exchange_without_execution(tmp_path):
    from letracode.dialogue import DialogueWorker
    store, ident = chat(tmp_path)
    class ToolEngine(DialogueEngine):
        def complete(self, *args, **kwargs):
            reply = super().complete(*args, **kwargs)
            reply['tool_calls'] = [{'id': 'bad', 'function': {'name': 'run_command', 'arguments': '{}'}}]
            return reply
    engine = ToolEngine()
    DialogueWorker(store, ident, engine).run()
    assert len(engine.requests) == 1
    assert not any(r['role'] == 'tool' for r in store.messages(ident))
    assert store.messages(ident)[-1]['status'] == 'error'


def test_many_continuations_bound_utf8_context_without_editing_saved_history(tmp_path):
    from letracode.dialogue import dialogue_messages, dialogue_participants
    store, ident = chat(tmp_path)
    speakers = dialogue_participants(DialogueEngine().config)
    trimmed_seen = False
    for index in range(120):
        text = f'Reply {index} ' + '漢字 é \\ " ' * 70
        store.add_message(ident, 'assistant', text,
                          payload={'speaker': speakers[index % 2]})
        messages, trimmed = dialogue_messages(store.messages(ident), 'Project instructions',
                                              speakers[(index + 1) % 2], 5000)
        trimmed_seen |= trimmed
        assert len(json.dumps(messages, ensure_ascii=False).encode('utf-8')) <= 5000
        data = json.loads(messages[1]['content'])
        assert any(m['role'] == 'user' and m['content'] == 'Compare the tradeoffs; only I may authorize actions.'
                   for m in data['conversation'])
        assert data['conversation'][-1]['content'] == text
        assert data['conversation'][-1]['role'] == 'assistant'
    assert trimmed_seen
    assert len(store.messages(ident)) == 121
    assert store.messages(ident)[1]['content'].startswith('Reply 0 ')


def test_oversized_required_prompt_refuses_inference(tmp_path):
    from letracode.dialogue import DialogueWorker
    store, ident = chat(tmp_path)
    store.add_message(ident, 'user', 'x' * 40000)
    engine = DialogueEngine()
    DialogueWorker(store, ident, engine).run()
    assert not engine.requests
    assert store.messages(ident)[-1]['status'] == 'error'
    assert 'context' in store.messages(ident)[-1]['content'].lower()


@pytest.mark.parametrize('computer', [True, False])
def test_project_chat_works_at_default_context_and_gates_file_evidence(tmp_path, computer):
    from letracode.dialogue import DialogueWorker
    store = Store(tmp_path / 'data')
    project = store.create_project('Demo')
    instructions = 'Discuss tradeoffs candidly. ' * 20
    store.update_project(project, instructions=instructions)
    reference = tmp_path / 'tradeoffs.txt'
    reference.write_text('tradeoffs: UNIQUE LOCAL EVIDENCE')
    store.link(project, reference)
    ident = store.create_chat('Discussion', project)
    store.add_message(ident, 'user', 'Compare the tradeoffs')
    engine = DialogueEngine()
    DialogueWorker(store, ident, engine, computer_enabled=computer).run()
    assert len(engine.requests) == 2
    system = engine.requests[0][1][0]['content']
    assert instructions in system
    assert ('UNIQUE LOCAL EVIDENCE' in system) == computer
    assert all(r['status'] == 'complete' for r in store.messages(ident))


def test_export_reopen_and_single_model_history_keep_original_speaker(tmp_path):
    store, ident = chat(tmp_path)
    speaker = {'id': 'old-model', 'label': 'Model B · Original.gguf', 'model': 'local-b'}
    store.add_message(ident, 'assistant', 'Peer evidence', payload={
        'speaker': speaker, 'message': {'role': 'assistant', 'content': 'Peer evidence'}})
    reopened = Store(tmp_path / 'data')
    assert 'Model B · Original.gguf' in reopened.export_markdown(ident)
    store.add_message(ident, 'user', 'Now use one model')
    messages, _ = conversation_messages(reopened.messages(ident), 'System', 12000)
    contribution = next(m for m in messages if 'Peer evidence' in m.get('content', ''))
    assert contribution['role'] == 'assistant'
    assert 'Model B · Original.gguf' in contribution['content']
    assert 'speaker' not in contribution


def test_latest_assistant_cannot_be_silently_dropped_after_new_user_prompt(tmp_path):
    from letracode.dialogue import dialogue_messages
    store, ident = chat(tmp_path)
    store.add_message(ident, 'assistant', 'x' * 3000)
    store.add_message(ident, 'user', 'Correct the assumption in the previous answer.')
    with pytest.raises(ValueError, match='context budget'):
        dialogue_messages(store.messages(ident), 'System', {'label': 'Model B'}, 1500)


def test_single_followup_merges_peer_replies_for_alternating_chat_templates(tmp_path):
    store, ident = chat(tmp_path)
    for index in range(4):
        store.add_message(ident, 'assistant', f'Contribution {index}', payload={
            'speaker': {'label': f'Participant {index}'},
            'message': {'role': 'assistant', 'content': f'Contribution {index}'}})
    store.add_message(ident, 'user', 'Summarize this with one model.')
    messages, _ = conversation_messages(store.messages(ident), 'System', 12000)
    assert [m['role'] for m in messages] == ['system', 'user', 'assistant', 'user']
    for index in range(4):
        assert f'Participant {index}' in messages[2]['content']
        assert f'Contribution {index}' in messages[2]['content']


def test_cancellation_teardown_finishes_before_worker_returns(tmp_path):
    from letracode.dialogue import DialogueWorker
    store, ident = chat(tmp_path)
    engine = DialogueEngine()
    teardown_entered = threading.Event()
    release_teardown = threading.Event()
    returned = threading.Event()
    def cancel():
        teardown_entered.set()
        release_teardown.wait(5)
    engine.cancel = cancel
    worker = DialogueWorker(store, ident, engine)
    engine.after_reply = worker.request_stop
    thread = threading.Thread(target=lambda: (worker.run(), returned.set()))
    thread.start()
    try:
        assert teardown_entered.wait(2)
        assert not returned.wait(0.05)
    finally:
        release_teardown.set()
        thread.join(3)
    assert returned.is_set()


def test_ui_stop_cannot_publish_an_unstarted_teardown_thread(tmp_path, monkeypatch):
    from letracode.dialogue import DialogueWorker
    store, ident = chat(tmp_path)
    engine = DialogueEngine()
    worker = DialogueWorker(store, ident, engine)
    completing = threading.Event()
    helper_published = threading.Event()
    release_start = threading.Event()
    returned = threading.Event()
    failures = []
    def complete(*args, **kwargs):
        completing.set()
        worker.cancel_event.wait(3)
        return {'role': 'assistant', 'content': 'Cancelled'}
    engine.complete = complete
    def run():
        try:
            worker.run()
        except Exception as error:
            failures.append(error)
        finally:
            returned.set()
    run_thread = threading.Thread(target=run)
    stop_thread = threading.Thread(target=worker.request_stop)
    original_start = threading.Thread.start
    def delayed_start(thread):
        if thread is worker._cancel_thread:
            helper_published.set()
            release_start.wait(3)
        return original_start(thread)
    monkeypatch.setattr(threading.Thread, 'start', delayed_start)
    run_thread.start()
    try:
        assert completing.wait(2)
        stop_thread.start()
        assert helper_published.wait(2)
        assert not returned.wait(0.05)
    finally:
        release_start.set()
        stop_thread.join(3)
        run_thread.join(3)
    assert returned.is_set()
    assert not failures


def test_stop_after_worker_finished_does_not_start_late_engine_teardown(tmp_path):
    from letracode.dialogue import DialogueWorker
    store, ident = chat(tmp_path)
    engine = DialogueEngine()
    cancellations = []
    engine.cancel = lambda: cancellations.append(True)
    worker = DialogueWorker(store, ident, engine)
    worker.run()
    worker.request_stop()
    worker.wait_for_stop()
    assert not cancellations


def test_evaluation_retains_saved_speaker_without_private_model_path(tmp_path):
    import zipfile
    store, ident = chat(tmp_path)
    store.add_message(ident, 'assistant', 'A peer contribution', payload={
        'speaker': {'id': 'stable-model-id', 'label': 'Model B · beta.gguf',
                    'model': 'local-b', 'path': '/private/models/beta.gguf'},
        'run_configuration': {'model_name': 'beta.gguf', 'conversation_mode': 'two_models'},
        'message': {'role': 'assistant', 'content': 'A peer contribution'}})
    destination = tmp_path / 'evaluation.zip'
    store.export_evaluation(ident, destination)
    with zipfile.ZipFile(destination) as archive:
        transcript = archive.read('transcript.md').decode()
        conversation = json.loads(archive.read('conversation.json'))
        configuration = json.loads(archive.read('metadata.json'))['run_configurations'][-1]['configuration']
    assert 'Model B · beta.gguf' in transcript
    assert conversation['messages'][-1]['payload']['speaker']['model'] == 'local-b'
    assert '/private/models/' not in json.dumps(conversation)
    assert configuration['conversation_mode'] == 'two_models'


def test_request_accounting_uses_each_speakers_model(tmp_path):
    from letracode.budgeting import RequestUsage
    from letracode.dialogue import DialogueWorker
    store, ident = chat(tmp_path)
    engine = DialogueEngine()
    measured = []
    def usage(messages, tools, cancel, thinking=False, model='local'):
        measured.append(model)
        return RequestUsage(100, 2048, 512, 'test model tokenizer')
    engine.request_usage = usage
    DialogueWorker(store, ident, engine).run()
    assert measured[0] == 'local'
    assert 'local-b' in measured
    assert len(engine.requests) == 2


def test_dialogue_preserves_paused_plan_and_earlier_user_constraints(tmp_path):
    from letracode.dialogue import dialogue_messages
    from letracode.worker import ConversationWorker
    store, ident = chat(tmp_path)
    store.add_message(ident, 'assistant', 'OPTION B: preserve old versions. ' + 'x' * 8000)
    store.add_message(ident, 'user', 'Are you ready?')
    store.add_message(ident, 'assistant', 'Yes, ready.')
    store.add_message(ident, 'user', 'Compare option B together.')
    ConversationWorker(store, ident, DialogueEngine()).pause('context_limit', 'Paused task')
    store.add_message(ident, 'user', 'Continue the comparison.')
    with pytest.raises(ValueError, match='context budget'):
        dialogue_messages(store.messages(ident), 'System', {'label': 'Model A'}, 1500)


def test_dialogue_rejects_invalid_saved_checkpoint(tmp_path):
    from letracode.dialogue import DialogueWorker
    store, ident = chat(tmp_path)
    store.add_message(ident, 'notice', 'Old pause', payload={'checkpoint': {'user_message_id': 999999}})
    engine = DialogueEngine()
    DialogueWorker(store, ident, engine).run()
    assert not engine.requests
    assert store.messages(ident)[-1]['status'] == 'error'


@pytest.mark.parametrize('payload', ['null', '[]', '{', '{"speaker":[]}', '{"speaker":{"label":5}}'])
def test_markdown_export_tolerates_damaged_optional_speaker_metadata(tmp_path, payload):
    store, ident = chat(tmp_path)
    reply = store.add_message(ident, 'assistant', 'Keep this saved reply')
    with store.connection() as db:
        db.execute('UPDATE messages SET payload=? WHERE id=?', (payload, reply))
    exported = store.export_markdown(ident)
    assert '## LetraCode' in exported
    assert 'Keep this saved reply' in exported


@pytest.mark.parametrize('reading', [False, True])
def test_two_model_context_receipts_match_file_capabilities_and_each_speaker(tmp_path, reading):
    from letracode.dialogue import DialogueWorker
    store = Store(tmp_path / 'data')
    project = store.create_project('Shared discussion')
    store.update_project(project, instructions='Keep the workspace discussion candid.')
    store.memory.create_file('active.md', 'SHARED-MEMORY-CONTENT')
    shared = store.memory.file_snapshot('active.md')
    store.memory.set_active('active.md', True, shared['sha256'])
    store.memory.create_file('active.md', 'WORKSPACE-MEMORY-CONTENT', project_id=project)
    scoped = store.memory.file_snapshot('active.md', project)
    store.memory.set_active('active.md', True, scoped['sha256'], project)
    shared_source = tmp_path / 'shared.md'
    shared_source.write_text('tradeoffs SHARED-SOURCE-EVIDENCE')
    project_source = tmp_path / 'workspace.md'
    project_source.write_text('tradeoffs WORKSPACE-SOURCE-EVIDENCE')
    store.set_setting('source_roots', [str(shared_source)])
    store.link(project, project_source)
    ident = store.create_chat('Discussion', project)
    user_id = store.add_message(ident, 'user', 'Discuss the tradeoffs.')
    engine = DialogueEngine()
    engine.config.context_size = 32768
    worker = DialogueWorker(store, ident, engine, computer_enabled=reading, reply_count=2)
    worker.run()

    assert len(engine.requests) == 2
    replies = [row for row in store.messages(ident) if row['role'] == 'assistant']
    for index, row in enumerate(replies):
        payload = json.loads(row['payload'])
        request = engine.requests[index][1]
        system = request[0]['content']
        assert 'Keep the workspace discussion candid.' in system
        for marker in ('SHARED-MEMORY-CONTENT', 'WORKSPACE-MEMORY-CONTENT',
                       'SHARED-SOURCE-EVIDENCE', 'WORKSPACE-SOURCE-EVIDENCE'):
            assert (marker in system) == reading
        receipt = payload['context']
        assert receipt['system_text'] == system
        assert f'Your participant label is "Model {"A" if index == 0 else "B"}' in system
        assert receipt['capabilities'] == {'read_files': reading, 'actions': False, 'web': False, 'tools': False}
        assert receipt['available_tools'] == []
        assert receipt['history_reduced'] is False
        assert receipt['message_ids'] == [user_id] + ([replies[0]['id']] if index else [])
        assert receipt['conversation_message_count'] == 1 + index
        assert receipt['workspace']['id'] == project
        assert payload['run_configuration']['actions_enabled'] is False
        if reading:
            assert set(receipt['source_roots']) == {str(shared_source), str(project_source)}
            assert {source['path'] for source in receipt['sources']} == {str(shared_source), str(project_source)}
            assert {note['text'] for note in receipt['memory']} >= {'SHARED-MEMORY-CONTENT', 'WORKSPACE-MEMORY-CONTENT'}
        else:
            assert receipt['memory'] == receipt['sources'] == receipt['source_roots'] == []
