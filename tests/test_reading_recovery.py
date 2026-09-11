"""Autonomous long-file reading with scripted models and real source/receipt storage."""
import copy
import json

import pytest

from letracode.budgeting import RequestUsage
from letracode.continuation import RunLimits
from letracode.evidence import evidence_state
from letracode.store import Store
from letracode.worker import ConversationWorker


class Reader:
    config = type('Config', (), {'context_size': 16384, 'max_tokens': 1024})()

    def __init__(self, path, arguments=None, repeat=False):
        self.path = path
        self.arguments = arguments or {'offset': 0, 'max_chars': 100}
        self.repeat = repeat
        self.requests = []

    def start(self, *args):
        pass

    def cancel(self):
        pass

    def request_usage(self, messages, tools, *args):
        return RequestUsage(len(json.dumps({'messages': messages, 'tools': tools})) // 2,
                            1024, 128, 'scripted')

    def complete(self, messages, *args):
        self.requests.append(copy.deepcopy(messages))
        pages = [json.loads(m['content']) for request in self.requests for m in request
                 if m['role'] == 'tool' and m['name'] == 'read_file']
        if len(self.requests) == 1 or self.repeat and not any('THE END' in p.get('text', '') for p in pages):
            return {'role': 'assistant', 'content': '', 'tool_calls': [{
                'id': str(len(self.requests)), 'type': 'function', 'function': {
                    'name': 'read_file', 'arguments': json.dumps({'path': str(self.path), **self.arguments})}}]}
        return {'role': 'assistant', 'content': 'I read the entire file.'}


def setup(tmp_path, text, arguments=None, repeat=False):
    path = tmp_path / 'long.txt'
    path.write_text(text, encoding='utf-8', newline='')
    store = Store(tmp_path / 'data')
    chat = store.create_chat('Read all')
    origin = store.add_message(chat, 'user', f'Read {path} from beginning to end, every line.')
    engine = Reader(path, arguments, repeat)
    return store, chat, origin, engine


def saved_pages(store, chat):
    return [(json.loads(row['payload']), json.loads(json.loads(row['payload'])['message']['content']))
            for row in store.messages(chat) if row['role'] == 'tool']


@pytest.mark.parametrize('repeat', [False, True])
def test_automatic_reading_reaches_end_across_segments_without_model_paging(tmp_path, repeat):
    source = '\ufeff' + ('opening αβ\r\n' * 8000) + 'THE END'
    store, chat, origin, engine = setup(tmp_path, source, repeat=repeat)
    ConversationWorker(store, chat, engine).run()
    rows = store.messages(chat)
    state = evidence_state(rows, origin)
    assert state['files'][0]['exposed_ranges'] == [[0, len(source)]]
    assert not state['incomplete']
    assert rows[-1]['role'] == 'assistant' and rows[-1]['status'] == 'complete'
    assert json.loads(rows[-1]['payload'])['coverage'] == state
    assert sum(row['role'] == 'user' for row in rows) == 1
    assert any(json.loads(row['payload']).get('segment_boundary') for row in rows)
    assert any('THE END' in page.get('text', '') for _, page in saved_pages(store, chat))
    assert all(row['status'] != 'complete' for row in rows if row['role'] == 'assistant'
               and row['content'] == 'I read the entire file.' and row != rows[-1])


@pytest.mark.parametrize('arguments', [
    {'offset': -1, 'max_lines': 180, 'max_chars': 99999},
    {'offset': 500000, 'start_line': 1, 'max_chars': 4000},
    {'start_line': 400000, 'max_lines': 180},
])
def test_invalid_paging_recovers_without_model_repair(tmp_path, arguments):
    store, chat, origin, engine = setup(tmp_path, 'opening\n' * 1600 + 'THE END', arguments)
    ConversationWorker(store, chat, engine).run()
    assert not evidence_state(store.messages(chat), origin)['incomplete']
    assert store.messages(chat)[-1]['status'] == 'complete'
    results = saved_pages(store, chat)
    assert any(result.get('code') == 'invalid_pagination' for _, result in results)
    assert any('THE END' in result.get('text', '') for _, result in results)


def test_end_page_alone_causes_automatic_recovery_of_opening_and_middle(tmp_path):
    source = 'opening' + 'middle' * 2000 + 'THE END'
    store, chat, origin, engine = setup(tmp_path, source, {'offset': len(source) - 7, 'max_chars': 100})
    ConversationWorker(store, chat, engine).run()
    assert not evidence_state(store.messages(chat), origin)['incomplete']
    assert any(page.get('offset') == 0 for _, page in saved_pages(store, chat)[1:])


def test_automatic_pages_respect_action_budget_and_do_not_finish_early(tmp_path):
    store, chat, origin, engine = setup(tmp_path, 'x' * 16000 + 'THE END')
    ConversationWorker(store, chat, engine, limits=RunLimits(max_actions=2)).run()
    rows = store.messages(chat)
    assert rows[-1]['status'] == 'paused'
    assert json.loads(rows[-1]['payload'])['checkpoint']['reason'] == 'action_budget'
    assert evidence_state(rows, origin)['incomplete']
    successful = [result for _, result in saved_pages(store, chat) if 'text' in result]
    assert len(successful) == 2


def test_auto_read_shrinks_pages_that_were_only_exposed_as_context_previews(tmp_path):
    store, chat, origin, engine = setup(tmp_path, 'x' * 18000 + 'THE END', {'offset': 0, 'max_chars': 16000})
    engine.config = type('Config', (), {'context_size': 11000, 'max_tokens': 1024})()
    ConversationWorker(store, chat, engine).run()
    assert not evidence_state(store.messages(chat), origin)['incomplete']
    assert any(page.get('offset') == 0 and 0 < len(page.get('text', '')) < 16000
               for _, page in saved_pages(store, chat)[1:])


@pytest.mark.parametrize('distinct_paths', [False, True])
def test_batched_pagination_errors_are_repaired_before_stall_limit(tmp_path, distinct_paths):
    store, chat, origin, engine = setup(tmp_path, 'beginning ' * 500 + 'THE END')
    paths = [engine.path]
    if distinct_paths:
        for number in range(3):
            path = tmp_path / f'extra-{number}.txt'
            path.write_text('additional file ' * 400 + 'THE END')
            paths.append(path)
    else:
        paths *= 4
    complete = engine.complete

    def batch(messages, *args):
        reply = complete(messages, *args)
        if len(engine.requests) == 1:
            reply['tool_calls'] = [{'id': f'bad-{number}', 'type': 'function', 'function': {
                'name': 'read_file', 'arguments': json.dumps({'path': str(path), 'offset': -1})}}
                for number, path in enumerate(paths)]
        return reply

    engine.complete = batch
    ConversationWorker(store, chat, engine).run()
    rows = store.messages(chat)
    assert not evidence_state(rows, origin)['incomplete']
    assert len(evidence_state(rows, origin)['files']) == len(set(paths))
    assert rows[-1]['status'] == 'complete'
    pending = []
    for row in rows:
        message = json.loads(row['payload']).get('message', {})
        if row['role'] == 'assistant' and message.get('tool_calls'):
            assert not pending
            pending = [call['id'] for call in message['tool_calls']]
        if row['role'] == 'tool':
            assert message['tool_call_id'] == pending.pop(0)
    assert not pending


def test_recovery_can_deliver_a_compacted_page_with_one_stall_allowance(tmp_path):
    store, chat, origin, engine = setup(tmp_path, 'x' * 18000 + 'THE END', {'offset': 0, 'max_chars': 16000})
    engine.config = type('Config', (), {'context_size': 8600, 'max_tokens': 1024})()
    ConversationWorker(store, chat, engine, limits=RunLimits(max_stalls=1)).run()
    assert not evidence_state(store.messages(chat), origin)['incomplete']


def test_repeated_oversized_unexposed_page_triggers_smaller_automatic_reads(tmp_path):
    store, chat, origin, engine = setup(tmp_path, 'x' * 18000 + 'THE END',
                                      {'offset': 0, 'max_chars': 16000}, repeat=True)
    engine.config = type('Config', (), {'context_size': 11000, 'max_tokens': 1024})()
    ConversationWorker(store, chat, engine).run()
    assert not evidence_state(store.messages(chat), origin)['incomplete']
    assert store.messages(chat)[-1]['status'] == 'complete'
