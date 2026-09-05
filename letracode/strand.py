"""Authoritative, ordinary Strand files with guarded writes and durable Undo.

No method grants permission: callers must resolve scope and obtain approval first.
All paths are app-owned; no-follow directory descriptors prevent link redirection.
"""
from __future__ import annotations

import hashlib
import ctypes
import fcntl
import json
import os
import re
import stat
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

MAX_FILE_BYTES = 2 * 1024 * 1024
# JSON escaping can expand a supported memory file by up to six times.
MAX_RECEIPT_BYTES = 16 * 1024 * 1024


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def rename_noreplace(src_fd, src, dst_fd, dst):
    """Linux atomic move that NEVER replaces a name created by another writer."""
    libc = ctypes.CDLL(None, use_errno=True)
    rename = getattr(libc, 'renameat2', None)
    if rename is None:
        raise OSError('Safe Strand saves require Linux renameat2 support')
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(src_fd, os.fsencode(src), dst_fd, os.fsencode(dst), 1):  # RENAME_NOREPLACE
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), dst)


@contextmanager
def safe_directory(path: Path, create=False):
    """Open every ancestor without following symlinks, including the data root."""
    path = Path(path).absolute()
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for name in path.parts[1:]:
            if name in ('.', '..'):
                raise ValueError('Unsafe directory component')
            if create:
                try:
                    os.mkdir(name, 0o700, dir_fd=fd)
                    os.fsync(fd)
                except FileExistsError:
                    pass
            try:
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            except OSError as error:
                raise ValueError(f'Unsafe or missing directory (links are refused): {path}') from error
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


def _read_at(fd, name, max_bytes=MAX_FILE_BYTES):
    try:
        info = os.stat(name, dir_fd=fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError(f'Unsafe file or linked target: {name}')
    if info.st_size > max_bytes:
        raise ValueError(f'File is too large (size limit {max_bytes} bytes): {name}')
    stream = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    with os.fdopen(stream, 'rb') as incoming:
        opened = os.fstat(incoming.fileno())
        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
            raise ValueError(f'Unsafe file or linked target: {name}')
        if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
            raise ValueError(f'File changed while opening: {name}')
        if opened.st_size > max_bytes:
            raise ValueError(f'File is too large (size limit {max_bytes} bytes): {name}')
        data = incoming.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise ValueError(f'File is too large (size limit {max_bytes} bytes): {name}')
        after = os.fstat(incoming.fileno())
        if (opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise ValueError(f'File changed while reading: {name}')
    return data


def _write_new(fd, name, data):
    # Publish control records only when complete: a killed process must not
    # leave a partial final journal or completion marker that poisons recovery.
    staging = '.strand-stage-' + uuid.uuid4().hex
    try:
        out_fd = os.open(staging, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
        with os.fdopen(out_fd, 'wb') as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        rename_noreplace(fd, staging, fd, name)
        os.fsync(fd)
    finally:
        try:
            os.unlink(staging, dir_fd=fd)
        except FileNotFoundError:
            pass


@contextmanager
def _recovery_directory(path, create=False):
    # A separate directory per target isolates a damaged project's recovery data.
    recovery = path.parent / '.strand-recovery' / path.name
    if not create:
        with safe_directory(path.parent) as parent:
            try:
                os.stat('.strand-recovery', dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                yield None
                return
        with safe_directory(recovery.parent) as parent:
            try:
                os.stat(path.name, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                yield None
                return
    with safe_directory(recovery, create=create) as fd:
        lock = os.open('.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=fd)
        try:
            info = os.fstat(lock)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError('Unsafe Strand recovery lock')
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield fd
        finally:
            os.close(lock)


def _finish_recovery(fd, ident, status, before_hash):
    _write_new(fd, ident + '.done', json.dumps({'status': status, 'before_sha256': before_hash}).encode())


def _check_recovery(path, parent, recovery, max_bytes):
    """Recover interrupted moves; detect later edits through a displaced open FD.

    Actual old inodes are retained, not copied then unlinked. A writer holding an
    old descriptor can otherwise silently lose a save even after our return.
    """
    if recovery is None:
        return
    location = path.parent / '.strand-recovery' / path.name
    for name in sorted(os.listdir(recovery)):
        if not re.fullmatch(r'[a-f0-9]{32}\.json', name):
            continue
        ident = name[:-5]
        record = json.loads(_read_at(recovery, name, 4096))
        done = _read_at(recovery, ident + '.done', 4096)
        try:
            old = _read_at(recovery, ident + '.before', max_bytes)
        except (OSError, ValueError) as error:
            raise ValueError(f'Memory conflict: cannot verify preserved external file '
                             f'{location / (ident + ".before")}: {error}') from error
        if done is None:
            # A crash may leave the name absent. Restore first; ensure() must
            # never replace the missing file with an empty default in this case.
            if old is not None:
                try:
                    rename_noreplace(recovery, ident + '.before', parent, path.name)
                    os.fsync(parent)
                    os.fsync(recovery)
                    old = None
                except FileExistsError:
                    pass
            proposal = _read_at(recovery, ident + '.proposed', max_bytes)
            status = 'saved' if old is not None and proposal is None else 'aborted'
            _finish_recovery(recovery, ident, status, record['before_sha256'])
            done = _read_at(recovery, ident + '.done', 4096)
        finished = json.loads(done)
        if old is not None and digest(old) != finished['before_sha256']:
            raise ValueError(f'Memory conflict: external edit preserved at {location / (ident + ".before")}. '
                             f'Compare with {path}; reconcile both files before removing recovery record {location / name}.')
        if finished['status'] == 'saved' and old is None:
            raise ValueError(f'Memory conflict: saved recovery file is missing in {location}')


def safe_read(path: Path, max_bytes=MAX_FILE_BYTES):
    with safe_directory(path.parent) as fd, _recovery_directory(path) as recovery:
        _check_recovery(path, fd, recovery, max_bytes)
        return _read_at(fd, path.name, max_bytes)


def recover_file(path: Path):
    """Resolve interrupted saves before archiving ownership of a memory path."""
    with safe_directory(path.parent) as fd, _recovery_directory(path) as recovery:
        _check_recovery(path, fd, recovery, MAX_FILE_BYTES)


def safe_write(path: Path, data: bytes, expected_sha256: str | None, *, max_bytes=MAX_FILE_BYTES):
    """Preserve the displaced inode, validate it, then publish without replacement.

    Advisory locks cannot coordinate ordinary editors. Every move uses Linux
    NOREPLACE, including restoration; a competing pathname is never overwritten.
    The brief absent-name interval is recoverable from the durable journal.
    """
    if len(data) > max_bytes:
        raise ValueError(f'File is too large (size limit {max_bytes} bytes): {path}')
    with safe_directory(path.parent) as fd, _recovery_directory(path, create=True) as recovery:
        _check_recovery(path, fd, recovery, max_bytes)
        original = _read_at(fd, path.name, max_bytes)
        current_hash = None if original is None else digest(original)
        if current_hash != expected_sha256:
            raise ValueError(f'File changed; reload to resolve the conflict: {path}')
        ident = uuid.uuid4().hex
        temporary = ident + '.proposed'
        previous = ident + '.before'
        captured = False
        published = False
        try:
            _write_new(recovery, temporary, data)
            # Reopen the ancestor chain and compare directory identity before commit.
            with safe_directory(path.parent) as fresh:
                a, b = os.fstat(fd), os.fstat(fresh)
                if (a.st_dev, a.st_ino) != (b.st_dev, b.st_ino):
                    raise ValueError('Directory changed during write')
            if original is not None:
                _write_new(recovery, ident + '.json', json.dumps({'before_sha256': expected_sha256}).encode())
                rename_noreplace(fd, path.name, recovery, previous)
                captured = True
                os.fsync(fd)
                os.fsync(recovery)
                if _read_at(recovery, previous, max_bytes) != original:
                    raise ValueError(f'File changed; reload to resolve the conflict: {path}')
            try:
                rename_noreplace(recovery, temporary, fd, path.name)
            except FileExistsError as error:
                raise ValueError(f'File changed; reload to resolve the conflict: {path}') from error
            published = True
            os.fsync(fd)
            os.fsync(recovery)
            if captured:
                _finish_recovery(recovery, ident, 'saved', expected_sha256)
                _check_recovery(path, fd, recovery, max_bytes)
        except Exception as error:
            # Restore only into an absent name, preserving any later external
            # creation. Never delete a captured inode: editors may still own it.
            if captured and not published:
                try:
                    rename_noreplace(recovery, previous, fd, path.name)
                    os.fsync(fd)
                    os.fsync(recovery)
                except FileExistsError:
                    location = path.parent / '.strand-recovery' / path.name / previous
                    raise ValueError(f'File changed; conflict versions retained at {path} and {location}') from error
                except OSError as restore_error:
                    location = path.parent / '.strand-recovery' / path.name / previous
                    raise OSError(f'Save failed; original is recoverable at {location}: {restore_error}') from error
            raise
        finally:
            # Unpublished proposals are useful for crash/conflict recovery. Only
            # create-only attempts have no journal and can discard their staging.
            try:
                if original is None:
                    os.unlink(temporary, dir_fd=recovery)
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
            fd = os.open('.write-lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                         0o600, dir_fd=directory)
            try:
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise ValueError('Unsafe linked Strand write lock')
                fcntl.flock(fd, fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

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
                ident=None, date=None, undo_of=None):
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
                   'after_sha256': digest(text.encode('utf-8')), 'status': 'prepared'}
        if undo_of:
            receipt['undo_of'] = undo_of
        safe_write(backup, before['text'].encode('utf-8'), None)
        prepared = json.dumps(receipt, ensure_ascii=False, indent=2).encode('utf-8')
        safe_write(receipt_path, prepared, None, max_bytes=MAX_RECEIPT_BYTES)
        safe_write(path, text.encode('utf-8'), expected_sha256)
        receipt['status'] = 'saved'
        try:
            safe_write(receipt_path, json.dumps(receipt, ensure_ascii=False, indent=2).encode('utf-8'),
                       digest(prepared), max_bytes=MAX_RECEIPT_BYTES)
        except OSError:
            # The prepared receipt is durable before the file changes. It can be
            # recovered by comparing the committed hash after a disk failure.
            return self.receipt(ident)
        return receipt

    def receipt(self, receipt_id):
        if not isinstance(receipt_id, str) or not re.fullmatch(r'[a-f0-9]{32}', receipt_id):
            raise ValueError('Invalid receipt ID')
        raw = safe_read(self.root / '.receipts' / f'{receipt_id}.json', MAX_RECEIPT_BYTES)
        if raw is None:
            raise ValueError('Receipt not found')
        record = json.loads(raw)
        path = self.path(record['scope'], record.get('project_id'))
        if record['id'] != receipt_id or record['relative_path'] != path.relative_to(self.root).as_posix():
            raise ValueError('Invalid receipt destination')
        record['path'] = str(path)
        if record['status'] == 'prepared':
            try:
                current = self.snapshot(record['scope'], record.get('project_id'))['sha256']
                record['status'] = 'saved' if current == record['after_sha256'] else 'unconfirmed'
            except (ValueError, OSError):
                record['status'] = 'unconfirmed'
        return record

    def receipts(self, limit=50):
        if not isinstance(limit, int) or limit < 1 or limit > 1000:
            raise ValueError('Invalid receipt limit')
        with safe_directory(self.root / '.receipts') as fd:
            ids = [name[:-5] for name in os.listdir(fd) if re.fullmatch(r'[a-f0-9]{32}\.json', name)]
        records = [self.receipt(ident) for ident in ids]
        return sorted(records, key=lambda row: (row['date'], row['id']), reverse=True)[:limit]

    def undo(self, receipt_id):
        with self._operation():
            record = self.receipt(receipt_id)
            if record['status'] != 'saved':
                raise ValueError('Unconfirmed write cannot be undone')
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

    def backup_entries(self):
        """Yield verified bytes, retaining ordinary future notes/manifests, excluding weights."""
        def walk(directory):
            with safe_directory(directory) as fd:
                for name in sorted(os.listdir(fd)):
                    info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                    path = directory / name
                    if stat.S_ISDIR(info.st_mode):
                        yield from walk(path)
                    elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                        if path.suffix.lower() != '.gguf' and name not in ('.write-lock', '.lock') and not name.startswith('.strand-'):
                            limit = MAX_RECEIPT_BYTES if self.root / '.receipts' in path.parents else MAX_FILE_BYTES
                            data = safe_read(path, limit)
                            if data is None:
                                raise ValueError('Strand file disappeared during backup')
                            yield path.relative_to(self.root).as_posix(), data
                    else:
                        raise ValueError(f'Unsafe linked file in Strand backup: {path}')
        with self._operation():
            yield from walk(self.root)
