"""Review regressions exercise real persistence/tools with scripted inference."""
import json
import shlex
import sys

import pytest

from letracode.budgeting import RequestUsage
from letracode.continuation import RunHalted, RunLimits, RunProgress
from letracode.engine import Cancelled
from letracode.evidence import evidence_state
from letracode.store import Store, message_status
from letracode.worker import ConversationWorker


class Engine:
    config = type('Config', (), {'context_size': 32768, 'max_tokens': 1024})()

    def __init__(self, reply):
        self.reply = reply
        self.requests = []

    def start(self, *args):
        pass

    def cancel(self):
        pass

    def request_usage(self, messages, tools, *args):
        return RequestUsage(len(json.dumps({'messages': messages, 'tools': tools})) // 2, 1024, 128, 'scripted')

    def complete(self, messages, *args):
        self.requests.append(messages)
        return self.reply(len(self.requests), messages)


def call(name, arguments, ident):
    return {'id': str(ident), 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(arguments)}}


def fixture(tmp_path):
    source = tmp_path / 'sources'
    source.mkdir()
    store = Store(tmp_path / 'data')
    project = store.create_project('Review fixes')
    store.link(project, source)
    chat = store.create_chat('Bounded work', project)
    origin = store.add_message(chat, 'user', 'Read the entire chapter and preserve my latest direction.')
    return source, store, chat, origin


def outcomes(store, chat):
    return [json.loads(json.loads(row['payload'])['message']['content'])
            for row in store.messages(chat) if row['role'] == 'tool']


def test_saved_steering_during_approval_blocks_current_and_later_effects(tmp_path):
    source, store, chat, _ = fixture(tmp_path)
    engine = Engine(lambda number, _: {'role': 'assistant', 'content': '', 'tool_calls': [
        call('write_file', {'path': str(source / f'never-{index}.txt'), 'content': 'obsolete', 'expected_sha256': None}, index)
        for index in range(2)]})
    worker = ConversationWorker(store, chat, engine)
    approvals = []

    def steer(pending):
        approvals.append(pending)
        store.add_message(chat, 'user', 'Do not write any file.')
        pending.decide(True)

    worker.approval_needed.connect(steer)
    worker.run()
    assert not list(source.glob('never-*.txt')), 'Approval returned after steering but the obsolete write ran'
    assert len(approvals) == 1 and len(engine.requests) == 1
    results = outcomes(store, chat)
    assert len(results) == 2
    assert all(result['executed'] is False and result['code'] == 'new_input' for result in results)
    assert json.loads(store.messages(chat)[-1]['payload'])['checkpoint']['reason'] == 'new_input'


@pytest.mark.parametrize('before_run', [False, True])
def test_stop_requires_new_user_input_even_after_context_closes_and_store_reopens(tmp_path, before_run):
    _, store, chat, _ = fixture(tmp_path)

    def interrupted(*args):
        worker.request_stop()
        raise Cancelled()

    worker = ConversationWorker(store, chat, Engine(interrupted), use_tools=False)
    if before_run:
        worker.request_stop()
    worker.run()
    saved = store.messages(chat)
    assert saved[-1]['status'] == 'interrupted'
    assert json.loads(saved[-1]['payload'])['pause_context_closed'] is True
    reopened = Store(store.directory)
    fresh = Engine(lambda *_: {'role': 'assistant', 'content': 'Fresh requested response.'})
    ConversationWorker(reopened, chat, fresh, use_tools=False).run()
    assert not fresh.requests, 'A stopped run restarted from its old user row'
    assert reopened.messages(chat) == saved
    reopened.add_message(chat, 'user', 'Now start a fresh response.')
    ConversationWorker(reopened, chat, fresh, use_tools=False).run()
    assert len(fresh.requests) == 1
    assert reopened.messages(chat)[-1]['content'] == 'Fresh requested response.'


@pytest.mark.parametrize('legacy_completion_marker', [False, True])
def test_provisional_completed_requests_retain_source_exposure_after_reopen(tmp_path, legacy_completion_marker):
    source, store, chat, origin = fixture(tmp_path)
    path = source / 'chapter.txt'
    path.write_text('opening-middle-ending')

    def reply(number, _):
        if number == 1:
            return {'role': 'assistant', 'content': '', 'tool_calls': [
                call('read_file', {'path': str(path), 'offset': 0, 'max_chars': 7}, number)]}
        return {'role': 'assistant', 'content': 'The opening is available; the rest remains unread.'}

    ConversationWorker(store, chat, Engine(reply)).run()
    if legacy_completion_marker:
        for row in store.messages(chat):
            if row['status'] == 'incomplete':
                data = json.loads(row['payload'])
                data.pop('request_completed', None)
                store.update_message(row['id'], row['content'], row['status'], payload=data)
    rows = Store(store.directory).messages(chat)
    assert sum(row['status'] == 'incomplete' for row in rows) == 3
    state = evidence_state(rows, origin)
    assert state['files'][0]['exposed_ranges'] == [[0, 7]], 'Provisional status erased completed-request exposure'
    assert state['files'][0]['missing_ranges'] == [[7, 21]]
    assert state['incomplete'] is True


def test_saved_final_response_is_not_reopen_authority(tmp_path):
    _, store, chat, _ = fixture(tmp_path)
    engine = Engine(lambda *_: {'role': 'assistant', 'content': 'Requested response.'})
    ConversationWorker(store, chat, engine, use_tools=False).run()
    saved = store.messages(chat)
    ConversationWorker(Store(store.directory), chat, engine, use_tools=False).run()
    assert len(engine.requests) == 1
    assert store.messages(chat) == saved


def test_smaller_source_pages_recover_compacted_exposure_without_false_stalls(tmp_path):
    source, store, chat, origin = fixture(tmp_path)
    path = source / 'chapter.txt'
    text = 'a' * 16000
    path.write_text(text)

    def reply(number, messages):
        if number == 2:
            receipt = json.loads(messages[-1]['content'])
            assert receipt['context_truncated'] is True and 'text' not in receipt
        elif number > 2:
            page = json.loads(messages[-1]['content'])
            assert page['text'] == 'a' * 4000 and page['offset'] == (number - 3) * 4000
        if number == 6:
            return {'role': 'assistant', 'content': 'All supported text is now available.'}
        offset, maximum = (0, 16000) if number == 1 else ((number - 2) * 4000, 4000)
        return {'role': 'assistant', 'content': '', 'tool_calls': [
            call('read_file', {'path': str(path), 'offset': offset, 'max_chars': maximum}, number)]}

    engine = Engine(reply)
    engine.config = type('Config', (), {'context_size': 16384, 'max_tokens': 1024})()
    ConversationWorker(store, chat, engine).run()
    rows = store.messages(chat)
    assert len(engine.requests) == 6, 'New exposure was counted as repeated retrieval and stopped early'
    assert rows[-1]['role'] == 'assistant' and rows[-1]['status'] == 'complete'
    assert len(outcomes(store, chat)) == 5
    state = evidence_state(rows, origin)
    assert state['files'][0]['exposed_ranges'] == [[0, 16000]]
    assert state['incomplete'] is False


@pytest.mark.parametrize('variant', ['reason', 'default_timeout', 'cwd_alias'])
def test_equivalent_effectful_commands_reference_saved_outcome_instead_of_rerunning(tmp_path, variant):
    source, store, chat, _ = fixture(tmp_path)
    marker = source / 'executions.txt'
    command = 'printf x >> ' + shlex.quote(str(marker))
    alias = tmp_path / 'source-alias'
    alias.symlink_to(source, target_is_directory=True)

    def reply(number, _):
        if number == 3:
            return {'role': 'assistant', 'content': 'Use the saved command result.'}
        args = {'command': command, 'cwd': str(source), 'reason': 'First verification'}
        if number == 2:
            if variant == 'reason':
                args['reason'] = 'Recover the prior output'
            elif variant == 'default_timeout':
                args['timeout'] = 60
            else:
                args['cwd'] = str(alias)
        return {'role': 'assistant', 'content': '', 'tool_calls': [call('run_command', args, number)]}

    engine = Engine(reply)
    worker = ConversationWorker(store, chat, engine)
    approvals = []
    worker.approval_needed.connect(lambda pending: (approvals.append(pending), pending.decide(True)))
    worker.run()
    assert marker.read_text() == 'x', 'Equivalent commands repeated effects without source change'
    assert len(approvals) == 1
    rows = [row for row in store.messages(chat) if row['role'] == 'tool']
    repeated = outcomes(store, chat)[1]
    assert repeated['executed'] is False and repeated['code'] == 'duplicate_action'
    assert repeated['result_id'] == rows[0]['id']


def test_pure_command_identity_uses_execution_fields_and_default_timeout():
    progress = RunProgress()
    progress.observe('run_command', {'command': 'printf x', 'cwd': '/work', 'reason': 'First'},
        {'executed': True, 'exit_code': 0}, 7)
    assert progress.duplicate_effect('run_command', {'command': 'printf x', 'cwd': '/work', 'timeout': 60, 'reason': 'Again'}) == 7
    progress.observe_source_exposure()
    assert progress.duplicate_effect('run_command', {'command': 'printf x', 'cwd': '/work'}) == 7
    assert progress.duplicate_effect('run_command', {'command': 'printf y', 'cwd': '/work'}) is None
    assert progress.duplicate_effect('run_command', {'command': 'printf x', 'cwd': '/other'}) is None
    assert progress.duplicate_effect('run_command', {'command': 'printf x', 'cwd': '/work', 'timeout': 5}) is None


def test_real_output_cap_halts_batch_before_later_approval_effects_or_request(tmp_path):
    source, store, chat, _ = fixture(tmp_path)
    script = 'import sys,time; sys.stdout.write("x" * 100000); sys.stdout.flush(); time.sleep(5)'
    command = shlex.quote(sys.executable) + ' -u -c ' + shlex.quote(script)
    batch = [call('run_command', {'command': command, 'cwd': str(source)}, 'capped')]
    batch += [call('write_file', {'path': str(source / f'never-{index}.txt'),
                                 'content': 'obsolete', 'expected_sha256': None}, index)
              for index in range(2)]
    engine = Engine(lambda *_: {'role': 'assistant', 'content': '', 'tool_calls': batch})
    worker = ConversationWorker(store, chat, engine)
    approvals = []
    worker.approval_needed.connect(lambda pending: (approvals.append(pending), pending.decide(True)))
    worker.run()
    results = outcomes(store, chat)
    assert results[0]['executed'] is True and results[0]['output_limit_reached'] is True
    assert len(results[0]['output']) == 64000 and results[0]['exit_code'] < 0
    assert not list(source.glob('never-*.txt')), 'Output-cap termination allowed later effects'
    assert len(approvals) == 1 and len(engine.requests) == 1
    assert len(results) == 3
    assert all(result['executed'] is False and result['code'] == 'action_blocker' for result in results[1:])
    rows = [row for row in store.messages(chat) if row['role'] == 'tool']
    assert [json.loads(row['payload'])['message']['tool_call_id'] for row in rows] == ['capped', '0', '1']
    assert message_status(rows[0]) == 'Interrupted · effects require review'
    assert 'Interrupted · effects require review' in store.export_markdown(chat)
    assert json.loads(store.messages(chat)[-1]['payload'])['checkpoint']['reason'] == 'action_blocker'


def test_output_cap_is_interrupted_even_after_later_source_change():
    progress = RunProgress()
    args = {'command': 'noisy command', 'cwd': '/work'}
    result = {'executed': True, 'exit_code': -15, 'output_limit_reached': True}
    assert not progress.observe('run_command', args, result, 7)
    progress.observe('write_file', {'path': 'changed'}, {'path': 'changed', 'written_characters': 1}, 8)
    assert progress.duplicate_effect('run_command', args) == 7
    row = {'role': 'tool', 'status': 'complete', 'payload': json.dumps({
        'message': {'name': 'run_command', 'content': json.dumps(result)}})}
    assert message_status(row) == 'Interrupted · effects require review'


def test_real_saved_memory_identical_pages_with_different_sizes_hit_stall_limit(tmp_path):
    _, store, chat, _ = fixture(tmp_path)
    snapshot = store.strand.snapshot('global')
    store.strand.remember('global', 'A saved memory page.', expected_sha256=snapshot['sha256'])
    saved = store.strand.read_page('global')
    size = len(saved['text'])
    assert 0 < size < 1000

    def reply(number, _):
        args = {'scope': 'global', 'max_chars': size + number * 100}
        if number % 2 == 0:
            args['offset'] = 0
        return {'role': 'assistant', 'content': '', 'tool_calls': [call('read_memory', args, number)]}

    engine = Engine(reply)
    worker = ConversationWorker(store, chat, engine, limits=RunLimits(max_requests=5))
    worker.run()
    assert len(engine.requests) == 4, 'Identical memory pages evaded the three-stall limit'
    results = outcomes(store, chat)
    assert len(results) == 4 and all(result['text'] == saved['text'] for result in results)
    assert worker.progress.stalls == 3
    assert json.loads(store.messages(chat)[-1]['payload'])['checkpoint']['reason'] == 'no_progress'


@pytest.mark.parametrize('name,field', [('read_memory', 'text'), ('read_tool_result', 'content')])
def test_page_novelty_uses_returned_offset_content_and_source_version_not_requested_size(name, field):
    progress = RunProgress()
    args = {'scope': 'global'} if name == 'read_memory' else {'result_id': 5}
    page = {'offset': 0, 'next_offset': 5, 'total_chars': 10,
            field: json.dumps({'source_sha256': 'v1', 'text': 'first'})}
    assert progress.observe(name, args, page, 1)
    assert not progress.observe(name, dict(args, offset=0, max_chars=4000), page, 2)
    # Actual returned offsets, content and embedded source versions remain new evidence.
    page = dict(page, offset=5, next_offset=None)
    assert progress.observe(name, dict(args, offset=5, max_chars=8000), page, 3)
    page[field] = json.dumps({'source_sha256': 'v1', 'text': 'second'})
    assert progress.observe(name, args, page, 4)
    page[field] = json.dumps({'source_sha256': 'v2', 'text': 'second'})
    assert progress.observe(name, args, page, 5)
    for index in range(2):
        assert not progress.observe(name, dict(args, max_chars=9000 + index), page, 6 + index)
    with pytest.raises(RunHalted, match='3 consecutive') as stopped:
        progress.observe(name, dict(args, max_chars=10000), page, 8)
    assert stopped.value.reason == 'no_progress'


def test_unknown_command_argument_is_validated_before_duplicate_diagnostic(tmp_path):
    source, store, chat, _ = fixture(tmp_path)
    marker = source / 'executions.txt'
    command = 'printf x >> ' + shlex.quote(str(marker))

    def reply(number, _):
        if number == 3:
            return {'role': 'assistant', 'content': 'Use the saved result.'}
        args = {'command': command, 'cwd': str(source)}
        if number == 2:
            args['unknown'] = 'not an execution argument'
        return {'role': 'assistant', 'content': '', 'tool_calls': [call('run_command', args, number)]}

    engine = Engine(reply)
    worker = ConversationWorker(store, chat, engine)
    approvals = []
    worker.approval_needed.connect(lambda pending: (approvals.append(pending), pending.decide(True)))
    worker.run()
    assert marker.read_text() == 'x' and len(approvals) == 1
    repeated = outcomes(store, chat)[1]
    assert repeated['executed'] is False and repeated['error'] == 'Unknown argument.'
    assert repeated.get('code') != 'duplicate_action'
