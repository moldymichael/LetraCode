import copy
import json
import threading

import pytest

from letracode.store import Store
from letracode.worker import ConversationWorker, conversation_messages


class ScriptedEngine:
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
        return {'role':'assistant','content':'','tool_calls':[{'id':'call_1','type':'function','function':{'name':'write_file','arguments':'{"path":"/tmp/letracode-never-write-test","content":"bad"}'}}]}
    def cancel(self):
        pass


def test_worker_routes_denial_to_model_and_persists_turn(tmp_path):
    s = Store(tmp_path / 'data')
    c = s.create_chat('Tools')
    s.add_message(c,'user','Try the action')
    w = ConversationWorker(s, c, ScriptedEngine())
    w.approval_needed.connect(lambda pending: pending.decide(False))
    w.run()
    messages = s.messages(c)
    assert any(m['role']=='tool' and 'denied' in m['content'].lower() for m in messages)
    assert messages[-1]['content'] == 'The action was denied. No file was changed.'
    assert messages[-1]['status'] == 'complete'


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


def test_excess_tool_requests_are_all_saved_as_not_executed_and_pause(tmp_path):
    class TooMany(ScriptedEngine):
        requests = 0
        def complete(self, messages, *args, **kwargs):
            self.requests += 1
            assert_paired_tools(messages)
            return {'role':'assistant','content':'Many actions','tool_calls':[
                {'id':f'{self.requests}_{i}','type':'function','function':{
                    'name':'write_file','arguments':json.dumps({'path':str(tmp_path / 'never.txt'), 'content':'bad'})}}
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
        if index == len(self.batches):
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
    assert rows[-1]['content'] == 'Finished reading the requested files.'
    assert rows[-1]['status'] == 'complete'
    assert not any(row['status'] == 'error' for row in rows)
    assert len(engine.requests) == 9
    assert engine.config.context_size == 32768
    for request in engine.requests:
        # Existing 32768-context / 3072-reply character allowance: 57392.
        assert len(json.dumps(request, ensure_ascii=False)) <= 57392
        assert_paired_tools(request)
        assert request[0] == engine.requests[0][0]
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
               for m in engine.requests[-1] if m['role'] == 'tool')

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
    assert replay[-1]['content'] == 'Finished reading the requested files.'
    assert reopened.messages(chat) == rows


def test_single_oversized_read_keeps_a_marked_preview_and_full_saved_result(tmp_path):
    store, chat, paths = linked_reading_chat(tmp_path, 1, escaped=True)
    engine = ReadingEngine([paths], context_size=8192, max_tokens=1024)
    ConversationWorker(store, chat, engine).run()
    rows = store.messages(chat)
    assert rows[-1]['content'] == 'Finished reading the requested files.'
    assert len(engine.requests) == 2
    request = engine.requests[-1]
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


def test_action_round_pause_resumes_from_saved_history_without_reexecution(tmp_path):
    class Endless(ScriptedEngine):
        requests=0
        def complete(self, messages, *args, **kwargs):
            self.requests+=1
            return {'role':'assistant','content':'Inspect missing source','tool_calls':[{
                'id':str(self.requests),'type':'function','function':{
                    'name':'read_file','arguments':json.dumps({'path':str(path)})}}]}
    store=Store(tmp_path/'data'); chat=store.create_chat('Pause')
    path=tmp_path/'missing.txt'
    engine=Endless()
    store.add_message(chat,'user','Inspect')
    worker=ConversationWorker(store,chat,engine)
    worker.approval_needed.connect(lambda pending: pending.decide(False))
    worker.run()
    rows=store.messages(chat)
    assert engine.requests==10
    assert rows[-1]['status']=='paused'
    checkpoint=json.loads(rows[-1]['payload'])['checkpoint']
    assert checkpoint['reason']=='action_round_limit'
    assert len(checkpoint['saved_result_ids'])==10
    class Resume(ScriptedEngine):
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


def test_compacted_receipts_reference_saved_result_without_protocol_metadata(tmp_path):
    store,chat,paths=linked_reading_chat(tmp_path,1)
    engine=ReadingEngine([paths],context_size=8192,max_tokens=1024)
    ConversationWorker(store,chat,engine).run()
    row=next(row for row in store.messages(chat) if row['role']=='tool')
    receipt=json.loads(engine.requests[-1][-1]['content'])
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
            assert names=={'read_tool_result','read_memory'}
            return {'role':'assistant','content':'Offline history available.'}
    store=Store(tmp_path/'data');chat=store.create_chat('Offline')
    store.add_message(chat,'user','Review saved evidence')
    ConversationWorker(store,chat,Offline(),computer_enabled=False,web_enabled=False).run()
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
    ConversationWorker(store,chat,engine,computer_enabled=False,web_enabled=False).run()
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
    identity=store.strand.root/'identity/strand.md'
    identity.write_text('Identity correction FIRST',encoding='utf-8')
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
