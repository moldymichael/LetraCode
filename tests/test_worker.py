import threading
from letracode.store import Store
from letracode.worker import ConversationWorker


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
