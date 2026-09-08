"""Synthetic inference acceptance: real paging, persistence, edits and subprocesses.

These tests establish orchestration behavior, not actual-model reasoning quality.
Approval callbacks are explicitly scripted test decisions, not human approvals.
"""
import copy
import hashlib
import json
import subprocess
import sys
import threading

from tools.run_acceptance import python_command
from letracode.budgeting import RequestUsage
from letracode.store import Store
from letracode.worker import ConversationWorker


def tool_call(name, arguments):
    return {'role': 'assistant', 'content': 'Continue the original acceptance task.',
            'tool_calls': [{'id': 'replaced-by-script', 'type': 'function',
                            'function': {'name': name, 'arguments': json.dumps(arguments)}}]}


def paired(messages):
    pending = []
    for message in messages:
        if message['role'] == 'tool':
            assert message['tool_call_id'] in pending
            pending.remove(message['tool_call_id'])
        else:
            assert not pending
            pending = [call['id'] for call in message.get('tool_calls', [])]
    assert not pending


class ScriptedAcceptanceEngine:
    config = type('Config', (), {'context_size': 32768, 'max_tokens': 1024})()

    def __init__(self, objective, script):
        self.objective = objective
        self.script = script
        self.requests = []
        self.request_check = None
        self.cancelled = threading.Event()

    def start(self, *args):
        pass

    def cancel(self):
        self.cancelled.set()

    def request_usage(self, messages, tools, cancel, thinking=False):
        size = len(json.dumps({'messages': messages, 'tools': tools}, ensure_ascii=False))
        return RequestUsage((size + 1) // 2, 1024, 128, 'synthetic acceptance accounting')

    def complete(self, messages, tools, *args):
        paired(messages)
        assert any(m['role'] == 'user' and m['content'] == self.objective for m in messages)
        self.requests.append(copy.deepcopy(messages))
        if self.request_check is not None:
            self.request_check(len(self.requests), messages)
        try:
            if len(self.requests) == 1:
                reply = next(self.script)
            else:
                assert messages[-1]['role'] == 'tool'
                reply = self.script.send(json.loads(messages[-1]['content']))
        except StopIteration as done:
            return {'role': 'assistant', 'content': done.value}
        reply['tool_calls'][0]['id'] = f'acceptance-{len(self.requests)}'
        return reply


def chat_fixture(tmp_path, objective):
    folder = tmp_path / 'linked-fixture'
    folder.mkdir()
    store = Store(tmp_path / 'disposable-store')
    project = store.create_project('Synthetic automatic acceptance')
    store.update_project(project, instructions='Keep the original task, source provenance and verification outcomes through every automatic segment.')
    store.link(project, folder)
    chat = store.create_chat('One bounded user task', project)
    origin_id = store.add_message(chat, 'user', objective)
    return folder, store, chat, origin_id


def saved_tools(rows):
    return [(row, json.loads(row['payload'])['message']) for row in rows if row['role'] == 'tool']


def assert_finished(store, chat, engine, objective, final, expected_requests):
    rows = store.messages(chat)
    assert len(engine.requests) == expected_requests, [(r['role'], r['status'], r['content'][-500:]) for r in rows[-3:]]
    assert rows[-1]['role'] == 'assistant' and rows[-1]['content'] == final
    assert [r['content'] for r in rows if r['role'] == 'user'] == [objective]
    boundaries = [r for r in rows if json.loads(r['payload']).get('segment_boundary')]
    assert len(boundaries) == (expected_requests - 1) // 10
    assert all(json.loads(row['payload'])['checkpoint']['continuation'] for row in boundaries)
    assert not any(r['status'] in ('error', 'paused', 'interrupted') for r in rows)
    return rows


def test_whole_work_reads_27_real_pages_and_recovers_old_evidence_without_user_continue(tmp_path):
    objective = ('Read the entire synthetic Grey Area-style work in character pages, following every actual next_offset. '
                 'Explain Mira\u2019s final route, the chapter-three warning, the later correction, and whether a departure hour is established. '
                 'Cite source character ranges and retain this exact whole-work question through all automatic segments.')
    folder, store, chat, origin_id = chat_fixture(tmp_path, objective)
    markers = ['Mira initially plans North Gate.', 'The map repeats North Gate.',
               'MIDWORK_WARNING: the bridge cannot carry a cart.', 'Mira abandons the cart.',
               'LATER_CORRECTION: the final route is East Pier.', 'The departure hour is not recorded.']
    paths, contents = [], []
    for index, marker in enumerate(markers):
        size = 16000 if index < 3 else 20000
        text = (f'Chapter {index + 1}: source narration. ' * size)[:size]
        offset = 8900 if index == 2 else 500
        text = text[:offset] + marker + text[offset + len(marker):]
        path = folder / f'chapter-{index + 1}.txt'
        path.write_bytes(text.encode('utf-8'))
        paths.append(path)
        contents.append(text)
    observed = []
    citations = [(4, 500, 500 + len(markers[4])), (2, 8900, 8900 + len(markers[2])),
                 (5, 500, 500 + len(markers[5]))]
    final = (f'Mira changes from North Gate to East Pier [chapter-5.txt chars 500:{citations[0][2]}]. '
             f'The bridge cannot carry a cart [chapter-3.txt chars 8900:{citations[1][2]}], and she abandons it. '
             f'No departure hour is established [chapter-6.txt chars 500:{citations[2][2]}].')

    def script():
        first_page = None
        for path, text in zip(paths, contents):
            offset = 0
            while True:
                result = yield tool_call('read_file', {'path': str(path), 'offset': offset, 'max_chars': 4000})
                assert result['offset'] == offset
                assert result['text'] == text[offset:offset + 4000]
                assert result['sha256'] == hashlib.sha256(text.encode()).hexdigest()
                observed.append((str(path), offset, offset + len(result['text'])))
                if first_page is None:
                    first_page = copy.deepcopy(result)
                cursor = result['next_offset']
                if cursor is None:
                    break
                assert cursor == offset + len(result['text'])
                offset = cursor
        catalog = yield tool_call('list_tool_results', {'limit': 1})
        oldest = catalog['results'][0]
        assert oldest['name'] == 'read_file'
        page = yield tool_call('read_tool_result', {'result_id': oldest['result_id'], 'max_chars': 16000})
        recovered = json.loads(page['content'])
        assert recovered['text'] == first_page['text']
        assert recovered['sha256'] == first_page['sha256']
        for chapter, start, end in citations:
            assert contents[chapter][start:end] == markers[chapter]
            assert any(name == str(paths[chapter]) and low <= start < end <= high
                       for name, low, high in observed)
        return final

    engine = ScriptedAcceptanceEngine(objective, script())
    worker = ConversationWorker(store, chat, engine, web_enabled=False)
    approvals = []
    worker.approval_needed.connect(lambda pending: (approvals.append(pending.request), pending.decide(False)))
    worker.run()
    rows = assert_finished(store, chat, engine, objective, final, 30)
    assert not approvals
    assert len(observed) == 27
    for path, text in zip(paths, contents):
        assert [(start, end) for name, start, end in observed if name == str(path)] == [
            (start, min(start + 4000, len(text))) for start in range(0, len(text), 4000)]
    outcomes = saved_tools(rows)
    assert [message['name'] for _, message in outcomes].count('read_file') == 27
    assert len(outcomes) == 29  # Saved retrieval never reruns the source tool.
    # Evidence is based on text actually exposed to completed requests, including
    # the middle warning and late correction; a plausible answer alone is insufficient.
    from letracode.evidence import evidence_state
    evidence = evidence_state(rows, origin_id)
    assert not evidence['incomplete']
    by_path = {entry['path']: entry for entry in evidence['files']}
    for path, text in zip(paths, contents):
        entry = by_path[str(path)]
        assert entry['source_sha256'] == hashlib.sha256(text.encode()).hexdigest()
        assert entry['total_chars'] == len(text)
        assert entry['complete_supported_text'] and not entry['missing_ranges']
        assert entry['source_result_ids']
    # Request packing is allowed to discard old bulky evidence, but persisted
    # source results and the original question remain available after reopening.
    assert any(
        len([m for m in request if m['role'] == 'tool']) < index
        or any(json.loads(m['content']).get('context_truncated') for m in request if m['role'] == 'tool')
        for index, request in enumerate(engine.requests) if index >= 21)
    reopened = Store(store.directory)
    assert reopened.messages(chat) == rows
    assert evidence_state(reopened.messages(chat), origin_id) == evidence


def test_coding_goal_crosses_three_boundaries_with_real_red_green_and_preserved_index(tmp_path):
    objective = ('Repair clamp so values are bounded inclusively to 0 through 10. Read the linked design pages, '
                 'run verify.py and retain each failed check across automatic segments, then inspect the final diff. '
                 'Preserve the existing staged change and untracked sentinel.')
    folder, store, chat, _ = chat_fixture(tmp_path, objective)
    source = folder / 'clamp.py'
    original = 'def clamp(value):\n    return value\n'
    source.write_bytes(original.encode('utf-8'))
    verify = ('from pathlib import Path\nfrom clamp import clamp\n'
              'with Path("executions.txt").open("a") as log: log.write("verification\\n")\n'
              'assert clamp(-1) == 0, "NEGATIVE_BOUNDARY"\n'
              'assert clamp(11) == 10, "UPPER_BOUNDARY"\n'
              'assert clamp(4) == 4, "INTERIOR_VALUE"\nprint("ACCEPTANCE_PASS")\n')
    (folder / 'verify.py').write_text(verify)
    support = []
    for index in range(7):
        path = folder / f'design-{index}.txt'
        text = (f'Design section {index}: clamp retains interior values and bounds endpoints to 0 and 10. ' * 200)[:12000]
        path.write_bytes(text.encode('utf-8'))
        support.extend((path, offset, text[offset:offset + 4000]) for offset in range(0, len(text), 4000))
    assert len(support) == 21

    def git(*args):
        return subprocess.run(['git', '-C', str(folder), *args], check=True, capture_output=True).stdout

    git('init', '--quiet')
    git('add', '.')
    git('-c', 'user.name=Acceptance Fixture', '-c', 'user.email=fixture@example.invalid',
        '-c', 'core.hooksPath=' + str(tmp_path / 'empty-hooks'), '-c', 'commit.gpgsign=false',
        'commit', '--quiet', '-m', 'Disposable synthetic baseline')
    staged = folder / 'staged-note.txt'
    staged.write_text('Preserve this independently staged user change.\n')
    git('add', staged.name)
    sentinel = folder / 'untracked-sentinel.txt'
    sentinel.write_bytes(b'untouched sentinel\x00\xff')
    staged_before = git('diff', '--cached', '--binary')
    command = python_command('-B', 'verify.py')
    command_timeout = 30 if sys.platform == 'win32' else 5
    args = {'command': command, 'cwd': str(folder), 'timeout': command_timeout}
    verification = []
    final = 'Verified clamp after negative and upper boundary failures. The final diff is ready for review.'

    def inspect(pages):
        for path, offset, text in pages:
            result = yield tool_call('read_file', {'path': str(path), 'offset': offset, 'max_chars': 4000})
            assert result['text'] == text

    def script():
        initial = yield tool_call('read_file', {'path': str(source), 'offset': 0, 'max_chars': 4000})
        sha = initial['sha256']
        yield from inspect(support[:11])
        red = yield tool_call('run_command', args)
        assert red['exit_code'] == 1 and 'NEGATIVE_BOUNDARY' in red['output']
        verification.append(red)
        # The failed verification is deliberately separated from its repair by
        # ten meaningful source actions and an automatic segment boundary.
        yield from inspect(support[11:])
        edit = yield tool_call('edit_file', {'path': str(source), 'expected_sha256': sha,
                                           'old_text': 'return value', 'new_text': 'return max(0, value)'})
        assert edit['sha256'] == hashlib.sha256(source.read_bytes()).hexdigest()
        red = yield tool_call('run_command', args)
        assert red['exit_code'] == 1 and 'UPPER_BOUNDARY' in red['output']
        verification.append(red)
        edit = yield tool_call('edit_file', {'path': str(source), 'expected_sha256': edit['sha256'],
                                           'old_text': 'return max(0, value)', 'new_text': 'return min(10, max(0, value))'})
        assert edit['sha256'] == hashlib.sha256(source.read_bytes()).hexdigest()
        current = yield tool_call('read_file', {'path': str(source), 'offset': 0, 'max_chars': 4000})
        assert current['sha256'] == edit['sha256']
        assert current['text'] == original.replace('return value', 'return min(10, max(0, value))')
        green = yield tool_call('run_command', args)
        assert green['exit_code'] == 0 and 'ACCEPTANCE_PASS' in green['output']
        verification.append(green)
        diff = yield tool_call('run_command', {'command': 'git diff -- clamp.py', 'cwd': str(folder), 'timeout': command_timeout})
        assert '-    return value' in diff['output'] and '+    return min(10, max(0, value))' in diff['output']
        catalog = yield tool_call('list_tool_results', {'limit': 20})
        oldest_check = next(item for item in catalog['results'] if item['name'] == 'run_command')
        page = yield tool_call('read_tool_result', {'result_id': oldest_check['result_id'], 'max_chars': 16000})
        recovered = json.loads(page['content'])
        assert recovered == verification[0]
        return final

    engine = ScriptedAcceptanceEngine(objective, script())
    retained_check_ids = []

    def check_failed_verification_after_boundary(request_number, messages):
        if request_number != 21:
            return
        red_row = next(row for row, message in saved_tools(store.messages(chat)) if message['name'] == 'run_command')
        # This evidence must be in required application context even if old
        # reasoning/tool bodies were packed out. The script's Python variables
        # alone would not establish retention in an actual model request.
        required = messages[0]['content']
        assert 'run_command' in required and '"exit_code": 1' in required
        assert json.dumps(command)[1:-1] in required
        assert f'"result_id": {red_row["id"]}' in required
        retained_check_ids.append(red_row['id'])

    engine.request_check = check_failed_verification_after_boundary
    worker = ConversationWorker(store, chat, engine, web_enabled=False)
    approvals = []

    def scripted_fixture_approval(pending):
        approved = False
        try:
            assert pending.request.kind in ('write', 'command')
            assert str(folder) in pending.request.details
            if pending.request.kind == 'write':
                assert str(source) in pending.request.details
            else:
                assert command in pending.request.details or 'git diff -- clamp.py' in pending.request.details
            approvals.append(pending.request.kind)
            approved = True
        finally:
            pending.decide(approved)

    worker.approval_needed.connect(scripted_fixture_approval)
    worker.run()
    rows = assert_finished(store, chat, engine, objective, final, 32)
    assert len(retained_check_ids) == 1
    assert approvals == ['command', 'write', 'command', 'write', 'command', 'command']
    assert [outcome['exit_code'] for outcome in verification] == [1, 1, 0]
    assert all(not outcome['timed_out'] and not outcome['cancelled'] for outcome in verification)
    assert (folder / 'executions.txt').read_text().splitlines() == ['verification'] * 3
    assert source.read_text() == original.replace('return value', 'return min(10, max(0, value))')
    assert git('diff', '--cached', '--binary') == staged_before
    assert staged.read_text() == 'Preserve this independently staged user change.\n'
    assert sentinel.read_bytes() == b'untouched sentinel\x00\xff'
    assert (folder / 'verify.py').read_text() == verify
    assert len(saved_tools(rows)) == 31
    assert Store(store.directory).messages(chat) == rows
