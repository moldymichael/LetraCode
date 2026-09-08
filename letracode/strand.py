"""Authoritative, ordinary Strand files with guarded writes and durable Undo.

No method grants permission: callers must resolve scope and obtain approval first.
All paths are app-owned; no-follow directory descriptors prevent link redirection.
"""
from __future__ import annotations

import hashlib
import json
import os

from . import filesystem as fs
import re
import stat
import threading
import uuid
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path

MAX_FILE_BYTES = 2 * 1024 * 1024
# JSON escaping can expand a supported memory file by up to six times.
MAX_RECEIPT_BYTES = 16 * 1024 * 1024
BACKUP_CHUNK_BYTES = 128 * 1024


def backup_tree(root):
    """Yield (relative name, open binary stream) for opaque retained files.

    Consume each stream before advancing. Descriptors stay anchored to checked
    directories; final identity and mutation checks run when iteration resumes.
    This copies recovery evidence without parsing it or replaying recovery.
    Callers serialize cooperating writers and publish only after exhaustion.
    """
    def identity(info):
        return info.st_dev, info.st_ino

    def version(info):
        return (info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_mode, info.st_nlink)

    def walk(directory, prefix=''):
        before = fs.fstat(directory)
        for name in sorted(fs.listdir(directory)):
            relative = prefix + name
            info = fs.stat(name, dir_fd=directory, follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode):
                child = fs.open(name, os.O_RDONLY | fs.O_DIRECTORY | fs.O_NOFOLLOW, dir_fd=directory)
                try:
                    if identity(fs.fstat(child)) != identity(info):
                        raise ValueError(f'Directory changed during backup: {relative}')
                    yield from walk(child, relative + '/')
                    if identity(fs.stat(name, dir_fd=directory, follow_symlinks=False)) != identity(info):
                        raise ValueError(f'Directory changed during backup: {relative}')
                finally:
                    fs.close(child)
            elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                if Path(name).suffix.lower() == '.gguf' or name in ('.write-lock', '.lock') or name.startswith('.strand-'):
                    continue
                fd = fs.open(name, os.O_RDONLY | fs.O_NOFOLLOW | fs.O_NONBLOCK, dir_fd=directory)
                with os.fdopen(fd, 'rb') as incoming:
                    opened = fs.fstat(incoming.fileno())
                    if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
                            or identity(opened) != identity(info) or version(opened) != version(info)):
                        raise ValueError(f'File changed while opening backup: {relative}')
                    yield relative, incoming
                    after = fs.fstat(incoming.fileno())
                    named = fs.stat(name, dir_fd=directory, follow_symlinks=False)
                    if (version(after) != version(opened) or identity(named) != identity(opened)
                            or version(named) != version(opened)):
                        raise ValueError(f'File changed while reading backup: {relative}')
            else:
                raise ValueError(f'Unsafe linked file in backup: {root / relative}')
        if version(fs.fstat(directory)) != version(before):
            raise ValueError(f'Directory changed during backup: {root / prefix}')

    with safe_directory(root) as directory:
        yield from walk(directory)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# Keep these entry points stable for callers and fault-injection tests.
def rename_noreplace(src_fd, src, dst_fd, dst, **options):
    return fs.rename_noreplace(src_fd, src, dst_fd, dst, **options)


safe_directory = fs.safe_directory


def _read_at(fd, name, max_bytes=MAX_FILE_BYTES, *, with_stat=False):
    try:
        info = fs.stat(name, dir_fd=fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError(f'Unsafe file or linked target: {name}')
    if info.st_size > max_bytes:
        raise ValueError(f'File is too large (size limit {max_bytes} bytes): {name}')
    stream = fs.open(name, os.O_RDONLY | fs.O_NOFOLLOW | fs.O_NONBLOCK, dir_fd=fd)
    with os.fdopen(stream, 'rb') as incoming:
        opened = fs.fstat(incoming.fileno())
        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
            raise ValueError(f'Unsafe file or linked target: {name}')
        if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
            raise ValueError(f'File changed while opening: {name}')
        if opened.st_size > max_bytes:
            raise ValueError(f'File is too large (size limit {max_bytes} bytes): {name}')
        data = incoming.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise ValueError(f'File is too large (size limit {max_bytes} bytes): {name}')
        after = fs.fstat(incoming.fileno())
        if (opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns, opened.st_mode) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns, after.st_mode):
            raise ValueError(f'File changed while reading: {name}')
    return (data, opened) if with_stat else data


def _write_new(fd, name, data, *, mode=None):
    # Publish control records only when complete: a killed process must not
    # leave a partial final journal or completion marker that poisons recovery.
    staging = '.strand-stage-' + uuid.uuid4().hex
    try:
        out_fd = fs.open(staging, os.O_WRONLY | os.O_CREAT | os.O_EXCL | fs.O_NOFOLLOW, 0o600, dir_fd=fd)
        with os.fdopen(out_fd, 'wb') as out:
            out.write(data)
            out.flush()
            if mode is not None:
                fs.fchmod(out.fileno(), mode)
            fs.fsync(out.fileno())
        rename_noreplace(fd, staging, fd, name)
        fs.fsync(fd)
    finally:
        try:
            fs.unlink(staging, dir_fd=fd)
        except FileNotFoundError:
            pass


@contextmanager
def _recovery_directory(path, create=False, *, namespace='.strand-recovery'):
    # A separate directory per target isolates a damaged project's recovery data.
    if namespace not in ('.strand-recovery', '.letracode-recovery'):
        raise ValueError('Invalid recovery namespace')
    recovery = path.parent / namespace / path.name
    if not create:
        with safe_directory(path.parent) as parent:
            try:
                fs.stat(namespace, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                yield None
                return
        with safe_directory(recovery.parent) as parent:
            try:
                fs.stat(path.name, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                yield None
                return
    with safe_directory(recovery, create=create) as fd:
        flags = os.O_RDWR | fs.O_NOFOLLOW | fs.O_NONBLOCK
        if create or namespace == '.strand-recovery':
            flags |= os.O_CREAT
        try:
            lock = fs.open('.lock', flags, 0o600, dir_fd=fd)
        except FileNotFoundError:
            # Reading untrusted source recovery metadata does not authorize
            # creating even its control files in the linked repository.
            raise ValueError(f'Source recovery requires explicit reconciliation: missing lock in {recovery}') from None
        try:
            info = fs.fstat(lock)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError('Unsafe Strand recovery lock')
            try:
                operation = fs.LOCK_EX | (fs.LOCK_NB if namespace == '.letracode-recovery' else 0)
                fs.flock(lock, operation)
            except BlockingIOError:
                raise ValueError(f'Source recovery is busy; retry after the other operation finishes: {recovery}') from None
            yield fd
        finally:
            fs.close(lock)


def _finish_recovery(fd, ident, status, before_hash):
    _write_new(fd, ident + '.done', json.dumps({'status': status, 'before_sha256': before_hash}).encode())


def _check_recovery(path, parent, recovery, max_bytes, *, namespace='.strand-recovery'):
    """Recover interrupted moves; detect later edits through a displaced open FD.

    Actual old inodes are retained, not copied then unlinked. A writer holding an
    old descriptor can otherwise silently lose a save even after our return.
    """
    if recovery is None:
        return
    location = path.parent / namespace / path.name
    kind = 'Memory' if namespace == '.strand-recovery' else 'Source file'
    for name in sorted(fs.listdir(recovery)):
        if not re.fullmatch(r'[a-f0-9]{32}\.json', name):
            continue
        ident = name[:-5]
        record = json.loads(_read_at(recovery, name, 4096))
        done = _read_at(recovery, ident + '.done', 4096)
        try:
            old = _read_at(recovery, ident + '.before', max_bytes)
        except (OSError, ValueError) as error:
            raise ValueError(f'{kind} conflict: cannot verify preserved external file '
                             f'{location / (ident + ".before")}: {error}') from error
        if done is None:
            if namespace == '.letracode-recovery':
                # Linked repositories are untrusted data. A plausible adjacent
                # journal is not authority to create or replace a source file.
                raise ValueError(f'Source recovery requires explicit reconciliation: inspect {path}, '
                                 f'{location / (ident + ".before")} and {location / (ident + ".proposed")}. '
                                 f'Keep recoverable versions before removing recovery record {location / name}.')
            # A crash may leave the name absent. Restore first; ensure() must
            # never replace the missing file with an empty default in this case.
            if old is not None:
                try:
                    rename_noreplace(recovery, ident + '.before', parent, path.name)
                    fs.fsync(parent)
                    fs.fsync(recovery)
                    old = None
                except FileExistsError:
                    pass
            proposal = _read_at(recovery, ident + '.proposed', max_bytes)
            status = 'saved' if old is not None and proposal is None else 'aborted'
            _finish_recovery(recovery, ident, status, record['before_sha256'])
            done = _read_at(recovery, ident + '.done', 4096)
        finished = json.loads(done)
        if old is not None and digest(old) != finished['before_sha256']:
            raise ValueError(f'{kind} conflict: external edit preserved at {location / (ident + ".before")}. '
                             f'Compare with {path}; reconcile both files before removing recovery record {location / name}.')
        if finished['status'] == 'saved' and old is None:
            raise ValueError(f'{kind} conflict: saved recovery file is missing in {location}')


def safe_read(path: Path, max_bytes=MAX_FILE_BYTES):
    with safe_directory(path.parent) as fd, _recovery_directory(path) as recovery:
        _check_recovery(path, fd, recovery, max_bytes)
        return _read_at(fd, path.name, max_bytes)


def safe_snapshot(path: Path, max_bytes=MAX_FILE_BYTES, *, namespace='.strand-recovery'):
    """Read bytes and metadata from one descriptor after checking recovery."""
    with safe_directory(path.parent) as fd, _recovery_directory(path, namespace=namespace) as recovery:
        _check_recovery(path, fd, recovery, max_bytes, namespace=namespace)
        return _read_at(fd, path.name, max_bytes, with_stat=True)


def recover_file(path: Path):
    """Resolve interrupted saves before archiving ownership of a memory path."""
    with safe_directory(path.parent) as fd, _recovery_directory(path) as recovery:
        _check_recovery(path, fd, recovery, MAX_FILE_BYTES)


def safe_write(path: Path, data: bytes, expected_sha256: str | None, *, max_bytes=MAX_FILE_BYTES,
               transaction_id=None, namespace='.strand-recovery', mode=None, cancel=None, expected_entry_identity=None):
    """Preserve the displaced inode, validate it, then publish without replacement.

    Advisory locks cannot coordinate ordinary editors. Every move uses atomic
    NOREPLACE, including restoration; a competing pathname is never overwritten.
    The brief absent-name interval is recoverable from the durable journal.
    """
    if len(data) > max_bytes:
        raise ValueError(f'File is too large (size limit {max_bytes} bytes): {path}')
    if transaction_id is not None and (not isinstance(transaction_id, str) or not re.fullmatch(r'[a-f0-9]{32}', transaction_id)):
        raise ValueError('Invalid write transaction ID')
    if mode is not None and (type(mode) is not int or not 0 <= mode <= 0o7777):
        raise ValueError('Invalid file mode')
    if fs.IS_WINDOWS and mode is not None and not mode & 0o222:
        raise PermissionError('File is read-only; change its attribute before saving')

    def check_cancel():
        if cancel is not None and cancel.is_set():
            raise InterruptedError('Cancelled before saving.')

    check_cancel()
    with safe_directory(path.parent) as fd, _recovery_directory(path, create=True, namespace=namespace) as recovery:
        _check_recovery(path, fd, recovery, max_bytes, namespace=namespace)
        observed = _read_at(fd, path.name, max_bytes, with_stat=True)
        if fs.IS_WINDOWS and observed is not None and not observed[1].st_mode & 0o222:
            raise PermissionError('File is read-only; change its attribute before saving')
        if expected_entry_identity is not None and (observed is None or
                [observed[1].st_dev, observed[1].st_ino] != list(expected_entry_identity)):
            raise ValueError('File identity changed; reload to review the replacement before saving')
        original = None if observed is None else observed[0]
        current_hash = None if original is None else digest(original)
        if current_hash != expected_sha256:
            raise ValueError(f'File changed; reload to resolve the conflict: {path}')
        if mode is not None and observed is not None and stat.S_IMODE(observed[1].st_mode) != mode:
            raise ValueError(f'File mode changed; reload to resolve the conflict: {path}')
        ident = transaction_id or uuid.uuid4().hex
        temporary = ident + '.proposed'
        previous = ident + '.before'
        captured = False
        published = False
        try:
            _write_new(recovery, temporary, data, mode=mode)
            proposed_info = fs.stat(temporary, dir_fd=recovery, follow_symlinks=False)
            proposed_identity = [proposed_info.st_dev, proposed_info.st_ino]
            check_cancel()
            # Reopen the ancestor chain and compare directory identity before commit.
            with safe_directory(path.parent) as fresh:
                a, b = fs.fstat(fd), fs.fstat(fresh)
                if (a.st_dev, a.st_ino) != (b.st_dev, b.st_ino):
                    raise ValueError('Directory changed during write')
            if original is not None:
                _write_new(recovery, ident + '.json', json.dumps({'before_sha256': expected_sha256}).encode())
                rename_noreplace(fd, path.name, recovery, previous)
                captured = True
                fs.fsync(fd)
                fs.fsync(recovery)
                captured_snapshot = _read_at(recovery, previous, max_bytes, with_stat=True)
                if (captured_snapshot is None or captured_snapshot[0] != original or
                        (expected_entry_identity is not None and [captured_snapshot[1].st_dev, captured_snapshot[1].st_ino] != list(expected_entry_identity)) or
                        (mode is not None and stat.S_IMODE(captured_snapshot[1].st_mode) != mode)):
                    raise ValueError(f'File changed; reload to resolve the conflict: {path}')
            check_cancel()
            try:
                rename_noreplace(recovery, temporary, fd, path.name)
            except FileExistsError as error:
                raise ValueError(f'File changed; reload to resolve the conflict: {path}') from error
            published = True
            fs.fsync(fd)
            fs.fsync(recovery)
            if captured:
                _finish_recovery(recovery, ident, 'saved', expected_sha256)
                _check_recovery(path, fd, recovery, max_bytes, namespace=namespace)
            return proposed_identity
        except Exception as error:
            # Restore only into an absent name, preserving any later external
            # creation. Never delete a captured inode: editors may still own it.
            if captured and not published:
                try:
                    rename_noreplace(recovery, previous, fd, path.name)
                    fs.fsync(fd)
                    fs.fsync(recovery)
                    if namespace == '.letracode-recovery':
                        # This process owns this attempted save and has just
                        # restored the captured inode. Future source reads must
                        # not infer completion from an untrusted adjacent file.
                        _finish_recovery(recovery, ident, 'aborted', expected_sha256)
                except FileExistsError:
                    location = path.parent / namespace / path.name / previous
                    raise ValueError(f'File changed; conflict versions retained at {path} and {location}') from error
                except OSError as restore_error:
                    location = path.parent / namespace / path.name / previous
                    raise OSError(f'Save failed; original is recoverable at {location}: {restore_error}') from error
            raise
        finally:
            # Unpublished proposals are useful for crash/conflict recovery. Only
            # create-only attempts have no journal and can discard their staging.
            try:
                if original is None:
                    fs.unlink(temporary, dir_fd=recovery)
            except FileNotFoundError:
                pass


class StrandFiles:
    SCOPES = {
        'identity': 'identity/strand.md',
        'preferences': 'identity/preferences.md',
        'global': 'memory/global.md',
        'learning': 'learning/programming.md',
    }

    def __init__(self, root: Path):
        self.root = Path(root).absolute()
        self._lock = threading.RLock()
        for relative in ('', 'identity', 'memory', 'memory/projects', 'learning', '.history', '.receipts'):
            with safe_directory(self.root / relative, create=True):
                pass
        for scope in self.SCOPES:
            initial = (
                '# Strand\n\nYou are Strand, a general-purpose assistant in LetraCode.\n'
                'Respect the user’s creative agency: help them think, understand and revise; '
                'do not take over their writing unless asked.\n'
                'Memory is selected context, not proof of exhaustive reading or understanding.\n'
                if scope == 'identity' else ''
            )
            self.ensure(scope, text=initial)

    @contextmanager
    def _operation(self):
        """Serialize cooperating writers across Store instances and processes."""
        with self._lock, safe_directory(self.root) as directory:
            fd = fs.open('.write-lock', os.O_RDWR | os.O_CREAT | fs.O_NOFOLLOW | fs.O_NONBLOCK,
                         0o600, dir_fd=directory)
            try:
                info = fs.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise ValueError('Unsafe linked Strand write lock')
                fs.flock(fd, fs.LOCK_EX)
                try:
                    yield
                finally:
                    fs.flock(fd, fs.LOCK_UN)
            finally:
                fs.close(fd)

    def path(self, scope, project_id=None):
        if scope == 'project':
            if not isinstance(project_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', project_id):
                raise ValueError('Invalid project ID')
            relative = f'memory/projects/{project_id}.md'
        else:
            if scope not in self.SCOPES or project_id is not None:
                raise ValueError('Invalid memory scope or project ID')
            relative = self.SCOPES[scope]
        return self.root / relative

    def ensure(self, scope, project_id=None, text=''):
        path = self.path(scope, project_id)
        with self._operation():
            if safe_read(path) is None:
                safe_write(path, text.encode('utf-8'), None)
        return path

    def snapshot(self, scope, project_id=None):
        path = self.path(scope, project_id)
        data = safe_read(path)
        if data is None:
            raise ValueError(f'Memory file is missing: {path}')
        return {'text': data.decode('utf-8'), 'sha256': digest(data), 'path': str(path)}

    def replace(self, scope, text, expected_sha256, project_id=None, origin='user'):
        if not isinstance(text, str) or not isinstance(expected_sha256, str):
            raise ValueError('Text and an expected snapshot hash are required')
        with self._operation():
            return self._change(scope, text, expected_sha256, project_id, origin, text)

    def remember(self, scope, text, project_id=None, origin='', *, expected_sha256):
        if scope not in ('global', 'project', 'learning'):
            raise ValueError('Remember only writes global, project or learning memory')
        if not isinstance(text, str) or not text.strip():
            raise ValueError('Memory text must not be empty')
        with self._operation():
            before = self.snapshot(scope, project_id)
            ident = uuid.uuid4().hex
            date = datetime.now(timezone.utc).isoformat(timespec='seconds')
            metadata = json.dumps({'id': ident, 'date': date, 'scope': scope,
                                   'project_id': project_id, 'origin': origin}, ensure_ascii=False)
            entry = f'<!-- Strand entry {metadata} -->\n{text}\n'
            updated = before['text'].rstrip() + ('\n\n' if before['text'].strip() else '') + entry
            return self._change(scope, updated, expected_sha256, project_id, origin, text, ident, date)

    def _change(self, scope, text, expected_sha256, project_id, origin, saved_text,
                ident=None, date=None, undo_of=None, expected_entry_identity=None):
        if len(text.encode('utf-8')) > MAX_FILE_BYTES:
            raise ValueError(f'Memory file is too large (size limit {MAX_FILE_BYTES} bytes)')
        before = self.snapshot(scope, project_id)
        if before['sha256'] != expected_sha256:
            raise ValueError('File changed; reload to resolve the conflict')
        ident = ident or uuid.uuid4().hex
        date = date or datetime.now(timezone.utc).isoformat(timespec='seconds')
        path = self.path(scope, project_id)
        backup = self.root / '.history' / f'{ident}.md'
        receipt_path = self.root / '.receipts' / f'{ident}.json'
        receipt = {'id': ident, 'receipt_id': ident, 'date': date, 'scope': scope,
                   'project_id': project_id, 'origin': origin, 'path': str(path),
                   'relative_path': path.relative_to(self.root).as_posix(),
                   'saved_text': saved_text, 'before_sha256': before['sha256'],
                   'after_sha256': digest(text.encode('utf-8')), 'status': 'prepared',
                   # The root operation lock serializes allocation across Store
                   # instances. Prepared failures also reserve their sequence.
                   'sequence': self._next_sequence(),
                   'write_id': ident}
        if undo_of:
            receipt['undo_of'] = undo_of
        safe_write(backup, before['text'].encode('utf-8'), None)
        prepared = json.dumps(receipt, ensure_ascii=False, indent=2).encode('utf-8')
        safe_write(receipt_path, prepared, None, max_bytes=MAX_RECEIPT_BYTES)
        options = {'expected_entry_identity': expected_entry_identity} if expected_entry_identity is not None else {}
        published_identity = safe_write(path, text.encode('utf-8'), expected_sha256, transaction_id=ident, **options)
        if published_identity is not None:
            receipt['entry_identity'] = published_identity
        receipt['status'] = 'saved'
        try:
            safe_write(receipt_path, json.dumps(receipt, ensure_ascii=False, indent=2).encode('utf-8'),
                       digest(prepared), max_bytes=MAX_RECEIPT_BYTES)
        except OSError:
            # The prepared receipt is durable before the file changes. It can be
            # recovered from its matching write journal after a disk failure.
            return self.receipt(ident)
        return receipt

    def _next_sequence(self):
        return max((row.get('sequence', 0) for row in self._receipt_records()), default=0) + 1

    def _receipt_destination(self, record):
        path = self.path(record['scope'], record.get('project_id'))
        if record['relative_path'] != path.relative_to(self.root).as_posix():
            raise ValueError('Invalid receipt destination')
        return path

    def _recover_receipt_file(self, record, path):
        recover_file(path)

    def receipt(self, receipt_id):
        if not isinstance(receipt_id, str) or not re.fullmatch(r'[a-f0-9]{32}', receipt_id):
            raise ValueError('Invalid receipt ID')
        receipt_path = self.root / '.receipts' / f'{receipt_id}.json'
        try:
            raw = safe_read(receipt_path, MAX_RECEIPT_BYTES)
            if raw is None:
                raise ValueError('Receipt not found')
            record = json.loads(raw)
            if not isinstance(record, dict):
                raise ValueError('Receipt must be an object')
            for key in ('scope', 'id', 'relative_path', 'date', 'origin', 'saved_text'):
                if not isinstance(record.get(key), str):
                    raise ValueError(f'Invalid or missing receipt {key}')
            path = self._receipt_destination(record)
            if record['id'] != receipt_id:
                raise ValueError('Invalid receipt destination')
            if record.get('status') not in ('prepared', 'saved'):
                raise ValueError('Invalid receipt status')
            for key in ('before_sha256', 'after_sha256'):
                if not isinstance(record.get(key), str) or not re.fullmatch(r'[a-f0-9]{64}', record[key]):
                    raise ValueError(f'Invalid receipt {key}')
            for key in ('write_id', 'undo_of'):
                if key in record and (not isinstance(record[key], str) or not re.fullmatch(r'[a-f0-9]{32}', record[key])):
                    raise ValueError(f'Invalid receipt {key}')
            if 'sequence' in record and (type(record['sequence']) is not int or record['sequence'] < 1):
                raise ValueError('Invalid receipt sequence')
            record['path'] = str(path)
        except (OSError, ValueError, TypeError, KeyError, RecursionError) as error:
            raise ValueError(f'Unresolved memory history at {receipt_path}: {error}. '
                             'Existing history files are preserved. Reconcile this record before saving or Undo.') from error
        if record['status'] == 'prepared':
            record['status'] = 'unconfirmed'
            try:
                # Matching bytes alone could be an external edit made after a
                # failed save. Only this receipt's actual write journal proves
                # publication. Older prepared receipts lack that proof.
                if record.get('write_id') == receipt_id:
                    self._recover_receipt_file(record, path)
                    journal = path.parent / '.strand-recovery' / path.name / (receipt_id + '.done')
                    raw_done = safe_read(journal, 4096)
                    if raw_done is not None:
                        done = json.loads(raw_done)
                        if done.get('status') == 'saved' and done.get('before_sha256') == record['before_sha256']:
                            record['status'] = 'saved'
            except (ValueError, OSError):
                pass
        return record

    def _receipt_records(self):
        with safe_directory(self.root / '.receipts') as fd:
            ids = [name[:-5] for name in fs.listdir(fd) if re.fullmatch(r'[a-f0-9]{32}\.json', name)]
        records = [self.receipt(ident) for ident in ids]
        sequences = {}
        for record in records:
            sequence = record.get('sequence')
            if sequence is None:  # Supported legacy records have no sequence.
                continue
            path = self.root / '.receipts' / (record['id'] + '.json')
            if sequence in sequences:
                raise ValueError(f'Duplicate receipt sequence {sequence}: {sequences[sequence]} and {path}. '
                                 'History is preserved; reconcile both records before saving or Undo.')
            sequences[sequence] = path
        return records

    @staticmethod
    def _legacy_order(records):
        """Only explicit Undo dependencies prove order in legacy history.

        Matching before/after hashes cannot establish chronology: an external
        edit can bridge unrelated saves. Dates and UUIDs are presentation only.
        """
        by_id = {row['id']: row for row in records}
        successors = {ident: set() for ident in by_id}
        for row in records:
            if row.get('undo_of') in by_id:
                successors[row['undo_of']].add(row['id'])
        remaining, ordered = set(by_id), []
        while remaining:
            tips = [ident for ident in remaining if not successors[ident] & remaining]
            if len(tips) != 1:
                break
            tip = tips[0]
            if not ordered:
                connected = {tip}
                while True:
                    previous = connected | {ident for ident in remaining if successors[ident] & connected}
                    if previous == connected:
                        break
                    connected = previous
                if connected != remaining:
                    break
            ordered.append(by_id[tip]); remaining.remove(tip)
        uncertain = [row for row in records if row['id'] in remaining]
        # Stable presentation only; uncertain entries never grant Undo authority.
        uncertain.sort(key=lambda row: (row['date'], row['id']), reverse=True)
        return ordered + uncertain, ordered[0]['id'] if ordered else None, remaining

    def _receipt_group_key(self, row):
        return row['relative_path']

    def _undo_history(self, records):
        groups = {}
        for row in records:
            groups.setdefault(self._receipt_group_key(row), []).append(row)
        for group in groups.values():
            saved = [row for row in group if row['status'] == 'saved']
            sequenced = sorted((row for row in saved if 'sequence' in row), key=lambda row: row['sequence'], reverse=True)
            legacy, legacy_head, uncertain = self._legacy_order([row for row in saved if 'sequence' not in row])
            head = sequenced[0]['id'] if sequenced else legacy_head
            if len(sequenced) > 1 and sequenced[0]['sequence'] == sequenced[1]['sequence']:
                head = None
            # An interrupted later write may have published even though its
            # receipt cannot prove completion. It must not expose an older save
            # as the head. A newer confirmed sequence establishes a new anchor.
            if any(row['status'] != 'saved' and
                   (not sequenced or row.get('sequence', 0) >= sequenced[0]['sequence'])
                   for row in group):
                head = None
            current, error = None, ''
            try:
                source = group[0]
                current = self.snapshot(source['scope'], source.get('project_id'))['sha256']
            except (ValueError, OSError) as unavailable:
                error = str(unavailable)
            rank = {row['id']: len(legacy) - index for index, row in enumerate(legacy)}
            for row in group:
                row['is_latest'] = row['id'] == head
                row['order_uncertain'] = row['id'] in uncertain or (head is None and bool(saved))
                row['_legacy_rank'] = rank.get(row['id'], 0)
                if row['status'] != 'saved':
                    row['undo_error'] = 'Unconfirmed write cannot be undone'
                elif head is None:
                    row['undo_error'] = 'Undo order is ambiguous in this history; no latest change can be established safely.'
                elif row['id'] != head:
                    row['undo_error'] = 'A later saved change exists; select the latest change for this file.'
                else:
                    row['undo_error'] = error or ('' if current == row['after_sha256'] else 'File changed; reload to resolve the conflict')
        records.sort(key=lambda row: (row['is_latest'], row.get('sequence', 0), row['_legacy_rank'], row['date'], row['id']), reverse=True)
        for row in records:
            row.pop('_legacy_rank')
        return records

    def receipts(self, limit=50, *, scope=None, project_id=None):
        if not isinstance(limit, int) or limit < 1 or limit > 1000:
            raise ValueError('Invalid receipt limit')
        if scope is None:
            if project_id is not None:
                raise ValueError('Project ID requires a project memory scope')
        else:
            if not isinstance(scope, str):
                raise ValueError('Invalid memory scope')
            self.path(scope, project_id)
        records = self._receipt_records()
        if scope is not None:
            records = [row for row in records if row['scope'] == scope and row.get('project_id') == project_id]
        return self._undo_history(records)[:limit]

    def undo(self, receipt_id):
        with self._operation():
            record = self.receipt(receipt_id)
            if record['status'] != 'saved':
                raise ValueError('Unconfirmed write cannot be undone')
            history = self._undo_history([row for row in self._receipt_records()
                if self._receipt_group_key(row) == self._receipt_group_key(record)])
            selected = next(row for row in history if row['id'] == receipt_id)
            if selected['undo_error']:
                raise ValueError(selected['undo_error'])
            backup = safe_read(self.root / '.history' / f'{receipt_id}.md')
            if backup is None or digest(backup) != record['before_sha256']:
                raise ValueError('Undo backup is missing or changed')
            return self._change(record['scope'], backup.decode('utf-8'), record['after_sha256'],
                                record.get('project_id'), 'user:undo', backup.decode('utf-8'), undo_of=receipt_id)

    def core(self):
        return '\n\n'.join(self.snapshot(scope)['text'] for scope in ('identity', 'preferences'))

    def read_page(self, scope, project_id=None, offset=0, max_chars=4000):
        if type(offset) is not int or offset < 0 or type(max_chars) is not int or not 1 <= max_chars <= 16000:
            raise ValueError('Invalid memory page bounds')
        source = self.snapshot(scope, project_id)
        content = source.pop('text')
        end = min(len(content), offset + max_chars)
        return {**source, 'scope': scope, 'project_id': project_id, 'text': content[offset:end],
                'offset': offset, 'next_offset': end if end < len(content) else None, 'total_chars': len(content)}

    def context(self, project_id, query, budget):
        """Select applicable excerpts; budget is characters, never a token claim."""
        if type(budget) is not int or budget < 0:
            raise ValueError('Invalid memory context budget')
        if budget == 0:
            return ''
        sources = [('global', None), ('learning', None)]
        if project_id is not None:
            sources.insert(1, ('project', project_id))
        notes = []
        unavailable = ''
        for scope, ident in sources:
            try:
                notes.append((scope, self.snapshot(scope, ident)['text']))
            except (ValueError, OSError):
                if scope != 'project':
                    raise
                unavailable = '[Project memory unavailable; do not assume its contents.]\n'
        unavailable = unavailable[:budget]
        budget -= len(unavailable)
        if budget == 0:
            return unavailable
        notes = [(scope, content) for scope, content in notes if content.strip()]
        if not notes:
            return unavailable
        full = '\n\n'.join(f'[{scope} memory]\n{content}' for scope, content in notes)
        if len(full) <= budget:
            return unavailable + full
        # Keep the coverage warning even for very small budgets.
        marker = '[Partial memory coverage; use read_memory for more.]\n'
        if budget <= len(marker):
            return unavailable + marker[:budget]
        terms = set(re.findall(r'\w+', query.lower()))
        excerpts = []
        allowance = max(0, (budget - len(marker) - 2 * len(notes)) // len(notes))
        for scope, content in notes:
            heading = f'[{scope} memory excerpt]\n'
            blocks = re.split(r'\n\s*\n', content)
            ranked = sorted(enumerate(blocks), key=lambda item: (-len(terms & set(re.findall(r'\w+', item[1].lower()))), item[0]))
            selected = '\n\n'.join(block for _, block in ranked)
            excerpts.append((heading + selected[:max(0, allowance - len(heading))])[:allowance])
        return unavailable + (marker + '\n\n'.join(excerpts))[:budget]

    @contextmanager
    def backup_entries(self):
        """Hold the Strand writer lock while the caller snapshots DB and files.

        The yielded iterator contains open streams, consumed one at a time.
        Lock order matches project deletion: Strand first, then SQLite.
        """
        with self._operation(), closing(backup_tree(self.root)) as entries:
            yield entries
