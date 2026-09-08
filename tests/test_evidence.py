"""Ordinary-source coverage comes from saved evidence, never model assertions."""
import copy
import hashlib
import importlib
import json
import threading

import pytest

from letracode.tools import ToolExecutor


def api():
    return importlib.import_module('letracode.evidence')


def source(offset=0, text='abcd', total=12, sha='a' * 64, **extra):
    return {'path': '/synthetic/chapter.txt', 'offset': offset, 'text': text,
            'total_chars': total, 'next_offset': offset + len(text) if offset + len(text) < total else None,
            'sha256': sha, 'editable': True, 'source_truncated': False, **extra}


def row(ident, role, content='', payload=None, status='complete', chat='chat'):
    return {'id': ident, 'chat_id': chat, 'role': role, 'content': content,
            'status': status, 'payload': json.dumps(payload or {})}


def tool_row(ident, result, name='read_file', call=None, tracked=True):
    message = {'role': 'tool', 'name': name, 'tool_call_id': call or f'call-{ident}',
               'content': json.dumps(result, ensure_ascii=False)}
    data = {'message': message}
    if tracked and name in ('read_file', 'search_project'):
        data['source_evidence'] = api().source_evidence(name, result)
    return row(ident, 'tool', payload=data)


def expose(ident, rows, tools=None):
    messages = [json.loads(item['payload'])['message'] for item in (tools or rows)
                if item['role'] == 'tool']
    return row(ident, 'assistant', 'A model response.',
               {'source_exposure': api().request_exposure(messages, rows)})


def test_raw_read_metadata_records_actual_unicode_span(tmp_path):
    path = tmp_path / 'source.txt'
    path.write_bytes('\ufeffcafé\r\n尾'.encode())
    tool = ToolExecutor([str(tmp_path)], tmp_path / 'data', lambda _: False, threading.Event())
    result = json.loads(tool.execute('read_file', {'path': str(path), 'offset': 1, 'max_chars': 5}))
    assert result['text'] == 'café\r'
    assert result['coverage']['ranges'] == [[1, 6]]
    assert result['coverage']['representation'] == 'raw-characters'


def test_numbered_read_metadata_does_not_credit_unreturned_tail(tmp_path):
    path = tmp_path / 'source.txt'
    path.write_bytes(('first\r\n' + 'x' * 20000).encode())
    tool = ToolExecutor([str(tmp_path)], tmp_path / 'data', lambda _: False, threading.Event())
    result = json.loads(tool.execute('read_file', {'path': str(path)}))
    assert result['coverage']['ranges'] == [[0, 15995]]
    assert result['coverage']['representation'] == 'numbered-lines'
    assert result['next_offset'] == 15995


def test_exposed_union_keeps_skipped_middle_despite_model_claims():
    rows = [row(1, 'user', 'Read all chapters.')]
    for ident, offset, length in [(2, 0, 4000), (3, 4000, 4000), (4, 21542, 4000)]:
        rows.append(tool_row(ident, source(offset, 'x' * length, 25542)))
    rows.append(expose(5, rows))
    rows.append(row(6, 'assistant', 'I fully read every chapter. Coverage is 100%.'))
    state = api().evidence_state(rows, 1)
    file = state['files'][0]
    assert file['exposed_ranges'] == [[0, 8000], [21542, 25542]]
    assert file['missing_ranges'] == [[8000, 21542]]
    assert file['complete_supported_text'] is False and state['incomplete'] is True
    assert file['source_result_ids'] == [2, 3, 4]
    assert any('system' in issue.lower() for issue in state['issues'])


def test_retrieved_but_compacted_result_is_not_exposed():
    rows = [row(1, 'user', 'Read all.'), tool_row(2, source(0, 'a' * 12))]
    original = json.loads(rows[1]['payload'])['message']
    compacted = {**original, 'content': json.dumps({'result_id': 2, 'context_truncated': True,
                                                  'context_preview': 'partial'})}
    exposure = api().request_exposure([compacted], rows)
    assert exposure == []
    rows.append(row(3, 'assistant', payload={'source_exposure': exposure}))
    file = api().evidence_state(rows, 1)['files'][0]
    assert file['retrieved_ranges'] == [[0, 12]]
    assert file['exposed_ranges'] == [] and file['missing_ranges'] == [[0, 12]]


def test_overlapping_reads_merge_without_false_progress_and_inputs_unchanged():
    rows = [row(1, 'user', 'Read all.'), tool_row(2, source(4, 'x' * 8)),
            tool_row(3, source(0, 'x' * 8)), tool_row(4, source(0, 'x' * 8))]
    rows.append(expose(5, rows))
    before = copy.deepcopy(rows)
    state = api().evidence_state(rows, 1)
    assert state['files'][0]['exposed_ranges'] == [[0, 12]]
    assert state['files'][0]['missing_ranges'] == []
    assert state['files'][0]['complete_supported_text'] is True
    assert state['incomplete'] is False
    assert rows == before


def test_new_hash_never_borrows_previous_version_coverage():
    rows = [row(1, 'user', 'Read all.'), tool_row(2, source(0, 'x' * 12))]
    rows.append(expose(3, rows))
    rows.append(tool_row(4, source(8, 'tail', sha='b' * 64)))
    rows.append(expose(5, rows, [rows[-1]]))
    state = api().evidence_state(rows, 1)
    file = state['files'][0]
    assert file['source_sha256'] == 'b' * 64
    assert file['missing_ranges'] == [[0, 8]]
    assert len(file['versions']) == 2
    assert any('version' in issue.lower() for issue in state['issues'])


def test_extraction_version_and_truncation_remain_visible_at_eof():
    binary = source(0, 'all', 3, None, editable=False, source_sha256='c' * 64,
                    source_truncated=True, extraction={'version': 'pdf-v1', 'coverage': 'No OCR'})
    rows = [row(1, 'user', 'Read PDF'), tool_row(2, binary)]
    rows.append(expose(3, rows))
    binary = {**binary, 'extraction': {'version': 'pdf-v2', 'coverage': 'No OCR'}}
    rows.append(tool_row(4, binary))
    rows.append(expose(5, rows, [rows[-1]]))
    file = api().evidence_state(rows, 1)['files'][0]
    assert file['extractor_version'] == 'pdf-v2'
    assert len(file['versions']) == 2
    assert file['missing_ranges'] == [] and file['source_truncated'] is True
    assert file['complete_supported_text'] is False


def test_genuine_empty_source_requires_visible_result():
    rows = [row(1, 'user', 'Read empty'), tool_row(2, source(0, '', 0))]
    assert api().evidence_state(rows, 1)['incomplete'] is True
    rows.append(expose(3, rows))
    assert api().evidence_state(rows, 1)['files'][0]['complete_supported_text'] is True


def test_search_scanning_only_credits_returned_excerpt():
    hit = source(4, 'find', 12)
    hit['searched_chars'] = 12
    rows = [row(1, 'user', 'Find evidence'), tool_row(2, {'results': [hit]}, 'search_project')]
    rows.append(expose(3, rows))
    file = api().evidence_state(rows, 1)['files'][0]
    assert file['exposed_ranges'] == [[4, 8]]
    assert file['missing_ranges'] == [[0, 4], [8, 12]]


def saved_page_row(ident, original, offset, length):
    raw = json.loads(original['payload'])['message']['content']
    end = min(len(raw), offset + length)
    result = {'result_id': original['id'], 'name': 'read_file', 'content': raw[offset:end],
              'offset': offset, 'next_offset': end if end < len(raw) else None,
              'total_chars': len(raw), 'sha256': hashlib.sha256(raw.encode()).hexdigest()}
    return tool_row(ident, result, 'read_tool_result')


def test_saved_json_pages_recover_only_after_complete_serialized_result_exposure():
    original = tool_row(2, source(0, 'café' * 3))
    rows = [row(1, 'user', 'Read all.'), original]
    raw = json.loads(original['payload'])['message']['content']
    cut = len(raw) // 2
    first = saved_page_row(3, original, 0, cut)
    rows += [first, expose(4, rows + [first], [first])]
    assert api().evidence_state(rows, 1)['files'][0]['exposed_ranges'] == []
    second = saved_page_row(5, original, cut, len(raw))
    rows += [second, expose(6, rows + [second], [second])]
    file = api().evidence_state(rows, 1)['files'][0]
    assert file['exposed_ranges'] == [[0, 12]]
    assert file['complete_supported_text'] is True


def test_saved_json_wrong_page_hash_is_rejected():
    original = tool_row(2, source(0, 'x' * 12))
    page = saved_page_row(3, original, 0, 9999)
    data = json.loads(page['payload'])
    body = json.loads(data['message']['content']); body['sha256'] = 'f' * 64
    data['message']['content'] = json.dumps(body); page['payload'] = json.dumps(data)
    with pytest.raises(ValueError, match='saved|hash|version'):
        api().request_exposure([data['message']], [row(1, 'user'), original, page])


def test_legacy_untracked_reads_and_other_scope_never_claim_complete():
    legacy = tool_row(2, source(0, 'x' * 12), tracked=False)
    rows = [row(1, 'user'), legacy]
    state = api().evidence_state(rows, 1)
    assert state['incomplete'] is True and any('untracked' in issue.lower() for issue in state['issues'])
    assert api().source_evidence('run_command', source(0, 'x' * 12)) == []
    assert api().source_evidence('read_file', {'denied': 'No'}) == []
    rows.append(row(3, 'user', 'New objective'))
    assert api().evidence_state(rows, 3)['files'] == []
    with pytest.raises(ValueError, match='chat'):
        api().evidence_state(rows + [row(4, 'user', chat='other')], 1)


@pytest.mark.parametrize('legacy,failed_path,late_failure,want_incomplete', [
    (False, '/synthetic/./chapter.txt', False, False),
    (True, '/synthetic/chapter.txt', False, True),
    (False, '/synthetic/other.txt', False, True),
    (False, '/synthetic/chapter.txt', True, True),
])
def test_only_resolved_modern_read_attempts_stop_blocking_coverage(legacy, failed_path, late_failure, want_incomplete):
    failed = tool_row(2, {'error': 'File was unavailable'}, tracked=not legacy)
    data = json.loads(failed['payload'])
    data['arguments'] = {'path': failed_path}
    failed['payload'] = json.dumps(data)
    read = tool_row(3, source(0, 'x' * 12))
    rows = [row(1, 'user'), failed, read]
    rows.append(expose(4, rows))
    if late_failure:
        later = dict(failed, id=5)
        rows.append(later)
    state = api().evidence_state(rows, 1)
    assert state['files'][0]['complete_supported_text'] is True
    assert state['incomplete'] is want_incomplete
    assert any('2' in issue and 'unsuccessful' in issue for issue in state['issues'])


def test_recovery_matches_saved_requested_path_when_source_resolves_to_an_alias():
    failed = tool_row(2, {'error': 'File was unavailable'})
    read = tool_row(3, source(0, 'x' * 12))
    for item in (failed, read):
        data = json.loads(item['payload'])
        data['arguments'] = {'path': '/synthetic/linked-chapter.txt'}
        item['payload'] = json.dumps(data)
    rows = [row(1, 'user'), failed, read]
    rows.append(expose(4, rows))
    state = api().evidence_state(rows, 1)
    assert state['files'][0]['path'] == '/synthetic/chapter.txt'
    assert state['incomplete'] is False


def test_interrupted_request_exposure_does_not_count():
    rows = [row(1, 'user'), tool_row(2, source(0, 'x' * 12))]
    interrupted = expose(3, rows); interrupted['status'] = 'interrupted'
    rows.append(interrupted)
    assert api().evidence_state(rows, 1)['files'][0]['exposed_ranges'] == []


@pytest.mark.parametrize('change', [{'offset': True}, {'total_chars': -1}, {'sha256': 'invalid'},
                                    {'text': 'x' * 13}, {'source_truncated': 'false'},
                                    {'coverage': {'version': 1, 'ranges': [[0, 999]], 'representation': 'raw-characters'}}])
def test_invalid_source_metadata_fails_closed(change):
    with pytest.raises(ValueError):
        api().source_evidence('read_file', source(**change))


def test_file_and_range_limits_are_bounded():
    hits = [source(path=f'/synthetic/{i}.txt') for i in range(129)]
    with pytest.raises(ValueError, match='128|limit'):
        api().source_evidence('search_project', {'results': hits})
    with pytest.raises(ValueError, match='4096|range|limit'):
        api().source_evidence('read_file', source(coverage={'version': 1,
            'representation': 'numbered-lines', 'ranges': [[0, 1]] * 4097}))


def test_summary_is_bounded_and_never_claims_whole_work_or_understanding():
    rows = [row(1, 'user')]
    for ident in range(2, 15):
        rows.append(tool_row(ident, source(path=f'/synthetic/chapter-{ident}.txt')))
    text = api().summary(api().evidence_state(rows, 1), max_files=8)
    assert len(text) <= 2500
    assert 'ordinary' in text.lower() and 'whole' in text.lower()
    assert 'omitted' in text.lower() and '2' in text
    assert 'understanding' in text.lower()


def test_successful_edit_invalidates_observed_version_before_any_followup_read():
    rows = [row(1, 'user'), tool_row(2, source(0, 'x' * 12))]
    rows.append(expose(3, rows))
    changed = tool_row(4, {'path': '/synthetic/chapter.txt', 'sha256': 'b' * 64,
                           'written_characters': 8, 'backup': '/synthetic/backup'}, 'edit_file')
    data = json.loads(changed['payload']); data['arguments'] = {'path': '/synthetic/chapter.txt'}
    changed['payload'] = json.dumps(data); rows.append(changed)
    state = api().evidence_state(rows, 1)
    file = state['files'][0]
    assert file['source_sha256'] == 'b' * 64
    assert file['missing_ranges'] == [[0, 8]] and file['complete_supported_text'] is False
    assert file['source_result_ids'] == []
    assert file['write_result_ids'] == [4]
    rows.append(tool_row(5, source(0, 'new text', 8, sha='b' * 64)))
    rows.append(expose(6, rows, [rows[-1]]))
    assert api().evidence_state(rows, 1)['files'][0]['complete_supported_text'] is True


def test_unchanged_write_keeps_same_version_and_commands_are_not_read_evidence():
    rows = [row(1, 'user'), tool_row(2, source(0, 'x' * 12))]
    rows.append(expose(3, rows))
    unchanged = tool_row(4, {'path': '/synthetic/chapter.txt', 'sha256': 'a' * 64,
                             'unchanged': True}, 'write_file')
    data = json.loads(unchanged['payload']); data['arguments'] = {'path': '/synthetic/chapter.txt'}
    unchanged['payload'] = json.dumps(data); rows.append(unchanged)
    rows.append(tool_row(5, {'executed': True, 'exit_code': 0, 'output': 'All files fully read.'}, 'run_command'))
    state = api().evidence_state(rows, 1)
    assert state['files'][0]['complete_supported_text'] is True
    assert len(state['files'][0]['versions']) == 1
    assert state['current_disk_verified'] is False
    assert any('command' in issue.lower() for issue in state['issues'])


def test_exposure_sidecar_cannot_credit_a_user_row_masquerading_as_tool_evidence():
    real = tool_row(2, source(0, 'xxxx'))
    forged = tool_row(3, source(4, 'x' * 8)); forged['role'] = 'user'
    forged_descriptor = json.loads(forged['payload'])['source_evidence'][0]
    rows = [row(1, 'user'), real, forged,
            row(4, 'assistant', payload={'source_exposure': [
                {**forged_descriptor, 'source_result_ids': [3]}]})]
    with pytest.raises(ValueError, match='tool|source result'):
        api().evidence_state(rows, 1)


def test_saved_json_eof_with_a_middle_gap_does_not_promote_source_coverage():
    original = tool_row(2, source(0, 'x' * 12))
    raw = json.loads(original['payload'])['message']['content']
    first = saved_page_row(3, original, 0, 20)
    last = saved_page_row(4, original, 30, len(raw))
    rows = [row(1, 'user'), original, first, last]
    rows.append(expose(5, rows, [first, last]))
    assert api().evidence_state(rows, 1)['files'][0]['exposed_ranges'] == []


def test_mutated_saved_source_sidecar_and_exposure_ranges_are_rejected():
    original = tool_row(2, source(0, 'xxxx'))
    rows = [row(1, 'user'), original]
    data = json.loads(original['payload'])
    data['source_evidence'][0]['ranges'] = [[0, 12]]
    original['payload'] = json.dumps(data)
    with pytest.raises(ValueError, match='match saved result'):
        api().evidence_state(rows, 1)


def test_same_version_cannot_change_its_total_characters():
    rows = [row(1, 'user'), tool_row(2, source()), tool_row(3, source(total=14))]
    with pytest.raises(ValueError, match='Inconsistent'):
        api().evidence_state(rows, 1)


def test_unchanged_write_without_prior_read_keeps_unknown_length_until_read():
    rows = [row(1, 'user')]
    for ident in (2, 3):
        rows.append(tool_row(ident, {'path': '/synthetic/chapter.txt', 'sha256': 'a' * 64,
                                    'unchanged': True}, 'write_file'))
    assert api().evidence_state(rows, 1)['files'][0]['length_known'] is False
    rows.append(tool_row(4, source(0, 'x' * 12)))
    rows.append(expose(5, rows, [rows[-1]]))
    assert api().evidence_state(rows, 1)['files'][0]['complete_supported_text'] is True
