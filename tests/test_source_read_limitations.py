"""A failed guessed path must not turn completed source answers into new work.

The saved reproduction missed an index filename's extension, listed its folder,
read the correct index, and answered. Further answers interleaved with new reads
kept resetting the stall counter. This fixture uses that sequence with synthetic
text; production file tools, evidence, request exposure and run control stay real.
"""
import copy
import json

import pytest

from letracode.budgeting import RequestUsage
from letracode.evidence import evidence_state
from letracode.reading import SourceReadRecovery
from letracode.store import Store, message_status
from letracode.worker import ConversationWorker, conversation_messages


ANSWER = 'The index establishes the route as East Pier. It does not establish a departure time.'


class ScriptedSource:
    config = type('Config', (), {'context_size': 32768, 'max_tokens': 1024})()

    def __init__(self, steps):
        self.steps = steps
        self.requests = []

    def start(self, *args):
        pass

    def cancel(self):
        pass

    def request_usage(self, messages, tools, *args):
        return RequestUsage(len(json.dumps({'messages': messages, 'tools': tools})) // 3,
                            1024, 128, 'scripted source-limitation regression')

    def complete(self, messages, *args):
        self.requests.append(copy.deepcopy(messages))
        index = len(self.requests) - 1
        step = self.steps[index] if index < len(self.steps) else None
        if step is None:
            return {'role': 'assistant', 'content': ANSWER}
        name, arguments = step
        return {'role': 'assistant', 'content': '', 'tool_calls': [{
            'id': f'call-{index}', 'type': 'function', 'function': {
                'name': name, 'arguments': json.dumps(arguments)}}]}


def read(path, **paging):
    return 'read_file', {'path': str(path), 'offset': 0, 'max_chars': 4000, **paging}


def fixture(tmp_path):
    sources = tmp_path / 'sources'; sources.mkdir()
    source = sources / 'Index.md'; source.write_text('The route is East Pier. No departure time is given.\n')
    store = Store(tmp_path / 'data'); chat = store.create_chat('Source limitation')
    origin = store.add_message(chat, 'user', 'Use the source index to identify the route and departure time.')
    return store, chat, origin, source


def assert_limited(store, chat, origin, engine, requests):
    rows = store.messages(chat)
    answers = [row for row in rows if row['role'] == 'assistant' and row['content'] == ANSWER]
    answer = answers[-1]
    data = json.loads(answer['payload'])
    assert len(engine.requests) == requests
    assert answer['status'] == 'incomplete'
    assert data['task_outcome'] == 'source_limited'
    assert data['request_completed'] is True
    assert data['pause_context_closed'] is True
    assert data['terminal_input_cursor'] == origin
    state = evidence_state(rows, origin)
    assert state['incomplete'] and data['coverage'] == state
    assert not state['whole_work_verified']
    assert SourceReadRecovery().next_read(state) is None
    assert 'Source limitation' in message_status(answer)
    assert 'Provisional' not in message_status(answer)
    assert message_status(answer) in store.export_markdown(chat)
    assert any('Automatic source reading stopped' in row['content'] for row in rows if row['role'] == 'notice')
    assert [row['id'] for row in rows if row['role'] == 'user'] == [origin]
    assert all(sum(message['role'] == 'user' for message in request) == 1 for request in engine.requests)
    assert not any(row['status'] in ('streaming', 'paused', 'continuing') for row in rows)
    return rows, state


def test_failed_guess_then_complete_alternative_answer_stops_before_extra_novel_files(tmp_path):
    store, chat, origin, source = fixture(tmp_path)
    steps = [read(source.with_suffix('')), ('list_files', {'path': str(source.parent)}), read(source), None]
    # Previously each new file reset RunProgress.stalls, letting the worker
    # reject another substantive answer and enter another segment.
    for index in range(8):
        extra = source.parent / f'Additional-{index}.md'
        extra.write_text(f'Unrelated source detail {index}.\n')
        steps.extend([read(extra), None])
    engine = ScriptedSource(steps)
    ConversationWorker(store, chat, engine).run()
    rows, state = assert_limited(store, chat, origin, engine, 4)
    assert len(state['files']) == 1 and state['files'][0]['complete_supported_text']
    assert len([row for row in rows if row['role'] == 'tool']) == 3
    assert sum(row['content'] == ANSWER for row in rows) == 1
    failed = next(row for row in rows if row['role'] == 'tool')
    assert json.loads(failed['payload'])['source_evidence'] == []
    assert 'missing' in json.loads(json.loads(failed['payload'])['message']['content'])['error'].lower()
    assert any(f"result {failed['id']}" in issue for issue in state['issues'])
    assert not any(json.loads(row['payload']).get('segment_boundary') for row in rows)


def test_missing_read_without_any_recoverable_page_stops_after_first_answer(tmp_path):
    store, chat, origin, source = fixture(tmp_path)
    engine = ScriptedSource([read(source.with_suffix('')), None])
    ConversationWorker(store, chat, engine).run()
    rows, state = assert_limited(store, chat, origin, engine, 2)
    assert state['files'] == []
    assert sum(row['content'] == ANSWER for row in rows) == 1


def test_legacy_untracked_source_retains_limitation_without_answer_retries(tmp_path):
    store, chat, origin, source = fixture(tmp_path)
    call = {'id': 'legacy-read', 'type': 'function', 'function': {
        'name': 'read_file', 'arguments': json.dumps({'path': str(source)})}}
    store.add_message(chat, 'assistant', '', payload={'message': {
        'role': 'assistant', 'content': '', 'tool_calls': [call]}})
    result = {'role': 'tool', 'name': 'read_file', 'tool_call_id': call['id'],
              'content': json.dumps({'text': 'Legacy source without verifiable coverage metadata'})}
    store.add_message(chat, 'tool', result['content'], payload={'message': result})
    engine = ScriptedSource([None])
    ConversationWorker(store, chat, engine).run()
    _, state = assert_limited(store, chat, origin, engine, 1)
    assert state['files'] == []


@pytest.mark.parametrize('failed_guess', [False, True])
def test_real_missing_exposure_is_paged_before_the_terminal_answer(tmp_path, failed_guess):
    store, chat, origin, source = fixture(tmp_path)
    text = 'Source evidence αβ\n' * 800 + 'END OF SOURCE'
    source.write_text(text)
    steps = ([read(source.with_suffix(''))] if failed_guess else []) + [read(source, max_chars=100), None]
    engine = ScriptedSource(steps)
    ConversationWorker(store, chat, engine).run()
    rows = store.messages(chat); state = evidence_state(rows, origin)
    assert state['files'][0]['exposed_ranges'] == [[0, len(text)]]
    assert state['files'][0]['complete_supported_text']
    assert state['incomplete'] is failed_guess
    assert any(json.loads(row['payload']).get('application_generated') == 'source_read_recovery' for row in rows)
    answers = [row for row in rows if row['content'] == ANSWER]
    assert len(answers) > 1
    assert all(row['status'] == 'incomplete' and json.loads(row['payload'])['task_outcome'] == 'source_incomplete'
               for row in answers[:-1])
    assert json.loads(answers[-1]['payload'])['task_outcome'] == ('source_limited' if failed_guess else 'response_unverified')
    assert sum(row['role'] == 'user' for row in rows) == 1
    if failed_guess:
        assert_limited(store, chat, origin, engine, 7)


def test_source_limited_answer_survives_reopen_and_new_user_input_without_auto_resume(tmp_path):
    store, chat, origin, source = fixture(tmp_path)
    engine = ScriptedSource([read(source.with_suffix('')), read(source), None])
    ConversationWorker(store, chat, engine).run()
    rows, state = assert_limited(store, chat, origin, engine, 3)
    reopened = Store(store.directory)
    fresh = ScriptedSource([None])
    ConversationWorker(reopened, chat, fresh).run()
    assert fresh.requests == []
    assert reopened.messages(chat) == rows
    assert evidence_state(reopened.messages(chat), origin) == state
    new_user = reopened.add_message(chat, 'user', 'Thanks. Use the known route to write one short sentence.')
    replay, _ = conversation_messages(reopened.messages(chat), 'Answer the latest question.', 65536)
    assert any(message.get('content') == ANSWER for message in replay)
    ConversationWorker(reopened, chat, fresh).run()
    assert len(fresh.requests) == 1
    assert [row['id'] for row in reopened.messages(chat) if row['role'] == 'user'] == [origin, new_user]
