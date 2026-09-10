"""Saved history stays available without becoming mandatory task context."""
import json

import pytest

from letracode.pause_context import resolve_intent
from letracode.engine import ContextOverflowError
from letracode.store import Store


def test_long_ordinary_chat_retains_recent_turns_without_required_history_ceiling(tmp_path):
    from letracode.worker import conversation_messages
    store = Store(tmp_path / 'data')
    chat = store.create_chat('Long conversation')
    store.add_message(chat, 'user', 'OLD-OVERSIZED-REQUEST ' * 1000)
    for index in range(70):
        store.add_message(chat, 'user', f'Earlier question {index}')
        store.add_message(chat, 'assistant', f'Earlier answer {index}')
    latest = store.add_message(chat, 'user', 'What is next?')
    saved = store.messages(chat)

    intent, required, checkpoint = resolve_intent(saved, chat)
    messages, trimmed = conversation_messages(saved, 'System', 1500)

    assert checkpoint is None
    assert intent['origin_user_message_id'] == latest
    assert [row['id'] for row in required] == [latest]
    assert trimmed
    assert messages[-1]['content'] == 'What is next?'
    assert 'Earlier answer 69' in json.dumps(messages)
    assert 'OLD-OVERSIZED-REQUEST' not in json.dumps(messages)
    assert len(json.dumps(messages, ensure_ascii=False)) <= 1500
    assert Store(tmp_path / 'data').messages(chat) == saved


def test_new_checkpoint_bounds_starting_context_and_preserves_intent_on_resume(tmp_path):
    from letracode.worker import conversation_messages
    store = Store(tmp_path / 'data')
    chat = store.create_chat('Bounded checkpoint')
    for index in range(70):
        store.add_message(chat, 'user', f'Old question {index}')
        store.add_message(chat, 'assistant', f'Old answer {index}')
    plan = store.add_message(chat, 'assistant', 'RECENT-PLAN: preserve every chapter.')
    original = store.add_message(chat, 'user', 'Carry out the plan in all chapters.')
    context, required, _ = resolve_intent(store.messages(chat), chat, starting_task=True)
    assert context['reference_policy'] == 'recent_v1'
    assert 0 < len(context['referenced_context_ids']) <= 12
    assert plan in context['referenced_context_ids']
    assert required[-1]['id'] == original
    store.add_message(chat, 'notice', 'Paused', 'paused', {'checkpoint': {
        'user_message_id': original, 'last_user_message_id': original, 'pause_context': context}})
    for _ in range(60):
        store.add_message(chat, 'assistant', 'Optional working notes. ' * 50)
    latest = store.add_message(chat, 'user', 'Continue, retaining every chapter.')

    resumed, required, _ = resolve_intent(store.messages(chat), chat)
    assert resumed['origin_user_message_id'] == original
    assert [row['id'] for row in required if row['role'] == 'user'][-2:] == [original, latest]
    messages, trimmed = conversation_messages(store.messages(chat), 'System', 4000)
    assert trimmed
    assert 'Carry out the plan in all chapters.' in json.dumps(messages)
    assert 'RECENT-PLAN: preserve every chapter.' in json.dumps(messages)


def test_explicit_end_closes_checkpoint_without_editing_unknown_actions(tmp_path):
    from letracode.worker import conversation_messages
    store = Store(tmp_path / 'data')
    chat = store.create_chat('End task')
    origin = store.add_message(chat, 'user', 'OLD-LARGE-OBJECTIVE ' * 1000)
    call = {'id': 'unknown-call', 'type': 'function', 'function': {
        'name': 'run_command', 'arguments': '{"command":"side-effect"}'}}
    store.add_message(chat, 'assistant', '', payload={'message': {
        'role': 'assistant', 'content': '', 'tool_calls': [call]}})
    store.add_message(chat, 'notice', 'Paused', 'paused', {'checkpoint': {'user_message_id': origin}})
    original = store.messages(chat)

    ended_id = store.end_task(chat)
    ended = store.messages(chat)[-1]
    assert ended['id'] == ended_id
    data = json.loads(ended['payload'])
    assert data['pause_context_closed'] is True
    assert data['task_outcome'] == 'ended_by_user'
    assert data['terminal_input_cursor'] == origin
    assert data['ended_through_message_id'] == original[-1]['id']
    assert store.messages(chat)[:-1] == original
    assert store.end_task(chat) == ended_id
    latest = store.add_message(chat, 'user', 'Start a small unrelated task.')
    context, required, checkpoint = resolve_intent(store.messages(chat), chat, starting_task=True)
    assert checkpoint is None
    assert context['origin_user_message_id'] == latest
    assert context['referenced_context_ids'] == []
    assert [row['id'] for row in required] == [latest]
    messages, _ = conversation_messages(store.messages(chat), 'System', 1500)
    assert messages[-1]['content'] == 'Start a small unrelated task.'
    assert 'OLD-LARGE-OBJECTIVE' not in json.dumps(messages)
    assert not any(row['role'] == 'tool' for row in store.messages(chat))


def test_end_task_requires_existing_chat_with_saved_user_input(tmp_path):
    store = Store(tmp_path / 'data')
    with pytest.raises(ValueError, match='chat'):
        store.end_task('missing')
    chat = store.create_chat('Empty')
    with pytest.raises(ValueError, match='user'):
        store.end_task(chat)
    assert store.messages(chat) == []


def test_search_history_is_bounded_cross_project_saved_evidence(tmp_path):
    store = Store(tmp_path / 'data')
    project = store.create_project('Novel')
    first = store.create_chat('Chapter discussion', project)
    other = store.create_chat('General discussion')
    old = store.add_message(first, 'user', 'Search for the Quartz clue.')
    latest = store.add_message(other, 'assistant', 'quartz is mentioned ' + 'x' * 2000, status='interrupted')
    before = store.memory.snapshot('global')

    results = store.search_history('QUARTZ', limit=1)
    assert len(results) == 1
    assert results[0]['message_id'] == latest
    assert results[0]['chat_id'] == other
    assert results[0]['chat_title'] == 'General discussion'
    assert results[0]['project_id'] is None
    assert results[0]['role'] == 'assistant'
    assert results[0]['status'] == 'interrupted'
    assert results[0]['created']
    assert results[0]['truncated'] is True
    assert len(results[0]['excerpt']) <= 600
    scoped = store.search_history('quartz', chat_id=first)
    assert [row['message_id'] for row in scoped] == [old]
    assert scoped[0]['project_id'] == project
    assert scoped[0]['project_title'] == 'Novel'
    assert scoped[0]['truncated'] is False
    assert store.search_history('missing') == []
    assert store.search_history('quartz', chat_id='missing') == []
    assert store.memory.snapshot('global') == before


@pytest.mark.parametrize('query,limit', [('', 10), ('   ', 10), ('x' * 501, 10),
    ('query', True), ('query', 0), ('query', 51), ('query', '2')])
def test_search_history_rejects_unbounded_or_empty_requests(tmp_path, query, limit):
    store = Store(tmp_path / 'data')
    with pytest.raises(ValueError):
        store.search_history(query, limit=limit)


@pytest.mark.parametrize('outcome', ['reply', 'error'])
def test_ending_task_during_inference_cannot_reopen_its_checkpoint(tmp_path, outcome):
    from types import SimpleNamespace
    from letracode.worker import ConversationWorker
    store = Store(tmp_path / 'data')
    chat = store.create_chat('End during inference')
    store.add_message(chat, 'user', 'ORIGINAL ENDED TASK')
    class Engine:
        config = SimpleNamespace(context_size=65536, max_tokens=1024)
        def start(self, *args):
            pass
        def complete(self, *args, **kwargs):
            store.end_task(chat)
            if outcome == 'error':
                raise RuntimeError('Inference failed after the user ended the task')
            return {'role': 'assistant', 'content': 'Reply arrived after the task ended.'}
    ConversationWorker(store, chat, Engine(), computer_enabled=False, use_tools=False).run()
    new = store.add_message(chat, 'user', 'NEW UNRELATED TASK')

    intent, required, checkpoint = resolve_intent(store.messages(chat), chat)

    assert checkpoint is None
    assert intent['origin_user_message_id'] == new
    assert [row['id'] for row in required] == [new]


def test_unknown_crash_outcome_stays_blocked_until_explicit_task_end(tmp_path):
    from types import SimpleNamespace
    from letracode.worker import ConversationWorker
    store = Store(tmp_path / 'data'); chat = store.create_chat('Uncheckpointed crash')
    store.add_message(chat, 'user', 'Original operation')
    store.add_message(chat, 'assistant', '', payload={'message': {'role': 'assistant', 'content': '',
        'tool_calls': [{'id': 'lost', 'type': 'function', 'function': {'name': 'run_command', 'arguments': '{}'}}]}})
    store.add_message(chat, 'user', 'A later message does not establish what happened')
    class Engine:
        config = SimpleNamespace(context_size=65536, max_tokens=1024)
        starts = 0
        def start(self, *args): self.starts += 1
        def complete(self, *args): return {'role': 'assistant', 'content': 'A new task'}
    engine = Engine()
    ConversationWorker(store, chat, engine, computer_enabled=False, use_tools=False).run()
    assert engine.starts == 0
    assert json.loads(store.messages(chat)[-1]['payload'])['checkpoint']['reason'] == 'unknown_outcome'
    original = store.messages(chat)[1]
    store.end_task(chat)
    store.add_message(chat, 'user', 'Start an unrelated task')
    ConversationWorker(store, chat, engine, computer_enabled=False, use_tools=False).run()
    assert engine.starts == 1
    assert store.messages(chat)[1] == original
    assert not any(row['role'] == 'tool' for row in store.messages(chat))


def test_frozen_initial_intent_preserves_original_and_starting_plan_after_steering(tmp_path):
    store = Store(tmp_path / 'data')
    chat = store.create_chat('Active task')
    plan = store.add_message(chat, 'assistant', 'STARTING-PLAN: inspect every chapter')
    original = store.add_message(chat, 'user', 'Carry out the starting plan.')
    frozen, _, _ = resolve_intent(store.messages(chat), chat, starting_task=True)
    for index in range(13):
        store.add_message(chat, 'assistant', f'Optional working note {index}')
    steering = store.add_message(chat, 'user', 'Correction: use the revised chapter.')

    current, required, checkpoint = resolve_intent(store.messages(chat), chat, pinned_intent=frozen)

    assert checkpoint is None
    assert current['origin_user_message_id'] == original
    assert current['through_user_message_id'] == steering
    assert current['referenced_context_ids'] == [plan]
    assert [row['id'] for row in required] == [plan, original, steering]
    assert frozen['through_user_message_id'] == original


def test_closed_task_cannot_be_restored_by_stale_frozen_intent(tmp_path):
    store = Store(tmp_path / 'data')
    chat = store.create_chat('Closed task')
    store.add_message(chat, 'user', 'Old task')
    frozen, _, _ = resolve_intent(store.messages(chat), chat, starting_task=True)
    store.end_task(chat)
    store.add_message(chat, 'user', 'New task')
    with pytest.raises(ContextOverflowError, match='closed'):
        resolve_intent(store.messages(chat), chat, pinned_intent=frozen)


def test_first_pause_after_steering_keeps_the_admitted_original_intent(tmp_path):
    from types import SimpleNamespace
    from letracode.worker import ConversationWorker
    store = Store(tmp_path / 'data')
    chat = store.create_chat('Active task steering')
    original = store.add_message(chat, 'user', 'Retain my original multistep objective.')
    class Engine:
        config = SimpleNamespace(context_size=65536, max_tokens=1024)
        def start(self, *args):
            pass
        def complete(self, *args, **kwargs):
            for index in range(13):
                store.add_message(chat, 'assistant', f'Optional working note {index}')
            store.add_message(chat, 'user', 'Correction: use the revised chapter.')
            return {'role': 'assistant', 'content': 'Interrupted by steering.'}
    ConversationWorker(store, chat, Engine(), computer_enabled=False, use_tools=False).run()

    intent, required, checkpoint = resolve_intent(store.messages(chat), chat)

    assert checkpoint['reason'] == 'new_input'
    assert intent['origin_user_message_id'] == original
    assert [row['content'] for row in required if row['role'] == 'user'] == [
        'Retain my original multistep objective.', 'Correction: use the revised chapter.']
