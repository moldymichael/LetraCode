"""Damaged control metadata cannot cross Memory ownership or grant activation."""
import json

import pytest

from letracode.store import Store


@pytest.mark.parametrize('change', [
    {'always_active': 'false'}, {'deleted': 0}, {'legacy_scope': 'unexpected'},
    {'path': 'folder/.receipts/private.md'},
    {'history_paths': ['.projects/other/private.md']},
    {'recovery_paths': ['.projects/other/private.md']},
    {'recovery_paths': '.history/private.md'}, {'history_paths': []},
])
def test_registry_rejects_corrupt_ownership_or_flags_without_repair(tmp_path, change):
    memory = Store(tmp_path / 'data').memory
    memory.create_file('note.md', 'Keep this text')
    path = memory.registry_path
    meta = json.loads(path.read_text())
    row = next(row for row in meta['files'].values() if row['path'] == 'note.md')
    row.update(change)
    raw = json.dumps(meta); path.write_text(raw)
    with pytest.raises(ValueError, match='registry|Memory'):
        memory.file_snapshot('note.md')
    assert path.read_text() == raw
    assert (memory.root / 'note.md').read_text() == 'Keep this text'


def test_registry_rejects_duplicate_alias_and_live_destination(tmp_path):
    memory = Store(tmp_path / 'data').memory
    path = memory.registry_path; meta = json.loads(path.read_text())
    row = next(row for row in meta['files'].values() if row.get('legacy_scope') == 'identity')
    meta['files']['a' * 32] = dict(row)
    path.write_text(json.dumps(meta))
    with pytest.raises(ValueError, match='registry|identity|alias'):
        memory.core()


def test_registry_save_rejects_duplicate_destination_before_publishing(tmp_path):
    memory = Store(tmp_path / 'data').memory
    meta, before = memory._metadata()
    row = next(row for row in meta['files'].values() if row.get('legacy_scope') == 'identity')
    duplicate = dict(row); duplicate.pop('legacy_scope')
    meta['files']['a' * 32] = duplicate
    with pytest.raises(ValueError, match='Duplicate live Memory destination'):
        memory._save_metadata(meta, before)
    assert memory.registry_path.read_bytes() == before


@pytest.mark.parametrize('change', [
    {'operation': 'execute'}, {'source': '.memory.json'}, {'destination': '../outside'},
    {'status': 'approved'}, {'sha256': 'bad'}, {'inode': ['1', 2]},
    {'files_after': {'bad': {}}}, {'files_before': []}, {'sequence': False},
])
def test_operation_history_rejects_corrupt_records_and_preserves_raw(tmp_path, change):
    memory = Store(tmp_path / 'data').memory
    made = memory.create_file('note.md', 'Original')
    path = memory.root / '.operations' / (made['id'] + '.json')
    row = json.loads(path.read_text()); row.update(change)
    raw = json.dumps(row); path.write_text(raw)
    with pytest.raises(ValueError, match='operation|Memory|history'):
        memory.history()
    assert path.read_text() == raw
    assert (memory.root / 'note.md').read_text() == 'Original'
