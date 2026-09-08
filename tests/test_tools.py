import hashlib
import json
import os
import threading
import time
import zipfile
from pathlib import Path

import pytest

from letracode.tools import ToolExecutor
from letracode.context import ProjectFiles, build_context


def python_command(source):
    from tools.run_acceptance import python_command as command
    return command('-c', source)


def executor(tmp_path, approve=lambda request: False, roots=None):
    return ToolExecutor(roots or [], tmp_path / 'appdata', approve, threading.Event())


def test_unlinked_read_and_denied_commands_have_no_effect(tmp_path):
    f = tmp_path / 'private.md'
    f.write_text('private')
    tool = executor(tmp_path)
    assert 'denied' in tool.execute('read_file', {'path': str(f)}).lower()
    assert 'denied' in tool.execute('run_command', {'command': 'touch should-not-exist', 'cwd': str(tmp_path)}).lower()
    assert not (tmp_path / 'should-not-exist').exists()


def test_linked_read_and_symlink_escape(tmp_path, make_symlink):
    root = tmp_path / 'project'
    root.mkdir()
    (root / 'a.md').write_text('good evidence')
    secret = tmp_path / 'secret.md'
    secret.write_text('never automatically include')
    make_symlink(root / 'escape.md', secret)
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
    args = {'path':str(f), 'content':'after\n', 'expected_sha256':hashlib.sha256(f.read_bytes()).hexdigest()}
    assert 'denied' in denied.execute('write_file', args).lower()
    assert f.read_text() == 'before\n'
    requests = []
    def approve(request):
        requests.append(request)
        return True
    accepted = executor(tmp_path, approve, [str(tmp_path)])
    result = json.loads(accepted.execute('write_file', args))
    assert f.read_text() == 'after\n'
    assert '-before' in requests[0].details and '+after' in requests[0].details
    from pathlib import Path
    assert Path(result['backup']).read_text() == 'before\n'
    assert result['sha256'] == hashlib.sha256(b'after\n').hexdigest()


def test_write_does_not_overwrite_change_during_approval(tmp_path):
    f = tmp_path / 'a.md'
    f.write_text('before')
    def approval(_):
        f.write_text('human edit')
        return True
    tool = executor(tmp_path, approval)
    result = tool.execute('write_file', {'path':str(f), 'content':'model edit', 'expected_sha256':hashlib.sha256(b'before').hexdigest()})
    assert 'changed' in result.lower()
    assert f.read_text() == 'human edit'


def test_command_output_and_timeout(tmp_path):
    tool = executor(tmp_path, lambda _: True)
    data = json.loads(tool.execute('run_command', {'command':python_command("print('hello', end='')"), 'cwd':str(tmp_path)}))
    assert data['output'] == 'hello'
    assert data['exit_code'] == 0
    start = time.monotonic()
    command = python_command('import time; time.sleep(10)')
    data = json.loads(tool.execute('run_command', {'command':command, 'cwd':str(tmp_path), 'timeout':1}))
    assert data['timed_out']
    assert data['command'] == command
    assert data['cwd'] == str(tmp_path)
    assert data['timeout'] == 1
    assert data['executed'] is True
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


def test_retrieval_rejects_sensitive_symlinked_ancestor(tmp_path, make_symlink):
    hidden = tmp_path / '.private' / 'project'; hidden.mkdir(parents=True)
    (hidden/'note.txt').write_text('SECRET CONTENT')
    make_symlink(tmp_path/'visible', tmp_path/'.private', target_is_directory=True)
    files = ProjectFiles([str(tmp_path/'visible'/'project')])
    assert files.inventory() == []
    assert files.search('SECRET') == []


def test_read_file_hash_covers_same_raw_utf8_snapshot_as_numbered_text(tmp_path):
    path = tmp_path / 'source.py'
    raw = b'\xef\xbb\xbf# keep BOM\r\nvalue = "caf\xc3\xa9"\r\n# unseen tail\r\n'
    path.write_bytes(raw)
    result = json.loads(executor(tmp_path, roots=[str(tmp_path)]).execute(
        'read_file', {'path':str(path), 'max_lines':2}))
    assert result['text'] == '1: \ufeff# keep BOM\n2: value = "caf\u00e9"'
    assert result['sha256'] == hashlib.sha256(raw).hexdigest()
    assert result['editable'] is True
    assert result['truncated'] is True


def test_edit_file_changes_one_fragment_and_preserves_unrelated_bytes(tmp_path):
    path = tmp_path / 'source.py'
    before = b'\xef\xbb\xbf# human change\r\nvalue = 1\r\n' + b'# unrelated long tail\r\n' * 2000
    after = before.replace(b'value = 1', b'value = 2')
    path.write_bytes(before)
    requests = []
    tool = executor(tmp_path, lambda request: requests.append(request) or True, [str(tmp_path)])
    result = json.loads(tool.execute('edit_file', {'path':str(path),
        'expected_sha256':hashlib.sha256(before).hexdigest(), 'old_text':'value = 1', 'new_text':'value = 2'}))
    assert path.read_bytes() == after
    assert result['sha256'] == hashlib.sha256(after).hexdigest()
    assert Path(result['backup']).read_bytes() == before
    assert len(requests) == 1 and requests[0].kind == 'write'
    assert '-value = 1' in requests[0].details and '+value = 2' in requests[0].details


@pytest.mark.parametrize('before,after,visible_marker', [
    (b'value', b'value\n', 'No newline at end of file'),
    (b'value\n', b'value\r\n', '\\r'),
    (b'\xef\xbb\xbfvalue\n', b'value\n', '\\uFEFF'),
])
def test_source_approval_exposes_line_ending_and_bom_changes(tmp_path, before, after, visible_marker):
    path = tmp_path / 'source.txt'; path.write_bytes(before)
    requests = []
    result = json.loads(executor(tmp_path, lambda request: requests.append(request) or True).execute(
        'write_file', {'path':str(path), 'content':after.decode('utf-8'),
                       'expected_sha256':hashlib.sha256(before).hexdigest()}))
    assert 'error' not in result
    assert path.read_bytes() == after
    assert visible_marker in requests[0].details
    assert '-value' in requests[0].details or '-\\uFEFFvalue' in requests[0].details
    assert '+value' in requests[0].details


@pytest.mark.parametrize('old_text,new_text,expected', [('remove\n', '', 'keep\n'), ('\n', '\nadded\n', 'keep\nadded\nremove')])
def test_edit_file_accepts_deletion_and_nonempty_whitespace_fragment(tmp_path, old_text, new_text, expected):
    path = tmp_path / 'source.txt'
    before = 'keep\nremove\n' if old_text == 'remove\n' else 'keep\nremove'
    path.write_bytes(before.encode('utf-8'))
    result = json.loads(executor(tmp_path, lambda _: True).execute('edit_file', {
        'path':str(path), 'expected_sha256':hashlib.sha256(before.encode()).hexdigest(),
        'old_text':old_text, 'new_text':new_text}))
    assert 'error' not in result
    assert path.read_text() == expected


@pytest.mark.parametrize('before,old_text', [('same same', 'same'), ('aaa', 'aa'), ('keep', ''), ('keep', 'missing')])
def test_edit_file_rejects_missing_empty_and_ambiguous_fragments_before_approval(tmp_path, before, old_text):
    path = tmp_path / 'source.txt'; path.write_bytes(before.encode('utf-8'))
    requests = []
    result = json.loads(executor(tmp_path, lambda request: requests.append(request) or True).execute('edit_file', {
        'path':str(path), 'expected_sha256':hashlib.sha256(before.encode()).hexdigest(),
        'old_text':old_text, 'new_text':'replacement'}))
    assert 'old_text' in result.get('error', '')
    assert not requests
    assert path.read_text() == before


@pytest.mark.parametrize('name', ['write_file', 'edit_file'])
@pytest.mark.parametrize('expected', ['0' * 64, None, 'invalid', 42])
def test_source_writes_reject_stale_or_invalid_preconditions_without_approval(tmp_path, name, expected):
    path = tmp_path / 'source.txt'; path.write_text('human edit')
    requests = []
    args = {'path':str(path), 'expected_sha256':expected}
    args.update({'content':'replacement'} if name == 'write_file' else {'old_text':'human', 'new_text':'model'})
    result = json.loads(executor(tmp_path, lambda request: requests.append(request) or True).execute(name, args))
    assert 'expected_sha256' in result.get('error', '')
    assert not requests
    assert path.read_text() == 'human edit'


@pytest.mark.parametrize('exists', [False, True])
def test_write_file_requires_explicit_precondition_for_creation_and_replacement(tmp_path, exists):
    path = tmp_path / 'source.txt'
    if exists:
        path.write_text('keep')
    requests = []
    result = json.loads(executor(tmp_path, lambda request: requests.append(request) or True).execute(
        'write_file', {'path':str(path), 'content':'replacement'}))
    assert 'expected_sha256' in result.get('error', '')
    assert not requests
    assert path.exists() is exists
    if exists:
        assert path.read_text() == 'keep'


def test_write_file_explicit_null_creates_and_returns_hash(tmp_path):
    path = tmp_path / 'new.txt'
    requests = []
    result = json.loads(executor(tmp_path, lambda request: requests.append(request) or True).execute(
        'write_file', {'path':str(path), 'content':'new\n', 'expected_sha256':None}))
    assert path.read_bytes() == b'new\n'
    assert result['sha256'] == hashlib.sha256(b'new\n').hexdigest()
    assert result['backup'] is None
    assert len(requests) == 1


def test_edit_file_denial_and_change_during_approval_preserve_source(tmp_path):
    path = tmp_path / 'source.txt'; path.write_text('before')
    args = {'path':str(path), 'old_text':'before', 'new_text':'model',
            'expected_sha256':hashlib.sha256(b'before').hexdigest()}
    denied = json.loads(executor(tmp_path).execute('edit_file', args))
    assert 'denied' in denied
    assert path.read_text() == 'before'
    def approval(_):
        path.write_text('human edit')
        return True
    changed = json.loads(executor(tmp_path, approval).execute('edit_file', args))
    assert 'error' in changed
    assert path.read_text() == 'human edit'


@pytest.mark.parametrize('extension', ['.pdf', '.docx'])
def test_documents_remain_readable_but_cannot_be_edited_as_source_text(tmp_path, extension):
    path = tmp_path / ('document' + extension)
    if extension == '.pdf':
        from pypdf import PdfWriter
        writer = PdfWriter(); writer.add_blank_page(width=72, height=72)
        writer.write(path)
        expected_text = '[Page 1]'
    else:
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('word/document.xml', '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Document evidence</w:t></w:r></w:p></w:body></w:document>')
        expected_text = 'Document evidence'
    before = path.read_bytes()
    requests = []
    tool = executor(tmp_path, lambda request: requests.append(request) or True, [str(tmp_path)])
    read = json.loads(tool.execute('read_file', {'path':str(path)}))
    assert expected_text in read['text']
    assert read['editable'] is False and read['sha256'] is None
    for name in ('write_file', 'edit_file'):
        args = {'path':str(path), 'expected_sha256':hashlib.sha256(before).hexdigest()}
        args.update({'content':'overwrite'} if name == 'write_file' else {'old_text':expected_text, 'new_text':'overwrite'})
        result = json.loads(tool.execute(name, args))
        assert 'error' in result and 'read-only' in result['error'].lower()
    assert not requests
    assert path.read_bytes() == before


@pytest.mark.parametrize('code,output', [(0, 'passed'), (7, 'failed')])
def test_command_results_identify_command_cwd_and_effective_timeout(tmp_path, code, output):
    command = python_command(f'print({output!r}, end=""); raise SystemExit({code})')
    result = json.loads(executor(tmp_path, lambda _: True).execute(
        'run_command', {'command':command, 'cwd':str(tmp_path)}))
    assert result['command'] == command
    assert result['cwd'] == str(tmp_path)
    assert result['timeout'] == 60
    assert result['executed'] is True
    assert result['exit_code'] == code and result['output'] == output
    assert result['timed_out'] is False and result['cancelled'] is False
    assert result['output_limit_reached'] is False


def test_command_approval_names_shell_and_preserves_unicode(tmp_path):
    requests = []
    tool = executor(tmp_path, lambda request: requests.append(request) or True)
    folder = tmp_path / 'Project café with spaces'
    folder.mkdir()
    command = python_command("print('café \\u03bb')")
    result = json.loads(tool.execute('run_command', {'command':command, 'cwd':str(folder)}))
    assert result['exit_code'] == 0
    assert result['output'].strip() == 'café λ'
    assert ('PowerShell' if os.name == 'nt' else 'Bash') in requests[0].details
    assert command in requests[0].details


def test_command_pipe_reading_does_not_require_selectable_file_descriptors(tmp_path, monkeypatch):
    import selectors
    def reject_pipe(*args, **kwargs):
        raise OSError('Windows selectors cannot monitor anonymous pipes')
    monkeypatch.setattr(selectors, 'DefaultSelector', reject_pipe)
    result = json.loads(executor(tmp_path, lambda _: True).execute(
        'run_command', {'command':python_command("print('pipe works')"), 'cwd':str(tmp_path)}))
    assert result.get('output', '').strip() == 'pipe works'


@pytest.mark.parametrize('platform', ['linux', 'win32'])
def test_command_environment_excludes_secrets_and_keeps_windows_runtime(monkeypatch, platform):
    import letracode.tools as tools_module
    from types import SimpleNamespace
    from letracode.tools import command_environment
    monkeypatch.setattr(tools_module, 'sys', SimpleNamespace(platform=platform))
    monkeypatch.setenv('SystemRoot', r'C:\Windows')
    monkeypatch.setenv('USERPROFILE', r'C:\Users\Writer')
    monkeypatch.setenv('LOCALAPPDATA', r'C:\Users\Writer\AppData\Local')
    monkeypatch.setenv('OPENAI_API_KEY', 'secret-test-only')
    environment = command_environment()
    assert 'OPENAI_API_KEY' not in environment
    if platform == 'win32':
        assert environment['PYTHONIOENCODING'] == 'utf-8'
        assert environment['SYSTEMROOT'] == r'C:\Windows'
        assert environment['USERPROFILE'] == r'C:\Users\Writer'
        assert environment['LOCALAPPDATA'].endswith(r'AppData\Local')
    else:
        assert 'PYTHONIOENCODING' not in environment
        assert 'SYSTEMROOT' not in environment


def test_command_output_is_bounded_and_cancel_interrupts_wait(tmp_path):
    tool = executor(tmp_path, lambda _: True)
    result = json.loads(tool.execute('run_command', {
        'command':python_command("print('x' * 200000)"), 'cwd':str(tmp_path)}))
    assert result['output_limit_reached']
    assert len(result['output']) <= 64000
    timer = threading.Timer(0.5, tool.cancel.set)
    timer.start()
    started = time.monotonic()
    try:
        result = json.loads(tool.execute('run_command', {
            'command':python_command('import time; time.sleep(30)'), 'cwd':str(tmp_path)}))
    finally:
        timer.cancel()
    assert result['cancelled']
    assert time.monotonic() - started < 4


def test_command_preserves_exit_status_when_output_closes_first(tmp_path):
    tool = executor(tmp_path, lambda _: True)
    command = python_command('import os, time; os.close(1); os.close(2); time.sleep(0.1); raise SystemExit(7)')
    result = json.loads(tool.execute('run_command', {'command':command, 'cwd':str(tmp_path)}))
    assert result['exit_code'] == 7


@pytest.mark.skipif(os.name != 'nt', reason='Native Windows PowerShell')
def test_windows_powershell_syntax_output_and_failure_codes(tmp_path):
    tool = executor(tmp_path, lambda _: True)
    for command, output, exit_code in [
        ("Write-Output 'café λ'", 'café λ', 0),
        (python_command('import sys; sys.exit(7)'), '', 7),
        ("Write-Error 'failed as requested'", None, 1),
        ("cmd.exe /c exit 0; Get-Item -LiteralPath 'C:\\letracode-definitely-missing'", None, 1),
        ("cmd.exe /c exit 7; Write-Output 'recovered'", 'recovered', 0),
    ]:
        result = json.loads(tool.execute('run_command', {'command':command, 'cwd':str(tmp_path)}))
        assert result['exit_code'] == exit_code
        if output is not None:
            assert result['output'].strip() == output
