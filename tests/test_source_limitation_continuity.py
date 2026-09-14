"""Terminal answers remain usable context even when source coverage is limited."""
import json

import pytest

from letracode.engine import ContextOverflowError
from letracode.evidence import evidence_state
from letracode.pause_context import resolve_intent
from letracode.store import Store
from letracode.worker import ConversationWorker, conversation_messages
from test_source_read_limitations import ANSWER, ScriptedSource, fixture, read


def test_limited_answer_is_preserved_across_later_task_boundary_and_context_squeeze(tmp_path):
    store, chat, origin, source = fixture(tmp_path)
    ConversationWorker(store, chat, ScriptedSource([
        read(source.with_suffix('')), read(source), None])).run()
    saved = store.messages(chat)
    answer = next(row for row in saved if row['content'] == ANSWER)
    coverage = evidence_state(saved, origin)
    assert answer['status'] == 'incomplete' and coverage['incomplete']

    latest = store.add_message(chat, 'user', 'Use your answer above to prepare the route description.')
    # An actual worker checkpoint freezes the recent starting context. The
    # following optional notes force packing to drop the completed segment.
    store.add_message(chat, 'assistant', 'Optional working notes. ' * 2000)
    ConversationWorker(store, chat, ScriptedSource([])).pause(
        'action_round_limit', 'Continuing the route description.', 10, automatic=True)
    reopened = Store(store.directory)
    rows = reopened.messages(chat)
    packed, trimmed = conversation_messages(rows, 'Continue the latest task.', 10000)
    assert trimmed
    assert any(message.get('content') == ANSWER for message in packed)
    assert any(message.get('content') == 'Use your answer above to prepare the route description.'
               for message in packed)
    assert not any('Optional working notes.' in message.get('content', '') for message in packed)
    checkpoint = json.loads(rows[-1]['payload'])['checkpoint']
    assert answer['id'] in checkpoint['pause_context']['referenced_context_ids']
    assert checkpoint['user_message_id'] == latest
    assert reopened.messages(chat)[:len(saved)] == saved
    assert evidence_state(rows, origin) == coverage

    # Removing a required answer cannot silently turn a follow-up into an
    # answerless request, even though the original task had limited sources.
    with reopened.connection() as database:
        database.execute('DELETE FROM messages WHERE id=?', (answer['id'],))
    with pytest.raises(ContextOverflowError, match='referenced context.*(missing|unavailable)'):
        conversation_messages(reopened.messages(chat), 'Continue the latest task.', 10000)


@pytest.mark.parametrize(('status', 'changed'), [
    ('incomplete', {'task_outcome': 'source_incomplete'}),
    ('incomplete', {'request_completed': False}),
    ('incomplete', {'pause_context_closed': False}),
    ('interrupted', {}),
    ('error', {}),
])
def test_provisional_or_unfinished_answer_does_not_become_required_context(tmp_path, status, changed):
    store = Store(tmp_path / 'data')
    chat = store.create_chat('Incomplete context')
    store.add_message(chat, 'user', 'Read the source.')
    answer = store.add_message(chat, 'assistant', 'Unfinished reading claim.', status=status, payload={
        'task_outcome': 'source_limited', 'request_completed': True,
        'pause_context_closed': True, **changed})
    store.add_message(chat, 'user', 'Now a new task.')
    intent, required, _ = resolve_intent(store.messages(chat), chat, starting_task=True)
    assert answer not in intent['referenced_context_ids']
    assert all(row['id'] != answer for row in required)
