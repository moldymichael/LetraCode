import json
import threading
import time

from letracode.tools import ToolExecutor
from letracode.context import ProjectFiles, build_context


def executor(tmp_path, approve=lambda request: False, roots=None):
    return ToolExecutor(roots or [], tmp_path / 'appdata', approve, threading.Event())


def test_unlinked_read_and_denied_commands_have_no_effect(tmp_path):
    f = tmp_path / 'private.md'
    f.write_text('private')
    tool = executor(tmp_path)
    assert 'denied' in tool.execute('read_file', {'path': str(f)}).lower()
    assert 'denied' in tool.execute('run_command', {'command': 'touch should-not-exist', 'cwd': str(tmp_path)}).lower()
    assert not (tmp_path / 'should-not-exist').exists()


def test_linked_read_and_symlink_escape(tmp_path):
    root = tmp_path / 'project'
    root.mkdir()
    (root / 'a.md').write_text('good evidence')
    secret = tmp_path / 'secret.md'
    secret.write_text('never automatically include')
    (root / 'escape.md').symlink_to(secret)
    (root / '.env').write_text('API_KEY=secret')
    tool = executor(tmp_path, roots=[str(root)])
    assert 'good evidence' in tool.execute('read_file', {'path': str(root / 'a.md')})
    assert 'denied' in tool.execute('read_file', {'path': str(root / 'escape.md')}).lower()
    assert 'denied' in tool.execute('read_file', {'path': str(root / '.env')}).lower()
    context = build_context({'title':'Work','memory':'','current_context':'','instructions':''}, [str(root)], 'evidence', 12000)
    assert 'good evidence' in context
    assert 'never automatically include' not in context
    assert 'API_KEY=secret' not in context


def test_write_requires_approval_and_backs_up_previous_bytes(tmp_path):
    f = tmp_path / 'a.md'
    f.write_text('before\n')
    denied = executor(tmp_path, roots=[str(tmp_path)])
    assert 'denied' in denied.execute('write_file', {'path':str(f), 'content':'after\n'}).lower()
    assert f.read_text() == 'before\n'
    requests = []
    def approve(request):
        requests.append(request)
        return True
    accepted = executor(tmp_path, approve, [str(tmp_path)])
    result = json.loads(accepted.execute('write_file', {'path':str(f), 'content':'after\n'}))
    assert f.read_text() == 'after\n'
    assert '-before' in requests[0].details and '+after' in requests[0].details
    from pathlib import Path
    assert Path(result['backup']).read_text() == 'before\n'


def test_write_does_not_overwrite_change_during_approval(tmp_path):
    f = tmp_path / 'a.md'
    f.write_text('before')
    def approval(_):
        f.write_text('human edit')
        return True
    tool = executor(tmp_path, approval)
    result = tool.execute('write_file', {'path':str(f), 'content':'model edit'})
    assert 'changed' in result.lower()
    assert f.read_text() == 'human edit'


def test_command_output_and_timeout(tmp_path):
    tool = executor(tmp_path, lambda _: True)
    data = json.loads(tool.execute('run_command', {'command':'printf hello', 'cwd':str(tmp_path)}))
    assert data['output'] == 'hello'
    assert data['exit_code'] == 0
    start = time.monotonic()
    data = json.loads(tool.execute('run_command', {'command':'sleep 10', 'cwd':str(tmp_path), 'timeout':1}))
    assert data['timed_out']
    assert time.monotonic() - start < 4


def test_context_refreshes_and_preserves_project_separation(tmp_path):
    a = tmp_path / 'a'; a.mkdir()
    b = tmp_path / 'b'; b.mkdir()
    f = a / 'story.md'; f.write_text('House watches the violin.')
    (b / 'story.md').write_text('An unrelated spaceship.')
    project = {'title':'A','memory':'A remembered fact','current_context':'Working','instructions':'Use evidence'}
    first = build_context(project, [str(a)], 'violin', 12000)
    assert 'House watches the violin.' in first and 'spaceship' not in first
    f.write_text('House watches the piano.')
    second = build_context(project, [str(a)], 'piano', 12000)
    assert 'House watches the piano.' in second and 'violin' not in second


def test_invalid_tool_arguments_are_errors_not_actions(tmp_path):
    tool = executor(tmp_path, lambda _: True)
    assert 'error' in tool.execute('run_command', {'command':['bad']})
    assert 'error' in tool.execute('invented_tool', {})


def test_retrieval_rejects_sensitive_symlinked_ancestor(tmp_path):
    hidden = tmp_path / '.private' / 'project'; hidden.mkdir(parents=True)
    (hidden/'note.txt').write_text('SECRET CONTENT')
    (tmp_path/'visible').symlink_to(tmp_path/'.private',target_is_directory=True)
    files = ProjectFiles([str(tmp_path/'visible'/'project')])
    assert files.inventory() == []
    assert files.search('SECRET') == []
