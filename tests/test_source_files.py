"""Real disposable sources; injected boundaries model ordinary editor races."""
import importlib.util
from letracode import filesystem as fs
import json
import multiprocessing
import os
import stat
import subprocess
import sys
import threading

import pytest

import letracode.strand as guarded
from letracode.context import read_text


@pytest.fixture
def source_files():
    assert importlib.util.find_spec('letracode.source_files') is not None, 'The guarded source API is missing'
    from letracode import source_files
    return source_files


def test_snapshot_keeps_raw_utf8_bom_newlines_hash_and_mode(tmp_path, source_files):
    target = tmp_path / 'script.py'
    original = b'\xef\xbb\xbf# caf\xc3\xa9\r\nprint("before")\r\n'
    target.write_bytes(original)
    target.chmod(0o751)
    before = source_files.snapshot(target)
    expected_mode = stat.S_IMODE(target.stat().st_mode)
    assert before == {'path': str(target), 'raw': original,
                      'text': original.decode('utf-8'), 'sha256': guarded.digest(original), 'mode': expected_mode}
    updated = before['text'].replace('before', 'after').encode('utf-8')
    saved = source_files.publish(target, updated, before['sha256'], mode=before['mode'])
    assert saved['raw'] == updated == target.read_bytes()
    assert stat.S_IMODE(target.stat().st_mode) == expected_mode
    assert read_text(target) == updated.decode('utf-8-sig')
    retained = list((tmp_path / '.letracode-recovery' / target.name).glob('*.before'))
    assert len(retained) == 1 and retained[0].read_bytes() == original
    assert not (tmp_path / '.strand-recovery').exists()


@pytest.mark.parametrize('change', ['content', 'mode'])
def test_stale_snapshot_cannot_publish_over_external_change(tmp_path, source_files, change):
    target = tmp_path / 'source.py'
    target.write_text('original\n')
    target.chmod(0o640)
    before = source_files.snapshot(target)
    if change == 'content':
        target.write_text('external edit\n')
    else:
        if fs.IS_WINDOWS:
            pytest.skip('POSIX executable permission changes do not exist on Windows')
        target.chmod(0o750)
    expected_bytes, expected_mode = target.read_bytes(), stat.S_IMODE(target.stat().st_mode)
    with pytest.raises(ValueError, match='changed|conflict'):
        source_files.publish(target, b'model edit\n', before['sha256'], mode=before['mode'])
    assert target.read_bytes() == expected_bytes
    assert stat.S_IMODE(target.stat().st_mode) == expected_mode


@pytest.mark.parametrize('editor', ['inplace', 'atomic'])
def test_external_edit_at_capture_boundary_is_preserved(tmp_path, source_files, monkeypatch, editor):
    target = tmp_path / 'source.py'
    target.write_bytes(b'original\n')
    before = source_files.snapshot(target)
    move = guarded.rename_noreplace

    def race(source_fd, source, destination_fd, destination):
        if source == target.name:
            if editor == 'atomic':
                staged = tmp_path / 'editor.py'
                staged.write_bytes(b'external edit\n')
                os.replace(staged, target)
            else:
                target.write_bytes(b'external edit\n')
        return move(source_fd, source, destination_fd, destination)

    monkeypatch.setattr(guarded, 'rename_noreplace', race)
    with pytest.raises(ValueError, match='changed|conflict'):
        source_files.publish(target, b'model edit\n', before['sha256'], mode=before['mode'])
    assert source_files.snapshot(target)['raw'] == b'external edit\n'


@pytest.mark.parametrize('existed', [False, True])
def test_racing_path_creation_at_publication_is_never_replaced(tmp_path, source_files, monkeypatch, existed):
    target = tmp_path / 'source.py'
    if existed:
        target.write_bytes(b'original\n')
    before = source_files.snapshot(target, allow_missing=True)
    move = guarded.rename_noreplace

    def race(source_fd, source, destination_fd, destination):
        if destination == target.name and source.endswith('.proposed'):
            target.write_bytes(b'external creation\n')
        return move(source_fd, source, destination_fd, destination)

    monkeypatch.setattr(guarded, 'rename_noreplace', race)
    with pytest.raises(ValueError, match='changed|conflict'):
        source_files.publish(target, b'model edit\n', before['sha256'], mode=before['mode'])
    assert target.read_bytes() == b'external creation\n'
    if existed:
        assert any(path.read_bytes() == b'original\n' for path in
                   (tmp_path / '.letracode-recovery' / target.name).glob('*.before'))


def test_late_descriptor_edit_is_retained_and_detected_by_context_reads(tmp_path, source_files):
    target = tmp_path / 'source.py'
    target.write_bytes(b'original\n')
    before = source_files.snapshot(target)
    with target.open('r+b') as editor:
        if fs.IS_WINDOWS:
            with pytest.raises(PermissionError):
                source_files.publish(target, b'model edit\n', before['sha256'], mode=before['mode'])
            assert target.read_bytes() == b'original\n'
            return
        source_files.publish(target, b'model edit\n', before['sha256'], mode=before['mode'])
        editor.write(b'late descriptor save\n')
        editor.truncate()
        editor.flush()
        os.fsync(editor.fileno())
    assert target.read_bytes() == b'model edit\n'
    with pytest.raises(ValueError, match='external edit|conflict'):
        source_files.snapshot(target)
    with pytest.raises(ValueError, match='external edit|conflict'):
        read_text(target)
    assert any(path.read_bytes() == b'late descriptor save\n' for path in
               (tmp_path / '.letracode-recovery' / target.name).glob('*.before'))


@pytest.mark.parametrize('stage', ['capture', 'publish'])
def test_process_crash_keeps_recoverable_sources_without_automatic_restoration(tmp_path, source_files, stage):
    target = tmp_path / 'source.py'
    target.write_bytes(b'original\r\n')
    target.chmod(0o755)
    before = source_files.snapshot(target)

    program = r'''
import os, sys
from pathlib import Path
from letracode import filesystem as fs, source_files
import letracode.strand as guarded
target, stage = Path(sys.argv[1]), sys.argv[2]
before = source_files.snapshot(target)
move = guarded.rename_noreplace
def interrupt(source_fd, source, destination_fd, destination):
    move(source_fd, source, destination_fd, destination)
    if ((stage == 'capture' and source == target.name) or
            (stage == 'publish' and destination == target.name and source.endswith('.proposed'))):
        fs.fsync(source_fd); fs.fsync(destination_fd); os._exit(73)
guarded.rename_noreplace = interrupt
source_files.publish(target, b'model edit\r\n', before['sha256'], mode=before['mode'])
os._exit(74)
'''
    child = subprocess.run([sys.executable, '-c', program, str(target), stage], capture_output=True, timeout=10)
    assert child.returncode == 73, child.stderr.decode()
    recovery = tmp_path / '.letracode-recovery' / target.name
    old = list(recovery.glob('*.before'))
    assert len(old) == 1 and old[0].read_bytes() == b'original\r\n'
    expected_mode = 0o666 if fs.IS_WINDOWS else 0o755
    assert stat.S_IMODE(old[0].stat().st_mode) == expected_mode
    for read in (lambda: read_text(target), lambda: source_files.snapshot(target)):
        with pytest.raises(ValueError, match='recovery|recoverable') as error:
            read()
        assert str(recovery) in str(error.value)
        assert old[0].read_bytes() == b'original\r\n'
        assert not list(recovery.glob('*.done'))
        if stage == 'capture':
            assert not target.exists()
            assert any(path.read_bytes() == b'model edit\r\n' for path in recovery.glob('*.proposed'))
        else:
            assert target.read_bytes() == b'model edit\r\n'
            assert stat.S_IMODE(target.stat().st_mode) == expected_mode


@pytest.mark.parametrize('stage', ['already', 'staged', 'captured'])
def test_cancellation_before_publication_preserves_original(tmp_path, source_files, monkeypatch, stage):
    target = tmp_path / 'source.py'
    target.write_bytes(b'original\n')
    before = source_files.snapshot(target)
    cancel = threading.Event()
    if stage == 'already':
        cancel.set()
    move = guarded.rename_noreplace

    def interrupt(source_fd, source, destination_fd, destination):
        move(source_fd, source, destination_fd, destination)
        if ((stage == 'staged' and destination.endswith('.proposed')) or
                (stage == 'captured' and source == target.name)):
            cancel.set()

    monkeypatch.setattr(guarded, 'rename_noreplace', interrupt)
    with pytest.raises(InterruptedError, match='[Cc]ancel'):
        source_files.publish(target, b'model edit\n', before['sha256'], mode=before['mode'], cancel=cancel)
    assert source_files.snapshot(target)['raw'] == b'original\n'
    if stage == 'already':
        assert not (tmp_path / '.letracode-recovery').exists()


@pytest.mark.parametrize('unsafe', ['target-symlink', 'ancestor-symlink', 'hardlink', 'fifo'])
def test_unsafe_source_targets_are_rejected_without_modification(tmp_path, source_files, unsafe):
    folder = tmp_path / 'real'; folder.mkdir()
    target = folder / 'source.py'; target.write_bytes(b'original\n')
    selected = target
    if unsafe == 'target-symlink':
        selected = tmp_path / 'linked.py'; selected.symlink_to(target)
    elif unsafe == 'ancestor-symlink':
        linked = tmp_path / 'linked'; linked.symlink_to(folder, target_is_directory=True)
        selected = linked / target.name
    elif unsafe == 'hardlink':
        os.link(target, tmp_path / 'another.py')
    else:
        if fs.IS_WINDOWS:
            pytest.skip('Windows has no POSIX FIFO entries')
        selected = tmp_path / 'pipe'; os.mkfifo(selected)
    with pytest.raises((ValueError, OSError)):
        source_files.snapshot(selected)
    with pytest.raises((ValueError, OSError)):
        source_files.publish(selected, b'model edit\n', guarded.digest(b'original\n'))
    assert target.read_bytes() == b'original\n'


def test_snapshot_refuses_path_replaced_between_stat_and_open(tmp_path, source_files, monkeypatch):
    target = tmp_path / 'source.py'; target.write_bytes(b'original\n')
    real_open = fs.open

    def replace_then_open(path, flags, *args, **kwargs):
        if path == target.name and flags & fs.O_NONBLOCK:
            replacement = tmp_path / 'replacement.py'; replacement.write_bytes(b'external\n')
            os.replace(replacement, target)
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(fs, 'open', replace_then_open)
    with pytest.raises(ValueError, match='changed'):
        source_files.snapshot(target)
    assert target.read_bytes() == b'external\n'


def test_publish_does_not_report_external_followup_as_its_own_saved_revision(tmp_path, source_files, monkeypatch):
    target = tmp_path / 'source.py'; target.write_bytes(b'original\n')
    before = source_files.snapshot(target)
    write = source_files.safe_write

    def external_followup(path, *args, **kwargs):
        write(path, *args, **kwargs)
        target.write_bytes(b'external followup\n')

    monkeypatch.setattr(source_files, 'safe_write', external_followup)
    with pytest.raises(ValueError, match='changed|conflict'):
        source_files.publish(target, b'model edit\n', before['sha256'], mode=before['mode'])
    assert target.read_bytes() == b'external followup\n'


def test_create_source_uses_private_mode_and_preserves_exact_bytes(tmp_path, source_files):
    target = tmp_path / 'new.py'
    missing = source_files.snapshot(target, allow_missing=True)
    assert missing['raw'] is None and missing['sha256'] is None
    result = source_files.publish(target, b'\xef\xbb\xbf# created\r\n', None)
    assert result['raw'] == target.read_bytes() == b'\xef\xbb\xbf# created\r\n'
    # Windows reports ordinary writable bits; security is inherited through
    # ACLs. POSIX creation must retain the explicit private permission mode.
    expected_mode = 0o666 if fs.IS_WINDOWS else 0o600
    assert missing['mode'] == result['mode'] == stat.S_IMODE(target.stat().st_mode) == expected_mode


@pytest.mark.parametrize('raw', [b'not\x00text', b'not\xffutf8'])
def test_invalid_source_bytes_are_refused_without_creating_target(tmp_path, source_files, raw):
    target = tmp_path / 'new.py'
    with pytest.raises(ValueError, match='UTF-8'):
        source_files.publish(target, raw, None)
    assert not target.exists()
    assert not (tmp_path / '.letracode-recovery').exists()


@pytest.mark.parametrize('has_lock', [False, True])
def test_untrusted_adjacent_journal_cannot_create_source_during_read_or_save(tmp_path, source_files, has_lock):
    target = tmp_path / 'source.py'
    recovery = tmp_path / '.letracode-recovery' / target.name
    recovery.mkdir(parents=True)
    ident = 'a' * 32
    payload = b'forged source payload\n'
    (recovery / (ident + '.before')).write_bytes(payload)
    (recovery / (ident + '.json')).write_text(json.dumps({'before_sha256': guarded.digest(payload)}))
    if has_lock:
        (recovery / '.lock').touch()
    original_names = {path.name for path in recovery.iterdir()}
    for action in (lambda: source_files.snapshot(target, allow_missing=True),
                   lambda: read_text(target)):
        with pytest.raises(ValueError, match='recovery|recoverable'):
            action()
        assert not target.exists()
        assert (recovery / (ident + '.before')).read_bytes() == payload
        assert {path.name for path in recovery.iterdir()} == original_names
    with pytest.raises(ValueError, match='recovery|recoverable'):
        source_files.publish(target, b'authorized new source\n', None, mode=0o600)
    assert not target.exists()
    assert (recovery / (ident + '.before')).read_bytes() == payload
    assert not (recovery / (ident + '.done')).exists()


@pytest.mark.parametrize('action', ['read', 'publish'])
def test_busy_untrusted_source_lock_fails_promptly_without_mutation(tmp_path, source_files, action):
    target = tmp_path / 'source.py'; target.write_bytes(b'original\n')
    before = source_files.snapshot(target)
    recovery = tmp_path / '.letracode-recovery' / target.name
    recovery.mkdir(parents=True)
    program = r'''
import sys
from pathlib import Path
from letracode import source_files
target, action = Path(sys.argv[1]), sys.argv[2]
try:
    if action == 'read':
        source_files.snapshot(target)
    else:
        source_files.publish(target, b'new\n', sys.argv[3], mode=int(sys.argv[4]))
except (OSError, ValueError) as error:
    print(str(error))
else:
    sys.exit('unexpected success')
'''
    with (recovery / '.lock').open('wb') as held:
        fs.flock(held.fileno(), fs.LOCK_EX)
        child = subprocess.run([sys.executable, '-c', program, str(target), action,
                                before['sha256'], str(before['mode'])], capture_output=True, timeout=5)
        assert child.returncode == 0, child.stderr.decode()
        assert 'busy' in child.stdout.decode().lower()
    assert target.read_bytes() == b'original\n'
    assert {path.name for path in recovery.iterdir()} == {'.lock'}
