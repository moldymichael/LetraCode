"""Deterministic runner checks; these are NOT local-model acceptance evidence."""
import json
from pathlib import Path
import subprocess
import threading

import pytest

from tools import run_acceptance as runner


def test_acceptance_python_command_runs_with_quoted_paths_and_environment(tmp_path):
    from letracode.tools import command_argv

    folder = tmp_path / "Project café's notes"
    folder.mkdir()
    script = folder / 'check environment.py'
    script.write_text(
        "import json, os, sys\n"
        "print(json.dumps([os.environ['LETRACODE_FIXTURE_VALUE'], sys.argv[1]], ensure_ascii=True))\n",
        encoding='utf-8')
    value = "apostrophe ' and double quote \" and $literal"
    command = runner.python_command(str(script), value,
        environment={'LETRACODE_FIXTURE_VALUE': value})
    result = subprocess.run(command_argv(command), cwd=folder, text=True,
        capture_output=True, timeout=15, check=True)
    assert json.loads(result.stdout) == [value, value]


def git(path, *args):
    return subprocess.check_output(['git', *args], cwd=path)


def repository(tmp_path):
    source = tmp_path / 'source'
    (source / 'letracode').mkdir(parents=True)
    (source / 'tests').mkdir()
    (source / 'tools').mkdir()
    (source / 'tools/run_acceptance.py').write_text('# fixture runner\n')
    (source / 'pyproject.toml').write_text('[project]\nname = "fixture"\n')
    (source / 'letracode/tools.py').write_text('original = True\n')
    (source / 'tests/test_tools.py').write_text('def test_original(): pass\n')
    git(source, 'init', '-q')
    git(source, 'config', 'core.autocrlf', 'false')
    git(source, 'add', '.')
    git(source, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
        'commit', '-qm', 'fixture')
    return source


def test_coding_clone_includes_current_changes_and_preserves_source(tmp_path):
    source = repository(tmp_path)
    (source / 'letracode/tools.py').write_text('current_fix = True\n')
    before = git(source, 'diff', '--binary', 'HEAD')
    root = tmp_path / 'trial'
    fixture = runner.prepare_fixture(root, 'coding', source)
    target = Path(fixture['linked_root'])
    assert (target / 'letracode/tools.py').read_text().endswith('current_fix = True\n')
    assert git(target, 'remote') == b''
    assert git(source, 'diff', '--binary', 'HEAD') == before
    assert git(target, 'diff') == b''
    assert git(target, 'diff', '--cached')
    assert (target / 'unrelated-sentinel.txt').read_text() == 'Preserve this unrelated untracked sentinel.\n'
    assert Path(fixture['test_root']).is_dir()
    with pytest.raises(FileExistsError):
        runner.prepare_fixture(root, 'coding', source)


def test_reading_fixture_keeps_key_outside_links_and_has_deep_evidence(tmp_path):
    fixture = runner.prepare_fixture(tmp_path / 'reading', 'reading', repository(tmp_path))
    linked = Path(fixture['linked_root'])
    key = json.loads(Path(fixture['answer_key']).read_text())
    assert not Path(fixture['answer_key']).is_relative_to(linked)
    deep = (linked / 'chapter-02.txt').read_text()
    assert deep.index(key['deep_marker']) > 20000
    assert len(list(linked.glob('chapter-*.txt'))) >= 3
    assert key['corrected_destination'] not in fixture['continuation']


def test_coding_fixture_refuses_to_drop_new_application_modules(tmp_path):
    source = repository(tmp_path)
    (source / 'letracode/new_dependency.py').write_text('new_dependency = True\n')
    with pytest.raises(ValueError, match='untracked application modules'):
        runner.prepare_fixture(tmp_path / 'trial', 'coding', source)
    assert not (tmp_path / 'trial').exists()


@pytest.mark.parametrize('case', ['coding', 'reading'])
def test_prelaunch_provenance_records_head_patch_and_current_file_hashes(tmp_path, case):
    import hashlib
    source = repository(tmp_path)
    (source / 'letracode/tools.py').write_text('current_fix = True\n')
    new_module = source / 'letracode/new_dependency.py'
    new_module.write_bytes(b'new_dependency = True\n')
    git(source, 'add', 'letracode/new_dependency.py')
    original_head = git(source, 'rev-parse', 'HEAD').decode().strip()
    patch = git(source, 'diff', '--binary', 'HEAD')
    root = tmp_path / 'trial'
    fixture = runner.prepare_fixture(root, case, source)
    references = fixture['application_provenance']
    assert fixture['application_head'] == original_head
    provenance_file = root / references['path']
    assert hashlib.sha256(provenance_file.read_bytes()).hexdigest() == references['sha256']
    provenance = json.loads(provenance_file.read_text())
    assert provenance['head'] == original_head
    assert (root / provenance['tracked_patch']['path']).read_bytes() == patch
    assert provenance['tracked_patch']['sha256'] == hashlib.sha256(patch).hexdigest()
    for relative in ('letracode/tools.py', 'letracode/new_dependency.py', 'tools/run_acceptance.py', 'pyproject.toml'):
        assert provenance['files'][relative]['sha256'] == hashlib.sha256((source / relative).read_bytes()).hexdigest()
    if case == 'coding':
        target = Path(fixture['linked_root'])
        assert git(target, 'show', 'HEAD:letracode/new_dependency.py') == b'new_dependency = True\n'
        assert git(target, 'diff') == b''
    (source / 'letracode/tools.py').write_text('changed_after_recording = True\n')
    assert (root / provenance['tracked_patch']['path']).read_bytes() == patch


def test_request_budget_stops_before_extra_completion_and_records_error(tmp_path, monkeypatch):
    from letracode.engine import EngineConfig, LocalEngine, EngineError
    from letracode.budgeting import RequestUsage
    calls = []
    def scripted_complete(self, messages, tools, cancel, on_delta, thinking=False):
        calls.append(messages)
        return {'role': 'assistant', 'content': 'scripted fixture only'}
    monkeypatch.setattr(LocalEngine, 'complete', scripted_complete)
    monkeypatch.setattr(LocalEngine, 'request_usage', lambda *a: RequestUsage(50, 20, 128, 'scripted'))
    recorder = runner.Recorder(tmp_path, evidence_kind='scripted-engine-test')
    budget = runner.Budget(max_requests=1, max_seconds=20, max_turns=2, max_corrections=2)
    engine = runner.ObservedEngine(EngineConfig(), tmp_path, recorder, budget)
    engine.complete([{'role':'user','content':'fixture'}], None, threading.Event(), lambda _: None)
    with pytest.raises(EngineError, match='request budget'):
        engine.complete([], None, threading.Event(), lambda _: None)
    assert len(calls) == 1
    events = [json.loads(line) for line in (tmp_path / 'events.jsonl').read_text().splitlines()]
    assert any(event['kind'] == 'request' and event['messages'][0]['content'] == 'fixture' for event in events)
    assert any(event['kind'] == 'reply' for event in events)
    assert any(event['kind'] == 'budget_stop' for event in events)


def test_correction_budget_counts_verified_implementation_cycles():
    def row(name, result):
        return {'role':'tool', 'payload':json.dumps({'message':{'name':name, 'content':json.dumps(result)}})}
    rows = [row('edit_file', {'path':'/target/tools.py','written_characters':20}),
            row('edit_file', {'path':'/target/tools.py','written_characters':30}),
            row('run_command', {'executed':True,'command':'python3 -m pytest tests/test_tools.py','exit_code':1}),
            row('edit_file', {'path':'/target/tools.py','written_characters':40})]
    assert runner.implementation_cycles(rows, '/target/tools.py') == (2, True)


def test_evidence_summary_uses_server_timings_and_actual_tool_selections(tmp_path):
    recorder = runner.Recorder(tmp_path, evidence_kind='scripted-engine-test')
    recorder.event('request', number=1, messages=[])
    recorder.event('stream_event', number=1, event={'timings':{'prompt_ms':125, 'predicted_ms':250}})
    recorder.event('reply', number=1, reply={'tool_calls':[{'function':{'name':'read_file'}}]})
    recorder.event('request_finished', number=1, seconds=0.8)
    recorder.event('approval_decided', decision='denied_or_stopped_by_native_dialog')
    summary = runner.summarize_events(tmp_path / 'events.jsonl')
    assert summary['requests'] == 1
    assert summary['tool_selections'] == {'read_file':1}
    assert summary['request_seconds'] == {'1':0.8}
    assert summary['server_timings'] == {'1':{'prompt_ms':125, 'predicted_ms':250}}
    assert summary['approval_decisions'] == {'denied_or_stopped_by_native_dialog':1}


def test_native_harness_records_scripted_response_and_preserves_sources(tmp_path, monkeypatch):
    """Exercise the real Qt window/worker/store; only the inference peer is scripted."""
    import os
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from letracode.engine import EngineConfig, LocalEngine
    from letracode.budgeting import RequestUsage
    fixture = runner.prepare_fixture(tmp_path / 'trial', 'reading', repository(tmp_path))
    before = (Path(fixture['linked_root']) / 'chapter-02.txt').read_bytes()
    monkeypatch.setattr(LocalEngine, 'start', lambda self, *args: None)
    monkeypatch.setattr(LocalEngine, 'request_usage', lambda *args: RequestUsage(500, 100, 128, 'scripted'))
    monkeypatch.setattr(LocalEngine, 'complete', lambda *args: {'role':'assistant','content':'Scripted harness response, not model acceptance.'})
    root = Path(fixture['root'])
    recorder = runner.Recorder(root, evidence_kind='scripted-engine-test')
    runner.run_native(fixture, EngineConfig(executable='/scripted/runtime', model_path='/scripted/model.gguf'),
                      runner.Budget(max_seconds=15), recorder)
    result = json.loads((root / 'result.json').read_text())
    assert result['evidence_kind'] == 'scripted-engine-test'
    assert result['acceptance_passed'] is None
    assert result['rows'][-1]['content'] == 'Scripted harness response, not model acceptance.'
    assert result['engine_stopped'] is True
    assert (Path(fixture['linked_root']) / 'chapter-02.txt').read_bytes() == before


@pytest.mark.parametrize('value', [float('inf'), float('nan'), 0, 14401])
def test_unbounded_trial_is_rejected(value):
    with pytest.raises(ValueError):
        runner.Budget(max_seconds=value)


@pytest.mark.parametrize('opt_in', [False, True])
@pytest.mark.parametrize('blocked', ['repeated_read', 'denied_write'])
def test_production_stop_never_sends_fixture_continuation(tmp_path, monkeypatch, opt_in, blocked):
    import os
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from letracode.engine import EngineConfig, LocalEngine
    from letracode.budgeting import RequestUsage
    fixture = runner.prepare_fixture(tmp_path / 'trial', 'reading', repository(tmp_path))
    fixture['fixture_continuation'] = opt_in
    path = str(Path(fixture['linked_root']) / 'chapter-01.txt') if blocked == 'repeated_read' else fixture['answer_key']
    monkeypatch.setattr(LocalEngine, 'start', lambda *args: None)
    monkeypatch.setattr(LocalEngine, 'request_usage', lambda *args: RequestUsage(500, 100, 128, 'scripted'))
    counter = 0
    def scripted(self, messages, tools, cancel, on_delta, thinking=False):
        nonlocal counter
        counter += 1
        return {'role':'assistant','content':'', 'tool_calls':[{'id':f'call-{counter}',
            'type':'function', 'function':{'name':'read_file' if blocked == 'repeated_read' else 'write_file',
                'arguments':json.dumps({'path':path} if blocked == 'repeated_read' else {'path':path, 'content':'Must never be written'})}}]}
    monkeypatch.setattr(LocalEngine, 'complete', scripted)
    root = Path(fixture['root'])
    runner.run_native(fixture, EngineConfig(executable='/scripted/runtime', model_path='/scripted/model.gguf'),
                      runner.Budget(max_seconds=2), runner.Recorder(root, evidence_kind='scripted-engine-test'))
    result = json.loads((root / 'result.json').read_text())
    assert result['pause_exercised'] is True
    assert result['continuation_exercised'] is False
    assert len([row for row in result['rows'] if row['role'] == 'user']) == 1
    assert result['budget']['requests'] == (4 if blocked == 'repeated_read' else 1)
    assert result['outcome'] == 'application_stopped'
    assert not result['fixture_continuation_exercised']
    assert not result['automatic_continuation_exercised']
    events = [json.loads(line) for line in (root / 'events.jsonl').read_text().splitlines()]
    assert not any(item['kind'] == 'fixture_continuation' for item in events)


def test_native_automatic_segments_keep_one_worker_and_one_user_turn(tmp_path, monkeypatch):
    import os
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from letracode.engine import EngineConfig, LocalEngine
    from letracode.budgeting import RequestUsage
    fixture = runner.prepare_fixture(tmp_path / 'trial', 'reading', repository(tmp_path))
    pages = [(path, offset) for path in sorted(Path(fixture['linked_root']).glob('chapter-*.txt'))
             for offset in range(0, len(path.read_text()), 2000)]
    assert len(pages) > 20
    starts, requests = [], []
    monkeypatch.setattr(LocalEngine, 'start', lambda *args: starts.append(True))
    monkeypatch.setattr(LocalEngine, 'request_usage', lambda *args: RequestUsage(500, 100, 128, 'scripted'))
    def scripted(self, messages, tools, cancel, on_delta, thinking=False):
        requests.append(messages)
        assert any(message['role'] == 'user' and message['content'] == fixture['prompt'] for message in messages)
        index = len(requests) - 1
        if index == len(pages):
            return {'role':'assistant', 'content':'Scripted full-source traversal; independent acceptance remains unverified.'}
        path, offset = pages[index]
        return {'role':'assistant','content':'', 'tool_calls':[{'id':f'page-{index}', 'type':'function',
            'function':{'name':'read_file','arguments':json.dumps({'path':str(path),'offset':offset,'max_chars':2000})}}]}
    monkeypatch.setattr(LocalEngine, 'complete', scripted)
    root = Path(fixture['root'])
    runner.run_native(fixture, EngineConfig(executable='/scripted/runtime', model_path='/scripted/model.gguf'),
                      runner.Budget(max_seconds=15), runner.Recorder(root, evidence_kind='scripted-engine-test'))
    result = json.loads((root / 'result.json').read_text())
    assert len(starts) == 1
    assert result['budget']['turns'] == 1 and result['user_turns'] == 1
    assert result['budget']['requests'] == len(pages) + 1
    assert result['automatic_continuation_exercised'] and result['continuation_exercised']
    assert not result['fixture_continuation_exercised'] and not result['user_continuation_exercised']
    progress = result['application_progress']
    assert progress['segment_boundaries'] == len(pages) // 10
    assert len(progress['runs']) == 1
    assert progress['runs'][0]['segments'] == 1 + len(pages) // 10
    assert progress['runs'][0]['actions'] == len(pages)
    assert progress['runs'][0]['requests'] == len(pages) + 1
    assert result['outcome'] == 'worker_finished' and result['engine_stopped']
    events = [json.loads(line) for line in (root / 'events.jsonl').read_text().splitlines()]
    boundaries = [item for item in events if item['kind'] == 'segment_boundary']
    assert len(boundaries) == progress['segment_boundaries']
    assert len({item['row_id'] for item in boundaries}) == len(boundaries)
    assert all(item['continuation']['run_id'] == progress['runs'][0]['run_id'] for item in boundaries)
    assert sum(item['kind'] == 'turn_started' for item in events) == 1
    assert not any(item['kind'] in ('fixture_continuation', 'approval_requested') for item in events)


@pytest.mark.parametrize('finish_after_resume', [True, False])
def test_opted_in_legacy_fixture_continues_once_with_distinct_origin(tmp_path, monkeypatch, finish_after_resume):
    import os
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from letracode.engine import EngineConfig, LocalEngine
    from letracode.budgeting import RequestUsage
    from letracode.continuation import RunLimits
    from letracode.worker import ConversationWorker
    import letracode.ui as ui
    class ScriptedLegacyWorker(ConversationWorker):
        """Only this test recreates an archived manual ten-round checkpoint."""
        def __init__(self, *args, **kwargs):
            super().__init__(*args, limits=RunLimits(max_segments=1), **kwargs)
        def run(self):
            super().run()
            row = self.store.messages(self.chat_id)[-1]
            data = json.loads(row['payload'])
            if data.get('checkpoint', {}).get('reason') == 'segment_budget':
                data['checkpoint'].pop('continuation')
                data['checkpoint']['reason'] = 'action_round_limit'
                self.store.update_message(row['id'], 'Scripted legacy manual ten-round pause.', 'paused', payload=data)
                if counter == 20:
                    # End the idle second pause deterministically. This checks
                    # the single fixture continuation, not filesystem speed;
                    # the separate wall-budget test covers real elapsed time.
                    budget.started -= budget.max_seconds
    monkeypatch.setattr(ui, 'ConversationWorker', ScriptedLegacyWorker)
    fixture = runner.prepare_fixture(tmp_path / 'trial', 'reading', repository(tmp_path))
    fixture['fixture_continuation'] = True
    paths = [path for path in sorted(Path(fixture['linked_root']).glob('chapter-*.txt')) if path.name != 'chapter-02.txt'][:10]
    monkeypatch.setattr(LocalEngine, 'start', lambda *args: None)
    monkeypatch.setattr(LocalEngine, 'request_usage', lambda *args: RequestUsage(500, 100, 128, 'scripted'))
    counter = 0
    def scripted(self, messages, tools, cancel, on_delta, thinking=False):
        nonlocal counter
        counter += 1
        if counter > 10 and finish_after_resume:
            return {'role':'assistant', 'content':'Scripted completion after fixture continuation.'}
        if counter == 11:
            # The second legacy segment must observe new evidence, otherwise
            # the production no-progress rule correctly stops repeated reads.
            for path in paths:
                path.write_text(path.read_text() + '\nExternal fixture revision for the second legacy segment.\n')
        return {'role':'assistant','content':'', 'tool_calls':[{'id':f'call-{counter}',
            'type':'function', 'function':{'name':'read_file','arguments':json.dumps({'path':str(paths[(counter - 1) % 10])})}}]}
    monkeypatch.setattr(LocalEngine, 'complete', scripted)
    root = Path(fixture['root'])
    budget = runner.Budget(max_seconds=30, max_turns=3)
    runner.run_native(fixture, EngineConfig(executable='/scripted/runtime', model_path='/scripted/model.gguf'),
                      budget, runner.Recorder(root, evidence_kind='scripted-engine-test'))
    result = json.loads((root / 'result.json').read_text())
    assert result['pause_exercised'] is True
    assert result['fixture_continuation_exercised'] is True
    assert result['budget']['turns'] == 2
    assert result['budget']['requests'] == (11 if finish_after_resume else 20)
    users = [row for row in result['rows'] if row['role'] == 'user']
    assert len(users) == 2
    assert users[1]['content'] == fixture['continuation']
    events = [json.loads(line) for line in (root / 'events.jsonl').read_text().splitlines()]
    continuations = [item for item in events if item['kind'] == 'fixture_continuation']
    assert len(continuations) == 1
    assert continuations[0]['message'] == fixture['continuation']
    assert continuations[0]['origin'] == 'acceptance_fixture_operator'
    assert continuations[0]['provenance']['field'] == 'manifest.json:continuation'
    assert continuations[0]['provenance']['paused_message_id'] in [row['id'] for row in result['rows'] if row['status'] == 'paused']
    assert not any(item['kind'].startswith('approval_') for item in events)


def test_fixture_continuation_cli_rejects_coding_before_runtime_access(tmp_path, capsys):
    with pytest.raises(SystemExit) as error:
        runner.main(['--case','coding','--fixture-continuation','--output',str(tmp_path/'unused'),
                     '--executable','/unused/runtime','--model','/unused/model.gguf'])
    assert error.value.code == 2
    assert 'only supported for reading' in capsys.readouterr().err
    assert not (tmp_path/'unused').exists()


def test_scripted_active_request_cancels_at_wall_budget(tmp_path, monkeypatch):
    import os
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from letracode.engine import Cancelled, EngineConfig, LocalEngine
    from letracode.budgeting import RequestUsage
    fixture = runner.prepare_fixture(tmp_path / 'trial', 'reading', repository(tmp_path))
    monkeypatch.setattr(LocalEngine, 'start', lambda *args: None)
    monkeypatch.setattr(LocalEngine, 'request_usage', lambda *args: RequestUsage(500, 100, 128, 'scripted'))
    def scripted(self, messages, tools, cancel, on_delta, thinking=False):
        assert cancel.wait(5), 'The runner must cancel its active worker at the wall budget'
        raise Cancelled('scripted cancellation')
    monkeypatch.setattr(LocalEngine, 'complete', scripted)
    root = Path(fixture['root'])
    runner.run_native(fixture, EngineConfig(executable='/scripted/runtime', model_path='/scripted/model.gguf'),
                      runner.Budget(max_seconds=1), runner.Recorder(root, evidence_kind='scripted-engine-test'))
    result = json.loads((root / 'result.json').read_text())
    assert result['outcome'] == 'wall_time_budget_exhausted'
    assert result['rows'][-1]['status'] == 'interrupted'
    events = [json.loads(line) for line in (root / 'events.jsonl').read_text().splitlines()]
    assert any(event['kind'] == 'request_error' and event['error_type'] == 'Cancelled' for event in events)
