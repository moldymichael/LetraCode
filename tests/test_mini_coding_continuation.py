"""Mini Coding continuation: real tools/evidence, deterministic llama.cpp peer."""
import copy
import hashlib
import json
import threading

import pytest

from letracode.budgeting import RequestUsage
from letracode.evidence import evidence_state
from letracode.store import Store
from letracode.tools import ToolExecutor
from letracode.worker import ConversationWorker, conversation_messages
from tools.run_acceptance import python_command


PROVISIONAL = 'Blank notes are fixed. All four checks passed.'
FINAL = 'The edited app and tests have now been read in full; four checks passed.'


def tool_call(name, arguments, number):
    return {'role': 'assistant', 'content': '', 'tool_calls': [{
        'id': f'mini-{number}', 'type': 'function', 'function': {
            'name': name, 'arguments': json.dumps(arguments)}}]}


def paired(messages):
    pending = set()
    for message in messages:
        if message['role'] == 'tool':
            assert message['tool_call_id'] in pending
            pending.remove(message['tool_call_id'])
        else:
            assert not pending
            pending.update(call['id'] for call in message.get('tool_calls', []))
    assert not pending


class MiniCodingPeer:
    config = type('Config', (), {'context_size': 32768, 'max_tokens': 1024})()

    def __init__(self, reply):
        self.reply = reply
        self.requests = []
        self.prefills = []
        self.rejections = []

    def start(self, *args):
        pass

    def cancel(self):
        pass

    def validate_template(self, messages):
        paired(messages)
        # llama.cpp server-common.cpp chat_params_parse defaults prefill_assistant
        # to true: one trailing assistant prefills; two are invalid_argument.
        if messages[-1]['role'] == 'assistant':
            if len(messages) > 1 and messages[-2]['role'] == 'assistant':
                error = 'Cannot have 2 or more assistant messages at the end of the list.'
                self.rejections.append(error)
                raise ValueError(error)
            if messages[-1]['content'] == PROVISIONAL:
                self.prefills.append(len(self.requests) + 1)

    def request_usage(self, messages, tools, *args):
        self.validate_template(messages)
        # Full enabled tool schemas participate, like /apply-template. Only
        # tokenizer accounting is synthetic; no hosted model is required.
        assert {'edit_file', 'run_command', 'read_tool_result', 'list_tool_results'} <= {
            tool['function']['name'] for tool in tools}
        size = len(json.dumps({'messages': messages, 'tools': tools}, ensure_ascii=False))
        return RequestUsage((size + 3) // 4, 1024, 128, 'deterministic fixture tokenizer')

    def complete(self, messages, tools, *args):
        self.validate_template(messages)
        self.requests.append(copy.deepcopy(messages))
        return self.reply(len(self.requests), messages)


@pytest.fixture
def mini_run(tmp_path):
    folder = tmp_path / 'mini-project'
    folder.mkdir()
    app = folder / 'app.py'
    tests = folder / 'test_app.py'
    app.write_text('def add_note(notes, text):\n    notes.append(text)\n    return notes\n'
                   '# app-detail-1\n# app-detail-2\n', encoding='utf-8', newline='\n')
    tests.write_text(
        'import unittest\nfrom pathlib import Path\nfrom app import add_note\n'
        'class NotesTests(unittest.TestCase):\n'
        '    def test_empty(self):\n        self.assertEqual(add_note([], ""), [])\n'
        '    def test_spaces(self):\n        self.assertEqual(add_note([], "   "), [])\n'
        '    def test_trim(self):\n        self.assertEqual(add_note([], " note "), ["note"])\n'
        '    def test_keep_existing(self):\n        self.assertEqual(add_note(["old"], "new"), ["old", "new"])\n'
        '# test-detail-1\n# test-detail-2\n'
        'if __name__ == "__main__":\n'
        '    with Path("verification-count.txt").open("a") as count:\n        count.write("run\\n")\n'
        '    unittest.main()\n', encoding='utf-8', newline='\n')
    store = Store(tmp_path / 'data')
    project = store.create_project('Disposable Mini Coding Test')
    store.link(project, folder)
    chat = store.create_chat('Mini Coding continuation', project)
    history = [('user', 'Review the notes app and its tests.'),
               ('assistant', 'Review: blank entries are currently stored and whitespace is retained.'),
               ('user', 'Correction: retain existing notes and trim nonempty input.'),
               ('assistant', 'The review and correction are recorded for the repair.')]
    for role, content in history:
        store.add_message(chat, role, content, payload=(
            {'pause_context_closed': True} if role == 'assistant' else None))
    prompt = 'Fix the blank-notes bug from that review, preserve my correction, and verify the tests.'
    origin = store.add_message(chat, 'user', prompt)
    boundary_rows = []
    coverage_before_requests = {}
    observed_hashes = {}
    edits = [
        (app, '    notes.append(text)\n', '    text = text.strip()\n    if text:\n        notes.append(text)\n'),
        (app, '# app-detail-1\n', '# app-detail-1\n'),
        (tests, '# test-detail-1\n', '# test-detail-1\n'),
        (app, '# app-detail-2\n', '# app-detail-2\n'),
        (tests, '# test-detail-2\n', '# test-detail-2\n'),
    ]

    def reply(number, messages):
        coverage_before_requests[number] = evidence_state(store.messages(chat), origin)
        if messages[-1]['role'] == 'tool':
            result = json.loads(messages[-1]['content'])
            if 'path' in result and 'sha256' in result:
                observed_hashes[result['path']] = result['sha256']
        if number in (1, 2):
            return tool_call('read_file', {'path': str(app if number == 1 else tests),
                                         'offset': 0, 'max_chars': 16000}, number)
        if 3 <= number <= 7:
            path, old, new = edits[number - 3]
            # Distinct generated comments make edit arguments bulky enough to
            # require genuine segment eviction, without using personal data.
            detail = f'# Generated Mini Coding support detail {number}.\n' * 45
            return tool_call('edit_file', {'path': str(path),
                'expected_sha256': observed_hashes[str(path)],
                'old_text': old, 'new_text': new + detail}, number)
        if number == 8:
            return tool_call('run_command', {'command': python_command('-B', 'test_app.py'),
                                           'cwd': str(folder)}, number)
        if number in (9, 10):
            return {'role': 'assistant', 'content': PROVISIONAL}
        if number in (11, 12):
            return tool_call('read_file', {'path': str(app if number == 11 else tests),
                                         'offset': 0, 'max_chars': 16000}, number)
        assert number == 13
        return {'role': 'assistant', 'content': FINAL}

    peer = MiniCodingPeer(reply)
    worker = ConversationWorker(store, chat, peer, web_enabled=False)
    approvals = []
    worker.approval_needed.connect(lambda pending: (
        approvals.append(pending.request.kind), pending.decide(True)))

    def observe_boundary():
        rows = store.messages(chat)
        if rows and json.loads(rows[-1]['payload']).get('segment_boundary'):
            boundary_rows.append(copy.deepcopy(rows))

    worker.changed.connect(observe_boundary)
    worker.run()
    return {'store': store, 'chat': chat, 'folder': folder, 'paths': [app, tests],
            'origin': origin, 'peer': peer, 'boundary_rows': boundary_rows,
            'history': history, 'prompt': prompt, 'approvals': approvals,
            'coverage': coverage_before_requests}


def test_two_rejected_finals_continue_with_valid_protocol_and_real_source_evidence(mini_run):
    run = mini_run
    store, chat, peer = run['store'], run['chat'], run['peer']
    rows = store.messages(chat)
    assert not peer.rejections, f'Continuation reached llama.cpp with invalid assistant tail: {peer.rejections}'
    assert not peer.prefills, 'A rejected provisional answer became assistant-prefill input'
    assert len(peer.requests) == 13 and rows[-1]['content'] == FINAL
    assert rows[-1]['status'] == 'complete'
    assert len(run['boundary_rows']) == 1
    assert 'segment 2;' in peer.requests[10][0]['content']
    assert run['approvals'] == ['write'] * 5 + ['command']
    assert (run['folder'] / 'verification-count.txt').read_text() == 'run\n'
    assert [row['content'] for row in rows if row['role'] == 'user'] == [
        content for role, content in run['history'] if role == 'user'] + [run['prompt']]
    for request in peer.requests:
        paired(request)
        assert all(any(message['role'] == role and message['content'] == content for message in request)
                   for role, content in run['history'])
        assert any(message['role'] == 'user' and message['content'] == run['prompt'] for message in request)
        assert all(message['content'] != PROVISIONAL for message in request)
    provisional = [row for row in rows if row['content'] == PROVISIONAL]
    assert len(provisional) == 2
    assert all(row['status'] == 'incomplete' and json.loads(row['payload'])['request_completed']
               and json.loads(row['payload'])['task_outcome'] == 'source_incomplete'
               and json.loads(row['payload'])['source_exposure'] for row in provisional)
    # The successful test command and withheld finals never prove exposure of
    # edited versions. Even a retrieved final page is uncredited until submitted.
    assert all(run['coverage'][number]['incomplete'] for number in range(9, 14))
    for number in (9, 10, 11):
        assert all(not item['exposed_ranges'] for item in run['coverage'][number]['files'])
    assert evidence_state(rows, run['origin'])['incomplete'] is False
    for item in evidence_state(rows, run['origin'])['files']:
        assert item['source_sha256'] == hashlib.sha256(next(
            path for path in run['paths'] if str(path) == item['path']).read_bytes()).hexdigest()
        assert item['versions'][0]['complete_supported_text'], 'Saved original-version exposure was lost'
    reopened = Store(store.directory)
    assert reopened.messages(chat) == rows
    assert evidence_state(reopened.messages(chat), run['origin']) == evidence_state(rows, run['origin'])
    tools = [row for row in rows if row['role'] == 'tool']
    assert [json.loads(row['payload'])['message']['name'] for row in tools] == [
        'read_file', 'read_file', *(['edit_file'] * 5), 'run_command', 'read_file', 'read_file']
    saved_command = tools[7]
    executor = ToolExecutor([], reopened.directory, lambda _: pytest.fail('Saved recovery requested approval'),
                            threading.Event(), False, False, store=reopened, chat_id=chat)
    catalog = json.loads(executor.execute('list_tool_results', {'after_id': 0, 'limit': 3}))
    found = catalog['results'][:]
    while catalog['next_after_id'] is not None:
        catalog = json.loads(executor.execute('list_tool_results', {
            'after_id': catalog['next_after_id'], 'through_id': catalog['through_id'], 'limit': 3}))
        found.extend(catalog['results'])
    assert [item['result_id'] for item in found] == [row['id'] for row in tools]
    page = json.loads(executor.execute('read_tool_result', {'result_id': saved_command['id'], 'max_chars': 16000}))
    result = json.loads(page['content'])
    assert result == json.loads(json.loads(saved_command['payload'])['message']['content'])
    assert result['exit_code'] == 0 and 'Ran 4 tests' in result['output'] and 'OK' in result['output']
    assert (run['folder'] / 'verification-count.txt').read_text() == 'run\n'
    assert reopened.messages(chat) == rows


def test_first_request_after_real_boundary_can_release_bulky_completed_segment(mini_run):
    run = mini_run
    assert len(run['boundary_rows']) == 1
    rows = run['boundary_rows'][0]
    # A character budget well above required intent/history, but below just
    # the five saved edit arguments. This is the first request after boundary,
    # before any new assistant/tool row exists to make the older segment optional.
    required_size = len(json.dumps(run['history'])) + len(run['prompt'])
    budget = required_size + 3000
    packed, trimmed = conversation_messages(rows, 'Mini Coding continuation.', budget)
    assert trimmed
    paired(packed)
    assert len(json.dumps(packed, ensure_ascii=False)) <= budget
    assert not any(message.get('tool_calls') for message in packed)
    assert all(any(message['role'] == role and message['content'] == content for message in packed)
               for role, content in run['history'])
    assert packed[-1] == {'role': 'user', 'content': run['prompt']}
    assert 'list_tool_results' in packed[0]['content'] and 'read_tool_result' in packed[0]['content']
    assert run['store'].messages(run['chat'])[:len(rows)] == rows
