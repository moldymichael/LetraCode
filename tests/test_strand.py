import hashlib
import os
import multiprocessing
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from letracode.store import Store
import letracode.strand as strand_module


def test_ordinary_files_are_authoritative_and_scoped(tmp_path):
    store = Store(tmp_path / 'data')
    first = store.create_project('First')
    second = store.create_project('Second')
    strand = store.strand
    for relative in ('identity/strand.md', 'identity/preferences.md', 'memory/global.md',
                     'learning/programming.md', f'memory/projects/{first}.md'):
        assert (strand.root / relative).is_file()
    store.update_project(first, memory='First project secret')
    assert store.project(first)['memory'] == 'First project secret'
    assert store.project(second)['memory'] == ''
    with store.connection() as db:
        assert db.execute('SELECT memory FROM projects WHERE id=?', (first,)).fetchone()[0] == ''
    (strand.root / f'memory/projects/{first}.md').write_text('External correction')
    assert store.project(first)['memory'] == 'External correction'
    assert 'External correction' in strand.context(first, 'correction', 2000)
    assert 'External correction' not in strand.context(second, '', 2000)


def test_receipt_and_undo_survive_restart_and_preserve_external_edits(tmp_path):
    strand = Store(tmp_path / 'data').strand
    before = strand.snapshot('global')
    receipt = strand.remember('global', 'Likes concise explanations',
                              expected_sha256=before['sha256'], origin='chat:test')
    saved = strand.snapshot('global')
    assert receipt['id'] in saved['text']
    assert receipt['date'] in saved['text']
    assert receipt['origin'] == 'chat:test'
    assert receipt['scope'] == 'global'
    assert receipt['saved_text'] == 'Likes concise explanations'
    assert receipt['path'] == saved['path']
    again = Store(tmp_path / 'data').strand
    assert again.receipt(receipt['id'])['after_sha256'] == saved['sha256']
    assert again.undo(receipt['id'])['undo_of'] == receipt['id']
    assert again.snapshot('global')['text'] == before['text']
    receipt = again.remember('global', 'Another note', expected_sha256=before['sha256'])
    (again.root / 'memory/global.md').write_text('A later user correction')
    with pytest.raises(ValueError, match='changed|conflict'):
        again.undo(receipt['id'])
    assert again.snapshot('global')['text'] == 'A later user correction'


def test_scoped_receipts_filter_before_limit_and_keep_unfiltered_api(tmp_path, monkeypatch):
    from datetime import datetime, timedelta
    from itertools import count
    from types import SimpleNamespace
    ticks = count()
    monkeypatch.setattr(strand_module, 'datetime', SimpleNamespace(
        now=lambda zone:datetime(2026, 9, 5, tzinfo=zone) + timedelta(seconds=next(ticks))))
    store = Store(tmp_path / 'data')
    project = store.create_project('First'); other = store.create_project('Second')
    strand = store.strand
    saved = []
    for scope, ident, text in [('project', project, 'First project save'),
                               ('project', project, 'Second project save'),
                               ('global', None, 'Global save'),
                               ('project', other, 'Other project save'),
                               ('preferences', None, 'Newer preferences save')]:
        snapshot = strand.snapshot(scope, ident)
        saved.append(strand.replace(scope, text, snapshot['sha256'], ident))

    assert [row['id'] for row in strand.receipts(1, scope='project', project_id=project)] == [saved[1]['id']]
    assert [row['id'] for row in strand.receipts(1, scope='global')] == [saved[2]['id']]
    assert [row['id'] for row in strand.receipts(1, scope='project', project_id=other)] == [saved[3]['id']]
    assert [row['id'] for row in strand.receipts(1, scope='preferences')] == [saved[4]['id']]
    assert strand.receipts(scope='identity') == []
    assert [row['id'] for row in strand.receipts(1)] == [saved[4]['id']]
    assert len(strand.receipts()) == 5


@pytest.mark.parametrize(('scope', 'project_id'), [
    (None, 'project-id'), ('global', 'project-id'), ('preferences', 'project-id'),
    ('project', None), ('project', '../escape'), ('unknown', None), ([], None),
])
def test_scoped_receipts_reject_invalid_scope_project_pairs(tmp_path, scope, project_id):
    strand = Store(tmp_path / 'data').strand
    with pytest.raises(ValueError):
        strand.receipts(scope=scope, project_id=project_id)


def test_guarded_save_never_overwrites_an_external_change(tmp_path):
    strand = Store(tmp_path / 'data').strand
    snapshot = strand.snapshot('preferences')
    (strand.root / 'identity/preferences.md').write_text('User correction')
    with pytest.raises(ValueError, match='changed|conflict'):
        strand.replace('preferences', 'Old editor value', snapshot['sha256'])
    assert strand.snapshot('preferences')['text'] == 'User correction'


@pytest.mark.parametrize('kind', ['file_symlink', 'file_hardlink', 'directory_symlink', 'root_symlink'])
def test_link_redirection_is_rejected_without_touching_external_data(tmp_path, kind):
    strand = Store(tmp_path / 'data').strand
    old = strand.snapshot('global')
    victim = tmp_path / 'outside.md'
    victim.write_text('Private outside data')
    target = strand.root / 'memory/global.md'
    if kind == 'file_symlink':
        target.unlink()
        target.symlink_to(victim)
    elif kind == 'file_hardlink':
        target.unlink()
        os.link(victim, target)
    else:
        directory = target.parent if kind == 'directory_symlink' else strand.root
        outside = tmp_path / 'moved'
        directory.rename(outside)
        directory.symlink_to(outside, target_is_directory=True)
    with pytest.raises((ValueError, OSError), match='link|safe|directory'):
        strand.replace('global', 'Overwrite', old['sha256'])
    with pytest.raises((ValueError, OSError), match='link|safe|directory'):
        strand.snapshot('global')
    assert victim.read_text() == 'Private outside data'


def test_invalid_project_ids_cannot_escape_memory_directory(tmp_path):
    strand = Store(tmp_path / 'data').strand
    for ident in ('../outside', '/absolute', 'a/b', '..', ''):
        with pytest.raises(ValueError):
            strand.snapshot('project', ident)


def test_context_and_pages_are_bounded_and_signal_partial_coverage(tmp_path):
    strand = Store(tmp_path / 'data').strand
    old = strand.snapshot('global')
    strand.replace('global', ('General notes.\n' * 1000) + '\nRareword is relevant.\n', old['sha256'])
    context = strand.context(None, 'Rareword', 800)
    assert len(context) <= 800
    assert 'partial' in context.lower()
    assert 'Rareword is relevant' in context
    page = strand.read_page('global', offset=0, max_chars=100)
    assert len(page['text']) == 100
    assert page['next_offset'] == 100
    assert page['sha256'] == hashlib.sha256(strand.snapshot('global')['text'].encode()).hexdigest()


def test_atomic_write_failure_leaves_original_content(tmp_path, monkeypatch):
    strand = Store(tmp_path / 'data').strand
    original = strand.snapshot('global')
    real_move = strand_module.rename_noreplace

    def fail_target(src_fd, src, dst_fd, dst):
        if str(dst).endswith('global.md') and str(src).endswith('.proposed'):
            raise OSError('Simulated disk failure')
        return real_move(src_fd, src, dst_fd, dst)

    monkeypatch.setattr(strand_module, 'rename_noreplace', fail_target)
    with pytest.raises(OSError, match='disk failure'):
        strand.replace('global', 'New content', original['sha256'])
    assert strand.snapshot('global')['text'] == original['text']


def test_file_swapped_to_fifo_during_open_is_rejected_without_blocking(tmp_path):
    strand = Store(tmp_path / 'data').strand
    target = strand.root / 'memory/global.md'
    context = multiprocessing.get_context('fork')
    result = context.Queue()

    def read_during_swap():
        original_open = os.open

        def swap(name, flags, *args, **kwargs):
            if name == 'global.md':
                target.unlink()
                os.mkfifo(target)
            return original_open(name, flags, *args, **kwargs)

        os.open = swap
        try:
            strand.snapshot('global')
            result.put('accepted FIFO')
        except (ValueError, OSError) as error:
            result.put(str(error))

    child = context.Process(target=read_during_swap)
    child.start()
    child.join(1)
    blocked = child.is_alive()
    if blocked:
        child.terminate()
        child.join()
    assert not blocked, 'Memory read blocked after target changed to FIFO'
    assert 'Unsafe' in result.get(timeout=1)


@pytest.mark.parametrize('grow_during_open', [False, True])
def test_oversized_external_file_is_preserved_and_read_fails_explicitly(tmp_path, monkeypatch, grow_during_open):
    strand = Store(tmp_path / 'data').strand
    target = strand.root / 'memory/global.md'
    oversized = b'x' * (2 * 1024 * 1024 + 1)
    if grow_during_open:
        original_open = os.open

        def grow(name, flags, *args, **kwargs):
            if name == 'global.md':
                target.write_bytes(oversized)
            return original_open(name, flags, *args, **kwargs)

        monkeypatch.setattr(os, 'open', grow)
    else:
        target.write_bytes(oversized)
    with pytest.raises(ValueError, match='too large|size limit'):
        strand.snapshot('global')
    assert target.read_bytes() == oversized


def test_oversized_write_is_rejected_without_changing_memory(tmp_path):
    strand = Store(tmp_path / 'data').strand
    before = strand.snapshot('global')
    with pytest.raises(ValueError, match='too large|size limit'):
        strand.replace('global', 'x' * (2 * 1024 * 1024 + 1), before['sha256'])
    assert strand.snapshot('global') == before


def test_separate_instances_cannot_both_save_against_the_same_snapshot(tmp_path, monkeypatch):
    first = Store(tmp_path / 'data').strand
    second = Store(tmp_path / 'data').strand
    before = first.snapshot('global')
    barrier = threading.Barrier(2)
    original_move = strand_module.rename_noreplace

    def race(src_fd, src, dst_fd, dst):
        if str(src) == 'global.md':
            try:
                barrier.wait(timeout=0.25)
            except threading.BrokenBarrierError:
                pass
        return original_move(src_fd, src, dst_fd, dst)

    monkeypatch.setattr(strand_module, 'rename_noreplace', race)

    def save(strand, content):
        try:
            strand.replace('global', content, before['sha256'])
            return 'saved'
        except ValueError as error:
            assert 'changed' in str(error)
            return 'conflict'

    with ThreadPoolExecutor(max_workers=2) as executor:
        one = executor.submit(save, first, 'First change')
        two = executor.submit(save, second, 'Second change')
        assert sorted([one.result(timeout=3), two.result(timeout=3)]) == ['conflict', 'saved']


@pytest.mark.parametrize('editor', ['in_place', 'atomic_replace'])
@pytest.mark.parametrize('operation', ['save', 'undo'])
def test_external_edit_at_commit_boundary_is_preserved(tmp_path, monkeypatch, editor, operation):
    strand = Store(tmp_path / 'data').strand
    before = strand.snapshot('global')
    receipt = strand.replace('global', 'First save', before['sha256'])
    before = strand.snapshot('global')
    target = strand.path('global')
    real_replace = os.replace
    real_move = getattr(strand_module, 'rename_noreplace', None)
    fired = False

    def edit():
        nonlocal fired
        if fired:
            return
        fired = True
        if editor == 'in_place':
            target.write_text('External edit at the final commit boundary')
        else:
            replacement = target.with_name('editor.tmp')
            replacement.write_text('External edit at the final commit boundary')
            real_replace(replacement, target)

    def old_commit(src, dst, *args, **kwargs):
        if dst == target.name:
            edit()
        return real_replace(src, dst, *args, **kwargs)

    def guarded_commit(src_fd, src, dst_fd, dst):
        if src == target.name:
            edit()
        return real_move(src_fd, src, dst_fd, dst)

    monkeypatch.setattr(os, 'replace', old_commit)
    if real_move:
        monkeypatch.setattr(strand_module, 'rename_noreplace', guarded_commit)
    with pytest.raises(ValueError, match='changed|conflict'):
        if operation == 'save':
            strand.replace('global', 'Strand proposed overwrite', before['sha256'])
        else:
            strand.undo(receipt['id'])
    assert fired
    assert target.read_text() == 'External edit at the final commit boundary'
    assert strand.snapshot('global')['text'] == target.read_text()


def test_create_only_save_cannot_replace_racing_external_file(tmp_path, monkeypatch):
    target = tmp_path / 'new.md'
    real_replace = os.replace
    real_move = getattr(strand_module, 'rename_noreplace', None)

    def old_commit(src, dst, *args, **kwargs):
        target.write_text('External create')
        return real_replace(src, dst, *args, **kwargs)

    def guarded_commit(src_fd, src, dst_fd, dst):
        target.write_text('External create')
        return real_move(src_fd, src, dst_fd, dst)

    monkeypatch.setattr(os, 'replace', old_commit)
    if real_move:
        monkeypatch.setattr(strand_module, 'rename_noreplace', guarded_commit)
    with pytest.raises(ValueError, match='changed|conflict'):
        strand_module.safe_write(target, b'Proposed create', None)
    assert target.read_text() == 'External create'


def test_late_write_through_old_editor_descriptor_is_recoverable_and_reported(tmp_path):
    directory = tmp_path / 'data'
    strand = Store(directory).strand
    project = Store(directory).create_project('Editor race')
    before = strand.snapshot('project', project)
    with strand.path('project', project).open('r+b') as editor:
        strand.replace('project', 'Proposed save', before['sha256'], project)
        editor.write(b'Late external edit through an already open file')
        editor.flush()
        os.fsync(editor.fileno())
    for files in (strand, Store(directory).strand):
        with pytest.raises(ValueError, match='external edit|conflict'):
            files.snapshot('project', project)
    recovered = [p for p in strand.root.rglob('*.before')
                 if p.read_bytes() == b'Late external edit through an already open file']
    assert len(recovered) == 1


def test_unavailable_project_context_is_explicit_and_does_not_hide_global_memory(tmp_path):
    store = Store(tmp_path / 'data')
    bad = store.create_project('Missing memory')
    good = store.create_project('Working memory')
    store.strand.path('project', bad).unlink()
    store.strand.path('global').write_text('Global preference remains usable')
    context = store.strand.context(bad, '', 2000)
    assert 'project memory unavailable' in context.lower()
    assert 'Global preference remains usable' in context
    assert 'unavailable' not in store.strand.context(good, '', 2000)
    for budget in (1, 20, 55, 60, 100):
        assert len(store.strand.context(bad, '', budget)) <= budget


@pytest.mark.parametrize('stage', ['capture', 'publish'])
def test_process_crash_during_save_recovers_without_creating_empty_memory(tmp_path, stage):
    directory = tmp_path / 'data'
    strand = Store(directory).strand
    target = strand.path('global')
    target.write_text('Original before interruption')
    before = strand.snapshot('global')

    def crash_during_move():
        real_move = strand_module.rename_noreplace

        def move(src_fd, src, dst_fd, dst):
            real_move(src_fd, src, dst_fd, dst)
            if (stage == 'capture' and src == target.name or
                    stage == 'publish' and dst == target.name and src.endswith('.proposed')):
                os.fsync(src_fd)
                os.fsync(dst_fd)
                os._exit(0)

        strand_module.rename_noreplace = move
        strand.replace('global', 'Saved before interruption', before['sha256'])
        os._exit(2)

    child = multiprocessing.get_context('fork').Process(target=crash_during_move)
    child.start()
    child.join(5)
    if child.is_alive():
        child.terminate()
        child.join()
        pytest.fail('Save child did not reach the requested interruption')
    assert child.exitcode == 0
    again = Store(directory).strand
    expected = 'Original before interruption' if stage == 'capture' else 'Saved before interruption'
    assert again.snapshot('global')['text'] == expected
    assert again.receipts()[0]['status'] == ('unconfirmed' if stage == 'capture' else 'saved')
    if stage == 'publish':
        again.undo(again.receipts()[0]['id'])
        assert again.snapshot('global')['text'] == 'Original before interruption'


def test_new_external_path_between_capture_and_publish_is_never_replaced(tmp_path, monkeypatch):
    strand = Store(tmp_path / 'data').strand
    target = strand.path('global')
    before = strand.snapshot('global')
    real_move = strand_module.rename_noreplace

    def recreate(src_fd, src, dst_fd, dst):
        if dst == target.name and src.endswith('.proposed'):
            target.write_text('External file created during save')
        return real_move(src_fd, src, dst_fd, dst)

    monkeypatch.setattr(strand_module, 'rename_noreplace', recreate)
    with pytest.raises(ValueError, match='conflict'):
        strand.replace('global', 'Proposed save', before['sha256'])
    assert target.read_text() == 'External file created during save'
    assert strand.snapshot('global')['text'] == target.read_text()
    assert any(p.read_bytes() == b'' for p in strand.root.rglob('*.before'))


def test_rollback_preserves_both_external_versions_when_editor_saves_again(tmp_path, monkeypatch):
    strand = Store(tmp_path / 'data').strand
    target = strand.path('global')
    before = strand.snapshot('global')
    real_move = strand_module.rename_noreplace

    def racing_edits(src_fd, src, dst_fd, dst):
        if src == target.name:
            target.write_text('First external edit')
        if dst == target.name and src.endswith('.before'):
            target.write_text('Second external edit during rollback')
        return real_move(src_fd, src, dst_fd, dst)

    monkeypatch.setattr(strand_module, 'rename_noreplace', racing_edits)
    with pytest.raises(ValueError, match='conflict versions retained'):
        strand.replace('global', 'Proposed overwrite', before['sha256'])
    assert target.read_text() == 'Second external edit during rollback'
    assert any(p.read_bytes() == b'First external edit' for p in strand.root.rglob('*.before'))


def test_recovery_inodes_and_journals_survive_backup_restore(tmp_path):
    import zipfile

    store = Store(tmp_path / 'data')
    before = store.strand.snapshot('global')
    receipt = store.strand.replace('global', 'Saved content', before['sha256'])
    backup = tmp_path / 'backup.zip'
    store.backup(backup)
    with zipfile.ZipFile(backup) as archive:
        assert any(name.endswith('.before') for name in archive.namelist())
        assert any(name.endswith('.done') for name in archive.namelist())
        archive.extractall(tmp_path / 'restored')
    again = Store(tmp_path / 'restored').strand
    assert again.snapshot('global')['text'] == 'Saved content'
    again.undo(receipt['id'])
    assert again.snapshot('global')['text'] == ''


@pytest.mark.parametrize('record', ['journal', 'done'])
def test_crash_halfway_through_recovery_record_write_does_not_break_startup(tmp_path, record):
    directory = tmp_path / 'data'
    strand = Store(directory).strand
    before = strand.snapshot('global')

    def crash_during_record():
        real_fdopen = os.fdopen

        class InterruptedFile:
            def __init__(self, stream):
                self.stream = stream

            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.stream.close()

            def __getattr__(self, name):
                return getattr(self.stream, name)

            def write(self, data):
                prefix = b'{"before_sha256"' if record == 'journal' else b'{"status": "saved"'
                if data.startswith(prefix):
                    self.stream.write(data[:1])
                    self.stream.flush()
                    os.fsync(self.stream.fileno())
                    os._exit(0)
                return self.stream.write(data)

        os.fdopen = lambda *args, **kwargs: InterruptedFile(real_fdopen(*args, **kwargs))
        strand.replace('global', 'Published despite interruption', before['sha256'])
        os._exit(2)

    child = multiprocessing.get_context('fork').Process(target=crash_during_record)
    child.start()
    child.join(5)
    if child.is_alive():
        child.terminate()
        child.join()
        pytest.fail('Child did not reach the record write')
    assert child.exitcode == 0
    again = Store(directory).strand
    assert again.snapshot('global')['text'] == ('' if record == 'journal' else 'Published despite interruption')
