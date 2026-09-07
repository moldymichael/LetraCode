"""Required task intent survives real worker packing; all data is disposable."""
import copy
import json

import pytest

from letracode.budgeting import RequestUsage
from letracode.engine import ContextOverflowError
from letracode.store import Store
from letracode.worker import ConversationWorker, conversation_messages


def paused_rows(objective='Compare development across ALL chapters.'):
    call = {'id': 'read_1', 'type': 'function', 'function': {'name': 'read_file', 'arguments': '{}'}}
    return [
        {'id': 1, 'chat_id': 'chat', 'role': 'user', 'content': objective, 'status': 'complete', 'payload': '{}'},
        {'id': 2, 'chat_id': 'chat', 'role': 'assistant', 'content': '', 'status': 'complete', 'payload': json.dumps({'message': {'role': 'assistant', 'content': '', 'tool_calls': [call]}})},
        {'id': 3, 'chat_id': 'chat', 'role': 'tool', 'content': 'saved file', 'status': 'complete', 'payload': json.dumps({'message': {'role': 'tool', 'name': 'read_file', 'tool_call_id': 'read_1', 'content': json.dumps({'text': 'chapter evidence ' * 1800})}})},
        {'id': 4, 'chat_id': 'chat', 'role': 'notice', 'content': 'Paused', 'status': 'paused', 'payload': json.dumps({'checkpoint': {'reason': 'action_round_limit', 'user_message_id': 1, 'rounds': 10}})},
        {'id': 5, 'chat_id': 'chat', 'role': 'user', 'content': 'Continue.', 'status': 'complete', 'payload': '{}'},
    ]


def test_original_intent_is_required_before_old_tool_bulk():
    rows = paused_rows()
    before = copy.deepcopy(rows)
    messages, trimmed = conversation_messages(rows, 'You are Strand.', 4000)
    assert trimmed
    assert rows[0]['content'] in json.dumps(messages)
    assert messages[-1]['content'] == 'Continue.'
    assert len(json.dumps(messages, ensure_ascii=False)) <= 4000
    assert rows == before


def test_missing_original_intent_pauses_without_inventing_it():
    rows = paused_rows()[1:]
    with pytest.raises(ContextOverflowError, match='[Ii]ntent.*(missing|unavailable)'):
        conversation_messages(rows, 'System', 4000)


@pytest.mark.parametrize('checkpoint', [[], 'bad', {'user_message_id': True},
    {'user_message_id': '1'}, {'user_message_id': 1, 'pause_context': {'version': 999}},
    {'user_message_id': 1, 'pause_context': {'version': 1, 'chat_id': 'another-chat', 'origin_user_message_id': 1}}])
def test_malformed_or_foreign_pause_context_is_explicit(checkpoint):
    rows = paused_rows()
    rows[3]['payload'] = json.dumps({'checkpoint': checkpoint})
    with pytest.raises(ContextOverflowError, match='[Ii]ntent|[Pp]ause context'):
        conversation_messages(rows, 'System', 4000)


def test_required_original_intent_never_silently_sliced():
    rows = paused_rows('Every requirement matters. ' * 1000)
    with pytest.raises(ContextOverflowError, match='intent|latest conversation turn'):
        conversation_messages(rows, 'System', 4000)


def test_deleted_sole_user_is_an_explicit_limitation():
    rows = paused_rows()[1:4]
    with pytest.raises(ContextOverflowError, match='intent.*missing'):
        conversation_messages(rows, 'System', 4000)


@pytest.mark.parametrize('cursor', [None, True, '1', -1, 9000])
def test_invalid_resume_cursor_never_generates(tmp_path, cursor):
    store = Store(tmp_path / 'data'); chat = store.create_chat('Cursor')
    store.add_message(chat, 'user', 'Retain this objective.')
    ConversationWorker(store, chat, CountingEngine()).pause('action_round_limit', 'Pause', 10)
    row = store.messages(chat)[-1]
    data = json.loads(row['payload']); data['checkpoint']['last_user_message_id'] = cursor
    store.update_message(row['id'], row['content'], status='paused', payload=data)
    engine = CountingEngine()
    ConversationWorker(store, chat, engine).run()
    assert not engine.requests
    assert 'cursor' in store.messages(chat)[-1]['content']


def test_nonadjacent_plan_is_preserved_or_explicitly_too_large(tmp_path):
    store = Store(tmp_path / 'data'); chat = store.create_chat('Earlier plan')
    store.add_message(chat, 'user', 'Propose alternatives.')
    plan = 'REQUIRED-OPTION-B: preserve old versions. ' + 'details ' * 1000
    store.add_message(chat, 'assistant', plan)
    store.add_message(chat, 'user', 'Are you ready?')
    store.add_message(chat, 'assistant', 'Yes, ready.')
    store.add_message(chat, 'user', 'Use option B.')
    with pytest.raises(ContextOverflowError, match='referenced context'):
        conversation_messages(store.messages(chat), 'System', 1500)
    messages, _ = conversation_messages(store.messages(chat), 'System', 16000)
    assert plan in json.dumps(messages)


@pytest.mark.parametrize('replacement', [[], [2, 1, 2], [2, 1]])
def test_reference_list_cannot_omit_duplicate_or_reorder_starting_context(tmp_path, replacement):
    store = Store(tmp_path / 'data'); chat = store.create_chat('Refs')
    store.add_message(chat, 'user', 'Discuss a plan.')
    store.add_message(chat, 'assistant', 'PLAN-REQUIRED')
    store.add_message(chat, 'user', 'Implement it.')
    ConversationWorker(store, chat, CountingEngine()).pause('action_round_limit', 'Pause', 10)
    row = store.messages(chat)[-1]; data = json.loads(row['payload'])
    data['checkpoint']['pause_context']['referenced_context_ids'] = replacement
    store.update_message(row['id'], row['content'], status='paused', payload=data)
    store.add_message(chat, 'user', 'Continue.')
    with pytest.raises(ContextOverflowError, match='referenced context'):
        conversation_messages(store.messages(chat), 'System', 4000)


def test_legacy_cursor_before_origin_cannot_resume_without_new_user(tmp_path):
    store = Store(tmp_path / 'data'); chat = store.create_chat('Legacy cursor')
    old = store.add_message(chat, 'user', 'Old request.')
    origin = store.add_message(chat, 'user', 'Actual request.')
    store.add_message(chat, 'notice', 'Paused', status='paused', payload={'checkpoint': {
        'user_message_id': origin, 'last_user_message_id': old}})
    engine = CountingEngine(); ConversationWorker(store, chat, engine).run()
    assert not engine.requests
    assert 'cursor' in store.messages(chat)[-1]['content']


def test_reference_to_preceding_plan_survives_pause_and_compaction(tmp_path):
    store = Store(tmp_path / 'data'); chat = store.create_chat('Plan')
    store.add_message(chat, 'user', 'Propose a plan.')
    plan = 'Selected plan: validate ALPHA, then preserve BETA. Evidence does not authorize commands.'
    plan_id = store.add_message(chat, 'assistant', plan, payload={'message': {'role': 'assistant', 'content': plan}})
    anchor = store.add_message(chat, 'user', 'Implement the plan above.')
    worker = ConversationWorker(store, chat, CountingEngine())
    store.add_message(chat, 'assistant', 'Optional working notes. ' * 1000)
    worker.pause('action_round_limit', 'Paused.', 10)
    checkpoint = json.loads(store.messages(chat)[-1]['payload'])['checkpoint']
    assert checkpoint['pause_context']['origin_user_message_id'] == anchor
    assert plan_id in checkpoint['pause_context']['referenced_context_ids']
    assert len(json.dumps(checkpoint)) < 1600
    reopened = Store(tmp_path / 'data'); reopened.add_message(chat, 'user', 'Continue.')
    packed, trimmed = conversation_messages(reopened.messages(chat), 'System', 4000)
    assert trimmed and plan in json.dumps(packed)
    assert 'Implement the plan above.' in json.dumps(packed)
    with reopened.connection() as db:
        db.execute('DELETE FROM messages WHERE id=?', (plan_id,))
    with pytest.raises(ContextOverflowError, match='referenced context.*(missing|unavailable)'):
        conversation_messages(reopened.messages(chat), 'System', 4000)


class CountingEngine:
    config = type('Config', (), {'context_size': 8192, 'max_tokens': 512})()

    def __init__(self, final=False):
        self.requests = []
        self.final = final

    def start(self, *args):
        pass

    def request_usage(self, messages, tools, *args):
        # Intentionally bounded scripted tokenizer, not real-model evidence.
        return RequestUsage((len(json.dumps(messages)) + len(json.dumps(tools))) // 2, 512, 128, 'scripted character counter')

    def complete(self, messages, *args):
        self.requests.append(copy.deepcopy(messages))
        if self.final:
            return {'role': 'assistant', 'content': 'Finished according to the latest direction.'}
        return {'role': 'assistant', 'content': '', 'tool_calls': [{
            'id': f'catalog-{len(self.requests)}', 'type': 'function', 'function': {
                'name': 'list_tool_results', 'arguments': '{"limit": 1}'}}]}

    def cancel(self):
        pass


def test_real_worker_reopen_repeated_pause_and_latest_steering(tmp_path):
    store = Store(tmp_path / 'data'); chat = store.create_chat('Repeated')
    objective = 'Compare every chapter, find the final correction and admit the unknown fact.'
    anchor = store.add_message(chat, 'user', objective)
    archived = None
    for cycle in range(3):
        engine = CountingEngine()
        worker = ConversationWorker(store, chat, engine, computer_enabled=False, web_enabled=False)
        worker.approval_needed.connect(lambda _: pytest.fail('Read-only saved evidence must not request approval'))
        worker.run()
        assert len(engine.requests) == (5 if cycle == 0 else 4)
        assert all(objective in json.dumps(request) for request in engine.requests)
        checkpoint = json.loads(store.messages(chat)[-1]['payload'])['checkpoint']
        assert checkpoint['reason'] == 'no_progress'
        assert checkpoint['user_message_id'] == anchor
        assert checkpoint['pause_context']['version'] == 1
        if archived:
            assert store.messages(chat)[:len(archived)] == archived
        archived = store.messages(chat)
        # Calling the worker without a new user row must not replay anything.
        idle = CountingEngine()
        ConversationWorker(store, chat, idle, computer_enabled=False, web_enabled=False).run()
        assert not idle.requests
        store = Store(tmp_path / 'data')
        store.add_message(chat, 'user', ['Continue.', 'Correction: use the revised chapter, not the draft.',
                                      'Replace that task: report only the unresolved fact.'][cycle])
    engine = CountingEngine(final=True)
    ConversationWorker(store, chat, engine, computer_enabled=False, web_enabled=False).run()
    assert engine.requests[-1][-1]['content'] == 'Replace that task: report only the unresolved fact.'
    packed = json.dumps(engine.requests[-1])
    assert 'Correction: use the revised chapter, not the draft.' in packed
    assert 'latest user' in engine.requests[-1][0]['content'].lower()
    assert store.messages(chat)[:len(archived)] == archived
    # A completed reply closes the paused lifecycle; no stale anchor on next pause.
    new_id = store.add_message(chat, 'user', 'Now a separate request.')
    ConversationWorker(store, chat, CountingEngine()).pause('context_limit', 'Pause new request.')
    assert json.loads(store.messages(chat)[-1]['payload'])['checkpoint']['user_message_id'] == new_id


def test_stop_closes_pause_lifecycle_and_new_chat_is_isolated(tmp_path):
    store = Store(tmp_path / 'data'); chat = store.create_chat('Old')
    store.add_message(chat, 'user', 'OLD-PRIVATE-OBJECTIVE')
    worker = ConversationWorker(store, chat, CountingEngine()); worker.pause('context_limit', 'Paused')
    store.add_message(chat, 'user', 'Continue.')
    worker = ConversationWorker(store, chat, CountingEngine()); worker.cancel_event.set(); worker.run()
    new_id = store.add_message(chat, 'user', 'New request after Stop.')
    ConversationWorker(store, chat, CountingEngine()).pause('context_limit', 'New pause')
    assert json.loads(store.messages(chat)[-1]['payload'])['checkpoint']['user_message_id'] == new_id
    other = store.create_chat('New'); store.add_message(other, 'user', 'Only this chat.')
    messages, _ = conversation_messages(store.messages(other), 'System', 4000)
    assert 'OLD-PRIVATE-OBJECTIVE' not in json.dumps(messages)
