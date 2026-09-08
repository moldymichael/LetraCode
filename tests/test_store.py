import json
import sqlite3
import zipfile
import shutil
import os
import multiprocessing
from pathlib import Path

import pytest

from letracode import filesystem as fs

from letracode.store import Store


def test_project_context_and_chats_survive_restart(tmp_path):
    store = Store(tmp_path / 'data')
    project = store.create_project('Novel')
    chat = store.create_chat('Chapter discussion', project)
    other = store.create_chat('Groceries')
    store.update_project(project, memory='Wilson has a secret', current_context='Chapter 12', instructions='Analyze; do not write prose')
    store.add_message(chat, 'user', 'What changed?')
    store.add_message(chat, 'assistant', 'The relationship changed.')
    store.set_draft(chat, 'Next question')
    store.link(project, tmp_path)
    again = Store(tmp_path / 'data')
    assert again.project(project)['memory'] == 'Wilson has a secret'
    assert again.chat(chat)['draft'] == 'Next question'
    assert [m['content'] for m in again.messages(chat)] == ['What changed?', 'The relationship changed.']
    assert again.messages(other) == []
    assert again.links(project) == [str(tmp_path.resolve())]
    assert (tmp_path / 'data').stat().st_mode & 0o777 == 0o700


def test_delete_project_preserves_linked_files_and_other_chats(tmp_path):
    f = tmp_path / 'chapter.md'
    f.write_text('Keep me')
    s = Store(tmp_path / 'data')
    p = s.create_project('Delete me')
    c = s.create_chat('Inside', p)
    g = s.create_chat('Global')
    s.link(p, f)
    s.add_message(c, 'user', 'hello')
    s.delete_project(p)
    assert s.chat(c) is None
    assert s.chat(g)['title'] == 'Global'
    assert f.read_text() == 'Keep me'


def test_backup_is_recoverable_and_does_not_copy_linked_source(tmp_path):
    s = Store(tmp_path / 'data')
    p = s.create_project('Work')
    c = s.create_chat('Planning', p)
    s.add_message(c, 'user', 'Hello')
    s.link(p, tmp_path / 'large-source')
    archive = tmp_path / 'backup.zip'
    s.backup(archive)
    with zipfile.ZipFile(archive) as z:
        export = json.loads(z.read('letracode.json'))
        assert export['messages'][0]['content'] == 'Hello'
        z.extract('letracode.sqlite3', tmp_path / 'restore')
        assert s.memory.path('identity').relative_to(s.directory).as_posix() in z.namelist()
        assert not any('large-source' in name for name in z.namelist())
    db = sqlite3.connect(tmp_path / 'restore/letracode.sqlite3')
    assert db.execute('select title from projects').fetchone()[0] == 'Work'


def test_search_includes_message_text_and_export(tmp_path):
    s = Store(tmp_path / 'data')
    c = s.create_chat('Notes')
    s.add_message(c, 'user', 'The violet bicycle')
    assert [x['id'] for x in s.chats('violet')] == [c]
    assert 'The violet bicycle' in s.export_markdown(c)


def legacy_fixture(directory):
    store = Store(directory)
    ident = store.create_project('Legacy')
    with store.connection() as db:
        db.execute('UPDATE projects SET memory=? WHERE id=?', ('Preserve the legacy memory', ident))
        db.execute('PRAGMA user_version=1')
    # Construct a real schema-1 fixture, without the new Memory registry.
    for name in ('strand', 'Memory', '.strand-recovery', 'migration-backups'):
        shutil.rmtree(directory / name, ignore_errors=True)
    for name in ('memory-tree-migration.json', 'memory-initialization.json'):
        (directory / name).unlink(missing_ok=True)
    return ident


def test_v1_memory_migrates_once_with_consistent_original_backup(tmp_path):
    directory = tmp_path / 'data'
    ident = legacy_fixture(directory)
    store = Store(directory)
    assert store.project(ident)['memory'] == 'Preserve the legacy memory'
    with store.connection() as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 3
        assert db.execute('SELECT memory FROM projects').fetchone()[0] == ''
    backups = list((directory / 'migration-backups').glob('*.sqlite3'))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as db:
        assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert db.execute('PRAGMA user_version').fetchone()[0] == 1
        assert db.execute('SELECT memory FROM projects').fetchone()[0] == 'Preserve the legacy memory'
    memory = store.strand.path('project', ident)
    memory.write_text('User edits after migration')
    assert Store(directory).project(ident)['memory'] == 'User edits after migration'
    assert len(list((directory / 'migration-backups').glob('*.sqlite3'))) == 1


def test_migration_conflict_keeps_legacy_database_and_existing_file(tmp_path):
    directory = tmp_path / 'data'
    ident = legacy_fixture(directory)
    destination = directory / 'strand/memory/projects' / f'{ident}.md'
    destination.parent.mkdir(parents=True)
    destination.write_text('Existing user notes')
    with pytest.raises(ValueError, match='migration|conflict'):
        Store(directory)
    with sqlite3.connect(directory / 'letracode.sqlite3') as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 1
        assert db.execute('SELECT memory FROM projects').fetchone()[0] == 'Preserve the legacy memory'
    assert destination.read_text() == 'Existing user notes'


def test_future_database_is_rejected_without_downgrade(tmp_path):
    directory = tmp_path / 'data'
    Store(directory)
    with sqlite3.connect(directory / 'letracode.sqlite3') as db:
        db.execute('PRAGMA user_version=999')
    with pytest.raises(RuntimeError, match='newer'):
        Store(directory)
    with sqlite3.connect(directory / 'letracode.sqlite3') as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 999


def test_partial_migration_failure_retains_prepared_files_and_reuses_them_on_retry(tmp_path, monkeypatch):
    import letracode.store as store_module

    directory = tmp_path / 'data'
    first = legacy_fixture(directory)
    with sqlite3.connect(directory / 'letracode.sqlite3') as db:
        db.execute('INSERT INTO projects(id,title,memory,created) VALUES (?,?,?,?)',
                   ('second', 'Second', 'Second legacy note', '2026-09-05'))
    real_write = store_module.safe_write

    def disk_failure(path, data, expected):
        if path.name == 'second.md':
            raise OSError('Simulated migration disk failure')
        return real_write(path, data, expected)

    monkeypatch.setattr(store_module, 'safe_write', disk_failure)
    with pytest.raises(OSError, match='disk failure'):
        Store(directory)
    prepared = directory / 'strand/memory/projects' / f'{first}.md'
    assert prepared.read_text() == 'Preserve the legacy memory'
    prepared_inode = prepared.stat().st_ino
    with sqlite3.connect(directory / 'letracode.sqlite3') as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 1
        assert db.execute('SELECT memory FROM projects WHERE id=?', (first,)).fetchone()[0] == 'Preserve the legacy memory'
    monkeypatch.setattr(store_module, 'safe_write', real_write)
    if fs.IS_WINDOWS:
        with prepared.open('r+b'):
            with pytest.raises(PermissionError):
                Store(directory)
            assert prepared.read_text() == 'Preserve the legacy memory'
        restored = Store(directory)
        assert restored.project(first)['memory'] == 'Preserve the legacy memory'
        assert restored.project('second')['memory'] == 'Second legacy note'
        assert restored.strand.path('project', first).stat().st_ino == prepared_inode
        return
    with prepared.open('r+b') as editor:
        restored = Store(directory)
        assert restored.project(first)['memory'] == 'Preserve the legacy memory'
        assert restored.project('second')['memory'] == 'Second legacy note'
        assert restored.strand.path('project', first).stat().st_ino == prepared_inode, 'Retry must reuse the prepared file, including any open editor descriptor'
        editor.write(b'External edit through a descriptor opened before retry')
        editor.truncate()
        editor.flush()
        os.fsync(editor.fileno())
    assert restored.project(first)['memory'] == 'External edit through a descriptor opened before retry'


@pytest.mark.parametrize('editor', ['in_place', 'atomic_replace', 'open_descriptor'])
def test_migration_rollback_preserves_external_edits_and_retry_refuses_conflict(tmp_path, monkeypatch, editor):
    import letracode.store as store_module

    directory = tmp_path / 'data'
    first = legacy_fixture(directory)
    with sqlite3.connect(directory / 'letracode.sqlite3') as db:
        db.execute('INSERT INTO projects(id,title,memory,created) VALUES (?,?,?,?)',
                   ('second', 'Oversized second', 'X' * (2 * 1024 * 1024 + 1), '2026-09-05'))
    target = directory / 'strand/memory/projects' / f'{first}.md'
    external = b'External correction must survive failed migration'
    real_write, real_unlink = store_module.safe_write, os.unlink
    descriptor = None
    edited = False

    def edit_file():
        nonlocal edited
        edited = True
        if editor == 'in_place':
            target.write_bytes(external)
        elif editor == 'atomic_replace':
            staging = target.with_suffix('.external')
            staging.write_bytes(external)
            os.replace(staging, target)

    def oversized_second(path, content, expected):
        nonlocal descriptor
        if path.name == 'second.md' and editor == 'open_descriptor':
            descriptor = target.open('r+b')
        return real_write(path, content, expected)

    def intervening_edit(name, *args, **kwargs):
        # Reproduce the reviewed check/unlink race at the old destructive
        # boundary. A rollback that retains files has no such boundary.
        if name == target.name and kwargs.get('dir_fd') is not None and editor != 'open_descriptor':
            edit_file()
        return real_unlink(name, *args, **kwargs)

    monkeypatch.setattr(store_module, 'safe_write', oversized_second)
    monkeypatch.setattr(os, 'unlink', intervening_edit)
    try:
        with pytest.raises(ValueError, match='too large'):
            Store(directory)
        if descriptor is not None:
            descriptor.seek(0)
            descriptor.write(external)
            descriptor.truncate()
            descriptor.flush()
            os.fsync(descriptor.fileno())
        elif not edited:
            # With no destructive cleanup, editors can save normally to the
            # retained pathname after the migration reports its failure.
            assert target.exists()
            edit_file()
    finally:
        if descriptor is not None:
            descriptor.close()
    assert target.read_bytes() == external
    with sqlite3.connect(directory / 'letracode.sqlite3') as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 1
        assert db.execute('SELECT memory FROM projects WHERE id=?', (first,)).fetchone()[0] == 'Preserve the legacy memory'
        db.execute('UPDATE projects SET memory=? WHERE id=?', ('Repaired second', 'second'))
    backups = list((directory / 'migration-backups').glob('*.sqlite3'))
    with sqlite3.connect(backups[0]) as backup:
        assert backup.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert backup.execute('SELECT memory FROM projects WHERE id=?', (first,)).fetchone()[0] == 'Preserve the legacy memory'
    with pytest.raises(ValueError, match='migration conflict'):
        Store(directory)
    assert target.read_bytes() == external
    with sqlite3.connect(directory / 'letracode.sqlite3') as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 1
        assert db.execute('SELECT memory FROM projects WHERE id=?', (first,)).fetchone()[0] == 'Preserve the legacy memory'


def test_migration_locks_legacy_memory_before_reading_and_clearing(tmp_path, monkeypatch):
    import letracode.store as store_module

    directory = tmp_path / 'data'
    ident = legacy_fixture(directory)
    real_write = store_module.safe_write
    locked = []

    def concurrent_legacy_edit(path, data, expected):
        if path.name == f'{ident}.md':
            with sqlite3.connect(directory / 'letracode.sqlite3', timeout=0) as other:
                try:
                    other.execute('UPDATE projects SET memory=? WHERE id=?', ('A concurrent edit', ident))
                    locked.append(False)
                except sqlite3.OperationalError as error:
                    assert 'locked' in str(error)
                    locked.append(True)
        return real_write(path, data, expected)

    monkeypatch.setattr(store_module, 'safe_write', concurrent_legacy_edit)
    migrated = Store(directory)
    assert locked == [True], 'Migration allowed a legacy edit that its final clear would erase'
    assert migrated.project(ident)['memory'] == 'Preserve the legacy memory'


def test_backup_restores_memory_receipts_and_safe_future_manifests(tmp_path):
    original = Store(tmp_path / 'data')
    ident = original.create_project('Novel')
    original.link(ident, tmp_path / 'outside-source')
    snapshot = original.strand.snapshot('project', ident)
    receipt = original.strand.remember('project', 'Story fact', ident, expected_sha256=snapshot['sha256'])
    manifest = original.strand.root / 'development/models/example.json'
    manifest.parent.mkdir(parents=True)
    manifest.write_text('{"model":"example.gguf"}')
    (manifest.parent / 'weights.gguf').write_bytes(b'excluded synthetic weights')
    destination = tmp_path / 'backup.zip'
    original.backup(destination)
    with zipfile.ZipFile(destination) as archive:
        assert 'Memory/development/models/example.json' in archive.namelist()
        assert not any(name.endswith('.gguf') for name in archive.namelist())
        guide = archive.read('RESTORE.txt').decode().lower()
        assert 'retarget' in guide and 'linked' in guide
        archive.extractall(tmp_path / 'restored')
    # A copied database still contains its old source roots: remove them before opening.
    with sqlite3.connect(tmp_path / 'restored/letracode.sqlite3') as db:
        db.execute('DELETE FROM links')
    restored = Store(tmp_path / 'restored')
    assert restored.links(ident) == []
    assert restored.strand.receipt(receipt['id'])['saved_text'] == 'Story fact'
    restored.strand.undo(receipt['id'])
    assert restored.project(ident)['memory'] == ''


def test_backup_rejects_redirected_strand_file_and_preserves_previous_backup(tmp_path):
    store = Store(tmp_path / 'data')
    outside = tmp_path / 'private.md'
    outside.write_text('Do not export')
    (store.strand.root / 'memory/leak.md').symlink_to(outside)
    destination = tmp_path / 'backup.zip'
    destination.write_bytes(b'previous backup')
    with pytest.raises((ValueError, OSError)):
        store.backup(destination)
    assert destination.read_bytes() == b'previous backup'


def test_saved_tool_result_pages_preserve_full_text_and_deny_other_chats(tmp_path):
    store = Store(tmp_path / 'data')
    chat = store.create_chat('One')
    other = store.create_chat('Two')
    content = json.dumps({'text': 'x' * 10000, 'tail': 'still here'})
    ident = store.add_message(chat, 'tool', 'Read file', payload={'message': {
        'role': 'tool', 'name': 'read_file', 'tool_call_id': 'call_1', 'content': content}})
    first = store.tool_result_page(chat, ident, max_chars=100)
    assert first['content'] == content[:100]
    assert first['next_offset'] == 100
    assert first['tool_call_id'] == 'call_1'
    assert first['name'] == 'read_file'
    last = store.tool_result_page(chat, ident, offset=len(content) - 30, max_chars=100)
    assert 'still here' in last['content']
    assert last['next_offset'] is None
    with pytest.raises(ValueError):
        store.tool_result_page(other, ident)
    assert json.loads(store.messages(chat)[0]['payload'])['message']['content'] == content


@pytest.mark.parametrize('unavailable', ['missing', 'malformed', 'oversized', 'permission'])
def test_unavailable_project_memory_does_not_hide_other_projects_or_chats(tmp_path, monkeypatch, unavailable):
    store = Store(tmp_path / 'data')
    bad = store.create_project('Unavailable')
    good = store.create_project('Available')
    chat = store.create_chat('Unrelated global chat')
    store.update_project(good, memory='Still accessible')
    memory = store.strand.path('project', bad)
    if unavailable == 'missing':
        memory.unlink()
    elif unavailable == 'malformed':
        memory.write_bytes(b'Invalid UTF-8: \xff')
    elif unavailable == 'oversized':
        memory.write_bytes(b'x' * (2 * 1024 * 1024 + 1))
    else:
        real_snapshot = store.strand.snapshot

        def denied(scope, project_id=None):
            if scope == 'project' and project_id == bad:
                raise PermissionError('Memory is not readable')
            return real_snapshot(scope, project_id)

        monkeypatch.setattr(store.strand, 'snapshot', denied)
    projects = store.projects()
    assert {project['id'] for project in projects} == {good, bad}
    assert all('memory' not in project for project in projects)
    assert store.project(bad)['memory'] == ''
    assert store.project(bad)['memory_error']
    assert store.project(good)['memory'] == 'Still accessible'
    assert 'memory_error' not in store.project(good)
    assert store.chat(chat)['title'] == 'Unrelated global chat'


def test_deletion_archives_original_inode_raw_bytes_and_record_in_backup(tmp_path):
    store = Store(tmp_path / 'data')
    ident = store.create_project('Archived novel')
    memory = store.strand.path('project', ident)
    memory.write_bytes(b'An undecodable correction: \xff')
    inode = memory.stat().st_ino
    with memory.open('ab', buffering=0) as editor:
        archive = store.delete_project(ident)
        assert not memory.exists(), 'Deletion left authoritative project memory behind'
        archive_path = Path(archive['path'])
        assert archive_path.stat().st_ino == inode
        editor.write(b'\nA late editor save')
    assert archive_path.read_bytes() == b'An undecodable correction: \xff\nA late editor save'
    assert store.project(ident) is None
    record = json.loads(Path(archive['record_path']).read_text())
    assert record['project_id'] == ident
    assert record['title'] == 'Archived novel'
    assert record['date']
    assert record['status'] == 'deleted'
    assert record['path'] == str(archive_path)
    backup = tmp_path / 'backup.zip'
    store.backup(backup)
    with zipfile.ZipFile(backup) as exported:
        relative = archive_path.relative_to(store.directory).as_posix()
        assert exported.read(relative) == archive_path.read_bytes()


def test_deletion_of_missing_memory_removes_project_without_creating_a_file(tmp_path):
    store = Store(tmp_path / 'data')
    ident = store.create_project('Missing')
    memory = store.strand.path('project', ident)
    memory.unlink()
    assert store.delete_project(ident) is None
    assert store.project(ident) is None
    assert not memory.exists()


@pytest.mark.parametrize('kind', ['symlink', 'hardlink', 'directory'])
def test_deletion_refuses_unsafe_memory_targets_and_keeps_project(tmp_path, kind):
    store = Store(tmp_path / 'data')
    ident = store.create_project('Do not delete')
    memory = store.strand.path('project', ident)
    memory.unlink()
    outside = tmp_path / 'outside.md'
    outside.write_text('Keep original')
    if kind == 'symlink':
        memory.symlink_to(outside)
    elif kind == 'hardlink':
        os.link(outside, memory)
    else:
        memory.mkdir()
    with pytest.raises(ValueError, match='Unsafe|linked'):
        store.delete_project(ident)
    assert any(row['id'] == ident for row in store.projects())
    assert outside.read_text() == 'Keep original'


def test_failed_database_deletion_restores_original_memory_without_overwrite(tmp_path):
    store = Store(tmp_path / 'data')
    ident = store.create_project('Still present')
    memory = store.strand.path('project', ident)
    memory.write_text('Preserve this note')
    inode = memory.stat().st_ino
    with store.connection() as db:
        db.execute("CREATE TRIGGER prevent_delete BEFORE DELETE ON projects BEGIN SELECT RAISE(ABORT, 'Deletion refused'); END")
    with pytest.raises(sqlite3.IntegrityError, match='Deletion refused'):
        store.delete_project(ident)
    assert store.project(ident)['memory'] == 'Preserve this note'
    assert memory.stat().st_ino == inode
    records = list((store.strand.root / '.deleted-projects' / ident).glob('*.json'))
    assert len(records) == 1
    assert json.loads(records[0].read_text())['status'] == 'restored'


def test_deletion_conflict_preserves_new_memory_and_recoverable_original(tmp_path, monkeypatch):
    import letracode.strand as strand_module

    store = Store(tmp_path / 'data')
    ident = store.create_project('Keep both edits')
    memory = store.strand.path('project', ident)
    memory.write_text('Original memory')
    real_rename = strand_module.rename_noreplace

    def editor_save_after_archive(source, source_name, destination, destination_name):
        real_rename(source, source_name, destination, destination_name)
        if source_name == memory.name:
            memory.write_text('A concurrent new editor version')

    monkeypatch.setattr(strand_module, 'rename_noreplace', editor_save_after_archive)
    with pytest.raises(ValueError, match='preserved at'):
        store.delete_project(ident)
    assert store.project(ident)['memory'] == 'A concurrent new editor version'
    archive_dir = store.strand.root / '.deleted-projects' / ident
    retained = list(archive_dir.glob('*.md'))
    assert len(retained) == 1
    assert retained[0].read_text() == 'Original memory'
    record = json.loads(next(archive_dir.glob('*.json')).read_text())
    assert record['status'] == 'recovery_required'


def test_oversized_project_memory_can_be_archived_without_decoding_or_truncation(tmp_path):
    store = Store(tmp_path / 'data')
    ident = store.create_project('Large archived memory')
    memory = store.strand.path('project', ident)
    content = b'x' * (2 * 1024 * 1024 + 1)
    memory.write_bytes(content)
    archived = store.delete_project(ident)
    assert Path(archived['path']).read_bytes() == content
    assert not memory.exists()


@pytest.mark.skipif(os.name == 'nt', reason='POSIX fork checkpoint injection; portable subprocess recovery is covered in test_filesystem')
def test_interrupted_project_deletion_retains_archive_and_unavailable_project(tmp_path):
    import letracode.strand as strand_module

    directory = tmp_path / 'data'
    store = Store(directory)
    ident = store.create_project('Interrupted deletion')
    memory = store.strand.path('project', ident)
    memory.write_text('Recover after interruption')

    def crash_after_archive():
        real_rename = strand_module.rename_noreplace

        def stop_after_move(source, source_name, destination, destination_name):
            real_rename(source, source_name, destination, destination_name)
            if source_name == memory.name:
                os.fsync(source)
                os.fsync(destination)
                os._exit(0)

        strand_module.rename_noreplace = stop_after_move
        store.delete_project(ident)
        os._exit(2)

    child = multiprocessing.get_context('fork').Process(target=crash_after_archive)
    child.start()
    child.join(3)
    if child.is_alive():
        child.terminate()
        child.join()
        pytest.fail('Deletion child did not reach its archive checkpoint')
    assert child.exitcode == 0
    restored = Store(directory)
    assert restored.project(ident)['memory_error']
    assert not memory.exists()
    archive_dir = restored.strand.root / '.deleted-projects' / ident
    record = json.loads(next(archive_dir.glob('*.json')).read_text())
    assert record['status'] == 'prepared'
    assert Path(record['path']).read_text() == 'Recover after interruption'


@pytest.mark.parametrize('phase', ['capture', 'publish'])
def test_deletion_resolves_interrupted_save_before_archiving_without_resurrection(tmp_path, monkeypatch, phase):
    import letracode.strand as strand_module

    class SimulatedCrash(BaseException):
        pass

    store = Store(tmp_path / 'data')
    ident = store.create_project('Interrupted save then delete')
    memory = store.strand.path('project', ident)
    memory.write_text('Original memory')
    snapshot = store.strand.snapshot('project', ident)
    real_rename = strand_module.rename_noreplace

    def interrupt_save(source, source_name, destination, destination_name):
        real_rename(source, source_name, destination, destination_name)
        captured = source_name == memory.name and destination_name.endswith('.before')
        published = destination_name == memory.name and source_name.endswith('.proposed')
        if (phase == 'capture' and captured) or (phase == 'publish' and published):
            raise SimulatedCrash()

    monkeypatch.setattr(strand_module, 'rename_noreplace', interrupt_save)
    with pytest.raises(SimulatedCrash):
        store.strand.replace('project', 'Proposed memory', snapshot['sha256'], ident)
    monkeypatch.setattr(strand_module, 'rename_noreplace', real_rename)

    archived = store.delete_project(ident)
    assert not memory.exists()
    # Prepared receipt inspection re-enters snapshot/recovery. It must not
    # resurrect an authoritative file after database ownership was deleted.
    reopened = Store(store.directory)
    reopened.strand.receipts()
    assert not memory.exists(), 'Receipt recovery resurrected deleted project memory'
    assert reopened.project(ident) is None
    assert archived is not None
    expected = 'Original memory' if phase == 'capture' else 'Proposed memory'
    assert Path(archived['path']).read_text() == expected
