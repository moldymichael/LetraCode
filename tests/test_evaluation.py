"""Evaluation bundles are portable projections of one saved conversation."""
import hashlib
import json
from pathlib import Path
import zipfile

import pytest

from letracode.store import Store


def export(store, chat, path, **kwargs):
    assert hasattr(store, 'export_evaluation'), 'Store must expose Export Evaluation'
    store.export_evaluation(chat, path, **kwargs)
    with zipfile.ZipFile(path) as archive:
        assert archive.testzip() is None
        return {name: archive.read(name).decode('utf-8') for name in archive.namelist()}


def tool(store, chat, name, args, result):
    call = {'id': f'call-{name}', 'type': 'function',
            'function': {'name': name, 'arguments': json.dumps(args)}}
    request_id = store.add_message(chat, 'assistant', '', payload={
        'message': {'role': 'assistant', 'content': '', 'tool_calls': [call]}})
    response = {'role': 'tool', 'name': name, 'tool_call_id': call['id'], 'content': json.dumps(result)}
    result_id = store.add_message(chat, 'tool', f'RAW DUPLICATE {args} {result}',
                                 payload={'message': response, 'arguments': args})
    return request_id, result_id


def test_zip_transcript_events_and_coverage_follow_saved_order(tmp_path):
    store = Store(tmp_path / 'data')
    chat = store.create_chat('Evaluation café')
    user = store.add_message(chat, 'user', 'Inspect the failure')
    request, result = tool(store, chat, 'run_command', {'command': 'false', 'cwd': '/tmp/project'},
                           {'exit_code': 1, 'output': 'private command output', 'error': 'Command failed'})
    approval = store.add_message(chat, 'notice', 'Denied requested retry', payload={
        'approval': {'decision': 'denied', 'kind': 'command'}})
    coverage = store.add_message(chat, 'notice', 'Source coverage incomplete', 'incomplete', payload={
        'coverage': {'version': 1, 'files': [{'path': '/tmp/project/file.py',
            'source_sha256': 'a' * 64, 'missing_ranges': [[4, 10]]}],
            'incomplete': True, 'whole_work_verified': False}})
    continuation = store.add_message(chat, 'notice', 'Continuing with saved evidence', 'continuing', payload={
        'segment_boundary': True, 'checkpoint': {'reason': 'segment_limit', 'continuation': {
            'run_id': 'run-1', 'elapsed_seconds': 12.5, 'segments': 2}}})
    error = store.add_message(chat, 'notice', 'Local model disconnected', 'error')
    store.add_message(chat, 'assistant', 'Partial interrupted response', 'interrupted')
    files = export(store, chat, tmp_path / 'evaluation.zip', notes='Observed on CPU.')
    assert set(files) == {'README.md', 'transcript.md', 'conversation.json', 'events.jsonl',
                          'coverage.json', 'metadata.json', 'notes.md'}
    conversation = json.loads(files['conversation.json'])
    assert [row['id'] for row in conversation['messages']] == [row['id'] for row in store.messages(chat)]
    events = [json.loads(line) for line in files['events.jsonl'].splitlines()]
    assert [event['message_id'] for event in events] == sorted(event['message_id'] for event in events)
    assert [event['kind'] for event in events if event['message_id'] == request] == ['message', 'action_request']
    assert any(e['message_id'] == result and e['kind'] == 'action_result' and e['result']['exit_code'] == 1 for e in events)
    assert any(e['message_id'] == approval and e['kind'] == 'approval' and e['data']['decision'] == 'denied' for e in events)
    assert any(e['message_id'] == coverage and e['kind'] == 'coverage' for e in events)
    assert any(e['message_id'] == continuation and e['kind'] == 'continuation' for e in events)
    assert any(e['message_id'] == error and e['status'] == 'error' for e in events)
    assert files['transcript.md'].index('Inspect the failure') < files['transcript.md'].index('Command failed') < files['transcript.md'].index('Local model disconnected')
    ledger = json.loads(files['coverage.json'])
    assert ledger['observations'][0]['message_id'] == coverage
    assert ledger['observations'][0]['coverage']['files'][0]['missing_ranges'] == [[4, 10]]
    assert '12.5' in files['conversation.json']
    assert files['notes.md'].strip() == 'Observed on CPU.'
    assert 'not a backup' in files['README.md']


def test_old_chat_has_honest_missing_run_metadata_and_no_notes(tmp_path):
    store = Store(tmp_path / 'data')
    chat = store.create_chat('Old chat')
    store.set_setting('engine', {'model_path': '/home/person/current-model.gguf', 'api_key': 'DO NOT EXPORT'})
    store.set_setting('mode', 'Thinking')
    store.add_message(chat, 'assistant', 'Historic reply')
    files = export(store, chat, tmp_path / 'evaluation.zip')
    metadata = json.loads(files['metadata.json'])
    assert metadata['run_configurations'] == []
    assert 'not recorded' in metadata['availability']['run_configuration']
    assert 'notes.md' not in files
    assert 'current-model' not in ''.join(files.values())
    assert metadata['application']['version']
    assert metadata['application']['observed_at'] == 'export'
    assert 'git' in metadata['application']


def test_saved_run_configuration_is_allowlisted_and_keeps_mode(tmp_path):
    store = Store(tmp_path / 'data')
    chat = store.create_chat('Configured')
    store.add_message(chat, 'assistant', 'Response', payload={'run_configuration': {
        'thinking': True, 'model_path': '/home/alice/models/chosen.gguf',
        'context_size': 8192, 'max_tokens': 2048, 'temperature': 0.3,
        'api_key': 'CONFIG CREDENTIAL', 'environment': {'SECRET': 'OTHER SECRET'}},
        'continuation': {'run_id': 'r1'}})
    files = export(store, chat, tmp_path / 'evaluation.zip')
    configurations = json.loads(files['metadata.json'])['run_configurations']
    assert configurations[0]['configuration']['thinking'] is True
    assert configurations[0]['configuration']['model'] == 'chosen.gguf'
    assert configurations[0]['configuration']['context_size'] == 8192
    assert 'CONFIG CREDENTIAL' not in ''.join(files.values())
    assert 'OTHER SECRET' not in ''.join(files.values())
    assert '/home/alice' not in ''.join(files.values())


def test_privacy_omits_private_bodies_even_in_nested_saved_result_pages(tmp_path):
    store = Store(tmp_path / 'data')
    project = store.create_project('Private project')
    chat = store.create_chat('Selected', project)
    other = store.create_chat('Unrelated conversation')
    store.add_message(other, 'user', 'OTHER CHAT CONTENT')
    store.set_draft(chat, 'UNSENT DRAFT')
    store.update_project(project, memory='MEMORY ON DISK', instructions='PROJECT INSTRUCTIONS', current_context='CURRENT CONTEXT')
    store.set_setting('token', 'APP CREDENTIAL')
    linked = tmp_path / 'source.py'; linked.write_text('PROJECT SOURCE ON DISK')
    store.link(project, linked)
    store.add_message(chat, 'user', 'api_key=sk-superSecretCredential123456789 and /home/alice/project/file.py')
    tool(store, chat, 'read_file', {'path': '/home/alice/project/file.py'},
         {'path': '/home/alice/project/file.py', 'text': 'SOURCE BODY SECRET', 'sha256': 'a' * 64,
          'coverage': {'ranges': [[0, 18]]}, 'total_chars': 18})
    tool(store, chat, 'write_file', {'path': '/home/alice/project/file.py', 'content': 'WRITE BODY SECRET'}, {'written_characters': 17})
    tool(store, chat, 'edit_file', {'path': '/home/alice/project/file.py', 'old_text': 'OLD SOURCE SECRET', 'new_text': 'NEW SOURCE SECRET'}, {'sha256': 'b' * 64})
    tool(store, chat, 'remember', {'scope': 'global', 'text': 'REMEMBER BODY SECRET'},
         {'saved_text': 'MEMORY RECEIPT SECRET', 'relative_path': 'memory/global.md'})
    tool(store, chat, 'read_memory', {'scope': 'global'}, {'text': 'MEMORY READ SECRET', 'sha256': 'c' * 64})
    tool(store, chat, 'read_tool_result', {'result_id': 10}, {'content': json.dumps({'text': 'RECOVERED BODY SECRET'}), 'result_id': 10})
    tool(store, chat, 'run_command', {'command': 'cat /home/alice/.ssh/id_rsa', 'cwd': '/home/alice'},
         {'output': 'COMMAND BODY SECRET', 'exit_code': 0})
    files = export(store, chat, tmp_path / 'evaluation.zip', notes='Authorization: Bearer note_secret_123456789')
    all_text = ''.join(files.values())
    for secret in ('OTHER CHAT CONTENT', 'UNSENT DRAFT', 'MEMORY ON DISK', 'PROJECT INSTRUCTIONS',
                   'CURRENT CONTEXT', 'APP CREDENTIAL', 'PROJECT SOURCE ON DISK',
                   'SOURCE BODY SECRET', 'WRITE BODY SECRET', 'OLD SOURCE SECRET', 'NEW SOURCE SECRET',
                   'REMEMBER BODY SECRET', 'MEMORY RECEIPT SECRET', 'MEMORY READ SECRET',
                   'RECOVERED BODY SECRET', 'COMMAND BODY SECRET', 'sk-superSecretCredential123456789',
                   'note_secret_123456789', '/home/alice'):
        assert secret not in all_text, secret
    assert hashlib.sha256(b'SOURCE BODY SECRET').hexdigest() in all_text
    assert 'a' * 64 in all_text
    assert 'file.py' in all_text
    assert 'omitted' in all_text


def test_export_does_not_modify_database_memory_or_recover_files(tmp_path, monkeypatch):
    store = Store(tmp_path / 'data')
    chat = store.create_chat('Read only')
    store.add_message(chat, 'assistant', 'Streaming', 'streaming')
    # A malformed private recovery journal must neither block nor be repaired by export.
    recovery = store.strand.root / '.strand-recovery'
    recovery.mkdir(exist_ok=True); (recovery / 'unfinished').write_text('KEEP EXACTLY')
    before_rows = store.rows('SELECT * FROM messages')
    before_data = {p.relative_to(store.directory).as_posix(): p.read_bytes()
                   for p in store.directory.rglob('*') if p.is_file() and p.suffix not in ('.sqlite3', '-wal', '-shm')}
    monkeypatch.setattr(store.strand, 'snapshot', lambda *a, **k: pytest.fail('Export read Memory'))
    monkeypatch.setattr(store, 'set_setting', lambda *a, **k: pytest.fail('Export changed settings'))
    export(store, chat, tmp_path / 'evaluation.zip')
    assert store.rows('SELECT * FROM messages') == before_rows
    after_data = {p.relative_to(store.directory).as_posix(): p.read_bytes()
                  for p in store.directory.rglob('*') if p.is_file() and p.suffix not in ('.sqlite3', '-wal', '-shm')}
    assert after_data == before_data


def test_export_failure_preserves_existing_destination_and_rejects_app_data(tmp_path):
    store = Store(tmp_path / 'data')
    chat = store.create_chat('Safe export')
    target = tmp_path / 'existing.zip'; target.write_bytes(b'KEEP PRIOR EXPORT')
    with pytest.raises(ValueError, match='conversation|chat'):
        export(store, 'missing-chat', target)
    assert target.read_bytes() == b'KEEP PRIOR EXPORT'
    with pytest.raises(ValueError, match='data|Memory'):
        export(store, chat, store.path)
    assert store.chat(chat)['title'] == 'Safe export'
    with pytest.raises(ValueError, match='data|Memory'):
        export(store, chat, store.strand.root / 'danger.zip')


def test_malformed_optional_payload_is_reported_without_exporting_raw_bytes(tmp_path):
    store = Store(tmp_path / 'data')
    chat = store.create_chat('Damaged metadata')
    ident = store.add_message(chat, 'tool', 'PRIVATE RAW TOOL DUPLICATE')
    with store.connection() as db:
        db.execute('UPDATE messages SET payload=? WHERE id=?', ('broken SECRET PAYLOAD', ident))
    files = export(store, chat, tmp_path / 'evaluation.zip')
    text = ''.join(files.values())
    assert 'SECRET PAYLOAD' not in text
    assert 'PRIVATE RAW TOOL DUPLICATE' not in text
    assert 'malformed' in text.lower()


def test_unusual_paths_and_json_secret_values_do_not_corrupt_bundle(tmp_path):
    store = Store(tmp_path / 'data')
    chat = store.create_chat('Quoted paths')
    path = '/home/alice/project/a"b.py'
    store.add_message(chat, 'user', f'Inspect {path}; then check {{"password": "secret-in-json"}}')
    tool(store, chat, 'read_file', {'path': path}, {'path': path, 'text': 'private'})
    files = export(store, chat, tmp_path / 'evaluation.zip')
    parsed = json.loads(files['conversation.json'])
    assert len(parsed['messages']) == 3
    assert '/home/alice' not in ''.join(files.values())
    assert 'secret-in-json' not in ''.join(files.values())


def test_export_action_uses_optional_notes_and_leaves_unsaved_draft_alone(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication, QFileDialog, QInputDialog
    from letracode.ui import MainWindow
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path / 'data')
    chat = store.create_chat('UI export')
    store.add_message(chat, 'user', 'Saved prompt')
    window = MainWindow(store)
    window.select_chat(chat)
    window.composer.setPlainText('Still editing')
    destination = tmp_path / 'ui-evaluation.zip'
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a, **kw: (str(destination), 'ZIP archive (*.zip)'))
    monkeypatch.setattr(QInputDialog, 'getMultiLineText', lambda *a, **kw: ('Local reproduction note', True))
    monkeypatch.setattr(window, 'save_editors', lambda: pytest.fail('Export must not save editor state'))
    actions = [action for menu in window.menuBar().actions() if menu.menu() for action in menu.menu().actions()]
    action = next((action for action in actions if action.text() == 'Export Evaluation…'), None)
    assert action is not None, 'The conversation needs an Export Evaluation action'
    action.trigger()
    assert destination.is_file()
    assert store.chat(chat)['draft'] == ''
    with zipfile.ZipFile(destination) as archive:
        assert archive.read('notes.md').decode() == 'Local reproduction note'
        assert b'Still editing' not in archive.read('transcript.md')
    monkeypatch.undo()
    window.close()


def test_approval_decisions_precede_their_tool_result_and_keep_recorded_times(tmp_path):
    store = Store(tmp_path / 'data')
    chat = store.create_chat('Approval chronology')
    request, result = tool(store, chat, 'run_command', {'command': 'true'}, {'exit_code': 0})
    row = store.messages(chat)[-1]
    payload = json.loads(row['payload'])
    payload['approvals'] = [{'kind': 'command', 'title': 'Run command?', 'decision': 'approved',
                            'requested_at': '2026-09-07T10:00:00Z', 'decided_at': '2026-09-07T10:00:03Z'}]
    store.update_message(result, row['content'], payload=payload)
    files = export(store, chat, tmp_path / 'evaluation.zip')
    events = [json.loads(line) for line in files['events.jsonl'].splitlines()]
    kinds = [event['kind'] for event in events if event['message_id'] == result]
    assert kinds.index('approval') < kinds.index('action_result')
    decision = next(event for event in events if event['kind'] == 'approval')
    assert decision['data']['decided_at'] == '2026-09-07T10:00:03Z'


def test_failure_labels_do_not_present_complete_storage_rows_as_success(tmp_path):
    store = Store(tmp_path / 'data')
    chat = store.create_chat('Honest outcomes')
    tool(store, chat, 'run_command', {'command': 'false'}, {'exit_code': 1})
    files = export(store, chat, tmp_path / 'evaluation.zip')
    assert 'Failed' in files['transcript.md']


def test_malformed_continuation_does_not_discard_saved_configuration(tmp_path):
    store = Store(tmp_path / 'data')
    chat = store.create_chat('Optional metadata')
    store.add_message(chat, 'assistant', 'Saved reply', payload={
        'run_configuration': {'thinking': False, 'context_size': 4096}, 'continuation': []})
    files = export(store, chat, tmp_path / 'evaluation.zip')
    configurations = json.loads(files['metadata.json'])['run_configurations']
    assert configurations[0]['configuration']['thinking'] is False
    assert configurations[0]['run_id'] is None


def test_git_version_collection_does_not_run_filesystem_monitor_hooks(tmp_path, monkeypatch):
    import subprocess
    from letracode import evaluation
    root = tmp_path / 'app'; root.mkdir()
    subprocess.run(['git', 'init', '-q', str(root)], check=True)
    subprocess.run(['git', '-C', str(root), '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                    'commit', '-q', '--allow-empty', '-m', 'fixture'], check=True)
    marker = root / 'hook-ran'
    hook = root / 'monitor'
    hook.write_text('#!/bin/sh\ntouch "' + str(marker) + '"\n')
    hook.chmod(0o700)
    subprocess.run(['git', '-C', str(root), 'config', 'core.fsmonitor', str(hook)], check=True)
    monkeypatch.setattr(evaluation, '__file__', str(root / 'letracode' / 'evaluation.py'))
    info = evaluation._application_version()
    assert info['git']['commit']
    assert not marker.exists(), 'Export version collection must not invoke repository-configured hooks'


def test_destination_parent_swap_cannot_redirect_export_or_leave_staging_bytes(tmp_path, monkeypatch):
    store = Store(tmp_path / 'data')
    chat = store.create_chat('Directory safety')
    outside = tmp_path / 'exports'; outside.mkdir()
    moved = tmp_path / 'exports-moved'
    original = zipfile.ZipFile.writestr
    changed = False

    def swap_directory(archive, *args, **kwargs):
        nonlocal changed
        if not changed:
            changed = True
            outside.rename(moved)
            outside.symlink_to(store.directory, target_is_directory=True)
        return original(archive, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, 'writestr', swap_directory)
    with pytest.raises((ValueError, OSError)):
        export(store, chat, outside / 'evaluation.zip')
    assert not (store.directory / 'evaluation.zip').exists()
    assert list(moved.iterdir()) == []
