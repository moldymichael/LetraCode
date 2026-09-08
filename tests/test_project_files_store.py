import zipfile

import pytest

from letracode.store import Store


def test_legacy_context_becomes_an_ordinary_file_without_losing_original(tmp_path):
    store = Store(tmp_path / 'data')
    project = store.create_project('Writing')
    original = 'Chapter twelve\n\nKeep the exact spacing.\n'
    store.update_project(project, current_context=original, instructions='Discuss first')
    folder = store.ensure_project_files(project)
    assert (folder / 'Current Context.md').read_text() == original
    assert store.project(project)['current_context'] == original
    assert store.project_for_context(project)['current_context'] == ''
    assert store.project_for_context(project)['instructions'] == 'Discuss first'
    assert store.memory.file_snapshot('Current Context.md', project)['always_active'] is False
    (folder / 'Current Context.md').write_text('Edited in my file manager')
    again = Store(store.directory)
    assert again.ensure_project_files(project) == folder
    assert (folder / 'Current Context.md').read_text() == 'Edited in my file manager'
    assert len(list(folder.glob('Current Context*'))) == 1


def test_import_preserves_colliding_files_and_undo_does_not_reimport(tmp_path):
    store = Store(tmp_path / 'data')
    project = store.create_project('Writing')
    store.update_project(project, current_context='Original context')
    store.memory.create_file('Current Context.md', 'My existing note', project)
    folder = store.ensure_project_files(project)
    assert (folder / 'Current Context.md').read_text() == 'My existing note'
    assert (folder / 'Current Context (legacy).md').read_text() == 'Original context'
    history = store.memory.history(project_id=project)
    receipt = next(row for row in history if row.get('destination', '').endswith('Current Context (legacy).md'))
    store.memory.undo(receipt['id'])
    store.ensure_project_files(project)
    assert not (folder / 'Current Context (legacy).md').exists()
    assert store.project(project)['current_context'] == 'Original context'


def test_import_retries_after_marker_failure_and_never_replaces_external_edits(tmp_path, monkeypatch):
    store = Store(tmp_path / 'data')
    project = store.create_project('Writing')
    store.update_project(project, current_context='Original context')
    original_setting = store.set_setting
    def fail(key, value):
        if key.startswith('project_files_migrated:'):
            raise OSError('Simulated failed commit')
        original_setting(key, value)
    monkeypatch.setattr(store, 'set_setting', fail)
    with pytest.raises(OSError, match='failed commit'):
        store.ensure_project_files(project)
    folder = store.memory.root_for(project)
    (folder / 'Current Context.md').write_text('External edit after interrupted import')
    monkeypatch.setattr(store, 'set_setting', original_setting)
    store.ensure_project_files(project)
    assert (folder / 'Current Context.md').read_text() == 'External edit after interrupted import'
    assert (folder / 'Current Context (legacy).md').read_text() == 'Original context'


def test_file_import_is_included_in_existing_backup(tmp_path):
    store = Store(tmp_path / 'data')
    project = store.create_project('Writing')
    store.update_project(project, current_context='Keep this')
    folder = store.ensure_project_files(project)
    archive = tmp_path / 'backup.zip'
    store.backup(archive)
    with zipfile.ZipFile(archive) as backup:
        assert backup.read((folder / 'Current Context.md').relative_to(store.directory).as_posix()) == b'Keep this'


def test_unknown_project_cannot_create_files(tmp_path):
    store = Store(tmp_path / 'data')
    with pytest.raises(ValueError, match='Unknown project'):
        store.ensure_project_files('missing')


@pytest.mark.parametrize('character', ['x', '€'])
def test_oversized_legacy_text_exports_losslessly_without_relaxing_normal_edits(tmp_path, character):
    from letracode.strand import MAX_FILE_BYTES
    store = Store(tmp_path / 'data')
    project = store.create_project('Large notes')
    text = character * (MAX_FILE_BYTES // len(character.encode()) + 1)
    store.update_project(project, current_context=text)
    folder = store.ensure_project_files(project)
    assert (folder / 'Current Context.md').read_bytes() == text.encode()
    assert store.project_for_context(project)['current_context'] == ''
    assert Store(store.directory).ensure_project_files(project) == folder
    with pytest.raises(ValueError, match='oversized'):
        store.memory.create_file('Too large.md', text, project)
    archive = tmp_path / 'large.zip'
    store.backup(archive)
    with zipfile.ZipFile(archive) as backup:
        assert backup.read((folder / 'Current Context.md').relative_to(store.directory).as_posix()) == text.encode()


def test_oversized_import_reuses_durable_file_after_marker_failure(tmp_path, monkeypatch):
    from letracode.strand import MAX_FILE_BYTES
    store = Store(tmp_path / 'data')
    project = store.create_project('Large notes')
    store.update_project(project, current_context='€' * MAX_FILE_BYTES)
    original_setting = store.set_setting
    def fail(key, value):
        if key.startswith('project_files_migrated:'):
            raise OSError('Marker interrupted')
        original_setting(key, value)
    monkeypatch.setattr(store, 'set_setting', fail)
    with pytest.raises(OSError, match='Marker interrupted'):
        store.ensure_project_files(project)
    monkeypatch.setattr(store, 'set_setting', original_setting)
    folder = store.ensure_project_files(project)
    assert len(list(folder.glob('Current Context*'))) == 1
