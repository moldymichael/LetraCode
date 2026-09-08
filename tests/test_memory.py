"""User-owned directory memory, using real files and disposable migration data."""
import json
import os
import sqlite3
import zipfile

import pytest

from letracode import filesystem as fs

from letracode.store import Store


def test_tree_crud_keeps_nested_content_and_undo(tmp_path):
    store = Store(tmp_path / 'data'); memory = store.memory
    memory.create_folder('Research')
    memory.create_folder('Research/Notes')
    made = memory.create_file('Research/Notes/facts.md', 'First')
    before = memory.file_snapshot('Research/Notes/facts.md')
    saved = memory.replace_file('Research/Notes/facts.md', 'Second', before['sha256'])
    moved = memory.move('Research/Notes/facts.md', 'Research/renamed.txt', memory.file_snapshot('Research/Notes/facts.md')['sha256'])
    assert memory.file_snapshot('Research/renamed.txt')['text'] == 'Second'
    memory.undo(saved['id'])
    assert memory.file_snapshot('Research/renamed.txt')['text'] == 'First'
    removed = memory.delete('Research', memory.snapshot_entry('Research')['sha256'])
    assert not any(row['path'].startswith('Research') for row in memory.entries())
    again = Store(store.directory).memory
    again.undo(removed['id'])
    assert again.file_snapshot('Research/renamed.txt')['text'] == 'First'
    assert made['id'] and moved['id']


def test_only_explicit_active_files_are_core_and_others_are_searchable(tmp_path):
    m = Store(tmp_path / 'data').memory
    m.create_file('chosen.md', 'Always use clear explanations.')
    m.create_file('reference.md', 'The rare nebula has seven stars.')
    before = m.file_snapshot('chosen.md')
    m.set_active('chosen.md', True, before['sha256'])
    assert 'clear explanations' in m.core()
    assert 'seven stars' not in m.core()
    assert any('seven stars' in hit['text'] for hit in m.search('nebula'))
    again = Store(tmp_path / 'data').memory
    assert again.file_snapshot('chosen.md')['always_active']
    assert 'seven stars' not in again.context(None, 'unrelated', 3000)


def test_project_tree_isolation_and_active_context(tmp_path):
    s = Store(tmp_path / 'data'); a = s.create_project('A'); b = s.create_project('B')
    s.memory.create_file('facts.md', 'private A', project_id=a)
    s.memory.create_file('facts.md', 'private B', project_id=b)
    snap = s.memory.file_snapshot('facts.md', a)
    s.memory.set_active('facts.md', True, snap['sha256'], a)
    assert 'private A' in s.memory.core(a)
    assert 'private A' not in s.memory.core(b)
    assert not any('private A' in hit['text'] for hit in s.memory.search('private'))
    assert s.memory.file_snapshot('facts.md', b)['text'] == 'private B'


def test_external_change_conflicts_with_save_move_delete_and_activation(tmp_path):
    s = Store(tmp_path / 'data'); m = s.memory
    m.create_file('note.md', 'original'); snap = m.file_snapshot('note.md')
    from pathlib import Path
    Path(snap['path']).write_text('external')
    for operation in (lambda:m.replace_file('note.md','mine',snap['sha256']),
                      lambda:m.move('note.md','moved.md',snap['sha256']),
                      lambda:m.delete('note.md',snap['sha256']),
                      lambda:m.set_active('note.md',True,snap['sha256'])):
        with pytest.raises(ValueError, match='changed|conflict'):
            operation()
    assert m.file_snapshot('note.md')['text'] == 'external'


@pytest.mark.parametrize('path', ['../escape.md', '/tmp/escape.md', 'a/../escape.md', 'a\\b.md', '.history/hidden.md', '.projects/x/a.md', 'a//b.md', 'C:/escape.md'])
def test_tree_paths_cannot_escape_or_touch_control_state(tmp_path, path):
    m = Store(tmp_path / 'data').memory
    with pytest.raises(ValueError):
        m.create_file(path, 'blocked')


def test_tree_refuses_symlinks_and_hardlinks(tmp_path):
    m = Store(tmp_path / 'data').memory
    secret = tmp_path / 'secret.md'; secret.write_text('outside')
    (m.root / 'linked.md').symlink_to(secret)
    with pytest.raises(ValueError):
        m.file_snapshot('linked.md')
    os.link(secret, m.root / 'hard.md')
    with pytest.raises(ValueError):
        m.file_snapshot('hard.md')
    assert secret.read_text() == 'outside'


def test_migration_preserves_identity_projects_receipts_recovery_and_drafts(tmp_path):
    from letracode.strand import StrandFiles
    root = tmp_path / 'data'; root.mkdir()
    legacy = StrandFiles(root / 'strand')
    legacy.ensure('project', 'project-a', 'Project original')
    before = legacy.snapshot('global')
    receipt = legacy.replace('global', 'Global saved', before['sha256'])
    legacy.replace('identity', '# Strand\nMy own assistant', legacy.snapshot('identity')['sha256'])
    # Create current schema using the ordinary Store schema, then replace its
    # fresh tree with our realistic schema-2 files before reopening.
    s = Store(root)
    # This fixture is converted below once migration support exists.
    assert s.memory.snapshot('global')['text'] == 'Global saved'
    assert s.memory.snapshot('identity')['text'] == '# Strand\nMy own assistant'
    assert s.memory.file_snapshot('Memory.md', 'project-a')['text'] == 'Project original'
    s.memory.undo(receipt['id'])
    assert s.memory.snapshot('global')['text'] == ''
    assert s.memory.root.name == 'Memory'
    assert any(s.memory.root.rglob('*.before'))


def test_delete_file_retains_late_external_inode_and_undo_detects_changed_trash(tmp_path):
    m = Store(tmp_path / 'data').memory
    m.create_file('note.md', 'before')
    before = m.file_snapshot('note.md')
    with open(m.root / 'note.md', 'r+b') as editor:
        if fs.IS_WINDOWS:
            with pytest.raises(PermissionError):
                m.delete('note.md', before['sha256'])
            assert (m.root / 'note.md').read_text() == 'before'
            return
        removed = m.delete('note.md', m.file_snapshot('note.md')['sha256'])
        editor.seek(0); editor.write(b'changed'); editor.truncate(); editor.flush()
    with pytest.raises(ValueError, match='changed|conflict'):
        m.undo(removed['id'])
    assert any(p.read_bytes() == b'changed' for p in (m.root / '.trash').rglob('*') if p.is_file())


def test_memory_backup_restores_tree_activation_and_history(tmp_path):
    s = Store(tmp_path / 'data'); m = s.memory
    m.create_folder('Notes'); m.create_file('Notes/a.md','old')
    saved = m.replace_file('Notes/a.md', 'new', m.file_snapshot('Notes/a.md')['sha256'])
    m.set_active('Notes/a.md', True, m.file_snapshot('Notes/a.md')['sha256'])
    backup = tmp_path / 'backup.zip'; s.backup(backup)
    with zipfile.ZipFile(backup) as z:
        assert 'Memory/Notes/a.md' in z.namelist()
        z.extractall(tmp_path / 'restored')
    restored = Store(tmp_path / 'restored').memory
    assert restored.file_snapshot('Notes/a.md')['always_active']
    restored.undo(saved['id'])
    assert restored.file_snapshot('Notes/a.md')['text'] == 'old'


def test_same_path_recreated_has_new_identity_and_independent_undo(tmp_path):
    m = Store(tmp_path / 'data').memory
    m.create_file('note.md', 'old identity')
    old = m.replace_file('note.md', 'old changed', m.file_snapshot('note.md')['sha256'])
    m.delete('note.md', m.file_snapshot('note.md')['sha256'])
    m.create_file('note.md', 'new identity')
    new = m.replace_file('note.md', 'new changed', m.file_snapshot('note.md')['sha256'])
    m.undo(new['id'])
    assert m.file_snapshot('note.md')['text'] == 'new identity'
    with pytest.raises(ValueError):
        m.undo(old['id'])
    assert m.file_snapshot('note.md')['text'] == 'new identity'


def test_recreate_externally_removed_project_file_retires_old_identity(tmp_path):
    store = Store(tmp_path / 'data'); project = store.create_project('Project')
    m = store.memory
    original = m.replace('project', 'Original project note', m.snapshot('project', project)['sha256'], project)
    old_id = m.file_snapshot('Memory.md', project)['file_id']
    m.path('project', project).unlink()
    created = m.create_file('Memory.md', 'New project note', project)
    reopened = Store(store.directory).memory
    assert reopened.alias_deleted('project', project)
    snapshot = reopened.file_snapshot('Memory.md', project)
    assert snapshot['file_id'] != old_id and snapshot['text'] == 'New project note'
    saved = reopened.replace_file('Memory.md', 'Edited new note', snapshot['sha256'], project)
    reopened.undo(saved['id'])
    assert reopened.file_snapshot('Memory.md', project)['text'] == 'New project note'
    with pytest.raises(ValueError):
        reopened.undo(original['id'])
    assert (reopened.root / '.receipts' / (original['id'] + '.json')).exists()
    assert created['status'] == 'saved'


def test_move_into_externally_removed_destination_and_undo_keeps_old_identity_retired(tmp_path):
    store = Store(tmp_path / 'data'); m = store.memory
    m.create_file('destination.md', 'Former destination')
    stale_id = m.file_snapshot('destination.md')['file_id']
    (m.root / 'destination.md').unlink()
    m.create_file('source.md', 'Moving note')
    moved = m.move('source.md', 'destination.md', m.file_snapshot('source.md')['sha256'])
    m = Store(store.directory).memory
    assert m.file_snapshot('destination.md')['text'] == 'Moving note'
    m.undo(moved['id'])
    assert m.file_snapshot('source.md')['text'] == 'Moving note'
    assert not (m.root / 'destination.md').exists()
    meta = json.loads(m.registry_path.read_text())
    assert meta['files'][stale_id]['deleted'] is True


def test_tree_history_uses_sequences_and_cannot_undo_stale_create_by_matching_bytes(tmp_path):
    m = Store(tmp_path / 'data').memory
    created = m.create_file('note.md', 'original')
    one = m.replace_file('note.md', 'changed', m.file_snapshot('note.md')['sha256'])
    two = m.replace_file('note.md', 'original', m.file_snapshot('note.md')['sha256'])
    assert created['sequence'] < one['sequence'] < two['sequence']
    with pytest.raises(ValueError, match='later'):
        m.undo(created['id'])
    history = m.history('note.md')
    assert history[0]['id'] == two['id']
    assert next(row for row in history if row['id'] == created['id'])['undo_error']
    removed = m.delete('note.md', m.file_snapshot('note.md')['sha256'])
    assert Store(tmp_path / 'data').memory.history('note.md')[0]['id'] == removed['id']


def test_folder_create_has_history_and_undo(tmp_path):
    m = Store(tmp_path / 'data').memory
    made = m.create_folder('Ordinary')
    assert made['operation'] == 'create'
    m.undo(made['id'])
    assert not (m.root / 'Ordinary').exists()


def test_stale_activation_revision_preserves_other_user_choice(tmp_path):
    m = Store(tmp_path / 'data').memory
    m.create_file('note.md', 'same text')
    before = m.file_snapshot('note.md')
    m.set_active('note.md', True, before['sha256'], expected_revision=before['revision'])
    with pytest.raises(ValueError, match='activation|changed'):
        m.set_active('note.md', False, before['sha256'], expected_revision=before['revision'])
    assert m.file_snapshot('note.md')['always_active']


@pytest.mark.parametrize('operation', ['move', 'delete', 'create-file', 'create-folder'])
@pytest.mark.parametrize('phase', ['prepared', 'published'])
def test_crash_recovery_retains_complete_move_delete_and_creation(tmp_path, monkeypatch, operation, phase):
    import letracode.memory as memory_module
    m = Store(tmp_path / 'data').memory
    m.create_file('before.md', 'preserved')
    class Crash(BaseException):
        pass
    real_move = memory_module.rename_noreplace
    def crash(src_fd, src, dst_fd, dst):
        relevant = (operation in ('move', 'delete') and src == 'before.md' or
                    operation.startswith('create') and src.startswith('create-'))
        if relevant and phase == 'prepared':
            raise Crash()
        real_move(src_fd, src, dst_fd, dst)
        if relevant and phase == 'published':
            raise Crash()
    with monkeypatch.context() as patch:
        patch.setattr(memory_module, 'rename_noreplace', crash)
        with pytest.raises(Crash):
            if operation == 'move':
                m.move('before.md', 'after.md', m.file_snapshot('before.md')['sha256'])
            elif operation == 'delete':
                m.delete('before.md', m.file_snapshot('before.md')['sha256'])
            elif operation == 'create-file':
                m.create_file('created.md', 'new preserved')
            else:
                m.create_folder('Created')
    again = Store(tmp_path / 'data').memory
    op = max((row for row, raw in again._operation_records()), key=lambda row: row['sequence'])
    assert op['status'] == ('saved' if phase == 'published' else 'aborted')
    if operation == 'move':
        assert again.file_snapshot('after.md' if phase == 'published' else 'before.md')['text'] == 'preserved'
    elif operation == 'delete' and phase == 'published':
        again.undo(op['id'])
        assert again.file_snapshot('before.md')['text'] == 'preserved'
    elif operation == 'create-file' and phase == 'published':
        assert again.file_snapshot('created.md')['text'] == 'new preserved'
    elif operation == 'create-folder':
        assert (again.root / 'Created').exists() == (phase == 'published')


def test_move_retains_old_recovery_inodes_and_reports_late_editor_write(tmp_path):
    m = Store(tmp_path / 'data').memory
    m.create_file('before.md', 'original')
    before = m.file_snapshot('before.md')
    with open(m.root / 'before.md', 'r+b') as editor:
        if fs.IS_WINDOWS:
            with pytest.raises(PermissionError):
                m.replace_file('before.md', 'saved', before['sha256'])
            assert (m.root / 'before.md').read_text() == 'original'
            assert not (m.root / 'after.md').exists()
            return
        m.replace_file('before.md', 'saved', m.file_snapshot('before.md')['sha256'])
        m.move('before.md', 'after.md', m.file_snapshot('before.md')['sha256'])
        editor.seek(0); editor.write(b'late editor'); editor.truncate(); editor.flush()
    with pytest.raises(ValueError, match='external edit preserved|conflict'):
        m.file_snapshot('after.md')
    assert any(p.read_bytes() == b'late editor' for p in m.root.rglob('*.before'))


def test_project_deletion_archives_entire_tree_and_restores_on_database_failure(tmp_path):
    s = Store(tmp_path / 'data'); p = s.create_project('Project')
    s.memory.create_folder('Nested', p); s.memory.create_file('Nested/private.md', 'private', p)
    snap = s.memory.file_snapshot('Nested/private.md', p)
    s.memory.set_active('Nested/private.md', True, snap['sha256'], p)
    with s.connection() as db:
        db.execute("CREATE TRIGGER block_project_delete BEFORE DELETE ON projects BEGIN SELECT RAISE(ABORT, 'blocked'); END")
    with pytest.raises(sqlite3.IntegrityError, match='blocked'):
        s.delete_project(p)
    assert s.memory.file_snapshot('Nested/private.md', p)['text'] == 'private'
    with s.connection() as db:
        db.execute('DROP TRIGGER block_project_delete')
    archive = s.delete_project(p)
    from pathlib import Path
    assert (Path(archive['path']) / 'Nested/private.md').read_text() == 'private'
    assert s.memory.entries(p) == []
    assert 'private' not in s.memory.core(p)
    assert Store(s.directory).memory.entries(p) == []
    destination = tmp_path / 'archive.zip'; s.backup(destination)
    with zipfile.ZipFile(destination) as z:
        assert z.read((Path(archive['path']) / 'Nested/private.md').relative_to(s.directory).as_posix()) == b'private'


def test_migration_resumes_after_root_rename_without_recreating_legacy_paths(tmp_path, monkeypatch):
    import letracode.store as store_module
    from letracode.strand import StrandFiles
    import shutil
    s = Store(tmp_path / 'data')
    shutil.rmtree(s.memory.root)
    for name in ('memory-tree-migration.json', 'memory-initialization.json'):
        (s.directory / name).unlink(missing_ok=True)
    legacy = StrandFiles(s.directory / 'strand')
    legacy.path('identity').write_text('My Strand identity')
    legacy.path('preferences').unlink()
    with s.connection() as db:
        db.execute('PRAGMA user_version=2')
    real_class = store_module.MemoryFiles
    def failed_initialization(*args, **kwargs):
        raise OSError('Simulated crash after root move')
    with monkeypatch.context() as patch:
        patch.setattr(store_module, 'MemoryFiles', failed_initialization)
        with pytest.raises(OSError, match='root move'):
            Store(s.directory)
    assert not (s.directory / 'strand').exists()
    migrated = Store(s.directory).memory
    assert migrated.snapshot('identity')['text'] == 'My Strand identity'
    assert migrated.alias_deleted('preferences')
    assert not migrated.path('preferences').exists()
    assert migrated.file_snapshot('identity/strand.md')['always_active']


@pytest.mark.parametrize('operation', ['move', 'delete'])
def test_ancestor_of_retained_recovery_cannot_hide_late_editor_conflicts(tmp_path, operation):
    m = Store(tmp_path / 'data').memory
    m.create_folder('A'); m.create_folder('B'); m.create_file('A/note.md', 'original')
    # The recovery location must remain stable even if the editor closes its
    # handle temporarily before making a later correction to the retained file.
    m.replace_file('A/note.md', 'saved', m.file_snapshot('A/note.md')['sha256'])
    m.move('A/note.md', 'B/note.md', m.file_snapshot('A/note.md')['sha256'])
    retained = next((m.root / 'A/.strand-recovery/note.md').glob('*.before'))
    with pytest.raises(ValueError, match='recovery|Recovery'):
        if operation == 'move':
            m.move('A', 'C', m.snapshot_entry('A')['sha256'])
        else:
            m.delete('A', m.snapshot_entry('A')['sha256'])
    with open(retained, 'r+b') as editor:
        editor.seek(0); editor.write(b'late correction'); editor.truncate(); editor.flush()
    with pytest.raises(ValueError, match='conflict|external edit'):
        m.file_snapshot('B/note.md')


def test_missing_schema3_registry_fails_closed_without_recreating_defaults(tmp_path):
    s = Store(tmp_path / 'data')
    s.memory.create_file('custom.md', 'preserve')
    s.memory.registry_path.unlink()
    identity = s.memory.root / 'identity/assistant.md'
    identity.unlink()
    with pytest.raises(ValueError, match='registry is missing'):
        Store(s.directory)
    assert not identity.exists()
    assert not s.memory.registry_path.exists()
    assert (s.memory.root / 'custom.md').read_text() == 'preserve'


def test_root_migration_waits_for_existing_writer_and_holds_sqlite_lock(tmp_path, monkeypatch):
    from letracode import filesystem as fs
    import shutil
    import threading
    import letracode.store as store_module
    from letracode.strand import StrandFiles
    s = Store(tmp_path / 'data')
    shutil.rmtree(s.memory.root)
    for name in ('memory-tree-migration.json', 'memory-initialization.json'):
        (s.directory / name).unlink(missing_ok=True)
    legacy = StrandFiles(s.directory / 'strand')
    with s.connection() as db:
        db.execute('PRAGMA user_version=2')
    attempted = threading.Event(); finished = threading.Event(); result = []
    real_flock = fs.flock; real_class = store_module.MemoryFiles
    def observed_flock(fd, operation):
        if threading.current_thread().name == 'migrate-memory' and operation == fs.LOCK_EX:
            attempted.set()
        return real_flock(fd, operation)
    def check_schema_lock(*args, **kwargs):
        with sqlite3.connect(s.path, timeout=0) as db:
            with pytest.raises(sqlite3.OperationalError, match='locked'):
                db.execute("UPDATE settings SET value='null'")
        return real_class(*args, **kwargs)
    def migrate():
        try:
            result.append(Store(s.directory))
        except BaseException as error:
            result.append(error)
        finally:
            finished.set()
    monkeypatch.setattr(fs, 'flock', observed_flock)
    monkeypatch.setattr(store_module, 'MemoryFiles', check_schema_lock)
    worker = threading.Thread(target=migrate, name='migrate-memory')
    with legacy._operation():
        worker.start()
        assert attempted.wait(3)
        assert not finished.is_set()
        legacy.path('global').write_text('Writer completed before migration')
    worker.join(5)
    assert finished.is_set()
    assert isinstance(result[0], Store), result
    assert result[0].memory.snapshot('global')['text'] == 'Writer completed before migration'


def test_deleted_active_legacy_project_file_is_excluded_after_archive(tmp_path):
    s = Store(tmp_path / 'data'); project = s.create_project('Project')
    s.update_project(project, memory='Active project-only memory')
    snap = s.memory.file_snapshot('Memory.md', project)
    s.memory.set_active('Memory.md', True, snap['sha256'], project)
    assert 'project-only' in s.memory.core(project)
    s.delete_project(project)
    assert s.memory.alias_deleted('project', project)
    assert 'project-only' not in s.memory.core(project)
    assert Store(s.directory).memory.core(project) == ''


def test_duplicate_content_and_tree_sequences_block_undo_and_saves(tmp_path):
    s = Store(tmp_path / 'data'); m = s.memory
    created = m.create_file('note.md', 'original')
    saved = m.replace_file('note.md', 'changed', m.file_snapshot('note.md')['sha256'])
    journal = m._operation_path(created['id'])
    record = json.loads(journal.read_text()); record['sequence'] = saved['sequence']
    journal.write_text(json.dumps(record))
    with pytest.raises(ValueError, match='Duplicate|duplicate'):
        m.undo(saved['id'])
    with pytest.raises(ValueError, match='Duplicate|duplicate'):
        m.replace_file('note.md', 'another', m.file_snapshot('note.md')['sha256'])
    assert m.file_snapshot('note.md')['text'] == 'changed'


@pytest.mark.parametrize('parent', ['A', 'B'])
def test_move_refuses_replaced_ancestor_even_when_new_file_has_identical_bytes(tmp_path, monkeypatch, parent):
    m = Store(tmp_path / 'data').memory
    m.create_folder('A'); m.create_folder('B'); m.create_file('A/note.md', 'same bytes')
    expected = m.file_snapshot('A/note.md')['sha256']
    real_digest = m._entry_digest; calls = 0
    def replaced(path):
        nonlocal calls
        if path == m.root / 'A/note.md':
            calls += 1
            if calls == 2:
                (m.root / parent).rename(tmp_path / 'external')
                (m.root / parent).mkdir()
                if parent == 'A':
                    (m.root / 'A/note.md').write_text('same bytes')
        return real_digest(path)
    monkeypatch.setattr(m, '_entry_digest', replaced)
    with pytest.raises((ValueError, PermissionError)):
        m.move('A/note.md', 'B/note.md', expected)
    if os.name == 'nt':
        assert not (tmp_path / 'external').exists(), 'Native Windows guard must prevent the ancestor rename'
    elif parent == 'A':
        assert (tmp_path / 'external/note.md').read_text() == 'same bytes'
    else:
        assert not (tmp_path / 'external/note.md').exists()
    assert (m.root / 'A/note.md').read_text() == 'same bytes'
    assert not (m.root / 'B/note.md').exists()


@pytest.mark.parametrize('action', ['replace', 'activate'])
def test_reviewed_file_identity_rejects_same_bytes_recreation(tmp_path, action):
    s = Store(tmp_path / 'data'); m = s.memory
    m.create_file('note.md', 'same original bytes')
    reviewed = m.file_snapshot('note.md')
    other = Store(s.directory).memory
    other.delete('note.md', reviewed['sha256'])
    other.create_file('note.md', reviewed['text'])
    replacement = other.file_snapshot('note.md')
    assert replacement['file_id'] != reviewed['file_id']
    before = m.registry_path.read_bytes()
    history = list(m._operation_records())
    with pytest.raises(ValueError, match='identity|Identity'):
        if action == 'replace':
            m.replace_file('note.md', 'stale draft', reviewed['sha256'], expected_file_id=reviewed['file_id'])
        else:
            m.set_active('note.md', True, reviewed['sha256'], expected_file_id=reviewed['file_id'])
    assert m.registry_path.read_bytes() == before
    assert len(m._operation_records()) == len(history)
    assert m.file_snapshot('note.md')['text'] == reviewed['text']
    assert not m.file_snapshot('note.md')['always_active']


def test_reviewed_unregistered_memory_identity_is_checked_before_registering(tmp_path):
    m = Store(tmp_path / 'data').memory
    (m.root / 'external.md').write_text('user file')
    reviewed = m.file_snapshot('external.md')
    assert reviewed['file_id'] is None
    m.replace_file('external.md', 'reviewed save', reviewed['sha256'], expected_file_id=None)
    current = m.file_snapshot('external.md')
    assert current['file_id']
    with pytest.raises(ValueError, match='identity|Identity'):
        m.replace_file('external.md', 'different save', current['sha256'], expected_file_id=None)


@pytest.mark.parametrize('operation', ['move', 'delete'])
@pytest.mark.parametrize('kind', ['file', 'folder'])
def test_reviewed_tree_identity_refuses_same_content_replacement(tmp_path, operation, kind):
    s = Store(tmp_path / 'data'); m = s.memory
    if kind == 'file':
        path = 'note.md'; m.create_file(path, 'same')
    else:
        path = 'Folder'; m.create_folder(path); m.create_file('Folder/note.md', 'same')
    reviewed = m.snapshot_entry(path)
    # Keep the old inode alive while a different ordinary entry assumes its
    # former pathname with identical bytes/tree shape.
    (m.root / path).rename(tmp_path / 'retained-original')
    if kind == 'file':
        (m.root / path).write_text('same')
    else:
        (m.root / path).mkdir(); (m.root / path / 'note.md').write_text('same')
    before = m.registry_path.read_bytes(); operations = len(m._operation_records())
    with pytest.raises(ValueError, match='identity|Identity'):
        if operation == 'move':
            m.move(path, 'renamed.md' if kind == 'file' else 'Renamed', reviewed['sha256'],
                   expected_file_id=reviewed.get('file_id'), expected_entry_identity=reviewed['entry_identity'])
        else:
            m.delete(path, reviewed['sha256'], expected_file_id=reviewed.get('file_id'),
                     expected_entry_identity=reviewed['entry_identity'])
    assert (m.root / path).exists()
    assert (tmp_path / 'retained-original').exists()
    assert m.registry_path.read_bytes() == before
    assert len(m._operation_records()) == operations


@pytest.mark.parametrize('operation', ['replace', 'activate'])
def test_unregistered_file_review_checks_inode_when_same_bytes_replaced(tmp_path, operation):
    m = Store(tmp_path / 'data').memory
    path = m.root / 'external.md'; path.write_text('same')
    reviewed = m.file_snapshot('external.md')
    path.rename(tmp_path / 'old-inode'); path.write_text('same')
    before = m.registry_path.read_bytes()
    with pytest.raises(ValueError, match='identity|Identity'):
        if operation == 'replace':
            m.replace_file('external.md', 'stale', reviewed['sha256'], expected_file_id=None,
                           expected_entry_identity=reviewed['entry_identity'])
        else:
            m.set_active('external.md', True, reviewed['sha256'], expected_file_id=None,
                         expected_entry_identity=reviewed['entry_identity'])
    assert path.read_text() == 'same'
    assert m.registry_path.read_bytes() == before


def test_reviewed_save_checks_inode_again_at_guarded_publication(tmp_path, monkeypatch):
    import letracode.strand as strand_module
    m = Store(tmp_path / 'data').memory
    m.create_file('note.md', 'same')
    reviewed = m.file_snapshot('note.md'); path = m.root / 'note.md'
    real_write = strand_module.safe_write
    def changed_before_write(target, *args, **kwargs):
        if target == path:
            path.rename(tmp_path / 'old-inode'); path.write_text('same')
        return real_write(target, *args, **kwargs)
    monkeypatch.setattr(strand_module, 'safe_write', changed_before_write)
    with pytest.raises(ValueError, match='identity|Identity'):
        m.replace_file('note.md', 'stale save', reviewed['sha256'], expected_file_id=reviewed['file_id'],
                       expected_entry_identity=reviewed['entry_identity'])
    assert path.read_text() == 'same'
    assert (tmp_path / 'old-inode').read_text() == 'same'


def test_confirmed_save_returns_published_inode_for_activation(tmp_path):
    m = Store(tmp_path / 'data').memory
    m.create_file('note.md', 'before')
    reviewed = m.file_snapshot('note.md')
    receipt = m.replace_file('note.md', 'after', reviewed['sha256'], expected_file_id=reviewed['file_id'],
                             expected_entry_identity=reviewed['entry_identity'])
    current = m.file_snapshot('note.md')
    assert receipt['entry_identity'] == current['entry_identity']
    assert receipt['entry_identity'] != reviewed['entry_identity']
    m.set_active('note.md', True, receipt['after_sha256'], expected_file_id=reviewed['file_id'],
                 expected_entry_identity=receipt['entry_identity'])
    assert m.file_snapshot('note.md')['always_active']
