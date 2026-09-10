import copy
import json
import sys
import threading
from pathlib import Path

import pytest

from letracode.store import Store
from letracode.worker import ConversationWorker, conversation_messages


class ScriptedEngine:
    write_target = Path.cwd() / 'letracode-never-write-test'
    class Config:
        context_size = 8192
        max_tokens = 1024
    config = Config()
    def request_usage(self, messages, tools, cancel, thinking=False):
        from letracode.budgeting import RequestUsage
        size = len(json.dumps({'messages': messages, 'tools': tools}, ensure_ascii=False))
        return RequestUsage((size + 1) // 2, self.config.max_tokens, 128, 'synthetic tokenizer')
    def start(self, cancel, on_status=lambda _: None):
        pass
    def complete(self, messages, tools, cancel, on_delta, thinking=False):
        if messages[-1]['role'] == 'tool':
            assert 'denied' in messages[-1]['content'].lower()
            on_delta('The action was denied. No file was changed.')
            return {'role':'assistant','content':'The action was denied. No file was changed.'}
        return {'role':'assistant','content':'','tool_calls':[{'id':'call_1','type':'function','function':{'name':'write_file','arguments':json.dumps({'path':str(self.write_target),'content':'bad','expected_sha256':None})}}]}
    def cancel(self):
        pass


def test_worker_saves_denial_and_stops_without_another_model_request(tmp_path):
    s = Store(tmp_path / 'data')
    c = s.create_chat('Tools')
    s.add_message(c,'user','Try the action')
    engine = ScriptedEngine()
    engine.write_target = tmp_path / 'denied.txt'
    w = ConversationWorker(s, c, engine)
    w.approval_needed.connect(lambda pending: pending.decide(False))
    w.run()
    messages = s.messages(c)
    assert any(m['role']=='tool' and 'denied' in m['content'].lower() for m in messages)
    assert messages[-1]['status'] == 'paused'
    assert json.loads(messages[-1]['payload'])['checkpoint']['reason'] == 'approval_denied'
    assert sum(message['role'] == 'assistant' for message in messages) == 1


def test_cancelled_worker_keeps_user_message(tmp_path):
    s = Store(tmp_path / 'data')
    c = s.create_chat('Stop')
    s.add_message(c,'user','Keep my prompt')
    w = ConversationWorker(s,c,ScriptedEngine())
    w.cancel_event.set()
    w.run()
    assert s.messages(c)[0]['content'] == 'Keep my prompt'
    assert all(m['status'] != 'streaming' for m in s.messages(c))


def test_incomplete_tool_history_gets_explicit_unknown_outcomes(tmp_path):
    from letracode.worker import conversation_messages
    s = Store(tmp_path/'data'); c=s.create_chat('Recovered')
    s.add_message(c,'user','Do work')
    call={'id':'call_lost','type':'function','function':{'name':'run_command','arguments':'{}'}}
    s.add_message(c,'assistant','',payload={'message':{'role':'assistant','content':'','tool_calls':[call]}})
    s.add_message(c,'user','What happened?')
    messages,_=conversation_messages(s.messages(c),'System',20000)
    assert [m['role'] for m in messages] == ['system','user','assistant','tool','user']
    assert messages[3]['tool_call_id']=='call_lost'
    assert 'unknown' in messages[3]['content'].lower()


def test_rejected_provisional_answers_are_saved_without_prefilling_recovery(tmp_path):
    store = Store(tmp_path / 'data'); chat = store.create_chat('Provisional recovery')
    store.add_message(chat, 'user', 'Fix the bug and verify the changed source.')
    call = {'id': 'verify', 'type': 'function', 'function': {
        'name': 'run_command', 'arguments': '{"command": "verify"}'}}
    store.add_message(chat, 'assistant', '', payload={'message': {
        'role': 'assistant', 'content': '', 'tool_calls': [call]}})
    store.add_message(chat, 'tool', 'Saved verification', payload={'message': {
        'role': 'tool', 'name': 'run_command', 'tool_call_id': 'verify',
        'content': '{"exit_code": 0, "output": "Four tests passed"}'}})
    for _ in range(2):
        store.add_message(chat, 'assistant', 'PROVISIONAL-FINAL-ANSWER', 'incomplete', payload={
            'message': {'role': 'assistant', 'content': 'PROVISIONAL-FINAL-ANSWER'},
            'task_outcome': 'source_incomplete', 'request_completed': True,
            'source_exposure': [], 'pause_context_closed': False})
        store.add_message(chat, 'notice', 'Edited source still requires read exposure.')
    saved = store.messages(chat)

    messages, _ = conversation_messages(saved, 'Recover the missing source evidence.', 20000)

    assert messages[-1]['role'] == 'tool'
    assert 'PROVISIONAL-FINAL-ANSWER' not in json.dumps(messages)
    assert_paired_tools(messages)
    assert Store(tmp_path / 'data').messages(chat) == saved
    assert sum(row['status'] == 'incomplete' for row in saved) == 2


def test_provisional_filter_preserves_tool_calls_and_other_incomplete_answers(tmp_path):
    store = Store(tmp_path / 'data'); chat = store.create_chat('Keep action history')
    store.add_message(chat, 'user', 'Inspect the source.')
    store.add_message(chat, 'assistant', 'KEEP-INCOMPLETE-CONTEXT', 'incomplete')
    call = {'id': 'read', 'type': 'function', 'function': {
        'name': 'read_file', 'arguments': '{"path": "/source.py"}'}}
    store.add_message(chat, 'assistant', '', 'incomplete', payload={
        'message': {'role': 'assistant', 'content': '', 'tool_calls': [call]},
        'task_outcome': 'source_incomplete'})
    store.add_message(chat, 'tool', 'Saved source', payload={'message': {
        'role': 'tool', 'name': 'read_file', 'tool_call_id': 'read', 'content': '{"text": "source"}'}})

    messages, _ = conversation_messages(store.messages(chat), 'System', 20000)

    assert 'KEEP-INCOMPLETE-CONTEXT' in json.dumps(messages)
    assert messages[-2]['tool_calls'] == [call]
    assert messages[-1]['tool_call_id'] == 'read'
    assert_paired_tools(messages)


@pytest.mark.parametrize('boundary', [False, True])
def test_first_request_after_rollover_can_drop_completed_segment(tmp_path, boundary):
    from letracode.engine import ContextOverflowError

    store = Store(tmp_path / 'data'); chat = store.create_chat('Rollover')
    store.add_message(chat, 'user', 'Review the project.')
    store.add_message(chat, 'assistant', 'SELECTED-PLAN: fix blank notes only.')
    origin = store.add_message(chat, 'user', 'Implement that plan and run the tests.')
    call = {'id': 'edit', 'type': 'function', 'function': {
        'name': 'edit_file', 'arguments': json.dumps({'old_text': 'OLD-CODE ' * 1000,
                                                    'new_text': 'NEW-CODE ' * 1000})}}
    store.add_message(chat, 'assistant', '', payload={'message': {
        'role': 'assistant', 'content': '', 'tool_calls': [call]}})
    result_id = store.add_message(chat, 'tool', 'Saved edit outcome', payload={'message': {
        'role': 'tool', 'name': 'edit_file', 'tool_call_id': 'edit',
        'content': '{"path": "/source.py", "sha256": "saved-version"}'}})
    if boundary:
        store.add_message(chat, 'notice', 'Continuing into segment 2.', 'continuing', payload={
            'segment_boundary': True, 'checkpoint': {'reason': 'action_round_limit',
                'user_message_id': origin, 'last_user_message_id': origin, 'rounds': 10,
                'continuation': {'version': 1}}})
        # No model/user message has been added in segment 2 yet.
        store.add_message(chat, 'notice', 'Saved evidence remains available.')
        for status in ('error', 'streaming', 'interrupted', 'incomplete'):
            store.add_message(chat, 'assistant', 'Not an active segment message.', status,
                              payload={'task_outcome': 'source_incomplete'})
    saved = store.messages(chat)

    if not boundary:
        with pytest.raises(ContextOverflowError):
            conversation_messages(saved, 'System', 3000)
        return
    messages, trimmed = conversation_messages(saved, 'System', 3000)

    assert trimmed and len(json.dumps(messages, ensure_ascii=False)) <= 3000
    assert [m['content'] for m in messages if m['role'] == 'user'] == [
        'Review the project.', 'Implement that plan and run the tests.']
    assert 'SELECTED-PLAN' in json.dumps(messages)
    assert 'OLD-CODE' not in json.dumps(messages)
    assert f'"saved_result_ids": [{result_id}]' in messages[0]['content']
    assert 'read_tool_result' in messages[0]['content']
    assert_paired_tools(messages)
    assert store.messages(chat) == saved


def test_empty_new_segment_cannot_drop_required_user_instructions(tmp_path):
    from letracode.engine import ContextOverflowError

    store = Store(tmp_path / 'data'); chat = store.create_chat('Required intent')
    origin = store.add_message(chat, 'user', 'REQUIRED-INSTRUCTION ' * 1000)
    store.add_message(chat, 'notice', 'Continuing.', 'continuing', payload={
        'segment_boundary': True, 'checkpoint': {'reason': 'action_round_limit',
            'user_message_id': origin, 'last_user_message_id': origin,
            'continuation': {'version': 1}}})

    with pytest.raises(ContextOverflowError):
        conversation_messages(store.messages(chat), 'System', 3000)


def test_excess_tool_requests_are_all_saved_as_not_executed_and_pause(tmp_path):
    class TooMany(ScriptedEngine):
        # Keep this batch-limit fixture independent of schema text growth.
        config = type('Config', (), {'context_size': 16384, 'max_tokens': 1024})()
        requests = 0
        def complete(self, messages, *args, **kwargs):
            self.requests += 1
            assert_paired_tools(messages)
            return {'role':'assistant','content':'Many actions','tool_calls':[
                {'id':f'{self.requests}_{i}','type':'function','function':{
                    'name':'write_file','arguments':json.dumps({'path':str(tmp_path / 'never.txt'), 'content':'bad', 'expected_sha256':None})}}
                for i in range(9)]}
    store=Store(tmp_path/'data'); chat=store.create_chat('Limits')
    store.add_message(chat,'user','Do work')
    engine = TooMany()
    worker=ConversationWorker(store,chat,engine)
    approvals=[]
    worker.approval_needed.connect(lambda pending: (approvals.append(pending), pending.decide(False)))
    worker.run()
    rows = store.messages(chat)
    assert engine.requests == 2
    assert not approvals
    assert not (tmp_path / 'never.txt').exists()
    outcomes = [json.loads(json.loads(row['payload'])['message']['content']) for row in rows if row['role']=='tool']
    assert len(outcomes) == 18
    assert all(result['executed'] is False and result['code']=='tool_batch_limit' for result in outcomes)
    assert rows[-1]['status'] == 'paused'
    assert json.loads(rows[-1]['payload'])['checkpoint']['reason'] == 'tool_batch_limit'
    reopened = Store(tmp_path / 'data')
    ConversationWorker(reopened,chat,engine).run()
    assert engine.requests == 2  # Only a new explicit user turn can resume.
    assert reopened.messages(chat) == rows
    paired = [json.loads(row['payload'])['message'] for row in rows if row['role'] in ('assistant','tool')]
    assert_paired_tools(paired)


def test_excess_batch_allows_one_smaller_correction(tmp_path):
    class Corrected(ScriptedEngine):
        requests = 0
        def complete(self, messages, *args, **kwargs):
            self.requests += 1
            assert_paired_tools(messages)
            if self.requests == 3:
                return {'role':'assistant','content':'Done.'}
            count = 9 if self.requests == 1 else 1
            return {'role':'assistant','content':'Inspect','tool_calls':[
                {'id':f'{self.requests}_{i}','type':'function','function':{
                    'name':'read_file','arguments':json.dumps({'path':str(path)})}} for i in range(count)]}
    store,chat,paths=linked_reading_chat(tmp_path,1)
    path=paths[0]
    path.write_text('Complete source for the batch-correction fixture.')
    engine=Corrected()
    engine.config=type('Config', (), {'context_size':32768, 'max_tokens':1024})()
    ConversationWorker(store,chat,engine).run()
    rows=store.messages(chat)
    assert engine.requests==3
    assert rows[-1]['content']=='Done.'
    outcomes=[json.loads(json.loads(row['payload'])['message']['content']) for row in rows if row['role']=='tool']
    assert len(outcomes)==10
    assert all(result['executed'] is False for result in outcomes[:9])
    assert outcomes[-1]['path']==str(path)


def assert_paired_tools(messages):
    pending = []
    for message in messages:
        if message['role'] == 'tool':
            assert message['tool_call_id'] in pending
            pending.remove(message['tool_call_id'])
        else:
            assert not pending
            pending = [call['id'] for call in message.get('tool_calls', [])]
    assert not pending


class ReadingEngine(ScriptedEngine):
    """Script model replies; use real worker, file tools, and SQLite storage."""
    def __init__(self, batches, context_size=32768, max_tokens=3072):
        self.config = type('Config', (), {'context_size':context_size, 'max_tokens':max_tokens})()
        self.batches = batches
        self.requests = []

    def complete(self, messages, tools, cancel, on_delta, thinking=False):
        self.requests.append(copy.deepcopy(messages))
        index = len(self.requests) - 1
        if index >= len(self.batches):
            return {'role':'assistant', 'content':'Finished reading the requested files.'}
        return {'role':'assistant', 'content':f'Reading batch {index + 1}.', 'tool_calls':[
            {'id':f'read_{index}_{item}', 'type':'function', 'function':{
                'name':'read_file', 'arguments':json.dumps({'path':str(path), 'max_lines':180})}}
            for item, path in enumerate(self.batches[index])
        ]}


def linked_reading_chat(tmp_path, count, escaped=False):
    folder = tmp_path / 'project'
    folder.mkdir()
    paths = []
    for index in range(count):
        path = folder / f'chapter-{index}.txt'
        line = ('é "quoted" \\ evidence ' if escaped else 'Project evidence ') * 15
        path.write_text((line + '\n') * 100, encoding='utf-8')
        paths.append(path)
    store = Store(tmp_path / 'data')
    project = store.create_project('Reading project')
    store.link(project, folder)
    chat = store.create_chat('Read folder', project)
    store.add_message(chat, 'user', 'Read these project files, in order.')
    return store, chat, paths


@pytest.mark.parametrize('batch_size', [1, 4])
def test_repeated_file_reads_fit_context_and_keep_full_saved_results(tmp_path, batch_size):
    store, chat, paths = linked_reading_chat(tmp_path, 8 * batch_size)
    batches = [paths[i:i + batch_size] for i in range(0, len(paths), batch_size)]
    engine = ReadingEngine(batches)
    worker = ConversationWorker(store, chat, engine)
    worker.run()

    rows = store.messages(chat)
    # These capped/compacted first pages never exposed the entire sources.
    # Keep the scripted claim, label it provisional, then stop the stall loop.
    assert rows[-1]['status'] == 'paused'
    assert json.loads(rows[-1]['payload'])['checkpoint']['reason'] == 'no_progress'
    claims = [row for row in rows if row['content'] == 'Finished reading the requested files.']
    assert len(claims) == 3 and all(row['status'] == 'incomplete' for row in claims)
    assert not any(row['status'] == 'error' for row in rows)
    assert len(engine.requests) == 11
    assert engine.config.context_size == 32768
    for request in engine.requests:
        # Existing 32768-context / 3072-reply character allowance: 57392.
        assert len(json.dumps(request, ensure_ascii=False)) <= 57392
        assert_paired_tools(request)
        # Optional retrieval can shrink as versioned cursor receipts accumulate;
        # preserve the complete system/identity/project core ahead of it.
        core = engine.requests[0][0]['content'].split('\n## Linked roots\n')[0]
        assert request[0]['role'] == 'system'
        assert request[0]['content'].startswith(core)
        assert request[1]['content'] == 'Read these project files, in order.'
        if request[-1]['role'] == 'tool':
            outcomes = [json.loads(m['content']) for m in request if m['role'] == 'tool']
            if 'text' in outcomes[-1]:
                assert len(outcomes[-1]['text']) == 16000
            else:
                # Only shorten the newest read after all older bulky bodies
                # have yielded their space (32 call/result pairs add overhead).
                assert all(outcome.get('context_truncated') for outcome in outcomes)
                assert outcomes[-1]['context_preview']
    assert len(json.loads(engine.requests[1][-1]['content'])['text']) == 16000
    assert any(json.loads(m['content']).get('context_truncated')
               for m in engine.requests[8] if m['role'] == 'tool')

    saved_tools = [row for row in rows if row['role'] == 'tool']
    assert len(saved_tools) == len(paths)
    for row, path in zip(saved_tools, paths):
        result = json.loads(json.loads(row['payload'])['message']['content'])
        assert result['path'] == str(path)
        assert len(result['text']) == 16000
        assert json.loads(row['content'].split('\n\nResult:\n', 1)[1]) == result
        assert 'context_truncated' not in result

    # An oversized completed turn can also be repacked after reopening the DB.
    reopened = Store(tmp_path / 'data')
    replay, _ = conversation_messages(reopened.messages(chat), 'Project instructions', 57392)
    assert len(json.dumps(replay, ensure_ascii=False)) <= 57392
    assert_paired_tools(replay)
    # Rejected answers stay in the transcript, but never become retry prefills.
    assert all(message['content'] != 'Finished reading the requested files.' for message in replay)
    assert replay[-1]['role'] in ('user', 'tool')
    assert reopened.messages(chat) == rows


def test_single_oversized_read_keeps_a_marked_preview_and_full_saved_result(tmp_path):
    store, chat, paths = linked_reading_chat(tmp_path, 1, escaped=True)
    engine = ReadingEngine([paths], context_size=8192, max_tokens=1024)
    ConversationWorker(store, chat, engine).run()
    rows = store.messages(chat)
    assert rows[-1]['status'] == 'paused'
    assert json.loads(rows[-1]['payload'])['checkpoint']['reason'] == 'no_progress'
    assert sum(row['status'] == 'incomplete' for row in rows) == 3
    assert len(engine.requests) == 4
    request = engine.requests[1]
    assert len(json.dumps(request, ensure_ascii=False)) <= 12336
    assert_paired_tools(request)
    shortened = json.loads(request[-1]['content'])
    assert shortened['context_truncated'] is True
    assert shortened['path'] == str(paths[0])
    assert shortened['start_line'] == 1
    assert shortened['context_preview']
    assert 'omitted' in shortened['context_note'].lower()
    assert 'saved' in shortened['context_note'].lower()
    full = json.loads(json.loads(next(row for row in rows if row['role'] == 'tool')['payload'])['message']['content'])
    assert len(full['text']) == 16000
    assert 'context_truncated' not in full


def test_context_compaction_preserves_outcomes_calls_and_instructions(tmp_path):
    store = Store(tmp_path / 'data')
    chat = store.create_chat('Tool outcomes')
    prompt = 'Inspect the project. Do not repeat denied actions.'
    system = 'Follow the project instructions exactly.'
    store.add_message(chat, 'user', prompt)
    results = [
        ('run_command', {'output':'log ' * 16000, 'exit_code':7, 'timed_out':True,
                         'cancelled':False, 'output_limit_reached':True}),
        ('write_file', {'denied':'The user denied this write. Do not retry or bypass.'}),
        ('read_file', {'error':'File not found.'}),
        ('read_file', {'path':'/project/latest.txt', 'text':'evidence ' * 1000}),
    ]
    for index, (name, result) in enumerate(results):
        call = {'id':str(index), 'type':'function', 'function':{'name':name, 'arguments':'{}'}}
        assistant = {'role':'assistant', 'content':'Keep this working note.', 'tool_calls':[call]}
        tool = {'role':'tool', 'name':name, 'tool_call_id':str(index), 'content':json.dumps(result)}
        store.add_message(chat, 'assistant', assistant['content'], payload={'message':assistant})
        store.add_message(chat, 'tool', tool['content'], payload={'message':tool})
    rows = store.messages(chat)
    messages, _ = conversation_messages(rows, system, 18000)
    assert len(json.dumps(messages, ensure_ascii=False)) <= 18000
    assert_paired_tools(messages)
    assert messages[:2] == [{'role':'system','content':system}, {'role':'user','content':prompt}]
    assert [m for m in messages if m['role'] == 'assistant'] == [
        json.loads(row['payload'])['message'] for row in rows if row['role'] == 'assistant']
    outcomes = [json.loads(m['content']) for m in messages if m['role'] == 'tool']
    assert {key:outcomes[0][key] for key in ('exit_code','timed_out','cancelled','output_limit_reached')} == {
        'exit_code':7, 'timed_out':True, 'cancelled':False, 'output_limit_reached':True}
    assert outcomes[1:] == [result for _, result in results[1:]]
    assert store.messages(chat) == rows


def test_context_packing_never_shortens_an_oversized_user_prompt(tmp_path):
    store = Store(tmp_path / 'data')
    chat = store.create_chat('Long prompt')
    prompt = 'Keep every user instruction. ' * 1000
    store.add_message(chat, 'user', prompt)
    from letracode.engine import ContextOverflowError
    with pytest.raises(ContextOverflowError, match='latest conversation turn'):
        conversation_messages(store.messages(chat), 'System instructions', 8000)
    assert store.messages(chat)[0]['content'] == prompt


def test_run_limit_reopens_from_saved_history_without_reexecution(tmp_path):
    from letracode.continuation import RunLimits
    class Endless(ScriptedEngine):
        requests=0
        def complete(self, messages, *args, **kwargs):
            self.requests+=1
            return {'role':'assistant','content':'Inspect one source character','tool_calls':[{
                'id':str(self.requests),'type':'function','function':{
                    'name':'read_file','arguments':json.dumps({'path':str(path), 'offset':self.requests - 1, 'max_chars':1})}}]}
    store,chat,paths=linked_reading_chat(tmp_path,1)
    path=paths[0]; path.write_text('0123456789')
    engine=Endless()
    engine.config=type('Config', (), {'context_size':16384, 'max_tokens':1024})()
    worker=ConversationWorker(store,chat,engine,limits=RunLimits(max_requests=10))
    worker.approval_needed.connect(lambda pending: pending.decide(False))
    worker.run()
    rows=store.messages(chat)
    assert engine.requests==10
    assert rows[-1]['status']=='paused'
    checkpoint=json.loads(rows[-1]['payload'])['checkpoint']
    assert checkpoint['reason']=='request_budget'
    assert len(checkpoint['saved_result_ids'])==10
    class Resume(ScriptedEngine):
        config=type('Config', (), {'context_size':16384, 'max_tokens':1024})()
        def complete(self,messages,*args,**kwargs):
            assert_paired_tools(messages)
            assert 'saved' in messages[0]['content'].lower()
            return {'role':'assistant','content':'The previous actions are saved.'}
    reopened=Store(tmp_path/'data')
    reopened.add_message(chat,'user','Continue with the saved evidence.')
    ConversationWorker(reopened,chat,Resume()).run()
    after=reopened.messages(chat)
    assert len([row for row in after if row['role']=='tool'])==10
    assert after[-1]['content']=='The previous actions are saved.'


def test_repeated_pause_compaction_keeps_earlier_saved_references(tmp_path):
    store = Store(tmp_path / 'data')
    chat = store.create_chat('Repeated pauses')
    evidence_ids = []
    for cycle in range(3):
        store.add_message(chat, 'user', 'Continue using saved evidence.')
        call = {'id': f'evidence_{cycle}', 'type': 'function', 'function': {
            'name': 'read_file', 'arguments': '{}'}}
        store.add_message(chat, 'assistant', '', payload={'message': {
            'role': 'assistant', 'content': '', 'tool_calls': [call]}})
        worker = ConversationWorker(store, chat, ScriptedEngine())
        worker.save_tool_result(call, {}, json.dumps({'path': str(Path('/synthetic').absolute() / str(cycle)), 'text': 'saved evidence',
            'offset':0, 'total_chars':14, 'editable':True, 'sha256':'a' * 64, 'source_truncated':False}))
        evidence_ids.append(store.messages(chat)[-1]['id'])
        store.add_message(chat, 'assistant', 'Previous working notes. ' * 300)
        worker.pause('action_round_limit', 'Paused with saved evidence.', 10)
        store = Store(tmp_path / 'data')
        store.add_message(chat, 'user', 'Continue.')
        messages, trimmed = conversation_messages(store.messages(chat), 'System', 2200)
        assert trimmed
        assert_paired_tools(messages)
        assert len(json.dumps(messages, ensure_ascii=False)) <= 2200
        # Old action bulk is omitted; authoritative user steering survives.
        # The checkpoint still locates the original saved outcomes.
        assert all(message['role'] == 'user' for message in messages[1:])
        assert [message['content'] for message in messages[1:]] == [
            row['content'] for row in store.messages(chat) if row['role'] == 'user']
        checkpoint = json.loads(messages[0]['content'].split('## Saved pause checkpoint\n')[1].split('\n')[0])
        assert set(evidence_ids) <= set(checkpoint['saved_result_ids'])
    assert checkpoint['saved_result_count'] == len(evidence_ids)


def test_many_pauses_recover_old_result_from_bounded_catalog_after_reopen(tmp_path):
    from letracode.tools import ToolExecutor

    store = Store(tmp_path / 'data')
    chat = store.create_chat('Long saved history')
    other = store.create_chat('Private unrelated history')
    private_id = store.add_message(other, 'tool', 'UNRELATED SECRET', payload={'message': {
        'role': 'tool', 'name': 'run_command', 'tool_call_id': 'private', 'content': 'UNRELATED SECRET'}})
    original = json.dumps({'output': 'ARCHIVED-DELTA-41', 'exit_code': 7, 'timed_out': True})
    expected = []
    for cycle in range(24):
        store.add_message(chat, 'user', 'Continue with prior evidence.')
        name, outcome = ('run_command', original) if cycle == 0 else (
            ('write_file', json.dumps({'denied': 'User denied this write. Do not retry.'})) if cycle == 1 else
            ('read_file', json.dumps({'error': 'Missing original source.'})))
        call = {'id': f'old_{cycle}', 'type': 'function', 'function': {'name': name, 'arguments': '{}'}}
        store.add_message(chat, 'assistant', '', payload={'message': {
            'role': 'assistant', 'content': '', 'tool_calls': [call]}})
        worker = ConversationWorker(store, chat, ScriptedEngine())
        worker.save_tool_result(call, {}, outcome)
        expected.append(store.messages(chat)[-1]['id'])
        store.add_message(chat, 'assistant', 'Earlier notes require compaction. ' * 500)
        worker.pause('action_round_limit', 'Paused.', 10)
    reopened = Store(tmp_path / 'data')
    reopened.add_message(chat, 'user', 'Recover the earlier command output using only saved evidence.')
    checkpoint = json.loads(reopened.messages(chat)[-2]['payload'])['checkpoint']
    assert len(checkpoint['saved_result_ids']) < len(expected)
    assert checkpoint['saved_result_count'] == len(expected)
    before = reopened.messages(chat)
    executor = ToolExecutor([], reopened.directory, lambda _: pytest.fail('Recovery must not request approval'),
                            threading.Event(), False, False, store=reopened, chat_id=chat)
    recovered = []
    after_id = 0
    while True:
        page = json.loads(executor.execute('list_tool_results', {'after_id': after_id, 'limit': 7}))
        assert 'error' not in page and 'denied' not in page
        assert len(page['results']) <= 7
        assert 'UNRELATED SECRET' not in json.dumps(page)
        recovered.extend(page['results'])
        after_id = page['next_after_id']
        if after_id is None:
            break
    assert [item['result_id'] for item in recovered] == expected
    assert private_id not in [item['result_id'] for item in recovered]
    assert recovered[0]['outcome'] == {'exit_code': 7, 'timed_out': True}
    assert recovered[1]['outcome']['denied'] == 'User denied this write. Do not retry.'
    assert recovered[2]['outcome']['error'] == 'Missing original source.'
    assert reopened.messages(chat) == before

    class DiscoverSaved(ScriptedEngine):
        requests = 0

        def complete(self, messages, tools, *args, **kwargs):
            self.requests += 1
            assert_paired_tools(messages)
            assert 'list_tool_results' in {tool['function']['name'] for tool in tools}
            assert 'list_tool_results' in messages[0]['content']
            if self.requests == 1:
                assert all(message['role'] == 'user' for message in messages[1:])
                assert len(messages) == 26  # 24 steering rows, current user and system.
                assert 'ARCHIVED-DELTA-41' not in json.dumps(messages)
                name, arguments = 'list_tool_results', {'limit': 1}
            elif self.requests == 2:
                result_id = json.loads(messages[-1]['content'])['results'][0]['result_id']
                name, arguments = 'read_tool_result', {'result_id': result_id}
            elif self.requests == 3:
                assert json.loads(messages[-1]['content'])['content'] == original
                return {'role': 'assistant', 'content': 'Recovered ARCHIVED-DELTA-41; command exited 7.'}
            else:
                return {'role': 'assistant', 'content': 'Recovered ARCHIVED-DELTA-41; command exited 7.'}
            return {'role': 'assistant', 'content': '', 'tool_calls': [{
                'id': f'recover_{self.requests}', 'type': 'function', 'function': {
                    'name': name, 'arguments': json.dumps(arguments)}}]}

    engine = DiscoverSaved()
    ConversationWorker(reopened, chat, engine, computer_enabled=False, web_enabled=False).run()
    after = reopened.messages(chat)
    claims = [row for row in after[len(before):] if row['content'] == 'Recovered ARCHIVED-DELTA-41; command exited 7.']
    assert len(claims) == 3 and all(row['status'] == 'incomplete' for row in claims)
    assert json.loads(after[-1]['payload'])['checkpoint']['reason'] == 'no_progress'
    assert engine.requests == 5  # Historical failed reads remain explicitly incomplete.
    assert [json.loads(row['payload'])['message']['name'] for row in after[len(before):] if row['role'] == 'tool'] == [
        'list_tool_results', 'read_tool_result']
    assert reopened.tool_result_page(chat, expected[0])['content'] == original


def test_legacy_unbounded_checkpoint_fits_after_compaction(tmp_path):
    store = Store(tmp_path / 'data')
    chat = store.create_chat('Legacy checkpoint')
    # Old checkpoints may include every result in one large tool batch. Their
    # obsolete inline index must not consume all future continuation budgets.
    anchor = store.add_message(chat, 'user', 'Recover saved evidence.')
    saved_ids = [store.add_message(chat, 'tool', 'Original bounded evidence') for _ in range(500)]
    old_checkpoint = {'reason': 'action_round_limit', 'user_message_id': anchor, 'rounds': 10,
                      'saved_result_ids': saved_ids}
    store.add_message(chat, 'notice', 'Legacy pause', status='paused', payload={'checkpoint': old_checkpoint})
    store.add_message(chat, 'user', 'Continue.')
    messages, _ = conversation_messages(store.messages(chat), 'System', 1800)
    assert messages[-1]['content'] == 'Continue.'
    assert len(json.dumps(messages)) <= 1800
    checkpoint = json.loads(messages[0]['content'].split('## Saved pause checkpoint\n')[1].split('\n')[0])
    assert checkpoint['saved_result_count'] == 500
    assert checkpoint['saved_result_ids'][-1] == saved_ids[-1]


def test_saved_result_catalog_bounds_metadata_and_rejects_invalid_pages(tmp_path):
    from letracode.tools import ToolExecutor

    store = Store(tmp_path / 'data')
    chat = store.create_chat('Catalog bounds')
    content = json.dumps({'error': 'Failure detail. ' * 500, 'path': '/source/' + 'x' * 1000})
    result_id = store.add_message(chat, 'tool', content, payload={'message': {
        'role': 'tool', 'name': 'read_file', 'tool_call_id': 'saved', 'content': content}})
    executor = ToolExecutor([], store.directory, lambda _: pytest.fail('Read-only catalog'),
                            threading.Event(), False, False, store=store, chat_id=chat)
    page = json.loads(executor.execute('list_tool_results', {'limit': 1}))
    assert page['results'][0]['result_id'] == result_id
    assert page['results'][0]['metadata_truncated'] is True
    assert len(json.dumps(page)) < 1600
    assert page['next_after_id'] is None
    assert store.tool_result_page(chat, result_id, max_chars=16000)['content'] == content
    for args in ({'after_id': -1}, {'after_id': True}, {'after_id': '0'}, {'limit': 0},
                 {'limit': 21}, {'limit': False}, {'limit': 1.5}, {'chat_id': 'other'},
                 {'through_id': -1}, {'through_id': True}, {'through_id': None}):
        assert 'error' in json.loads(executor.execute('list_tool_results', args))
    assert json.loads(executor.execute('list_tool_results', {'after_id': result_id})) == {
        'results': [], 'through_id': result_id, 'next_after_id': None}


def test_worker_catalog_pagination_does_not_chase_its_own_saved_pages(tmp_path):
    store = Store(tmp_path / 'data')
    chat = store.create_chat('Stable evidence catalog')
    expected = [store.add_message(chat, 'tool', str(index), payload={'message': {
        'role': 'tool', 'name': 'read_file', 'tool_call_id': f'saved_{index}', 'content': str(index)}})
        for index in range(3)]
    store.add_message(chat, 'user', 'List all saved results one per page.')

    class PageAll(ScriptedEngine):
        found = []
        requests = 0

        def complete(self, messages, *args, **kwargs):
            self.requests += 1
            assert_paired_tools(messages)
            arguments = {'limit': 1}
            if messages[-1]['role'] == 'tool':
                page = json.loads(messages[-1]['content'])
                self.found.extend(item['result_id'] for item in page['results'])
                if page['next_after_id'] is None:
                    return {'role': 'assistant', 'content': 'Finished listing saved results.'}
                arguments['after_id'] = page['next_after_id']
                if 'through_id' in page:
                    assert page['through_id'] == expected[-1]
                    arguments['through_id'] = page['through_id']
            return {'role': 'assistant', 'content': '', 'tool_calls': [{
                'id': f'list_{self.requests}', 'type': 'function', 'function': {
                    'name': 'list_tool_results', 'arguments': json.dumps(arguments)}}]}

    engine = PageAll()
    ConversationWorker(store, chat, engine, computer_enabled=False, web_enabled=False).run()
    assert engine.found == expected
    assert engine.requests == 4
    assert store.messages(chat)[-1]['content'] == 'Finished listing saved results.'


def test_compacted_receipts_reference_saved_result_without_protocol_metadata(tmp_path):
    store,chat,paths=linked_reading_chat(tmp_path,1)
    engine=ReadingEngine([paths],context_size=8192,max_tokens=1024)
    ConversationWorker(store,chat,engine).run()
    row=next(row for row in store.messages(chat) if row['role']=='tool')
    receipt=json.loads(engine.requests[1][-1]['content'])
    assert receipt['result_id']==row['id']
    assert 'read_tool_result' in receipt['context_note']
    assert all('saved_result_id' not in message for request in engine.requests for message in request)
    assert json.loads(row['payload'])['saved_result_id']==row['id']


def test_worker_budget_accounts_enabled_schemas_before_generation(tmp_path):
    from letracode.budgeting import RequestUsage
    class Measured(ScriptedEngine):
        requests=[]
        def request_usage(self,messages,tools,cancel,thinking=False):
            # This synthetic template expands tool definitions heavily.
            return RequestUsage(8500 if tools else 100, self.config.max_tokens,128,'synthetic tokenizer')
        def complete(self,messages,tools,*args,**kwargs):
            self.requests.append(messages)
            return {'role':'assistant','content':'Done'}
    store=Store(tmp_path/'data');chat=store.create_chat('Budget')
    prompt='Keep this exact request. 漢字 \\ source'
    store.add_message(chat,'user',prompt)
    engine=Measured()
    ConversationWorker(store,chat,engine).run()
    assert not engine.requests
    assert store.messages(chat)[0]['content']==prompt
    assert store.messages(chat)[-1]['status']=='paused'
    store.add_message(chat,'user','Continue without tools.')
    ConversationWorker(store,chat,engine,use_tools=False).run()
    assert len(engine.requests)==1


def test_worker_exposes_saved_reads_when_computer_and_web_are_disabled(tmp_path):
    class Offline(ScriptedEngine):
        def complete(self,messages,tools,*args,**kwargs):
            names={tool['function']['name'] for tool in tools}
            assert names=={'read_tool_result','list_tool_results','search_history'}
            return {'role':'assistant','content':'Offline history available.'}
    store=Store(tmp_path/'data');chat=store.create_chat('Offline')
    store.add_message(chat,'user','Review saved evidence')
    ConversationWorker(store,chat,Offline(),computer_enabled=False,web_enabled=False,actions_enabled=False).run()
    assert store.messages(chat)[-1]['content']=='Offline history available.'


def test_saved_result_pages_remain_retrievable_after_source_changes_and_compaction(tmp_path):
    class SavedReader(ScriptedEngine):
        seen = ''
        requests = 0
        def complete(self, messages, *args, **kwargs):
            self.requests += 1
            assert_paired_tools(messages)
            if messages[-1]['role']=='tool':
                page=json.loads(messages[-1]['content'])
                assert 'content' in page  # The current bounded page fits in full.
                self.seen += page['content']
                offset=page['next_offset']
                if offset is None:
                    return {'role':'assistant','content':'Read the saved command output.'}
            else:
                offset=0
            return {'role':'assistant','content':'Read saved evidence','tool_calls':[{
                'id':f'page_{self.requests}','type':'function','function':{
                    'name':'read_tool_result','arguments':json.dumps({'result_id':source_id,'offset':offset,'max_chars':1500})}}]}
    store=Store(tmp_path/'data');chat=store.create_chat('Saved evidence')
    source=tmp_path/'source.txt';source.write_text('Original file')
    original=json.dumps({'output':'Saved 漢字 \\ evidence\n'*350, 'exit_code':7, 'timed_out':False},ensure_ascii=False)
    call={'id':'original','type':'function','function':{'name':'run_command','arguments':json.dumps({'command':'synthetic; never executed','cwd':str(tmp_path)})}}
    store.add_message(chat,'user','Earlier work')
    store.add_message(chat,'assistant','Earlier command',payload={'message':{'role':'assistant','content':'Earlier command','tool_calls':[call]}})
    source_id=store.add_message(chat,'tool',original,payload={'message':{'role':'tool','name':'run_command','tool_call_id':'original','content':original}})
    source.unlink()
    store.add_message(chat,'user',f'Read saved result {source_id}.')
    engine=SavedReader()
    ConversationWorker(store,chat,engine,computer_enabled=False,web_enabled=False,actions_enabled=False).run()
    assert engine.seen==original
    assert not source.exists()
    assert store.messages(chat)[-1]['content']=='Read the saved command output.'
    assert len([row for row in store.messages(chat) if row['role']=='tool'])==engine.requests
    messages,_=conversation_messages(store.messages(chat),'System',6500)
    receipts=[json.loads(message['content']) for message in messages if message['role']=='tool']
    assert any(result.get('context_truncated') and 'source_result_id' in result for result in receipts)
    assert Store(tmp_path/'data').tool_result_page(chat,source_id,max_chars=16000)['content']==original


def test_worker_rereads_strand_files_and_reports_configured_model_filename(tmp_path):
    class IdentityReader(ScriptedEngine):
        systems=[]
        def complete(self,messages,*args,**kwargs):
            self.systems.append(messages[0]['content'])
            return {'role':'assistant','content':'Read current context.'}
    store=Store(tmp_path/'data');chat=store.create_chat('Fresh identity')
    from pathlib import Path
    identity=Path(store.memory.snapshot('identity')['path'])
    identity.write_text('Identity correction FIRST',encoding='utf-8')
    relative=identity.relative_to(store.memory.root).as_posix()
    store.memory.set_active(relative, True, store.memory.file_snapshot(relative)['sha256'])
    engine=IdentityReader()
    engine.config=type('Config',(),{'context_size':32768,'max_tokens':1024,'model_path':'/models/configured-model.gguf'})()
    store.add_message(chat,'user','Read current identity')
    ConversationWorker(store,chat,engine).run()
    identity.write_text('Identity correction SECOND',encoding='utf-8')
    store.add_message(chat,'user','Read the correction')
    ConversationWorker(store,chat,engine).run()
    assert 'Identity correction FIRST' in engine.systems[0]
    assert 'Identity correction SECOND' in engine.systems[1]
    assert 'Identity correction FIRST' not in engine.systems[1]
    assert all('configured-model.gguf' in system for system in engine.systems)


@pytest.mark.parametrize('mutation', ['write_file', 'external_edit', 'failed_command'])
def test_worker_refreshes_source_context_before_each_model_request(tmp_path, mutation):
    import hashlib
    from tools.run_acceptance import python_command

    folder = tmp_path / 'project'; folder.mkdir()
    source = folder / 'marker.py'
    before = 'SOURCE_VERSION = "BEFORE_CHANGE"\n'
    after = 'SOURCE_VERSION = "AFTER_CHANGE"\n'
    source.write_bytes(before.encode('utf-8'))
    store = Store(tmp_path / 'data')
    project = store.create_project('Fresh source')
    instructions = 'Inspect SOURCE_VERSION. Preserve this objective and its acceptance checks.'
    store.update_project(project, instructions=instructions)
    store.link(project, folder)
    chat = store.create_chat('Refresh every round', project)
    store.add_message(chat, 'user', 'Inspect SOURCE_VERSION in marker.py, then report the current value.')

    class MutatingEngine(ScriptedEngine):
        config = type('Config', (), {'context_size': 32768, 'max_tokens': 1024})()

        def __init__(self):
            self.requests = []

        def complete(self, messages, *args, **kwargs):
            self.requests.append(copy.deepcopy(messages))
            assert_paired_tools(messages)
            assert instructions in messages[0]['content']
            if len(self.requests) == 1:
                assert before.strip() in messages[0]['content']
                if mutation == 'write_file':
                    name, args = 'write_file', {'path': str(source), 'content': after,
                                                'expected_sha256': hashlib.sha256(before.encode()).hexdigest()}
                elif mutation == 'external_edit':
                    # An ordinary editor changes the source between requests;
                    # the following tool only reads it.
                    source.write_text(after)
                    name, args = 'read_file', {'path': str(source)}
                else:
                    script = f'from pathlib import Path; Path({str(source)!r}).write_text({after!r}); raise SystemExit(7)'
                    name, args = 'run_command', {'command': python_command('-c', script),
                                                'cwd': str(folder), 'timeout': 30 if sys.platform == 'win32' else 5}
                return {'role': 'assistant', 'content': '', 'tool_calls': [{
                    'id': 'change', 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args)}}]}
            if mutation == 'failed_command':
                assert json.loads(messages[-1]['content'])['exit_code'] == 7
            assert before.strip() not in messages[0]['content']
            assert after.strip() in messages[0]['content']
            return {'role': 'assistant', 'content': 'Current source: AFTER_CHANGE.'}

    engine = MutatingEngine()
    worker = ConversationWorker(store, chat, engine, web_enabled=False)
    worker.approval_needed.connect(lambda pending: pending.decide(True))
    worker.run()
    assert len(engine.requests) == 2
    assert store.messages(chat)[-1]['content'] == 'Current source: AFTER_CHANGE.'
    assert source.read_text() == after


@pytest.mark.parametrize('context_size,max_tokens,instruction_chars', [
    (8192, 2048, 6580),
    (32768, 3072, 22000),
])
def test_runtime_count_accepts_intact_core_larger_than_retrieval_allowance(
    tmp_path, context_size, max_tokens, instruction_chars,
):
    from letracode.budgeting import RequestUsage
    instructions='Essential instruction. ' + 'x' * instruction_chars
    class FittingCore(ScriptedEngine):
        measured=[]
        generated=[]
        def request_usage(self, messages, tools, cancel, thinking=False):
            self.measured.append(copy.deepcopy(messages))
            assert instructions in messages[0]['content']
            # Synthetic runtime reports an exact count that fits this model.
            return RequestUsage(2457 if context_size==8192 else 8000, max_tokens, 128, 'synthetic tokenizer')
        def complete(self, messages, tools, *args, **kwargs):
            self.generated.append(copy.deepcopy(messages))
            return {'role':'assistant','content':'The intact instructions fit.'}
    store=Store(tmp_path/'data');project=store.create_project('Large core')
    store.update_project(project,instructions=instructions)
    (store.strand.root/'memory/global.md').write_text('Optional memory marker',encoding='utf-8')
    chat=store.create_chat('Token authority',project)
    prompt='Keep every instruction and this exact request.'
    store.add_message(chat,'user',prompt)
    engine=FittingCore()
    engine.config=type('Config',(),{'context_size':context_size,'max_tokens':max_tokens})()
    ConversationWorker(store,chat,engine,use_tools=False).run()
    assert len(engine.generated)==1
    assert engine.measured
    assert all(instructions in messages[0]['content'] for messages in engine.measured)
    assert 'Optional memory marker' not in engine.generated[0][0]['content']
    assert 'Optional memory and source excerpts omitted' in engine.generated[0][0]['content']
    assert '## Source inventory' not in engine.generated[0][0]['content']
    assert engine.generated[0][-1]['content']==prompt
    assert store.project(project)['instructions']==instructions
    assert store.messages(chat)[-1]['content']=='The intact instructions fit.'


def test_runtime_count_pauses_truly_oversized_core_without_truncation(tmp_path):
    from letracode.budgeting import RequestUsage
    instructions='Keep all of this core. ' + 'z' * 22000
    class OversizedCore(ScriptedEngine):
        measured=[]
        generated=[]
        def request_usage(self, messages, tools, cancel, thinking=False):
            self.measured.append(copy.deepcopy(messages))
            assert instructions in messages[0]['content']
            return RequestUsage(9000,2048,128,'synthetic tokenizer')
        def complete(self,messages,*args,**kwargs):
            self.generated.append(messages)
            return {'role':'assistant','content':'Should not generate'}
    store=Store(tmp_path/'data');project=store.create_project('Oversized core')
    store.update_project(project,instructions=instructions)
    chat=store.create_chat('Pause without truncation',project)
    prompt='Keep this exact request. 漢字'
    store.add_message(chat,'user',prompt)
    engine=OversizedCore()
    engine.config=type('Config',(),{'context_size':8192,'max_tokens':2048})()
    ConversationWorker(store,chat,engine,use_tools=False).run()
    assert engine.measured
    assert not engine.generated
    assert all(instructions in messages[0]['content'] for messages in engine.measured)
    assert store.project(project)['instructions']==instructions
    rows=store.messages(chat)
    assert rows[0]['content']==prompt
    assert rows[-1]['status']=='paused'
    assert json.loads(rows[-1]['payload'])['checkpoint']['reason']=='context_limit'


# The application run/evidence instructions now occupy mandatory context. At
# 5400 words the measured minimal request is 33605 tokens; 5200 words leaves
# only enough room after optional excerpts are omitted, still below 32768.
@pytest.mark.parametrize('word_count,should_fit', [(5200, True), (6000, False)])
def test_fallback_budget_crosses_optional_context_plateau_without_cutting_core_or_user(
    tmp_path, monkeypatch, word_count, should_fit,
):
    from letracode.budgeting import fallback_usage
    from letracode import worker as worker_module

    class FallbackEngine:
        # No request_usage method: exercise the supported conservative counter.
        config = type('Config', (), {'context_size': 32768, 'max_tokens': 1024})()

        def __init__(self):
            self.generated = []

        def start(self, *args):
            pass

        def complete(self, messages, *args):
            self.generated.append(copy.deepcopy(messages))
            return {'role': 'assistant', 'content': 'The intact request fits.'}

    store = Store(tmp_path / 'data')
    project = store.create_project('Optional context plateau')
    chat = store.create_chat('Optional context plateau', project)
    # Reference memory is now opt-in. Linked source excerpts still exercise
    # optional-context plateaus without making dormant Memory implicit core.
    source = tmp_path / 'reference.txt'
    source.write_text('Optional source word. ' * 130)
    store.link(project, source)
    prompt = 'Please analyze this passage: ' + 'word ' * word_count
    store.add_message(chat, 'user', prompt)
    identity = store.memory.snapshot('identity')['text'].strip()
    preferences = store.memory.snapshot('preferences')['text'].strip()
    built = []
    build_context = worker_module.build_context

    def record_context(project, roots, query, budget, *args, **kwargs):
        context = build_context(project, roots, query, budget, *args, **kwargs)
        built.append((budget, context))
        return context

    monkeypatch.setattr(worker_module, 'build_context', record_context)
    engine = FallbackEngine()
    worker = ConversationWorker(store, chat, engine, use_tools=False)
    worker.run()
    assert built[0][1] == built[1][1]  # Smaller allowances can yield identical excerpts.
    assert all(identity in context and preferences in context for _, context in built)
    assert store.messages(chat)[0]['content'] == prompt
    if should_fit:
        assert len(engine.generated) == 1
        messages = engine.generated[0]
        assert messages[-1] == {'role': 'user', 'content': prompt}
        assert fallback_usage(messages, None, 1024).total_tokens <= 32768
        assert 'Optional memory and source excerpts omitted' in messages[0]['content']
        assert store.messages(chat)[-1]['content'] == 'The intact request fits.'
    else:
        assert not engine.generated
        # Even if successive context strings are identical, exhaust permitted
        # optional reductions before declaring the intact core/request too big.
        assert built[-1][0] == 0
        rows = store.messages(chat)
        assert rows[-1]['status'] == 'paused'
        assert json.loads(rows[-1]['payload'])['checkpoint']['reason'] == 'context_limit'
