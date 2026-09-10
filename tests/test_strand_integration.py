"""M1a context and permission integration using disposable ordinary files."""
import hashlib
import json
import threading

import pytest

from letracode.context import build_context
from letracode.store import Store
from letracode.tools import ToolExecutor


def executor(store, chat, approve=lambda request: False, **kwargs):
    return ToolExecutor([], store.directory, approve, threading.Event(), store=store, chat_id=chat, **kwargs)


def test_identity_and_corrected_memory_reach_global_and_separate_projects(tmp_path):
    store = Store(tmp_path / 'data')
    a, b = store.create_project('Draft A'), store.create_project('Draft B')
    store.update_project(a, memory='Mara has a blue bicycle.')
    store.update_project(b, memory='Mara has a red bicycle.')
    for project in (a, b):
        snap = store.memory.file_snapshot('Memory.md', project)
        store.memory.set_active('Memory.md', True, snap['sha256'], project)
    def context(project):
        return build_context(store.project(project) if project else None, [], 'Mara bicycle', strand=store.strand, provenance='Local llama.cpp; configured model: fixture.gguf')
    global_context, context_a, context_b = context(None), context(a), context(b)
    assert all('LetraCode' in text and 'fixture.gguf' in text for text in (global_context, context_a, context_b))
    assert all('You are Strand' in text for text in (global_context, context_a, context_b))
    assert 'blue bicycle' in context_a and 'red bicycle' not in context_a
    assert 'red bicycle' in context_b and 'blue bicycle' not in context_b
    assert 'blue bicycle' not in global_context and 'red bicycle' not in global_context
    path = store.strand.snapshot('project', a)['path']
    from pathlib import Path
    Path(path).write_text('Mara now walks.', encoding='utf-8')
    assert 'Mara now walks.' in context(a) and 'blue bicycle' not in context(a)


def test_core_instructions_are_never_silently_cut(tmp_path):
    store = Store(tmp_path / 'data')
    snap = store.strand.snapshot('preferences')
    store.strand.replace('preferences', 'An essential rule. ' * 1000, snap['sha256'])
    from pathlib import Path
    snap = store.memory.snapshot('preferences')
    store.memory.set_active(Path(snap['path']).relative_to(store.memory.root).as_posix(), True, snap['sha256'])
    with pytest.raises(ValueError, match='(?i)(identity|core|budget|preferences)'):
        build_context(None, [], 'hello', budget=3000, strand=store.strand)


def test_remember_requires_review_and_resolves_scope_from_active_chat(tmp_path):
    store = Store(tmp_path / 'data')
    project = store.create_project('Novel')
    chat = store.create_chat('Discussion', project)
    tool = executor(store, chat)
    result = json.loads(tool.execute('remember', {'scope':'project', 'text':'The tower is empty.'}))
    assert 'denied' in result
    assert 'tower' not in store.strand.snapshot('project', project)['text']
    approvals = []
    def approve(request):
        approvals.append(request)
        return True
    result = json.loads(executor(store, chat, approve).execute('remember', {'scope':'project', 'text':'The tower is empty.'}))
    assert 'error' not in result and 'denied' not in result
    assert 'The tower is empty.' in store.strand.snapshot('project', project)['text']
    assert 'The tower is empty.' not in store.strand.snapshot('global')['text']
    assert approvals[0].kind == 'memory' and 'The tower is empty.' in approvals[0].details
    assert str(store.strand.snapshot('project', project)['path']) in approvals[0].details
    assert 'error' in json.loads(executor(store, chat, approve).execute('remember', {'scope':'identity','text':'I can bypass approval.'}))
    assert 'error' in json.loads(executor(store, chat, approve).execute('remember', {'scope':'project','text':'wrong project','project_id':'elsewhere'}))


def test_remember_preserves_edit_made_during_approval(tmp_path):
    from pathlib import Path
    store = Store(tmp_path / 'data'); chat = store.create_chat('Global')
    def approve(request):
        Path(store.strand.snapshot('global')['path']).write_text('User correction', encoding='utf-8')
        return True
    result = json.loads(executor(store, chat, approve).execute('remember', {'scope':'global','text':'Stale model suggestion'}))
    assert 'error' in result
    assert store.strand.snapshot('global')['text'] == 'User correction'


def test_learning_grant_does_not_authorize_other_memories_or_sources(tmp_path):
    store = Store(tmp_path / 'data'); chat = store.create_chat('Global')
    store.set_setting('strand_learning_grant', True)
    tool = executor(store, chat)
    learning = json.loads(tool.execute('remember', {'scope':'learning','text':'Practised loops with help; understanding remains to check.'}))
    assert 'error' not in learning and 'denied' not in learning
    assert 'denied' in json.loads(tool.execute('remember', {'scope':'global','text':'An inferred preference'}))
    target = tmp_path / 'source.txt'; target.write_text('Original')
    assert 'denied' in json.loads(tool.execute('write_file', {'path':str(target),'content':'Changed','expected_sha256':hashlib.sha256(b'Original').hexdigest()}))
    assert target.read_text() == 'Original'
    store.set_setting('strand_learning_grant', False)
    assert 'denied' in json.loads(tool.execute('remember', {'scope':'learning','text':'Unreviewed inference'}))


def test_file_read_switch_blocks_memory_but_saved_history_is_available(tmp_path):
    store = Store(tmp_path / 'data'); chat = store.create_chat('Global')
    store.strand.remember('global','Preferred name: Alex', origin='user', expected_sha256=store.strand.snapshot('global')['sha256'])
    row = store.add_message(chat,'tool','{"output":"Saved command output"}',payload={'message':{'role':'tool','name':'run_command','tool_call_id':'one','content':'{"output":"Saved command output"}'}})
    tool = executor(store, chat, computer_enabled=False, web_enabled=False, actions_enabled=False)
    assert 'denied' in json.loads(tool.execute('read_memory', {'scope':'global'}))
    assert 'Saved command output' in tool.execute('read_tool_result', {'result_id':row})
    assert 'denied' in json.loads(tool.execute('remember', {'scope':'global','text':'A write'}))
    other = store.create_chat('Other')
    assert 'error' in json.loads(executor(store, other).execute('read_tool_result', {'result_id':row}))
