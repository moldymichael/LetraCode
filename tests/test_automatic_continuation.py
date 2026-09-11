"""Lifecycle checks use scripted inference, real chat persistence and file tools."""
import copy
import json
from pathlib import Path
import threading

from letracode.budgeting import RequestUsage
from letracode.evidence import evidence_state
from letracode.store import Store
from letracode.worker import ConversationWorker


class Pages:
    config = type('Config', (), {'context_size':32768, 'max_tokens':1024})()
    def __init__(self, paths):
        self.paths = paths
        self.requests = []
        self.cancelled = False
    def request_usage(self, messages, tools, cancel, thinking=False):
        return RequestUsage(len(json.dumps(messages)) // 3 + 1500, 1024, 128, 'scripted fixture accounting')
    def start(self, cancel, on_status=lambda _:None):
        pass
    def cancel(self):
        self.cancelled = True
    def complete(self, messages, tools, cancel, on_delta, thinking=False):
        self.requests.append(copy.deepcopy(messages))
        index = len(self.requests) - 1
        if index == len(self.paths):
            return {'role':'assistant','content':'The final route is East Pier; the departure hour is unknown.'}
        return {'role':'assistant','content':f'Inspect chapter {index + 1}.', 'tool_calls':[
            {'id':f'page-{index}', 'type':'function', 'function':{'name':'read_file',
             'arguments':json.dumps({'path':str(self.paths[index]),'offset':0,'max_chars':4000})}}]}


def fixture(tmp_path, count=23):
    directory=tmp_path/'sources'; directory.mkdir()
    paths=[]
    for index in range(count):
        path=directory/f'chapter-{index:02}.txt'
        path.write_text(f'Chapter {index}: route evidence {index}. Later correction: East Pier. No departure hour.\n')
        paths.append(path)
    store=Store(tmp_path/'data'); project=store.create_project('Synthetic Grey Area reading')
    store.link(project,directory); chat=store.create_chat('Whole work',project)
    prompt='Read the whole work. What is the final route, and what departure hour is established?'
    store.add_message(chat,'user',prompt)
    return store,chat,paths,prompt


def test_one_user_request_crosses_three_segments_with_all_evidence(tmp_path):
    store,chat,paths,prompt=fixture(tmp_path)
    engine=Pages(paths)
    ConversationWorker(store,chat,engine).run()
    rows=store.messages(chat)
    assert len(engine.requests)==24  # Previously ended after ten.
    assert [r['content'] for r in rows if r['role']=='user']==[prompt]
    assert len([r for r in rows if r['role']=='tool'])==23
    assert sum(bool(json.loads(r['payload']).get('segment_boundary')) for r in rows)==2
    assert all(any(m['role']=='user' and m['content']==prompt for m in request) for request in engine.requests)
    assert any('East Pier' in r['content'] for r in rows if r['role']=='assistant')
    assert not any(r['status']=='error' for r in rows)


def test_stop_at_boundary_cannot_dispatch_next_segment(tmp_path):
    store,chat,paths,prompt=fixture(tmp_path)
    engine=Pages(paths); worker=ConversationWorker(store,chat,engine)
    def boundary_stop():
        if any(json.loads(r['payload']).get('segment_boundary') for r in store.messages(chat)):
            worker.request_stop()
    worker.changed.connect(boundary_stop)
    worker.run()
    assert len(engine.requests)==10
    rows=store.messages(chat)
    assert rows[-1]['status']=='interrupted'
    assert 'stopped' in rows[-1]['content'].lower()
    assert len([r for r in rows if r['role']=='tool'])==10


def test_denial_stops_other_calls_and_preserves_paired_not_run_outcomes(tmp_path):
    store,chat,paths,prompt=fixture(tmp_path,1)
    class Denied(Pages):
        def complete(self,*args,**kwargs):
            self.requests.append(True)
            return {'role':'assistant','content':'Proposed edits','tool_calls':[
                {'id':f'write-{i}','type':'function','function':{'name':'write_file','arguments':json.dumps({
                    'path':str(tmp_path/f'never-{i}.txt'),'content':'No','expected_sha256':None})}}
                for i in range(2)]}
    engine=Denied([]);worker=ConversationWorker(store,chat,engine)
    approvals=[]
    worker.approval_needed.connect(lambda p:(approvals.append(p),p.decide(False)))
    worker.run()
    assert len(engine.requests)==1
    assert len(approvals)==1
    outcomes=[json.loads(json.loads(r['payload'])['message']['content']) for r in store.messages(chat) if r['role']=='tool']
    assert len(outcomes)==2
    assert 'denied' in outcomes[0]
    assert outcomes[1]['executed'] is False
    assert not list(tmp_path.glob('never-*'))
    assert 'approval' in store.messages(chat)[-1]['content'].lower()


class Repeated(Pages):
    def __init__(self, name, arguments):
        super().__init__([])
        self.name, self.arguments = name, arguments
    def complete(self, messages, *args, **kwargs):
        self.requests.append(copy.deepcopy(messages))
        return {'role':'assistant','content':'One step', 'tool_calls':[
            {'id':f'call-{len(self.requests)}','type':'function','function':{
                'name':self.name,'arguments':json.dumps(self.arguments)}}]}


def test_repeated_missing_source_stops_after_three_failures(tmp_path):
    store,chat,paths,prompt=fixture(tmp_path,1)
    engine=Repeated('read_file',{'path':str(paths[0].parent/'missing.txt')})
    ConversationWorker(store,chat,engine).run()
    assert len(engine.requests)==3
    final=store.messages(chat)[-1]
    assert final['status']=='paused'
    assert json.loads(final['payload'])['checkpoint']['reason']=='no_progress'
    assert 'failed' in final['content'].lower()


def test_repeated_successful_read_stops_without_treating_saved_ids_as_progress(tmp_path):
    store,chat,paths,prompt=fixture(tmp_path,1)
    engine=Repeated('read_file',{'path':str(paths[0]),'offset':0,'max_chars':4000})
    ConversationWorker(store,chat,engine).run()
    assert len(engine.requests)==4
    final=store.messages(chat)[-1]
    assert json.loads(final['payload'])['checkpoint']['reason']=='no_progress'
    assert 'repeated' in final['content'].lower()


def test_completed_command_is_not_reexecuted_to_recover_its_result(tmp_path):
    import sys
    from tools.run_acceptance import python_command
    store,chat,paths,prompt=fixture(tmp_path,1)
    marker=tmp_path/'command-count.txt'
    command = python_command('-c', f'from pathlib import Path; Path({str(marker)!r}).open("ab").write(b"x")')
    engine=Repeated('run_command',{'command':command,'cwd':str(tmp_path),'timeout':30 if sys.platform == 'win32' else 5})
    worker=ConversationWorker(store,chat,engine)
    approvals=[]
    worker.approval_needed.connect(lambda p:(approvals.append(p),p.decide(True)))
    worker.run()
    assert marker.read_text()=='x'
    assert len(approvals)==1
    assert len(engine.requests)==4
    outcomes=[json.loads(json.loads(r['payload'])['message']['content']) for r in store.messages(chat) if r['role']=='tool']
    assert outcomes[0]['executed'] is True
    assert all(r['code']=='duplicate_action' and r['executed'] is False for r in outcomes[1:])


def test_request_budget_prevents_another_segment_dispatch(tmp_path):
    from letracode.continuation import RunLimits
    store,chat,paths,prompt=fixture(tmp_path)
    engine=Pages(paths)
    ConversationWorker(store,chat,engine,limits=RunLimits(max_requests=11)).run()
    assert len(engine.requests)==11
    checkpoint=json.loads(store.messages(chat)[-1]['payload'])['checkpoint']
    assert checkpoint['reason']=='request_budget'
    assert checkpoint['continuation']['requests']==11
    assert checkpoint['continuation']['segments']==2
    before=store.messages(chat)
    ConversationWorker(Store(tmp_path/'data'),chat,engine).run()
    assert len(engine.requests)==11
    assert store.messages(chat)==before  # Reopening is never dispatch authority.


def test_action_budget_records_unexecuted_batch_without_approval(tmp_path):
    from letracode.continuation import RunLimits
    store,chat,paths,prompt=fixture(tmp_path,1)
    engine=Repeated('read_file',{'path':str(paths[0])})
    worker=ConversationWorker(store,chat,engine,limits=RunLimits(max_actions=1))
    worker.run()
    assert len(engine.requests)==2
    outcomes=[json.loads(json.loads(r['payload'])['message']['content']) for r in store.messages(chat) if r['role']=='tool']
    assert outcomes[1]['executed'] is False
    assert outcomes[1]['code']=='action_budget'
    assert json.loads(store.messages(chat)[-1]['payload'])['checkpoint']['reason']=='action_budget'


def test_wall_deadline_cancels_a_silent_engine_and_is_not_user_stop(tmp_path):
    from letracode.continuation import RunLimits
    from letracode.engine import Cancelled
    store,chat,paths,prompt=fixture(tmp_path,1)
    class Silent(Pages):
        def complete(self,messages,tools,cancel,*args):
            self.requests.append(messages)
            assert cancel.wait(2), 'wall limit failed to cancel a blocked model'
            raise Cancelled()
    engine=Silent(paths)
    ConversationWorker(store,chat,engine,limits=RunLimits(max_seconds=0.1)).run()
    assert len(engine.requests)==1
    final=store.messages(chat)[-1]
    assert final['status']=='paused'
    assert json.loads(final['payload'])['checkpoint']['reason']=='time_budget'
    assert not any(r['status']=='streaming' for r in store.messages(chat))


def test_pending_approval_blocks_all_later_model_requests_until_stop(tmp_path):
    store,chat,paths,prompt=fixture(tmp_path,1)
    engine=Repeated('run_command',{'command':'printf should-not-run','cwd':str(tmp_path),'timeout':5})
    worker=ConversationWorker(store,chat,engine)
    seen=[]
    def stop_pending(pending):
        seen.append(len(engine.requests))
        assert not pending.event.is_set()
        worker.request_stop()
    worker.approval_needed.connect(stop_pending)
    worker.run()
    assert seen==[1] and len(engine.requests)==1
    assert store.messages(chat)[-1]['status']=='interrupted'


def test_partial_source_answer_is_released_only_after_automatic_reads_expose_all_gaps(tmp_path):
    store,chat,paths,prompt=fixture(tmp_path,1)
    contents = 'middle evidence '*500
    paths[0].write_text(contents)
    class Overclaim(Pages):
        def complete(self,messages,*args,**kwargs):
            self.requests.append(copy.deepcopy(messages))
            if len(self.requests)>1:
                return {'role':'assistant','content':'I inspected the whole work. All complete.'}
            return {'role':'assistant','content':'Read a page','tool_calls':[
                {'id':'partial','type':'function','function':{'name':'read_file','arguments':json.dumps({
                    'path':str(paths[0]),'offset':0,'max_chars':100})}}]}
    engine=Overclaim(paths)
    ConversationWorker(store,chat,engine).run()
    rows=store.messages(chat)
    origin = next(row['id'] for row in rows if row['role'] == 'user')
    claims=[r for r in rows if r['role']=='assistant' and 'All complete.' in r['content']]
    assert len(claims) >= 2
    assert all(r['status']=='incomplete' and not json.loads(r['payload'])['pause_context_closed']
               for r in claims[:-1])
    assert claims[-1] == rows[-1] and claims[-1]['status'] == 'complete'
    for claim in claims:
        state = evidence_state([row for row in rows if row['id'] <= claim['id']], origin)
        assert state['incomplete'] is (claim['status'] == 'incomplete')
    pages = [json.loads(json.loads(row['payload'])['message']['content'])
             for row in rows if row['role'] == 'tool']
    assert pages[0]['text'] == contents[:100]
    assert ''.join(page['text'] for page in pages) == contents
    cursor = 0
    for page in pages:
        assert page['offset'] == cursor
        cursor += len(page['text'])
        assert page['coverage']['ranges'] == [[page['offset'], cursor]]
    automatic = [row for row in rows if json.loads(row['payload']).get('application_generated')
                 == 'source_read_recovery']
    assert len(automatic) == len(pages) - 1
    assert evidence_state(rows, origin)['files'][0]['exposed_ranges'] == [[0, len(contents)]]
    assert [row['content'] for row in rows if row['role'] == 'user'] == [prompt]
    assert not any(row['status'] == 'paused' for row in rows)
    assert any(r['role']=='notice' and 'coverage is incomplete' in r['content'] for r in rows)


def test_ordinary_answer_without_source_work_does_not_enter_coverage_loop(tmp_path):
    store,chat,paths,prompt=fixture(tmp_path,1)
    engine=Pages([])
    ConversationWorker(store,chat,engine,use_tools=False).run()
    assert len(engine.requests)==1
    assert store.messages(chat)[-1]['status']=='complete'
    assert json.loads(store.messages(chat)[-1]['payload'])['task_outcome']=='response_unverified'
