"""Damaged history stays preserved and cannot authorize a stale Undo."""
import json
import zipfile

import pytest
from PySide6.QtWidgets import QApplication

from letracode.store import Store
from letracode.strand_ui import StrandDialog


@pytest.mark.parametrize('damage', ['truncated', 'not-object', 'missing-scope', 'unknown-scope',
                                  'unknown-sequence', 'boolean-sequence', 'unknown-status', 'bad-hash'])
def test_bad_receipt_reports_exact_path_and_keeps_unrelated_writes_blocked(tmp_path, damage):
    store = Store(tmp_path / 'data')
    strand = store.strand
    saved = strand.replace('global', 'Keep memory', strand.snapshot('global')['sha256'])
    path = strand.root / '.receipts' / (saved['id'] + '.json')
    record = json.loads(path.read_bytes())
    if damage == 'truncated':
        raw = b'{"sequence":'
    elif damage == 'not-object':
        raw = b'[]'
    else:
        if damage == 'missing-scope':
            del record['scope']
        elif damage == 'unknown-scope':
            record['scope'] = 'future-scope'
        elif damage == 'unknown-sequence':
            record['sequence'] = 'future-sequence'
        elif damage == 'boolean-sequence':
            record['sequence'] = True
        elif damage == 'unknown-status':
            record['status'] = 'future-state'
        else:
            record['after_sha256'] = 'not a hash'
        raw = json.dumps(record).encode()
    path.write_bytes(raw)
    inode = path.stat().st_ino
    unrelated = strand.snapshot('learning')
    for action in (lambda: strand.receipts(scope='learning'),
                   lambda: strand.replace('learning', 'Unsafe new allocation', unrelated['sha256']),
                   lambda: strand.undo(saved['id'])):
        with pytest.raises(ValueError) as error:
            action()
        assert str(path) in str(error.value)
        assert 'preserv' in str(error.value).lower()
        assert path.read_bytes() == raw
        assert path.stat().st_ino == inode
    assert strand.snapshot('learning')['text'] == unrelated['text']
    assert strand.snapshot('global')['text'] == 'Keep memory'


def test_duplicated_global_sequence_reports_both_paths_and_refuses_new_allocation(tmp_path):
    strand = Store(tmp_path / 'data').strand
    first = strand.replace('global', 'A', strand.snapshot('global')['sha256'])
    second = strand.replace('learning', 'B', strand.snapshot('learning')['sha256'])
    paths = [strand.root / '.receipts' / (row['id'] + '.json') for row in (first, second)]
    record = json.loads(paths[1].read_text())
    record['sequence'] = first['sequence']
    paths[1].write_text(json.dumps(record))
    originals = [path.read_bytes() for path in paths]
    for action in (strand.receipts,
                   lambda: strand.replace('preferences', 'No guessed sequence', strand.snapshot('preferences')['sha256']),
                   lambda: strand.undo(first['id'])):
        with pytest.raises(ValueError, match='[Dd]uplicate') as error:
            action()
        assert all(str(path) in str(error.value) for path in paths)
        assert [path.read_bytes() for path in paths] == originals
    assert strand.snapshot('global')['text'] == 'A'


def test_damaged_history_dialog_opens_keeps_draft_and_recovers_after_repair(tmp_path):
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path / 'data')
    saved = store.strand.replace('global', 'Saved fact', store.strand.snapshot('global')['sha256'])
    path = store.strand.root / '.receipts' / (saved['id'] + '.json')
    original = path.read_bytes()
    path.write_bytes(b'{')
    dialog = StrandDialog(store)
    try:
        assert str(path) in dialog.message.text()
        assert dialog.history.count() == 0
        assert not dialog.history.isEnabled()
        dialog.scope.setCurrentIndex(dialog.scope.findData('learning'))
        dialog.editor.setPlainText('Keep this draft')
        assert not dialog.save_current()
        assert store.setting(dialog.state.key)['text'] == 'Keep this draft'
        assert str(path) in dialog.message.text()
        assert path.read_bytes() == b'{'
        path.write_bytes(original)
        assert dialog.save_current()
        assert dialog.history.isEnabled()
        assert store.strand.snapshot('learning')['text'] == 'Keep this draft'
    finally:
        dialog.close()


def test_unconfirmed_later_history_still_blocks_older_undo(tmp_path):
    strand = Store(tmp_path / 'data').strand
    first = strand.replace('global', 'Earlier', strand.snapshot('global')['sha256'])
    later = strand.replace('global', 'Later', first['after_sha256'])
    path = strand.root / '.receipts' / (later['id'] + '.json')
    record = json.loads(path.read_text())
    record['status'] = 'prepared'
    record.pop('write_id')
    path.write_text(json.dumps(record))
    assert strand.receipt(later['id'])['status'] == 'unconfirmed'
    assert not any(row['is_latest'] for row in strand.receipts())
    with pytest.raises(ValueError, match='ambiguous'):
        strand.undo(first['id'])
    assert strand.snapshot('global')['text'] == 'Later'


def test_retained_descriptor_edits_remain_preserved_and_report_exact_path(tmp_path):
    strand = Store(tmp_path / 'data').strand
    current = strand.path('global')
    current.write_bytes(b'Original')
    with current.open('r+b', buffering=0) as editor:
        saved = strand.replace('global', 'New', strand.snapshot('global')['sha256'])
        editor.write(b'External')
    retained = current.parent / '.strand-recovery' / current.name / (saved['id'] + '.before')
    raw, inode = retained.read_bytes(), retained.stat().st_ino
    with pytest.raises(ValueError) as error:
        strand.snapshot('global')
    assert str(retained) in str(error.value)
    with pytest.raises(ValueError):
        strand.undo(saved['id'])
    assert retained.read_bytes() == raw
    assert retained.stat().st_ino == inode
    assert current.read_bytes() == b'New'


def test_restored_legacy_undo_dependency_stays_supported(tmp_path):
    store = Store(tmp_path / 'data')
    first = store.strand.replace('global', 'Saved', store.strand.snapshot('global')['sha256'])
    undone = store.strand.undo(first['id'])
    for row in (first, undone):
        path = store.strand.root / '.receipts' / (row['id'] + '.json')
        record = json.loads(path.read_text())
        record.pop('sequence')
        record.pop('write_id')
        path.write_text(json.dumps(record))
    archive_path = tmp_path / 'backup.zip'
    store.backup(archive_path)
    with zipfile.ZipFile(archive_path) as archive:
        archive.extractall(tmp_path / 'restored')
    restored = Store(tmp_path / 'restored').strand
    assert restored.receipts()[0]['id'] == undone['id']
    restored.undo(undone['id'])
    assert restored.snapshot('global')['text'] == 'Saved'
