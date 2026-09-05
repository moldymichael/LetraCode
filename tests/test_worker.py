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


def test_excess_tool_requests_do_not_poison_next_chat_turn(tmp_path):
    class TooMany(ScriptedEngine):
        def complete(self,*args,**kwargs):
            return {'role':'assistant','content':'Many actions','tool_calls':[{'id':str(i),'type':'function','function':{'name':'list_files','arguments':'{}'}} for i in range(9)]}
    from letracode.worker import conversation_messages
    s=Store(tmp_path/'data'); c=s.create_chat('Limits'); s.add_message(c,'user','Do work')
    w=ConversationWorker(s,c,TooMany()); w.run()
    s.add_message(c,'user','Continue')
    messages,_=conversation_messages(s.messages(c),'System',20000)
    assert all(not m.get('tool_calls') for m in messages)


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
    with pytest.raises(ValueError, match='latest conversation turn'):
        conversation_messages(store.messages(chat), 'System instructions', 8000)
    assert store.messages(chat)[0]['content'] == prompt
