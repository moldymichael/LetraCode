import json
import sqlite3
import zipfile
import shutil

import pytest

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
        assert 'strand/identity/strand.md' in z.namelist()
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
    shutil.rmtree(directory / 'strand', ignore_errors=True)
    return ident


def test_v1_memory_migrates_once_with_consistent_original_backup(tmp_path):
    directory = tmp_path / 'data'
    ident = legacy_fixture(directory)
    store = Store(directory)
    assert store.project(ident)['memory'] == 'Preserve the legacy memory'
    with store.connection() as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 2
        assert db.execute('SELECT memory FROM projects').fetchone()[0] == ''
    backups = list((directory / 'migration-backups').glob('*.sqlite3'))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as db:
        assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert db.execute('PRAGMA user_version').fetchone()[0] == 1
        assert db.execute('SELECT memory FROM projects').fetchone()[0] == 'Preserve the legacy memory'
    memory = directory / 'strand/memory/projects' / f'{ident}.md'
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


def test_partial_migration_failure_rolls_back_new_files_and_can_retry(tmp_path, monkeypatch):
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
    assert not (directory / 'strand/memory/projects' / f'{first}.md').exists()
    with sqlite3.connect(directory / 'letracode.sqlite3') as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 1
        assert db.execute('SELECT memory FROM projects WHERE id=?', (first,)).fetchone()[0] == 'Preserve the legacy memory'
    monkeypatch.setattr(store_module, 'safe_write', real_write)
    restored = Store(directory)
    assert restored.project(first)['memory'] == 'Preserve the legacy memory'
    assert restored.project('second')['memory'] == 'Second legacy note'


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
        assert 'strand/development/models/example.json' in archive.namelist()
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
