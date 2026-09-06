"""Exercise the local coding loop with scripted replies and real file/process tools."""
import copy
import hashlib
import json
import shlex
import subprocess
import sys
import threading
import time

import pytest

from letracode.budgeting import RequestUsage
from letracode.store import Store
from letracode.tools import ToolExecutor
from letracode.worker import ConversationWorker, conversation_messages


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


def call(name, arguments, ident='step'):
    return {'role': 'assistant', 'content': '', 'tool_calls': [{
        'id': ident, 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(arguments)}}]}


class CodingEngine:
    config = type('Config', (), {'context_size': 32768, 'max_tokens': 1024})()

    def __init__(self, reply):
        self.reply = reply
        self.requests = []
        self.cancelled = threading.Event()

    def start(self, *args):
        pass

    def cancel(self):
        self.cancelled.set()

    def request_usage(self, messages, tools, cancel, thinking=False):
        size = len(json.dumps({'messages': messages, 'tools': tools}, ensure_ascii=False))
        return RequestUsage((size + 1) // 2, self.config.max_tokens, 128, 'synthetic tokenizer')

    def complete(self, messages, tools, *args):
        paired(messages)
        self.requests.append(copy.deepcopy(messages))
        return self.reply(len(self.requests) - 1, messages)


def coding_chat(tmp_path, instructions='Inspect this project and verify changes before reporting success.'):
    folder = tmp_path / 'project'; folder.mkdir()
    store = Store(tmp_path / 'data')
    project = store.create_project('Disposable coding project')
    store.update_project(project, instructions=instructions)
    store.link(project, folder)
    chat = store.create_chat('Coding proof', project)
    store.add_message(chat, 'user', 'Inspect and repair the clamp function. Use the pinned acceptance checks.')
    return store, chat, folder


def test_coding_loop_repairs_after_two_real_test_failures_and_keeps_reviewable_diff(tmp_path):
    instructions = 'Objective: clamp values to 0 through 10. Acceptance: run verify.py, then inspect git diff. Keep this task on continuation.'
    store, chat, folder = coding_chat(tmp_path, instructions)
    source = folder / 'clamp.py'
    original = 'def clamp(value):\n    return value\n' + '# Unchanged supporting module documentation.\n' * 700
    source.write_text(original)
    (folder / 'verify.py').write_text(
        'from clamp import clamp\n'
        'print("SAVED TEST EVIDENCE " * 600, flush=True)\n'
        'assert clamp(-1) == 0, "NEGATIVE_BOUNDARY"\n'
        'assert clamp(11) == 10, "UPPER_BOUNDARY"\n'
        'print("ACCEPTANCE_PASS")\n')
    subprocess.run(['git', 'init', '--quiet', str(folder)], check=True, capture_output=True)
    subprocess.run(['git', '-C', str(folder), 'add', 'clamp.py', 'verify.py'], check=True, capture_output=True)
    command = f'{shlex.quote(sys.executable)} -B verify.py'
    command_args = {'command': command, 'cwd': str(folder), 'timeout': 5}
    snapshots = []
    observed_failures = []

    def reply(index, messages):
        assert instructions in messages[0]['content']
        result = json.loads(messages[-1]['content']) if messages[-1]['role'] == 'tool' else None
        if index == 0:
            return call('read_file', {'path': str(source), 'max_lines': 6}, str(index))
        if index == 1:
            assert result['sha256'] == hashlib.sha256(original.encode()).hexdigest()
            snapshots.append(result['sha256'])
            return call('run_command', command_args, str(index))
        if index in (2, 4):
            assert result['exit_code'] != 0 and not result['timed_out'] and not result['cancelled']
            failure = 'NEGATIVE_BOUNDARY' if index == 2 else 'UPPER_BOUNDARY'
            assert failure in result['output']
            observed_failures.append(failure)
            old, new = ('return value', 'return max(0, value)') if index == 2 else (
                'return max(0, value)', 'return min(10, max(0, value))')
            return call('edit_file', {'path': str(source), 'expected_sha256': snapshots[-1],
                                     'old_text': old, 'new_text': new}, str(index))
        if index in (3, 5):
            snapshots.append(result['sha256'])
            assert snapshots[-1] == hashlib.sha256(source.read_bytes()).hexdigest()
            changed = 'return max(0, value)' if index == 3 else 'return min(10, max(0, value))'
            assert changed in messages[0]['content']
            assert '\n    return value\n' not in messages[0]['content']
            return call('run_command', command_args, str(index))
        if index == 6:
            assert result['exit_code'] == 0 and 'ACCEPTANCE_PASS' in result['output']
            return call('run_command', {'command': 'git diff -- clamp.py', 'cwd': str(folder), 'timeout': 5}, str(index))
        assert index == 7
        assert result['exit_code'] == 0
        assert '-    return value' in result['output'] and '+    return min(10, max(0, value))' in result['output']
        return {'role': 'assistant', 'content': 'Acceptance passed after two failed checks. The saved Git diff is ready for review.'}

    engine = CodingEngine(reply)
    worker = ConversationWorker(store, chat, engine, web_enabled=False)
    approvals = []

    def approve(pending):
        allowed = False
        try:
            request = pending.request
            assert request.kind in ('write', 'command')
            assert str(folder) in request.details
            if request.kind == 'write':
                assert str(source) in request.details and 'return ' in request.details
            else:
                assert command in request.details or 'git diff -- clamp.py' in request.details
            approvals.append(request.kind)
            allowed = True
        finally:
            pending.decide(allowed)

    worker.approval_needed.connect(approve)
    worker.run()
    assert store.messages(chat)[-1]['content'] == 'Acceptance passed after two failed checks. The saved Git diff is ready for review.'
    assert observed_failures == ['NEGATIVE_BOUNDARY', 'UPPER_BOUNDARY']
    assert approvals == ['command', 'write', 'command', 'write', 'command', 'command']
    assert len(engine.requests) == 8
    assert source.read_text() == original.replace('return value', 'return min(10, max(0, value))')
    saved = store.messages(chat)
    edit_calls = [item for row in saved if row['role'] == 'assistant'
                  for item in json.loads(row['payload']).get('message', {}).get('tool_calls', [])
                  if item['function']['name'] == 'edit_file']
    assert len(edit_calls) == 2
    assert all(len(item['function']['arguments']) < 1000 for item in edit_calls)

    # Compaction retains exact provenance and failure flags; reopening can read
    # the complete bounded command response without executing the command again.
    packed, trimmed = conversation_messages(saved, instructions, 12000)
    assert trimmed
    paired(packed)
    outcomes = [json.loads(message['content']) for message in packed if message.get('name') == 'run_command']
    assert any(item.get('context_truncated') for item in outcomes)
    assert [item['exit_code'] for item in outcomes] == [1, 1, 0, 0]
    assert all(item['cwd'] == str(folder) and item['timeout'] == 5 for item in outcomes)
    assert [item['command'] for item in outcomes] == [command, command, command, 'git diff -- clamp.py']
    assert store.messages(chat) == saved
    reopened = Store(tmp_path / 'data')
    first_command = next(row for row in saved if row['role'] == 'tool'
                         and json.loads(row['payload'])['message']['name'] == 'run_command')
    executor = ToolExecutor([], reopened.directory, lambda _: pytest.fail('Saved retrieval asked for approval'),
                            threading.Event(), False, False, store=reopened, chat_id=chat)
    page = json.loads(executor.execute('read_tool_result', {'result_id': first_command['id'], 'max_chars': 16000}))
    recovered = json.loads(page['content'])
    assert recovered['command'] == command and recovered['cwd'] == str(folder) and recovered['timeout'] == 5
    assert recovered['exit_code'] == 1 and 'NEGATIVE_BOUNDARY' in recovered['output']
    assert reopened.messages(chat) == saved


@pytest.mark.parametrize('stop', [False, True])
def test_denied_or_stopped_edit_approval_keeps_source_and_saved_outcome_after_reopen(tmp_path, stop):
    instructions = 'Preserve the original coding objective. Never repeat denied or completed edits on continuation.'
    store, chat, folder = coding_chat(tmp_path, instructions)
    source = folder / 'clamp.py'; source.write_text('def clamp(value):\n    return value\n')
    original = source.read_bytes()
    arguments = {'path': str(source), 'expected_sha256': hashlib.sha256(original).hexdigest(),
                 'old_text': 'return value', 'new_text': 'return max(0, value)'}

    def reply(index, messages):
        if index == 0:
            return call('edit_file', arguments)
        assert not stop
        assert 'denied' in json.loads(messages[-1]['content'])
        return {'role': 'assistant', 'content': 'The edit was denied; no retry.'}

    engine = CodingEngine(reply)
    worker = ConversationWorker(store, chat, engine, web_enabled=False)
    approvals = []

    def decide(pending):
        try:
            assert pending.request.kind == 'write'
            assert str(source) in pending.request.details
            approvals.append(pending.request.details)
            if stop:
                worker.request_stop()
        finally:
            pending.decide(False)

    worker.approval_needed.connect(decide)
    worker.run()
    assert len(approvals) == 1
    assert source.read_bytes() == original
    assert len(engine.requests) == (1 if stop else 2)
    rows = store.messages(chat)
    saved = next(row for row in rows if row['role'] == 'tool')
    outcome = json.loads(json.loads(saved['payload'])['message']['content'])
    assert 'denied' in outcome
    if stop:
        assert engine.cancelled.wait(1)
        assert rows[-1]['status'] == 'interrupted'
    reopened = Store(tmp_path / 'data')
    assert reopened.messages(chat) == rows
    assert source.read_bytes() == original
    reopened.add_message(chat, 'user', 'Continue from the saved denial without retrying the edit.')

    def resume(index, messages):
        assert instructions in messages[0]['content']
        if index == 0:
            return call('read_tool_result', {'result_id': saved['id']}, 'saved-denial')
        page = json.loads(messages[-1]['content'])
        assert json.loads(page['content']) == outcome
        return {'role': 'assistant', 'content': 'Saved denial recovered; original source retained.'}

    resumed = ConversationWorker(reopened, chat, CodingEngine(resume), computer_enabled=False, web_enabled=False)
    resumed.approval_needed.connect(lambda _: pytest.fail('Read-only continuation requested approval'))
    resumed.run()
    assert reopened.messages(chat)[-1]['content'] == 'Saved denial recovered; original source retained.'
    assert source.read_bytes() == original
    tool_names = [json.loads(row['payload'])['message']['name'] for row in reopened.messages(chat) if row['role'] == 'tool']
    assert tool_names == ['edit_file', 'read_tool_result']


def test_stop_during_command_saves_partial_outcome_and_continues_without_reexecution(tmp_path, monkeypatch):
    import letracode.tools as tools_module

    instructions = 'Keep the task pinned. Inspect the saved command outcome before considering any retry.'
    store, chat, folder = coding_chat(tmp_path, instructions)
    started = folder / 'started.txt'
    forbidden = folder / 'must-not-run.txt'
    script = (f'from pathlib import Path; import time; '
              f'Path({str(started)!r}).open("a").write("started\\n"); '
              'print("PARTIAL_COMMAND_EVIDENCE", flush=True); time.sleep(30); '
              f'Path({str(forbidden)!r}).write_text("unexpected continuation")')
    command = f'{shlex.quote(sys.executable)} -B -c {shlex.quote(script)}'
    engine = CodingEngine(lambda index, messages: call('run_command', {
        'command': command, 'cwd': str(folder), 'timeout': 60}))
    worker = ConversationWorker(store, chat, engine, web_enabled=False)
    watchers = []
    watched_start = []
    read_seen = threading.Event()
    read = tools_module.os.read

    def observe_read(fd, size):
        chunk = read(fd, size)
        if b'PARTIAL_COMMAND_EVIDENCE' in chunk:
            read_seen.set()
        return chunk

    # Keep real process I/O; synchronize Stop with evidence actually received
    # by the executor rather than a timing guess about when stdout was read.
    monkeypatch.setattr(tools_module.os, 'read', observe_read)

    def approve(pending):
        allowed = False
        try:
            assert pending.request.kind == 'command'
            assert command in pending.request.details
            allowed = True
        finally:
            pending.decide(allowed)

        def stop_started_command():
            watched_start.append(read_seen.wait(5) and started.exists())
            worker.request_stop()

        watcher = threading.Thread(target=stop_started_command)
        watchers.append(watcher)
        watcher.start()

    worker.approval_needed.connect(approve)
    begin = time.monotonic()
    worker.run()
    for watcher in watchers:
        watcher.join(6)
        assert not watcher.is_alive()
    assert watched_start == [True]
    assert time.monotonic() - begin < 8
    assert engine.cancelled.wait(1)
    assert len(engine.requests) == 1
    assert started.read_text() == 'started\n'
    assert not forbidden.exists()
    saved_rows = store.messages(chat)
    assert saved_rows[-1]['status'] == 'interrupted'
    saved = next(row for row in saved_rows if row['role'] == 'tool')
    outcome = json.loads(json.loads(saved['payload'])['message']['content'])
    assert outcome['cancelled'] and not outcome['timed_out']
    assert outcome['command'] == command and outcome['cwd'] == str(folder) and outcome['timeout'] == 60
    assert outcome['exit_code'] != 0 and 'PARTIAL_COMMAND_EVIDENCE' in outcome['output']
    reopened = Store(tmp_path / 'data')
    assert reopened.messages(chat) == saved_rows
    reopened.add_message(chat, 'user', 'Continue by reporting the interrupted saved command; do not execute it again.')

    def resume(index, messages):
        assert instructions in messages[0]['content']
        if index == 0:
            return call('read_tool_result', {'result_id': saved['id'], 'max_chars': 16000}, 'saved-command')
        page = json.loads(messages[-1]['content'])
        assert json.loads(page['content']) == outcome
        return {'role': 'assistant', 'content': 'The command was interrupted; its partial outcome is saved.'}

    ConversationWorker(reopened, chat, CodingEngine(resume), computer_enabled=False, web_enabled=False).run()
    assert reopened.messages(chat)[-1]['content'] == 'The command was interrupted; its partial outcome is saved.'
    assert started.read_text() == 'started\n'
    assert not forbidden.exists()
    assert [json.loads(row['payload'])['message']['name'] for row in reopened.messages(chat) if row['role'] == 'tool'] == [
        'run_command', 'read_tool_result']
