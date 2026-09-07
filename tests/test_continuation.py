"""Run limits and observed progress must survive automatic segment boundaries."""
import math
import json

import pytest

from letracode.continuation import RunHalted, RunLimits, RunProgress


@pytest.mark.parametrize('field,value', [
    ('max_segments', 0), ('max_requests', True), ('max_actions', -1),
    ('max_stalls', 1.5), ('max_seconds', 0), ('max_seconds', True),
    ('max_seconds', math.inf), ('max_seconds', math.nan),
])
def test_limits_reject_invalid_or_unbounded_values(field, value):
    with pytest.raises(ValueError):
        RunLimits(**{field: value})


def test_request_and_action_budgets_survive_segment_transition_before_dispatch():
    run = RunProgress(RunLimits(max_requests=2, max_actions=3))
    run.reserve_request()
    run.reserve_actions(2)
    run.next_segment()
    run.reserve_request()
    assert run.snapshot()['requests'] == 2
    assert run.snapshot()['segments'] == 2
    with pytest.raises(RunHalted) as error:
        run.reserve_actions(2)
    assert error.value.reason == 'action_budget'
    assert run.snapshot()['actions'] == 2
    # A terminal controller cannot resume dispatch after a caller catches it.
    with pytest.raises(RunHalted):
        run.reserve_actions(1)


def test_final_allowed_request_dispatches_but_next_request_is_refused():
    run = RunProgress(RunLimits(max_requests=1))
    run.reserve_request()
    with pytest.raises(RunHalted) as error:
        run.reserve_request()
    assert error.value.reason == 'request_budget'
    assert run.snapshot()['requests'] == 1


def test_segment_and_elapsed_limits_are_run_wide():
    run = RunProgress(RunLimits(max_segments=1))
    with pytest.raises(RunHalted) as error:
        run.next_segment()
    assert error.value.reason == 'segment_budget'
    now = [10.0]
    run = RunProgress(RunLimits(max_seconds=2.0), clock=lambda: now[0])
    now[0] = 11.0
    run.next_segment()
    now[0] = 12.0
    with pytest.raises(RunHalted) as error:
        run.reserve_actions(1)
    assert error.value.reason == 'time_budget'
    assert run.snapshot()['actions'] == 0


def test_identical_reads_ignore_new_saved_ids_timestamps_and_call_ids():
    run = RunProgress(RunLimits(max_stalls=3))
    for ident in range(1, 4):
        progressed = run.observe('read_tool_result', {'result_id': ident}, {
            'text': 'same evidence', 'result_id': ident, 'timestamp': str(ident),
            'tool_call_id': str(ident),
        }, ident)
        assert progressed is (ident == 1)
    with pytest.raises(RunHalted) as error:
        run.observe('read_tool_result', {'result_id': 4}, {'text': 'same evidence', 'result_id': 4}, 4)
    assert error.value.reason == 'no_progress'
    assert run.snapshot()['last_result_ids'][-1] == 4


def test_alternating_read_cycle_eventually_stalls_across_segments():
    run = RunProgress(RunLimits(max_stalls=3))
    for ident, path in enumerate(('a.md', 'b.md', 'a.md', 'b.md'), 1):
        run.observe('read_file', {'path': path}, {'text': path, 'sha256': 'same'}, ident)
        run.next_segment()
    with pytest.raises(RunHalted) as error:
        run.observe('read_file', {'path': 'a.md'}, {'text': 'a.md', 'sha256': 'same'}, 5)
    assert error.value.reason == 'no_progress'


def test_unique_errors_are_failures_even_when_caller_claims_progress():
    run = RunProgress(RunLimits(max_stalls=3))
    for ident in (1, 2):
        assert not run.observe('read_file', {'path': f'{ident}.md'}, {'error': f'missing {ident}'}, ident, progress=True)
    with pytest.raises(RunHalted) as error:
        run.observe('read_file', {'path': '3.md'}, {'error': 'third failure'}, 3, progress=True)
    assert error.value.reason == 'no_progress'
    assert 'fail' in error.value.detail.lower()


@pytest.mark.parametrize('bad', [
    {'denied': 'No'}, {'executed': True, 'exit_code': 1, 'output': 'test failed'},
    {'executed': True, 'exit_code': 0, 'timed_out': True},
    {'executed': True, 'exit_code': 0, 'cancelled': True},
    {'executed': False, 'exit_code': 0},
])
def test_unsuccessful_outcomes_never_reset_stalls(bad):
    run = RunProgress(RunLimits(max_stalls=1))
    with pytest.raises(RunHalted):
        run.observe('run_command', {'command': 'test', 'cwd': '/fixture'}, bad, 1, progress=True)


def test_source_pages_and_changed_hash_reset_stalls():
    run = RunProgress(RunLimits(max_stalls=2))
    first = {'path': 'a.md', 'text': 'repeated text', 'offset': 0, 'next_offset': 13, 'sha256': 'v1'}
    assert run.observe('read_file', {'path': 'a.md', 'offset': 0}, first, 1)
    assert not run.observe('read_file', {'path': 'a.md', 'offset': 0}, first, 2)
    assert run.observe('read_file', {'path': 'a.md', 'offset': 13}, dict(first, offset=13, next_offset=None), 3)
    assert run.snapshot()['stalls'] == 0
    assert run.observe('read_file', {'path': 'a.md', 'offset': 0}, dict(first, sha256='v2'), 4)
    assert run.observe('search_project', {'query': 'fact'}, [], 5, progress=True)


def test_failed_test_then_successful_edit_allows_one_legitimate_test_rerun():
    run = RunProgress(RunLimits())
    command = {'command': 'pytest -q', 'cwd': '/fixture'}
    failure = {'executed': True, 'exit_code': 1, 'output': 'assertion failed'}
    assert not run.observe('run_command', command, failure, 11)
    assert run.duplicate_effect('run_command', command) == 11
    edit = {'path': '/fixture/a.py', 'old_text': 'bad', 'new_text': 'good', 'expected_sha256': 'old'}
    assert run.observe('edit_file', edit, {'path': edit['path'], 'sha256': 'new', 'written_characters': 4}, 12)
    assert run.duplicate_effect('run_command', command) is None
    assert run.duplicate_effect('edit_file', edit) == 12
    assert run.observe('run_command', command, {'executed': True, 'exit_code': 0, 'output': '1 passed'}, 13)
    assert run.duplicate_effect('run_command', command) == 13
    assert [row['result_id'] for row in run.snapshot()['last_outcomes']] == [11, 12, 13]
    assert run.snapshot()['last_outcomes'][-1]['outcome']['command'] == 'pytest -q'
    assert run.snapshot()['last_outcomes'][-1]['outcome']['cwd'] == '/fixture'


def test_changed_known_source_hash_allows_command_rerun_but_plain_reads_do_not():
    run = RunProgress(RunLimits(max_stalls=10))
    command = {'command': 'pytest', 'cwd': '/fixture'}
    run.observe('read_file', {'path': 'a'}, {'path': 'a', 'text': 'A', 'sha256': 'v1'}, 1)
    run.observe('run_command', command, {'executed': True, 'exit_code': 0}, 2)
    run.observe('read_file', {'path': 'a'}, {'path': 'a', 'text': 'A', 'sha256': 'v1'}, 3)
    assert run.duplicate_effect('run_command', command) == 2
    run.observe('read_file', {'path': 'b'}, {'path': 'b', 'text': 'B', 'sha256': 'first-observation'}, 4)
    assert run.duplicate_effect('run_command', command) == 2
    run.observe('read_file', {'path': 'a'}, {'path': 'a', 'text': 'A2', 'sha256': 'v2'}, 5)
    assert run.duplicate_effect('run_command', command) is None


def test_unchanged_write_does_not_unlock_prior_command_or_claim_new_progress():
    run = RunProgress(RunLimits())
    command = {'command': 'pytest', 'cwd': '/fixture'}
    run.observe('run_command', command, {'executed': True, 'exit_code': 0}, 1)
    assert not run.observe('write_file', {'path': 'a', 'content': 'same'}, {'path': 'a', 'unchanged': True, 'sha256': 'v1'}, 2)
    assert run.duplicate_effect('run_command', command) == 1


def test_snapshots_keep_bounded_concise_outcomes_and_do_not_expose_mutable_state():
    run = RunProgress(RunLimits(max_stalls=30))
    for ident in range(20):
        run.observe('run_command', {'command': str(ident)}, {'executed': True, 'exit_code': 0, 'output': 'x' * 10000}, ident)
    snapshot = run.snapshot()
    assert len(snapshot['last_outcomes']) <= 12
    assert len(snapshot['last_result_ids']) <= 24
    assert len(str(snapshot)) < 20000
    snapshot['last_result_ids'].clear()
    assert run.snapshot()['last_result_ids']


@pytest.mark.parametrize('name,field', [('read_file', 'text'), ('read_memory', 'text'), ('read_tool_result', 'content')])
def test_changing_empty_eof_cursors_never_establishes_progress(name, field):
    run = RunProgress(RunLimits(max_stalls=3))
    for ident in (1, 2):
        assert not run.observe(name, {'path': 'a', 'offset': 1000 + ident},
                               {field: '', 'total_chars': 20, 'offset': 1000 + ident, 'next_offset': None}, ident)
    with pytest.raises(RunHalted):
        run.observe(name, {'path': 'a', 'offset': 9999},
                    {field: '', 'total_chars': 20, 'offset': 9999, 'next_offset': None}, 3)


def test_first_empty_file_counts_once_but_not_arbitrary_empty_offsets():
    run = RunProgress(RunLimits(max_stalls=2))
    empty = {'path': 'a', 'text': '', 'total_chars': 0, 'sha256': 'empty', 'next_offset': None}
    assert run.observe('read_file', {'path': 'a', 'offset': 0}, dict(empty, offset=0), 1)
    assert not run.observe('read_file', {'path': 'a', 'offset': 100}, dict(empty, offset=100), 2)
    with pytest.raises(RunHalted):
        run.observe('read_file', {'path': 'a', 'offset': 200}, dict(empty, offset=200), 3)


def test_explicit_source_ledger_no_new_range_overrides_fingerprint_novelty():
    run = RunProgress(RunLimits())
    page = {'path': 'a', 'text': 'same known range', 'offset': 0, 'sha256': 'v1'}
    assert run.observe('read_file', {'path': 'a'}, page, 1, progress=True)
    assert not run.observe('read_file', {'path': 'a', 'max_chars': 20}, page, 2, progress=False)
    assert run.observe('read_file', {'path': 'a'}, dict(page, sha256='v2'), 3, progress=False)


def test_saved_json_content_and_catalog_ids_are_metadata_not_new_evidence():
    run = RunProgress(RunLimits(max_stalls=2))
    for ident in (1, 2):
        content = json.dumps({'text': 'same evidence', 'result_id': ident, 'timestamp': str(ident)})
        assert run.observe('read_tool_result', {'result_id': ident}, {
            'content': content, 'sha256': str(ident), 'total_chars': len(content), 'result_id': ident,
        }, ident) is (ident == 1)
    with pytest.raises(RunHalted):
        run.observe('read_tool_result', {'result_id': 3}, {
            'content': json.dumps({'text': 'same evidence', 'result_id': 3}), 'sha256': '3', 'total_chars': 43,
        }, 3)
    run = RunProgress(RunLimits(max_stalls=2))
    item = {'name': 'read_file', 'source': {'path': 'a'}, 'status': 'complete'}
    assert run.observe('list_tool_results', {}, {'results': [dict(item, result_id=1, preview='id1')], 'through_id': 1}, 1)
    assert not run.observe('list_tool_results', {'after_id': 1}, {
        'results': [dict(item, result_id=2, preview='id2'), dict(item, result_id=3, preview='id3')], 'through_id': 3,
    }, 2)
    with pytest.raises(RunHalted):
        run.observe('list_tool_results', {'after_id': 3}, {'results': [dict(item, result_id=4, preview='id4')]}, 3)


def test_interrupted_command_cannot_be_replayed_after_an_unrelated_source_change():
    run = RunProgress(RunLimits())
    args = {'command': 'command with possibly unfinished effects', 'cwd': '/fixture'}
    run.observe('run_command', args, {'executed': True, 'exit_code': -9, 'timed_out': True}, 1)
    run.observe('write_file', {'path': '/fixture/a', 'content': 'new'},
                {'path': '/fixture/a', 'written_characters': 3, 'sha256': 'new'}, 2)
    assert run.duplicate_effect('run_command', args) == 1
