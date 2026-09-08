"""Legacy deleted-project history survives fresh and interrupted migration."""
import json
import shutil
import sqlite3
import uuid
import zipfile
from pathlib import Path

import pytest

from letracode.store import Store
from letracode.strand import StrandFiles, safe_write


def legacy_deleted_project(tmp_path, *, partial=False, prepared=False):
    directory = tmp_path / 'data'
    store = Store(directory)
    shutil.rmtree(store.memory.root)
    for name in ('memory-tree-migration.json', 'memory-initialization.json'):
        (directory / name).unlink()
    with store.connection() as db:
        db.execute('PRAGMA user_version=2')
    store.set_setting('retained-migration-setting', {'draft': 'Keep this draft'})
    legacy = StrandFiles(directory / 'strand')
    project = 'deleted-project'
    legacy.ensure('project', project)
    receipt = legacy.replace('project', 'Archived project notes',
                             legacy.snapshot('project', project)['sha256'], project)
    archive_id = uuid.uuid4().hex
    archive = legacy.root / '.deleted-projects' / project / (archive_id + '.md')
    archive.parent.mkdir(parents=True)
    original = legacy.path('project', project)
    archive_record = {'project_id': project, 'title': 'Deleted project',
        'date': '2026-09-07T00:00:00Z', 'path': str(archive),
        'original_path': str(original), 'record_path': str(archive.with_suffix('.json')),
        'status': 'deleted'}
    safe_write(archive.with_suffix('.json'), json.dumps(archive_record).encode(), None)
    original.rename(archive)
    receipt_path = legacy.root / '.receipts' / (receipt['id'] + '.json')
    row = json.loads(receipt_path.read_text())
    row.pop('entry_identity', None)  # Schema-2 receipts predate this metadata.
    if prepared:
        # Retain an unresolved receipt/journal without treating its old before
        # image as authority to resurrect an intentionally archived project.
        row['status'] = 'prepared'
        done = original.parent / '.strand-recovery' / original.name / (receipt['id'] + '.done')
        done.unlink()
    receipt_path.write_text(json.dumps(row))
    if partial:
        legacy.root.rename(directory / 'Memory')
        legacy.root = directory / 'Memory'
        files = {uuid.uuid4().hex: {'path': path, 'project_id': None,
            'always_active': scope in ('identity', 'preferences'), 'legacy_scope': scope,
            'history_paths': [path], 'recovery_paths': [], 'deleted': False}
            for scope, path in StrandFiles.SCOPES.items()}
        safe_write(legacy.root / '.memory.json', json.dumps({'version': 1, 'files': files}).encode(), None)
        safe_write(directory / 'memory-tree-migration.json', json.dumps({
            'version': 1, 'source': 'strand', 'destination': 'Memory',
            'legacy': True, 'status': 'prepared'}).encode(), None)
    return directory, legacy.root, project, receipt


def retained_bytes(root):
    return {path.relative_to(root).as_posix(): path.read_bytes()
            for path in root.rglob('*') if path.is_file() and path.name != '.memory.json'}


@pytest.mark.parametrize('partial', [False, True], ids=['pre-upgrade', 'root-already-renamed'])
def test_migration_keeps_deleted_project_receipt_and_archive(tmp_path, partial):
    directory, root, project, receipt = legacy_deleted_project(tmp_path, partial=partial)
    before = retained_bytes(root)
    old_registry = json.loads((root / '.memory.json').read_text()) if partial else None

    store = Store(directory)

    with store.connection() as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 3
    assert json.loads((directory / 'memory-tree-migration.json').read_text())['status'] == 'complete'
    assert not (directory / 'strand').exists()
    for relative, raw in before.items():
        assert (store.memory.root / relative).read_bytes() == raw
    assert store.setting('retained-migration-setting') == {'draft': 'Keep this draft'}
    if old_registry:
        current = json.loads((store.memory.root / '.memory.json').read_text())
        assert all(current['files'][ident] == row for ident, row in old_registry['files'].items())
    history = store.memory.history(project_id=project)
    assert len(history) == 1 and history[0]['id'] == receipt['id']
    assert history[0]['status'] == 'saved' and 'deleted' in history[0]['undo_error']
    assert store.memory.alias_deleted('project', project)
    assert not store.memory.ensure('project', project).exists()
    assert store.memory.entries(project) == []
    with pytest.raises(ValueError, match='deleted'):
        store.memory.undo(receipt['id'])
    identity = history[0]['file_identity']
    assert Store(directory).memory.history(project_id=project)[0]['file_identity'] == identity
    saved = store.memory.replace('global', 'New global note', store.memory.snapshot('global')['sha256'])
    store.memory.undo(saved['id'])
    destination = tmp_path / 'retained.zip'
    store.backup(destination)
    with zipfile.ZipFile(destination) as backup:
        for relative, raw in before.items():
            if Path(relative).name in ('.write-lock', '.lock'):
                continue  # Existing backups intentionally recreate empty locks.
            assert backup.read('Memory/' + relative) == raw


def test_archived_prepared_receipt_does_not_resurrect_old_before_image(tmp_path):
    directory, root, project, receipt = legacy_deleted_project(tmp_path, partial=True, prepared=True)
    before = retained_bytes(root)
    store = Store(directory)
    assert store.memory.history(project_id=project)[0]['status'] == 'unconfirmed'
    assert not store.memory.path('project', project).exists()
    with pytest.raises(ValueError, match='Unconfirmed'):
        store.memory.undo(receipt['id'])
    for relative, raw in before.items():
        assert (root / relative).read_bytes() == raw


@pytest.mark.parametrize('field,value', [
    ('relative_path', 'memory/projects/another-project.md'),
    ('relative_path', '../outside.md'),
    ('project_id', '../outside'),
    ('id', '0' * 32),
    ('after_sha256', 'not-a-hash'),
])
def test_legacy_history_repair_keeps_invalid_receipts_blocked(tmp_path, field, value):
    directory, root, project, receipt = legacy_deleted_project(tmp_path, partial=True)
    path = root / '.receipts' / (receipt['id'] + '.json')
    row = json.loads(path.read_text()); row[field] = value
    path.write_text(json.dumps(row))
    before = retained_bytes(root)
    with pytest.raises(ValueError, match='Unresolved memory history'):
        Store(directory)
    with sqlite3.connect(directory / 'letracode.sqlite3') as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 2
    for relative, raw in before.items():
        assert (root / relative).read_bytes() == raw
