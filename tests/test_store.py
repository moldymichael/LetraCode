import json
import sqlite3
import zipfile

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
        assert len(z.namelist()) == 3
    db = sqlite3.connect(tmp_path / 'restore/letracode.sqlite3')
    assert db.execute('select title from projects').fetchone()[0] == 'Work'


def test_search_includes_message_text_and_export(tmp_path):
    s = Store(tmp_path / 'data')
    c = s.create_chat('Notes')
    s.add_message(c, 'user', 'The violet bicycle')
    assert [x['id'] for x in s.chats('violet')] == [c]
    assert 'The violet bicycle' in s.export_markdown(c)
