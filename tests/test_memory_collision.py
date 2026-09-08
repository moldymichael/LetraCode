import pytest

from letracode.store import Store


def test_context_import_collision_retry_reopens_with_both_contents_and_history(tmp_path, monkeypatch):
    import letracode.memory as memory_module

    store = Store(tmp_path / 'data')
    project = store.create_project('Writing')
    store.update_project(project, current_context='Original context')
    folder = store.memory.root_for(project)
    rename = memory_module.rename_noreplace

    def collide(src_fd, src, dst_fd, dst):
        if dst == 'Current Context.md':
            (folder / dst).write_text('External note')
        return rename(src_fd, src, dst_fd, dst)

    with monkeypatch.context() as patch:
        patch.setattr(memory_module, 'rename_noreplace', collide)
        store.ensure_project_files(project)

    again = Store(store.directory)
    assert (folder / 'Current Context.md').read_text() == 'External note'
    assert (folder / 'Current Context (legacy).md').read_text() == 'Original context'
    assert again.project(project)['current_context'] == 'Original context'
    records = [row for row, _ in again.memory._operation_records()
               if row['project_id'] == project]
    failed = next(row for row in records if row['destination'].endswith('/Current Context.md'))
    retained = again.memory.root / failed['source']
    assert failed['status'] == 'aborted'
    assert retained.read_text() == 'Original context'
    assert [retained.stat().st_dev, retained.stat().st_ino] == failed['inode']
    imported = next(row for row in records if row['destination'].endswith('/Current Context (legacy).md'))
    assert imported['status'] == 'saved'
    again.memory.undo(imported['id'])
    again.ensure_project_files(project)
    assert not (folder / 'Current Context (legacy).md').exists()
    assert (folder / 'Current Context.md').read_text() == 'External note'
    Store(store.directory)


def test_move_collision_reopens_with_source_identity_and_history_unchanged(tmp_path, monkeypatch):
    import letracode.memory as memory_module

    store = Store(tmp_path / 'data')
    memory = store.memory
    created = memory.create_file('before.md', 'Original note')
    before = memory.file_snapshot('before.md')
    rename = memory_module.rename_noreplace

    def collide(src_fd, src, dst_fd, dst):
        (memory.root / dst).write_text('External note')
        return rename(src_fd, src, dst_fd, dst)

    with monkeypatch.context() as patch:
        patch.setattr(memory_module, 'rename_noreplace', collide)
        with pytest.raises(FileExistsError):
            memory.move('before.md', 'after.md', before['sha256'])

    again = Store(store.directory).memory
    assert again.file_snapshot('before.md') == before
    assert again.file_snapshot('after.md')['text'] == 'External note'
    assert any(row['id'] == created['id'] for row in again.history())
    again.move('before.md', 'retried.md', before['sha256'])
    reopened = Store(store.directory).memory
    assert reopened.file_snapshot('retried.md')['text'] == 'Original note'
    assert reopened.file_snapshot('after.md')['text'] == 'External note'
