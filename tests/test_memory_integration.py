"""Memory selection and retrieval keep project and approval boundaries."""
import json
import threading
from pathlib import Path

import pytest

from letracode.context import build_context
from letracode.store import Store
from letracode.tools import ToolExecutor


def executor(store, chat, approve=lambda _: False, **kwargs):
    return ToolExecutor([], store.directory, approve, threading.Event(),
                        store=store, chat_id=chat, **kwargs)


def test_memory_tool_catalog_offers_user_paths_and_retrieval():
    from letracode.tools import TOOL_SCHEMAS, FILE_READ_TOOLS
    definitions = {tool['function']['name']: tool['function'] for tool in TOOL_SCHEMAS}
    assert {'list_memory', 'search_memory'} <= definitions.keys()
    assert {'list_memory', 'search_memory'} <= set(FILE_READ_TOOLS)
    assert 'path' in definitions['read_memory']['parameters']['properties']
    assert 'path' in definitions['remember']['parameters']['properties']


def test_nested_memory_tools_read_search_and_list_only_current_scope(tmp_path):
    store = Store(tmp_path / 'data')
    a, b = store.create_project('A'), store.create_project('B')
    store.memory.create_folder('Research', project_id=a)
    store.memory.create_file('Research/notes.md', 'The nebula contains seven stars.', project_id=a)
    store.memory.create_file('private.txt', 'Unrelated project secret', project_id=b)
    chat = store.create_chat(project_id=a)
    tool = executor(store, chat, computer_enabled=True, web_enabled=False, actions_enabled=False)
    listing = json.loads(tool.execute('list_memory', {'scope': 'project'}))
    assert any(row['path'] == 'Research/notes.md' for row in listing['entries'])
    assert 'private.txt' not in json.dumps(listing)
    found = json.loads(tool.execute('search_memory', {'scope': 'project', 'query': 'nebula'}))
    assert 'seven stars' in json.dumps(found)
    page = json.loads(tool.execute('read_memory', {'scope': 'project', 'path': 'Research/notes.md', 'max_chars': 10}))
    assert page['text'] == 'The nebula' and page['next_offset'] == 10
    for args in ({'scope': 'project', 'path': '../private.txt'},
                 {'scope': 'project', 'path': 'private.txt', 'project_id': b},
                 {'scope': 'global', 'path': f'.projects/{b}/private.txt'}):
        assert 'error' in json.loads(tool.execute('read_memory', args))


def test_remember_arbitrary_file_requires_review_and_checks_stale_snapshot(tmp_path):
    store = Store(tmp_path / 'data'); chat = store.create_chat()
    store.memory.create_file('notes.md', 'Original')
    store.set_setting('strand_learning_grant', True)
    denied = json.loads(executor(store, chat).execute('remember', {'scope': 'global', 'path': 'notes.md', 'text': 'Unapproved'}))
    assert 'denied' in denied
    def review(request):
        assert request.kind == 'memory' and 'notes.md' in request.details
        Path(store.memory.file_snapshot('notes.md')['path']).write_text('External edit')
        return True
    failed = json.loads(executor(store, chat, review).execute('remember', {'scope': 'global', 'path': 'notes.md', 'text': 'Stale suggestion'}))
    assert 'error' in failed and store.memory.file_snapshot('notes.md')['text'] == 'External edit'
    saved = json.loads(executor(store, chat, lambda _: True).execute('remember', {'scope': 'global', 'path': 'notes.md', 'text': 'Reviewed note'}))
    assert saved['status'] == 'saved'
    assert 'Reviewed note' in store.memory.file_snapshot('notes.md')['text']
    store.memory.undo(saved['id'])
    assert store.memory.file_snapshot('notes.md')['text'] == 'External edit'


def test_moved_identity_remains_user_editable_only(tmp_path):
    store = Store(tmp_path / 'data'); chat = store.create_chat()
    snap = store.memory.snapshot('identity')
    relative = Path(snap['path']).relative_to(store.memory.root).as_posix()
    store.memory.move(relative, 'my-assistant.md', snap['sha256'])
    result = json.loads(executor(store, chat, lambda _: pytest.fail('Identity is user-editable only')).execute(
        'remember', {'scope': 'global', 'path': 'my-assistant.md', 'text': 'Override identity'}))
    assert 'error' in result


def test_reviewed_append_does_not_follow_a_recreated_memory_identity(tmp_path):
    store = Store(tmp_path / 'data'); chat = store.create_chat()
    store.memory.create_file('notes.md', 'Original')
    original = store.memory.file_snapshot('notes.md')

    def review(_):
        other = Store(store.directory)
        other.memory.delete('notes.md', original['sha256'])
        other.memory.create_file('notes.md', original['text'])
        return True

    result = json.loads(executor(store, chat, review).execute(
        'remember', {'scope': 'global', 'path': 'notes.md', 'text': 'Old review'}))
    assert 'error' in result
    replacement = store.memory.file_snapshot('notes.md')
    assert replacement['file_id'] != original['file_id']
    assert replacement['text'] == 'Original'
    assert all(row.get('origin') != f'chat:{chat}; user approved'
               for row in store.memory.history())


def test_reviewed_append_rejects_replaced_unregistered_file(tmp_path):
    store = Store(tmp_path / 'data'); chat = store.create_chat()
    path = store.memory.root / 'external.md'
    path.write_text('Original')
    assert store.memory.file_snapshot('external.md')['file_id'] is None

    def review(_):
        path.rename(store.memory.root / 'retained.md')
        path.write_text('Original')
        return True

    result = json.loads(executor(store, chat, review).execute(
        'remember', {'scope': 'global', 'path': 'external.md', 'text': 'Old review'}))
    assert 'error' in result
    assert path.read_text() == 'Original'
    assert (store.memory.root / 'retained.md').read_text() == 'Original'


def test_prompt_includes_only_active_files_and_query_relevant_memory(tmp_path):
    store = Store(tmp_path / 'data'); project = store.create_project('A')
    store.memory.create_file('active.md', 'Explain abbreviations.')
    store.memory.create_file('dormant.md', 'Zebra has a silver saddle.')
    store.memory.create_file('active.md', 'Private project rule.', project_id=project)
    store.memory.set_active('active.md', True, store.memory.file_snapshot('active.md')['sha256'])
    snap = store.memory.file_snapshot('active.md', project)
    store.memory.set_active('active.md', True, snap['sha256'], project)
    global_context = build_context(None, [], 'hello', strand=store.memory)
    assert 'Explain abbreviations.' in global_context
    assert 'silver saddle' not in global_context and 'Private project rule.' not in global_context
    project_context = build_context(store.project(project), [], 'hello', strand=store.memory)
    assert 'Private project rule.' in project_context
    assert 'You are Strand' in global_context
    assert 'list_memory' in global_context and 'search_memory' in global_context
