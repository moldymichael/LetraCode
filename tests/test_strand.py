import hashlib
import os
import multiprocessing
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from letracode.store import Store


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
    real_replace = os.replace

    def fail_target(src, dst, *args, **kwargs):
        if str(dst).endswith('global.md'):
            raise OSError('Simulated disk failure')
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, 'replace', fail_target)
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
    original_replace = os.replace

    def race(src, dst, *args, **kwargs):
        if str(dst) == 'global.md':
            try:
                barrier.wait(timeout=0.25)
            except threading.BrokenBarrierError:
                pass
        return original_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, 'replace', race)

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
