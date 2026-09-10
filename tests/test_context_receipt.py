"""Receipts describe actual request construction, not model understanding."""
import json
import threading
from pathlib import Path

import pytest

from letracode.context import ProjectFiles, build_context
from letracode.store import Store
from letracode.tools import ToolExecutor
from letracode.worker import ConversationWorker


def test_explicit_account_reads_include_hidden_files_without_action_approval(tmp_path):
    secret = tmp_path / '.notes'; secret.mkdir()
    path = secret / 'ordinary.md'; path.write_text('Account readable')
    tool = ToolExecutor([], tmp_path / 'data', lambda _: pytest.fail('Reading is already authorized'),
                        threading.Event(), actions_enabled=False)
    assert 'Account readable' in tool.execute('read_file', {'path': str(path)})
    listing = json.loads(tool.execute('list_files', {'path': str(tmp_path)}))
    assert str(secret) in [entry['path'] for entry in listing['entries']]
    assert listing['hidden_files_omitted'] is False


def test_edit_command_and_web_capabilities_are_independent_of_reads(tmp_path):
    path = tmp_path / 'note.md'; path.write_text('Read me')
    requests = []
    def deny(request):
        requests.append(request.kind)
        return False
    tool = ToolExecutor([], tmp_path / 'data', deny, threading.Event(), actions_enabled=False)
    assert 'Read me' in tool.execute('read_file', {'path': str(path)})
    assert 'denied' in tool.execute('write_file', {'path': str(tmp_path / 'new.md'), 'content': 'No', 'expected_sha256': None})
    assert 'denied' in tool.execute('run_command', {'command': 'echo never', 'cwd': str(tmp_path)})
    assert requests == []
    assert 'denied' in tool.execute('web_search', {'query': 'public research'})
    assert requests == ['web']
    tool = ToolExecutor([], tmp_path / 'data', deny, threading.Event(), computer_enabled=False,
                        actions_enabled=True, web_enabled=False)
    assert 'denied' in tool.execute('read_file', {'path': str(path)})
    assert 'denied' in tool.execute('write_file', {'path': str(tmp_path / 'new.md'), 'content': 'No', 'expected_sha256': None})
    assert requests == ['web', 'write']
    assert 'denied' in tool.execute('fetch_url', {'url': 'https://example.com'})
    assert requests == ['web', 'write']
    assert not (tmp_path / 'new.md').exists()


def test_global_sources_and_memory_have_a_faithful_request_receipt(tmp_path):
    store = Store(tmp_path / 'data')
    store.memory.create_file('preferences.md', 'Use short explanations.')
    note = store.memory.file_snapshot('preferences.md')
    store.memory.set_active('preferences.md', True, note['sha256'])
    project = store.create_project('Focused work')
    store.memory.create_file('focus.md', 'Workspace-only context.', project_id=project)
    focus = store.memory.file_snapshot('focus.md', project)
    store.memory.set_active('focus.md', True, focus['sha256'], project)
    source = tmp_path / 'source.md'; source.write_text('The violet bicycle has two wheels.')
    receipt = {}
    text = build_context(None, [str(source)], 'violet bicycle', strand=store.memory, receipt=receipt)
    assert 'two wheels' in text and 'Use short explanations.' in text
    assert 'Workspace-only context.' not in text
    assert receipt['system_text'] == text
    assert receipt['sources'][0]['path'] == str(source)
    assert receipt['sources'][0]['text'] == source.read_text()
    assert receipt['memory'][0]['path'] == note['path']
    assert receipt['memory'][0]['sha256'] == note['sha256']
    source.write_text('Changed after request')
    assert 'two wheels' in receipt['sources'][0]['text']
    assert 'Changed after request' not in receipt['system_text']
    focus_receipt = {}
    focused = build_context(store.project_for_context(project), [], 'hello', strand=store.memory, receipt=focus_receipt)
    assert 'Workspace-only context.' in focused and 'Use short explanations.' in focused
    assert len(focus_receipt['memory']) == 2


def test_retrieval_receipt_reports_inventory_bound_and_failed_documents(tmp_path):
    for index in range(601):
        (tmp_path / f'{index:03}.md').write_text('unrelated prose')
    (tmp_path / '600.md').write_text('lastmatch')
    files = ProjectFiles([str(tmp_path)])
    assert files.search('lastmatch') == []
    assert files.report['inventory_truncated'] is True
    assert files.report['scanned_files'] == 600
    corrupt = tmp_path / 'bad.docx'; corrupt.write_text('Not a ZIP file')
    receipt = {}
    build_context(None, [str(corrupt)], 'bad', receipt=receipt)
    assert receipt['retrieval']['failed_sources'][0]['path'] == str(corrupt)
    assert receipt['sources'] == []


def test_missing_active_memory_cannot_silently_disappear_from_context(tmp_path):
    store = Store(tmp_path / 'data')
    store.memory.create_file('important.md', 'Required user instruction')
    note = store.memory.file_snapshot('important.md')
    store.memory.set_active('important.md', True, note['sha256'])
    Path(note['path']).unlink()
    with pytest.raises((OSError, ValueError)):
        build_context(None, [], 'hello', strand=store.memory, receipt={})


def test_worker_persists_the_final_packed_context_and_capabilities(tmp_path):
    store = Store(tmp_path / 'data'); chat = store.create_chat()
    source = tmp_path / 'source.md'; source.write_text('A violet bicycle.')
    store.set_setting('source_roots', [str(source)])
    store.add_message(chat, 'user', 'Tell me about the violet bicycle')
    class Engine:
        class Config:
            context_size = 65536
            max_tokens = 1024
            model_path = 'fixture.gguf'
        config = Config()
        def start(self, *args): pass
        def complete(self, messages, tools, *args):
            self.messages, self.tools = messages, tools
            return {'role': 'assistant', 'content': 'It is violet.'}
    engine = Engine()
    ConversationWorker(store, chat, engine, actions_enabled=False).run()
    row = next(row for row in store.messages(chat) if row['role'] == 'assistant')
    receipt = json.loads(row['payload'])['context']
    assert receipt['system_text'] == engine.messages[0]['content']
    assert receipt['sources'][0]['path'] == str(source)
    assert receipt['capabilities']['actions'] is False
    names = {definition['function']['name'] for definition in engine.tools}
    assert {'read_file', 'app_info', 'search_history'} <= names
    assert not {'write_file', 'edit_file', 'run_command', 'remember'} & names
    assert receipt['available_tools'] == [tool['function']['name'] for tool in engine.tools]


def test_disabled_file_reading_excludes_memory_but_keeps_saved_history(tmp_path):
    store = Store(tmp_path / 'data'); chat = store.create_chat()
    store.memory.create_file('active.md', 'FILE-CONTENT-MUST-NOT-APPEAR')
    note = store.memory.file_snapshot('active.md')
    store.memory.set_active('active.md', True, note['sha256'])
    store.add_message(chat, 'user', 'Earlier conversation is retained')
    store.add_message(chat, 'assistant', 'Yes, saved conversation')
    store.add_message(chat, 'user', 'Continue our discussion')
    class Engine:
        class Config:
            context_size = 65536
            max_tokens = 1024
        config = Config()
        def start(self, *args): pass
        def complete(self, messages, tools, *args):
            self.messages, self.tools = messages, tools
            return {'role': 'assistant', 'content': 'Continued.'}
    engine = Engine()
    ConversationWorker(store, chat, engine, computer_enabled=False).run()
    assert 'FILE-CONTENT-MUST-NOT-APPEAR' not in json.dumps(engine.messages)
    assert 'Earlier conversation is retained' in json.dumps(engine.messages)
    names = {tool['function']['name'] for tool in engine.tools}
    assert {'read_tool_result', 'search_history'} <= names
    assert not {'read_memory', 'list_memory', 'search_memory', 'read_file', 'app_info'} & names


def test_app_info_distinguishes_runtime_and_configured_development_sources(tmp_path):
    store = Store(tmp_path / 'data'); chat = store.create_chat()
    checkout = tmp_path / 'checkout'; checkout.mkdir()
    (checkout / 'letracode').mkdir()
    (checkout / 'letracode' / '__init__.py').write_text('__version__ = "development"')
    (checkout / 'pyproject.toml').write_text('[project]\nname="letracode"\n')
    (checkout / 'README.md').write_text('Development documentation')
    store.set_setting('development_root', str(checkout))
    tool = ToolExecutor([], store.directory, lambda _: False, threading.Event(), store=store, chat_id=chat)
    result = json.loads(tool.execute('app_info', {}))
    assert result['development_root'] == str(checkout)
    assert result['runtime_root'] != str(checkout)
    assert str(checkout / 'README.md') in result['documentation_paths']
    assert result['data_directory'] == str(store.directory)
    store.set_setting('development_root', str(tmp_path / 'missing'))
    result = json.loads(tool.execute('app_info', {}))
    assert result['development_root'] is None
    assert result['development_error']


def test_workspace_context_read_off_uses_only_saved_database_instructions(tmp_path, monkeypatch):
    store = Store(tmp_path / 'data')
    project = store.create_project('Database workspace')
    store.update_project(project, instructions='Preserve database instructions.',
                         current_context='Legacy file-derived context', memory='Private file content')
    def forbid_files(*args, **kwargs):
        pytest.fail('Reading is off: workspace context must not inspect or export files')
    monkeypatch.setattr(store, 'ensure_project_files', forbid_files)
    monkeypatch.setattr(store.memory, 'snapshot', forbid_files)

    context = store.project_for_context(project, read_files=False)

    assert context['id'] == project
    assert context['title'] == 'Database workspace'
    assert context['instructions'] == 'Preserve database instructions.'
    assert context['memory'] == context['current_context'] == ''
    assert store.project_for_context('missing', read_files=False) is None
