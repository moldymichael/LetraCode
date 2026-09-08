"""Regression from the real reading trial: source cursors are not result cursors."""
import copy
import json

import pytest

from letracode.worker import compact_tool_results, protocol_messages


@pytest.mark.parametrize('name', ['read_file', 'read_memory'])
def test_compacted_source_cursor_cannot_masquerade_as_saved_result_cursor(name):
    original = {'path': '/synthetic/chapter.txt', 'text': 'long paragraph ' * 1500,
                'offset': 0, 'next_offset': 15997, 'total_chars': 25542,
                'sha256': 'a' * 64, 'editable': True, 'source_truncated': False}
    turn = [{'role': 'tool', 'name': name, 'tool_call_id': 'source',
             'content': json.dumps(original), 'saved_result_id': 5}]
    before = copy.deepcopy(turn)
    packed = compact_tool_results(turn, 1400)
    receipt = json.loads(packed[0]['content'])
    assert receipt['result_id'] == 5
    assert receipt['read_tool_result_offset'] == 0
    assert 'next_offset' not in receipt and 'offset' not in receipt
    assert receipt['source_page'] == {'offset': 0, 'next_offset': 15997, 'total_chars': 25542}
    assert receipt['sha256'] == original['sha256']
    assert 'offset=0' in receipt['context_note']
    assert len(json.dumps(protocol_messages(packed), ensure_ascii=False)) <= 1400
    assert turn == before


def test_readonly_binary_version_is_preserved_when_cursor_is_compacted():
    source = {'path': '/synthetic/chapter.pdf', 'sha256': None, 'source_sha256': 'b' * 64,
              'editable': False, 'offset': 4000, 'next_offset': 8000, 'total_chars': 25000,
              'text': 'PDF text ' * 1000, 'extraction': {'version': 'pdf-text-v1'}}
    packed = compact_tool_results([{'role': 'tool', 'name': 'read_file', 'tool_call_id': 'binary',
        'content': json.dumps(source), 'saved_result_id': 7}], 1400)
    receipt = json.loads(packed[0]['content'])
    assert receipt['source_sha256'] == source['source_sha256']
    assert receipt['source_page']['next_offset'] == 8000
    assert receipt['sha256'] is None and receipt['editable'] is False
