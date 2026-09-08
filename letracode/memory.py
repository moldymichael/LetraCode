"""Ordinary user-owned Memory trees, retaining guarded writes and recovery inodes.

The registry provides stable file identities and project ownership; text always
comes from the ordinary file. Hidden control files are never model-retrievable.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os

from . import filesystem as fs
import re
import stat
import threading
import uuid
from pathlib import Path, PurePosixPath
from datetime import datetime, timezone
from contextlib import contextmanager

from .strand import (StrandFiles, MAX_FILE_BYTES, MAX_RECEIPT_BYTES, backup_tree,
                     digest, safe_directory, safe_read, safe_snapshot, safe_write, recover_file,
                     rename_noreplace)


_UNREVIEWED_IDENTITY = object()


class MemoryFiles(StrandFiles):
    def __init__(self, root, *, legacy=False, initialize=True, migration_locked=False):
        self.root = Path(root).absolute()
        self._lock = threading.RLock()
        self._local = threading.local()
        self._local.inside_operation = migration_locked
        for relative in ('', '.history', '.receipts', '.operations', '.trash', '.projects'):
            with safe_directory(self.root / relative, create=True):
                pass
        self.registry_path = self.root / '.memory.json'
        with self._operation():
            if safe_read(self.registry_path, MAX_RECEIPT_BYTES) is None:
                if not initialize:
                    raise ValueError('Memory registry is missing; restore .memory.json from backup. Existing files were not changed.')
                self._initialize(legacy)
            if legacy:
                self._register_legacy_history()
            self._recover_operations()
            # Each relocation is independently journaled. A restart resumes
            # only a move proven by its retained inode; it never guesses by text.
            meta, _ = self._metadata()
            for ident, row in list(meta['files'].items()):
                if row.get('legacy_scope') == 'project' and not row.get('deleted'):
                    wanted = f".projects/{row['project_id']}/Memory.md"
                    if row['path'] == f"memory/projects/{row['project_id']}.md":
                        with safe_directory((self.root / wanted).parent, create=True):
                            pass
                        if os.path.lexists(self.root / row['path']):
                            self._relocate(row['path'], wanted, self._entry_digest(self.root / row['path']),
                                           row['project_id'], 'migration')
        self._local.inside_operation = False

    @contextmanager
    def _operation(self):
        if getattr(self._local, 'inside_operation', False):
            yield
            return
        with super()._operation():
            self._local.inside_operation = True
            try:
                yield
            finally:
                self._local.inside_operation = False

    @staticmethod
    def _project(project_id):
        if project_id is not None and (not isinstance(project_id, str) or
                not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', project_id)):
            raise ValueError('Invalid project ID')
        return project_id

    @staticmethod
    def _relative(relative, *, empty=False):
        if not isinstance(relative, str) or (not relative and not empty) or len(relative) > 4096:
            raise ValueError('A relative Memory path is required')
        if not relative and empty:
            return ''
        parts = relative.split('/')
        if ('\\' in relative or '\x00' in relative or ':' in relative or
                any(part in ('', '.', '..') or part.startswith('.') for part in parts)):
            raise ValueError('Unsafe Memory path; hidden control paths and traversal are refused')
        if PurePosixPath(relative).is_absolute():
            raise ValueError('Use a relative Memory path')
        if fs.IS_WINDOWS:
            for part in parts:
                fs.windows_component(part)
        return relative

    def root_for(self, project_id=None):
        self._project(project_id)
        return self.root if project_id is None else self.root / '.projects' / project_id

    def _storage(self, relative, project_id=None, *, empty=False):
        relative = self._relative(relative, empty=empty)
        self._project(project_id)
        if project_id is None and (relative == 'memory/projects' or relative.startswith('memory/projects/')):
            raise ValueError('Legacy project storage is outside shared Memory scope')
        return (f'.projects/{project_id}/' if project_id else '') + relative

    def _checked_storage(self, relative):
        # Registry/control records can name reserved project roots, but never
        # traversal or another location outside the Memory directory.
        if not isinstance(relative, str) or not relative or '\\' in relative or ':' in relative or '\x00' in relative:
            raise ValueError('Invalid registry path')
        parts = relative.split('/')
        if any(part in ('', '.', '..') for part in parts) or relative.startswith('/'):
            raise ValueError('Invalid registry path')
        if fs.IS_WINDOWS:
            for part in parts:
                fs.windows_component(part)
        return self.root / relative

    def _owned_path(self, relative, project_id, *, tree=False, retained=False):
        self._checked_storage(relative)
        self._project(project_id)
        if retained and re.fullmatch(r'\.trash/(?:(?:create|deleted-project)-)?[a-f0-9]{32}', relative):
            return
        if project_id is not None:
            prefix = f'.projects/{project_id}'
            if tree and relative == prefix:
                return
            if relative == f'memory/projects/{project_id}.md':
                return
            if not relative.startswith(prefix + '/'):
                raise ValueError('Memory path crosses project ownership')
            relative = relative[len(prefix) + 1:]
        self._relative(relative)
        if project_id is None and (relative == 'memory/projects' or relative.startswith('memory/projects/')):
            raise ValueError('Legacy project files are outside shared Memory')
        if not tree and Path(relative).suffix.lower() not in ('.md', '.txt', '.markdown'):
            raise ValueError('Registry file is not Markdown or text')

    def _validate_file_record(self, ident, row):
        if not isinstance(ident, str) or not re.fullmatch(r'[a-f0-9]{32}', ident) or not isinstance(row, dict):
            raise ValueError('Invalid Memory file identity')
        project_id = row.get('project_id')
        self._owned_path(row['path'], project_id)
        if any(type(row.get(key)) is not bool for key in ('always_active', 'deleted')):
            raise ValueError('Memory activation and deletion flags must be booleans')
        alias = row.get('legacy_scope')
        if alias is not None and (alias not in (*self.SCOPES, 'project') or
                (alias == 'project') != (project_id is not None)):
            raise ValueError('Invalid Memory legacy alias')
        for key in ('history_paths', 'recovery_paths'):
            paths = row.get(key)
            if not isinstance(paths, list) or any(not isinstance(path, str) for path in paths):
                raise ValueError('Invalid Memory history path list')
            if len(paths) != len(set(paths)):
                raise ValueError('Duplicate Memory history paths')
            for path in paths:
                self._owned_path(path, project_id)
        if row['path'] not in row['history_paths']:
            raise ValueError('Current Memory path is absent from history identity')

    def _validate_metadata(self, meta):
        if not isinstance(meta, dict) or type(meta.get('version')) is not int or meta['version'] != 1 or not isinstance(meta.get('files'), dict):
            raise ValueError('Unsupported registry format')
        aliases, destinations = set(), set()
        for ident, row in meta['files'].items():
            self._validate_file_record(ident, row)
            if row.get('legacy_scope') is not None:
                alias = (row['legacy_scope'], row.get('project_id'))
                if alias in aliases:
                    raise ValueError('Duplicate Memory legacy alias')
                aliases.add(alias)
            if not row['deleted']:
                destination = row['path'].casefold() if fs.IS_WINDOWS else row['path']
                if destination in destinations:
                    raise ValueError('Duplicate live Memory destination')
                destinations.add(destination)

    def _metadata(self):
        raw = safe_read(self.registry_path, MAX_RECEIPT_BYTES)
        try:
            meta = json.loads(raw)
            self._validate_metadata(meta)
            return meta, raw
        except (TypeError, KeyError, ValueError) as error:
            raise ValueError(f'Memory registry is unavailable at {self.registry_path}: {error}. Existing files are preserved.') from error

    def _save_metadata(self, meta, before):
        self._validate_metadata(meta)
        safe_write(self.registry_path, json.dumps(meta, ensure_ascii=False, indent=2).encode(),
                   None if before is None else digest(before), max_bytes=MAX_RECEIPT_BYTES)

    def _initialize(self, legacy):
        meta = {'version': 1, 'files': {}}
        for scope, relative in self.SCOPES.items():
            if not legacy and scope == 'identity':
                relative = 'identity/assistant.md'
            target = self.root / relative
            if not legacy:
                with safe_directory(target.parent, create=True):
                    pass
                if not os.path.lexists(target):
                    safe_write(target, b'', None)
            meta['files'][uuid.uuid4().hex] = {'path': relative, 'project_id': None,
                'always_active': bool(legacy and scope in ('identity', 'preferences')),
                'legacy_scope': scope, 'history_paths': [relative], 'recovery_paths': [],
                'deleted': not os.path.lexists(target)}
        old_projects = self.root / 'memory/projects'
        if os.path.lexists(old_projects):
            with safe_directory(old_projects) as folder:
                names = fs.listdir(folder)
            for name in names:
                if name.endswith('.md') and re.fullmatch(r'[A-Za-z0-9_-]{1,128}', name[:-3]):
                    relative = 'memory/projects/' + name
                    meta['files'][uuid.uuid4().hex] = {'path': relative, 'project_id': name[:-3],
                        'always_active': False, 'legacy_scope': 'project',
                        'history_paths': [relative], 'recovery_paths': [], 'deleted': False}
        self._save_metadata(meta, None)

    def _register_legacy_history(self):
        """Retain identities for legacy project receipts whose file is gone.

        Project deletion archived the note but kept receipts at their original
        relative destination. Discovery from live files alone misses that
        ownership. Also run on a prepared migration's existing registry, so a
        retry preserves the identities already allocated before the failure.
        """
        meta, raw = self._metadata()
        known = {row['project_id'] for row in meta['files'].values()
                 if row.get('legacy_scope') == 'project'}
        with safe_directory(self.root / '.receipts') as folder:
            names = sorted(name for name in fs.listdir(folder)
                           if re.fullmatch(r'[a-f0-9]{32}\.json', name))
        changed = False
        for name in names:
            try:
                record = json.loads(safe_read(self.root / '.receipts' / name, MAX_RECEIPT_BYTES))
            except (OSError, ValueError, TypeError, RecursionError):
                # Normal receipt validation below supplies the exact diagnostic;
                # invalid data cannot supply a new destination or escape scope.
                continue
            if not isinstance(record, dict) or record.get('scope') != 'project':
                continue
            project = record.get('project_id')
            if not isinstance(project, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', project):
                continue
            relative = f'memory/projects/{project}.md'
            if (record.get('relative_path') != relative or project in known or
                    os.path.lexists(self.root / relative)):
                continue
            meta['files'][uuid.uuid4().hex] = {'path': relative, 'project_id': project,
                'always_active': False, 'legacy_scope': 'project',
                'history_paths': [relative], 'recovery_paths': [], 'deleted': True}
            known.add(project)
            changed = True
        if changed:
            self._save_metadata(meta, raw)

    def _alias(self, scope, project_id=None):
        self._project(project_id)
        if (scope == 'project') != (project_id is not None) or scope not in (*self.SCOPES, 'project'):
            raise ValueError('Invalid memory scope or project ID')
        meta, _ = self._metadata()
        return next(((ident, row) for ident, row in meta['files'].items()
                     if row.get('legacy_scope') == scope and row.get('project_id') == project_id), (None, None))

    def path(self, scope, project_id=None):
        if isinstance(scope, str) and scope.startswith('file:'):
            ident = scope[5:]
            meta, _ = self._metadata(); row = meta['files'].get(ident)
            if row is None or row.get('project_id') != project_id:
                raise ValueError('Memory file is not in this scope')
        else:
            ident, row = self._alias(scope, project_id)
            if row is None:
                if scope == 'project':
                    return self.root_for(project_id) / 'Memory.md'
                raise ValueError('Memory alias unavailable')
        return self._checked_storage(row['path'])

    def ensure(self, scope, project_id=None, text=''):
        with self._operation():
            ident, row = self._alias(scope, project_id)
            if row is not None:
                # An existing missing/deleted file belongs to the user. Never
                # resurrect it with an empty default when opening a project.
                return self.path(scope, project_id)
            target = self.path(scope, project_id)
            with safe_directory(target.parent, create=True):
                pass
            safe_write(target, text.encode(), None)
            meta, raw = self._metadata()
            relative = target.relative_to(self.root).as_posix()
            meta['files'][uuid.uuid4().hex] = {'path': relative, 'project_id': project_id,
                'always_active': False, 'legacy_scope': scope, 'history_paths': [relative],
                'recovery_paths': [], 'deleted': False}
            self._save_metadata(meta, raw)
            return target

    def _lookup(self, relative, project_id=None, *, register=False):
        storage = self._storage(relative, project_id)
        meta, raw = self._metadata()
        found = [(ident, row) for ident, row in meta['files'].items()
                 if (row['path'].casefold() == storage.casefold() if fs.IS_WINDOWS else row['path'] == storage)
                 and row.get('project_id') == project_id and not row.get('deleted')]
        if len(found) > 1:
            raise ValueError('Conflicting Memory file identities')
        if found:
            return found[0]
        if not register:
            return None, None
        target = self._checked_storage(storage)
        if target.suffix.lower() not in ('.md', '.txt', '.markdown'):
            raise ValueError('Memory files must be Markdown or text (.md or .txt)')
        data = safe_read(target)
        if data is None:
            raise ValueError('Memory file is missing')
        data.decode('utf-8')
        ident = uuid.uuid4().hex
        row = {'path': storage, 'project_id': project_id, 'always_active': False,
               'history_paths': [storage], 'recovery_paths': [], 'deleted': False}
        meta['files'][ident] = row; self._save_metadata(meta, raw)
        return ident, row

    def alias_deleted(self, scope, project_id=None):
        _, row = self._alias(scope, project_id)
        return bool(row and row.get('deleted'))

    def legacy_scope_for(self, relative, project_id=None):
        storage = self._storage(relative, project_id)
        meta, _ = self._metadata()
        return next((row.get('legacy_scope') for row in meta['files'].values()
                     if row['path'] == storage and row.get('project_id') == project_id), None)

    def _check_old_recovery(self, row):
        for relative in row.get('recovery_paths', []):
            path = self._checked_storage(relative)
            if os.path.lexists(path.parent):
                recover_file(path)

    def snapshot(self, scope, project_id=None):
        path = self.path(scope, project_id)
        if isinstance(scope, str) and scope.startswith('file:'):
            meta, _ = self._metadata()
            row = meta['files'].get(scope[5:])
        else:
            _, row = self._alias(scope, project_id)
        if row and row.get('deleted'):
            raise ValueError(f'Memory file is deleted: {path}')
        if row:
            self._check_old_recovery(row)
        return super().snapshot(scope, project_id)

    def file_snapshot(self, relative, project_id=None):
        storage = self._storage(relative, project_id)
        ident, row = self._lookup(relative, project_id)
        if row:
            self._check_old_recovery(row)
        target = self._checked_storage(storage)
        if target.suffix.lower() not in ('.md', '.txt', '.markdown'):
            raise ValueError('Memory files must be Markdown or text (.md or .txt)')
        observed = safe_snapshot(target)
        if observed is None:
            raise ValueError(f'Memory file is missing: {target}')
        raw, info = observed
        return {'text': raw.decode('utf-8'), 'sha256': digest(raw), 'path': str(target),
                'entry_identity': [info.st_dev, info.st_ino],
                'relative_path': relative, 'always_active': bool(row and row.get('always_active')),
                'legacy_scope': row.get('legacy_scope') if row else None, 'file_id': ident,
                'revision': digest(json.dumps(row, sort_keys=True).encode()) if row else None}

    def replace_file(self, relative, text, expected_sha256, project_id=None, origin='user editor', *, expected_file_id=_UNREVIEWED_IDENTITY, expected_entry_identity=_UNREVIEWED_IDENTITY):
        if not isinstance(text, str) or not isinstance(expected_sha256, str):
            raise ValueError('Text and expected snapshot hash are required')
        with self._operation():
            self._check_reviewed_identity(relative, project_id, expected_file_id)
            self._check_entry_identity(self._checked_storage(self._storage(relative, project_id)), expected_entry_identity)
            ident, _ = self._lookup(relative, project_id, register=True)
            return self._change('file:' + ident, text, expected_sha256, project_id, origin, text,
                                expected_entry_identity=None if expected_entry_identity is _UNREVIEWED_IDENTITY else expected_entry_identity)

    def _check_reviewed_identity(self, relative, project_id, expected_file_id):
        if expected_file_id is _UNREVIEWED_IDENTITY:
            return
        if expected_file_id is not None and (not isinstance(expected_file_id, str) or
                not re.fullmatch(r'[a-f0-9]{32}', expected_file_id)):
            raise ValueError('Invalid reviewed Memory file identity')
        current_id, _ = self._lookup(relative, project_id)
        if current_id != expected_file_id:
            raise ValueError('Memory file identity changed; this pathname belongs to a different file. '
                             'Your draft was not saved. Reload and review the current file first.')

    def _check_entry_identity(self, path, expected_entry_identity):
        if expected_entry_identity is _UNREVIEWED_IDENTITY:
            return
        if (not isinstance(expected_entry_identity, (list, tuple)) or len(expected_entry_identity) != 2 or
                any(type(value) is not int or value < 0 for value in expected_entry_identity)):
            raise ValueError('Invalid reviewed Memory entry identity')
        with safe_directory(path.parent) as parent:
            info = fs.stat(path.name, dir_fd=parent, follow_symlinks=False)
        if [info.st_dev, info.st_ino] != list(expected_entry_identity):
            raise ValueError('Memory entry identity changed; reload and review the current file or folder first.')

    def _receipt_destination(self, record):
        path = self.path(record['scope'], record.get('project_id'))
        meta, _ = self._metadata()
        if record['scope'].startswith('file:'):
            row = meta['files'].get(record['scope'][5:])
        else:
            ident, row = self._alias(record['scope'], record.get('project_id'))
        if row is None or record['relative_path'] not in row.get('history_paths', [row['path']]):
            raise ValueError('Invalid receipt destination')
        record['relative_path'] = row['path']
        record['file_identity'] = record['scope'][5:] if record['scope'].startswith('file:') else ident
        return path

    def _receipt_group_key(self, row):
        return row.get('file_identity', row['relative_path'])

    def _recover_receipt_file(self, record, path):
        meta, _ = self._metadata()
        row = meta['files'][record['file_identity']]
        if not row['deleted']:
            super()._recover_receipt_file(record, path)

    def receipt(self, receipt_id):
        record = super().receipt(receipt_id)
        if record['status'] != 'unconfirmed' or record.get('write_id') != receipt_id:
            return record
        meta, _ = self._metadata()
        row = meta['files'].get(record.get('file_identity'))
        for old in (row or {}).get('recovery_paths', []):
            path = self._checked_storage(old)
            try:
                self._recover_receipt_file(record, path)
                journal = path.parent / '.strand-recovery' / path.name / (receipt_id + '.done')
                raw = safe_read(journal, 4096)
                done = json.loads(raw) if raw is not None else {}
                if done.get('status') == 'saved' and done.get('before_sha256') == record['before_sha256']:
                    record['status'] = 'saved'
                    break
            except (OSError, ValueError):
                continue
        return record

    def _entry_digest(self, target):
        with safe_directory(target.parent) as parent:
            info = fs.stat(target.name, dir_fd=parent, follow_symlinks=False)
            if stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                stream = fs.open(target.name, os.O_RDONLY | fs.O_NOFOLLOW | fs.O_NONBLOCK, dir_fd=parent)
                with os.fdopen(stream, 'rb') as incoming:
                    opened = fs.fstat(incoming.fileno())
                    if not os.path.samestat(info, opened) or opened.st_nlink != 1 or not stat.S_ISREG(opened.st_mode):
                        raise ValueError('Memory file changed while opening')
                    result = hashlib.sha256()
                    for chunk in iter(lambda: incoming.read(128 * 1024), b''):
                        result.update(chunk)
                    after = fs.fstat(incoming.fileno())
                    named = fs.stat(target.name, dir_fd=parent, follow_symlinks=False)
                    if not os.path.samestat(opened, named) or (opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                        raise ValueError('Memory file changed during snapshot')
                    return result.hexdigest()
            if not stat.S_ISDIR(info.st_mode):
                raise ValueError('Unsafe linked Memory entry')
        result = hashlib.sha256()
        # Include empty directory names as well as opaque regular file bytes.
        def folders(path, prefix=''):
            with safe_directory(path) as fd:
                for name in sorted(fs.listdir(fd)):
                    child = fs.stat(name, dir_fd=fd, follow_symlinks=False)
                    if stat.S_ISDIR(child.st_mode):
                        result.update(('directory:' + prefix + name + '\0').encode())
                        folders(path / name, prefix + name + '/')
        folders(target)
        for name, incoming in backup_tree(target):
            result.update(('file:' + name + '\0').encode())
            for chunk in iter(lambda: incoming.read(128 * 1024), b''):
                result.update(chunk)
        return result.hexdigest()

    def snapshot_entry(self, relative, project_id=None):
        target = self._checked_storage(self._storage(relative, project_id))
        with safe_directory(target.parent) as fd:
            info = fs.stat(target.name, dir_fd=fd, follow_symlinks=False)
        kind = 'folder' if stat.S_ISDIR(info.st_mode) else 'file'
        if kind == 'file':
            return dict(self.file_snapshot(relative, project_id), kind=kind)
        token = self._entry_digest(target)
        self._check_entry_identity(target, [info.st_dev, info.st_ino])
        return {'path': str(target), 'relative_path': relative, 'kind': kind, 'sha256': token,
                'entry_identity': [info.st_dev, info.st_ino]}

    def entries(self, project_id=None):
        root = self.root_for(project_id)
        if not os.path.lexists(root):
            return []
        result = []
        def walk(folder, prefix=''):
            with safe_directory(folder) as fd:
                names = sorted(fs.listdir(fd), key=str.casefold)
                for name in names:
                    if name.startswith('.') or (project_id is None and prefix + name == 'memory/projects'):
                        continue
                    relative = prefix + name
                    info = fs.stat(name, dir_fd=fd, follow_symlinks=False)
                    if stat.S_ISDIR(info.st_mode):
                        result.append({'path': relative, 'kind': 'folder'})
                        walk(folder / name, relative + '/')
                    elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and Path(name).suffix.lower() in ('.md', '.txt', '.markdown'):
                        ident, row = self._lookup(relative, project_id)
                        result.append({'path': relative, 'kind': 'file',
                            'always_active': bool(row and row.get('always_active')),
                            'legacy_scope': row.get('legacy_scope') if row else None, 'file_id': ident})
                    else:
                        result.append({'path': relative, 'kind': 'unavailable', 'error': 'Unsupported or linked entry is preserved; not readable as Memory.'})
        walk(root)
        return result

    def read_file_page(self, relative, project_id=None, offset=0, max_chars=4000):
        if type(offset) is not int or offset < 0 or type(max_chars) is not int or not 1 <= max_chars <= 16000:
            raise ValueError('Invalid memory page bounds')
        snapshot = self.file_snapshot(relative, project_id)
        text = snapshot.pop('text'); end = min(len(text), offset + max_chars)
        return dict(snapshot, text=text[offset:end], offset=offset,
                    next_offset=end if end < len(text) else None, total_chars=len(text))

    def search(self, query, project_id=None, limit=20):
        if not isinstance(query, str) or not query.strip() or type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('A search query and bounded result limit are required')
        terms = set(re.findall(r'\w+', query.casefold()))
        result = []
        for entry in self.entries(project_id):
            if entry['kind'] != 'file':
                continue
            try:
                snapshot = self.file_snapshot(entry['path'], project_id)
            except (OSError, ValueError):
                continue
            for start in range(0, len(snapshot['text']), 3000):
                text = snapshot['text'][start:start + 4000]
                score = len(terms & set(re.findall(r'\w+', text.casefold())))
                if score:
                    result.append({'path': entry['path'], 'text': text, 'offset': start,
                                   'sha256': snapshot['sha256'], 'score': score})
        return sorted(result, key=lambda row: (-row['score'], row['path'], row['offset']))[:limit]

    def core(self, project_id=None):
        meta, _ = self._metadata(); result = []
        for ident, row in meta['files'].items():
            if row.get('always_active') and not row.get('deleted') and row.get('project_id') in (None, project_id):
                text = self.snapshot('file:' + ident, row.get('project_id'))['text']
                result.append(f"[Always-active Memory: {row['path']}]\n{text}")
        return '\n\n'.join(result)

    def context(self, project_id, query, budget):
        if type(budget) is not int or budget < 0:
            raise ValueError('Invalid memory context budget')
        # Explicit activation is the only automatic injection. Other files are
        # available through bounded listing/search/reading when relevant.
        return ''

    def _ordered_records(self):
        content = super()._receipt_records()
        operations = [row for row, raw in self._operation_records()]
        seen = {}
        for row in content + operations:
            sequence = row.get('sequence')
            if sequence is None:  # Legacy receipts retain their conservative ordering rules.
                continue
            if sequence in seen:
                raise ValueError(f'Duplicate Memory history sequence {sequence}: {seen[sequence]} and {row["id"]}. '
                                 'History is preserved; reconcile these records before saving or Undo.')
            seen[sequence] = row['id']
        return content + operations

    def _next_sequence(self):
        return max((row.get('sequence', 0) for row in self._ordered_records()), default=0) + 1

    def _operation_path(self, ident):
        if not isinstance(ident, str) or not re.fullmatch(r'[a-f0-9]{32}', ident):
            raise ValueError('Invalid Memory operation ID')
        return self.root / '.operations' / (ident + '.json')

    def _write_operation(self, record, before=None):
        path = self._operation_path(record['id'])
        safe_write(path, json.dumps(record, ensure_ascii=False, indent=2).encode(),
                   None if before is None else digest(before), max_bytes=MAX_RECEIPT_BYTES)

    def _operation_records(self):
        with safe_directory(self.root / '.operations') as fd:
            names = sorted(fs.listdir(fd))
        records, sequences = [], set()
        for name in names:
            if not re.fullmatch(r'[a-f0-9]{32}\.json', name):
                continue
            raw = safe_read(self.root / '.operations' / name, MAX_RECEIPT_BYTES)
            try:
                record = json.loads(raw)
                if not isinstance(record, dict) or record.get('id') != name[:-5] or record.get('status') not in ('prepared', 'saved', 'aborted'):
                    raise ValueError('Invalid operation journal')
                operation = record.get('operation')
                if operation not in ('active', 'create', 'move', 'delete', 'restore', 'migration'):
                    raise ValueError('Unknown Memory operation')
                sequence = record.get('sequence')
                if type(sequence) is not int or sequence < 1 or sequence in sequences:
                    raise ValueError('Invalid or duplicate Memory operation sequence')
                sequences.add(sequence)
                project_id = record.get('project_id')
                self._project(project_id)
                if not isinstance(record.get('sha256'), str) or not re.fullmatch(r'[a-f0-9]{64}', record['sha256']):
                    raise ValueError('Invalid reviewed Memory digest')
                for key in ('origin', 'date', 'relative_path', 'path'):
                    if not isinstance(record.get(key), str):
                        raise ValueError(f'Invalid Memory operation {key}')
                if record.get('scope') != 'tree':
                    raise ValueError('Invalid Memory operation scope')
                if operation == 'active':
                    if record.get('destination') is not None:
                        raise ValueError('Activation cannot move a file')
                else:
                    inode = record.get('inode')
                    if (not isinstance(inode, list) or len(inode) != 2 or
                            any(type(value) is not int or value < 0 for value in inode) or
                            record.get('kind') not in ('file', 'folder')):
                        raise ValueError('Invalid retained Memory inode')
                for key in ('files_before', 'files_after'):
                    values = record.get(key)
                    if not isinstance(values, dict):
                        raise ValueError(f'Invalid Memory operation {key}')
                    for ident, row in values.items():
                        if row is None and key == 'files_before' and operation == 'create':
                            if not isinstance(ident, str) or not re.fullmatch(r'[a-f0-9]{32}', ident):
                                raise ValueError('Invalid new Memory file identity')
                            continue
                        self._validate_file_record(ident, row)
                        if row.get('project_id') != project_id:
                            raise ValueError('Memory operation crosses project ownership')
                if set(record['files_before']) != set(record['files_after']):
                    raise ValueError('Memory operation identity sets disagree')
                if 'undo_of' in record and (not isinstance(record['undo_of'], str) or not re.fullmatch(r'[a-f0-9]{32}', record['undo_of'])):
                    raise ValueError('Invalid Memory Undo reference')
                for key in ('source', 'destination'):
                    if key == 'destination' and operation == 'active':
                        continue
                    self._owned_path(record[key], project_id, tree=record.get('kind') == 'folder', retained=operation != 'active')
                records.append((record, raw))
            except (ValueError, TypeError, KeyError, AttributeError, RecursionError) as error:
                raise ValueError(f'Unresolved Memory operation at {self.root / ".operations" / name}: {error}') from error
        return records

    def _apply_registry(self, record):
        meta, raw = self._metadata()
        for ident, row in record.get('files_after', {}).items():
            previous = record.get('files_before', {}).get(ident)
            current = meta['files'].get(ident)
            if current != previous and current != row:
                raise ValueError('Memory registry changed during operation; recovery versions preserved')
            meta['files'][ident] = row
        self._save_metadata(meta, raw)

    def _recover_operations(self):
        self._ordered_records()
        for record, raw in self._operation_records():
            if record['status'] != 'prepared':
                continue
            source = self._checked_storage(record['source']) if record.get('source') else None
            target = self._checked_storage(record['destination']) if record.get('destination') else None
            if record['operation'] == 'active':
                if source is None or not source.exists() or self._entry_digest(source) != record['sha256']:
                    record['status'] = 'aborted'; self._write_operation(record, raw)
                    continue
                self._apply_registry(record)
                record['status'] = 'saved'; self._write_operation(record, raw)
                continue
            source_exists = source is not None and os.path.lexists(source)
            target_exists = target is not None and os.path.lexists(target)
            if source_exists and not target_exists:
                record['status'] = 'aborted'; self._write_operation(record, raw)
                continue
            if target_exists and not source_exists:
                with safe_directory(target.parent) as fd:
                    info = fs.stat(target.name, dir_fd=fd, follow_symlinks=False)
                if [info.st_dev, info.st_ino] != record['inode'] or self._entry_digest(target) != record['sha256']:
                    raise ValueError(f'Memory operation conflict; retained entry changed at {target}. Journal: {self._operation_path(record["id"])}')
                self._apply_registry(record)
                record['status'] = 'saved'; self._write_operation(record, raw)
                continue
            raise ValueError(f'Memory operation needs recovery: preserve {source} and {target}; inspect {self._operation_path(record["id"])}')

    def _relocate(self, source, destination, expected, project_id, operation, *, undo_of=None, files_after=None,
                  expected_file_id=_UNREVIEWED_IDENTITY, expected_entry_identity=_UNREVIEWED_IDENTITY):
        old = self._checked_storage(source); target = self._checked_storage(destination)
        if self._entry_digest(old) != expected:
            raise ValueError('Memory entry changed; reload to resolve the conflict')
        if os.path.lexists(target):
            raise ValueError('Destination already exists; no Memory entry was overwritten')
        with safe_directory(old.parent) as fd:
            info = fs.stat(old.name, dir_fd=fd, follow_symlinks=False)
        if expected_entry_identity is not _UNREVIEWED_IDENTITY:
            self._check_entry_identity(old, expected_entry_identity)
            if [info.st_dev, info.st_ino] != list(expected_entry_identity):
                raise ValueError('Memory entry identity changed before journaling; no entry was moved')
        if expected_file_id is not _UNREVIEWED_IDENTITY:
            prefix = f'.projects/{project_id}/' if project_id else ''
            self._check_reviewed_identity(source[len(prefix):], project_id, expected_file_id)
        meta, _ = self._metadata(); before = {}; after = {}
        if stat.S_ISDIR(info.st_mode):
            # A retained old inode may belong to a file now elsewhere. Moving
            # its former containing folder would hide later editor writes from
            # that file's conflict checks. Keep this recovery location stable.
            for row in meta['files'].values():
                inside = row['path'].startswith(source + '/')
                if not inside and any(path.startswith(source + '/') for path in row.get('recovery_paths', [])):
                    raise ValueError('Folder contains retained recovery state for a Memory file elsewhere. '
                                     'Its recovery location must remain in place; individual ordinary entries can be moved or deleted.')
            if project_id is None and (source == 'memory' or source.startswith('memory/projects')) and os.path.lexists(self.root / 'memory/projects'):
                raise ValueError('Folder contains reserved project recovery state. Its recovery location must remain in place.')
        for ident, row in meta['files'].items():
            if not row.get('deleted') and (row['path'] == source or row['path'].startswith(source + '/')):
                if row.get('project_id') != project_id:
                    raise ValueError('Moving a folder across project ownership is refused')
                self._check_old_recovery(row)
                if os.path.lexists((self.root / row['path']).parent):
                    recover_file(self.root / row['path'])
                before[ident] = copy.deepcopy(row); changed = copy.deepcopy(row)
                if operation == 'delete':
                    changed['deleted'] = True
                else:
                    replacement = destination + row['path'][len(source):]
                    changed['path'] = replacement
                    changed['history_paths'] = list(dict.fromkeys(row.get('history_paths', []) + [row['path'], replacement]))
                    # File moves leave prior adjacent recovery inodes in place;
                    # folder moves carry their recovery directories with them.
                    if row['path'] == source:
                        changed['recovery_paths'] = list(dict.fromkeys(row.get('recovery_paths', []) + [source]))
                    else:
                        changed['recovery_paths'] = [destination + path[len(source):] if path.startswith(source + '/') else path for path in row.get('recovery_paths', [])]
                after[ident] = changed
        if files_after is not None:
            before = {ident: copy.deepcopy(meta['files'].get(ident)) for ident in files_after}
            after = copy.deepcopy(files_after)
        # The destination was checked absent on disk. Records left behind by
        # an external removal belong to the old file, never its replacement.
        # Retire them in the same journaled update, retaining all history.
        for ident, row in meta['files'].items():
            if ident not in after and not row['deleted'] and (
                    row['path'] == destination or row['path'].startswith(destination + '/')):
                if row.get('project_id') != project_id:
                    raise ValueError('Destination registry crosses project ownership')
                before[ident] = copy.deepcopy(row)
                after[ident] = dict(row, deleted=True)
        candidate = copy.deepcopy(meta)
        candidate['files'].update(after)
        self._validate_metadata(candidate)
        record = {'id': uuid.uuid4().hex, 'date': datetime.now(timezone.utc).isoformat(timespec='seconds'),
            'operation': operation, 'sequence': self._next_sequence(), 'project_id': project_id, 'source': source, 'destination': destination,
            'sha256': expected, 'inode': [info.st_dev, info.st_ino], 'kind': 'folder' if stat.S_ISDIR(info.st_mode) else 'file',
            'files_before': before, 'files_after': after, 'status': 'prepared', 'origin': 'user',
            'scope': 'tree', 'relative_path': source, 'path': str(target)}
        if undo_of:
            record['undo_of'] = undo_of
        self._write_operation(record)
        prepared = safe_read(self._operation_path(record['id']), MAX_RECEIPT_BYTES)
        with safe_directory(old.parent) as src, safe_directory(target.parent) as dst:
            # Check again after preparing the durable journal, then retain the
            # actual inode. NOREPLACE protects a racing destination creation.
            if self._entry_digest(old) != expected:
                record['status'] = 'aborted'; self._write_operation(record, prepared)
                raise ValueError('Memory entry changed; reload to resolve the conflict')
            # Hashing deliberately reopens the path to detect external edits.
            # It must not authorize a rename through an older directory FD
            # that an external actor has since moved outside this Memory tree.
            for parent, anchored in ((old.parent, src), (target.parent, dst)):
                with safe_directory(parent) as fresh:
                    if not os.path.samestat(fs.fstat(anchored), fs.fstat(fresh)):
                        record['status'] = 'aborted'; self._write_operation(record, prepared)
                        raise ValueError('Memory directory identity changed during operation; no entry was moved')
            current = fs.stat(old.name, dir_fd=src, follow_symlinks=False)
            if not os.path.samestat(info, current) or (not stat.S_ISDIR(current.st_mode) and
                    (not stat.S_ISREG(current.st_mode) or current.st_nlink != 1)):
                record['status'] = 'aborted'; self._write_operation(record, prepared)
                raise ValueError('Memory entry identity changed during operation; no entry was moved')
            try:
                rename_noreplace(src, old.name, dst, target.name)
            except FileExistsError:
                # Atomic NOREPLACE failed without moving the guarded source.
                # Retain both entries, but finish this journal before a caller
                # retries: two present names are otherwise ambiguous on restart.
                record['status'] = 'aborted'; self._write_operation(record, prepared)
                raise
            fs.fsync(src); fs.fsync(dst)
        if self._entry_digest(target) != expected:
            raise ValueError(f'Memory entry changed during operation; conflict retained at {target}')
        self._apply_registry(record)
        record['status'] = 'saved'; self._write_operation(record, prepared)
        return record

    def create_folder(self, relative, project_id=None):
        storage = self._storage(relative, project_id)
        target = self._checked_storage(storage)
        with self._operation():
            with safe_directory(target.parent):
                pass
            temporary = '.trash/create-' + uuid.uuid4().hex
            with safe_directory(self.root / '.trash') as fd:
                fs.mkdir(Path(temporary).name, 0o700, dir_fd=fd); fs.fsync(fd)
            return self._relocate(temporary, storage, self._entry_digest(self.root / temporary),
                                  project_id, 'create', files_after={})

    def create_file(self, relative, text='', project_id=None, *, max_bytes=MAX_FILE_BYTES):
        # Legacy SQLite exports may explicitly preserve larger opaque text.
        # Ordinary editor/tool calls retain the normal bounded write limit.
        if (type(max_bytes) is not int or max_bytes < 0 or not isinstance(text, str)
                or len(text.encode('utf-8')) > max_bytes):
            raise ValueError('Invalid or oversized Memory text')
        storage = self._storage(relative, project_id); target = self._checked_storage(storage)
        if target.suffix.lower() not in ('.md', '.txt', '.markdown'):
            raise ValueError('Memory files must be Markdown or text (.md or .txt)')
        with self._operation():
            with safe_directory(target.parent):
                pass
            temporary = '.trash/create-' + uuid.uuid4().hex
            safe_write(self.root / temporary, text.encode('utf-8'), None, max_bytes=max_bytes)
            ident = uuid.uuid4().hex
            row = {'path': storage, 'project_id': project_id, 'always_active': False,
                'history_paths': [storage], 'recovery_paths': [], 'deleted': False}
            return self._relocate(temporary, storage, digest(text.encode()), project_id,
                                  'create', files_after={ident: row})

    def move(self, relative, destination, expected_sha256, project_id=None, *, expected_file_id=_UNREVIEWED_IDENTITY, expected_entry_identity=_UNREVIEWED_IDENTITY):
        source = self._storage(relative, project_id); destination = self._storage(destination, project_id)
        if source == destination or destination.startswith(source + '/'):
            raise ValueError('A folder cannot be moved into itself')
        target = self._checked_storage(destination)
        with self._operation():
            if not (self.root / source).is_dir() and target.suffix.lower() not in ('.md', '.txt', '.markdown'):
                raise ValueError('Memory files must retain a Markdown or text extension')
            return self._relocate(source, destination, expected_sha256, project_id, 'move',
                                  expected_file_id=expected_file_id, expected_entry_identity=expected_entry_identity)

    def delete(self, relative, expected_sha256, project_id=None, *, expected_file_id=_UNREVIEWED_IDENTITY, expected_entry_identity=_UNREVIEWED_IDENTITY):
        source = self._storage(relative, project_id)
        destination = '.trash/' + uuid.uuid4().hex
        with self._operation():
            return self._relocate(source, destination, expected_sha256, project_id, 'delete',
                                  expected_file_id=expected_file_id, expected_entry_identity=expected_entry_identity)

    def set_active(self, relative, enabled, expected_sha256, project_id=None, *, expected_revision=None, expected_file_id=_UNREVIEWED_IDENTITY, expected_entry_identity=_UNREVIEWED_IDENTITY):
        if type(enabled) is not bool:
            raise ValueError('Always active must be true or false')
        with self._operation():
            self._check_reviewed_identity(relative, project_id, expected_file_id)
            self._check_entry_identity(self._checked_storage(self._storage(relative, project_id)), expected_entry_identity)
            snapshot = self.file_snapshot(relative, project_id)
            if snapshot['sha256'] != expected_sha256 or (expected_revision is not None and snapshot['revision'] != expected_revision):
                raise ValueError('Memory file or activation changed; reload to resolve the conflict')
            ident, row = self._lookup(relative, project_id, register=True)
            changed = dict(row, always_active=enabled)
            record = {'id': uuid.uuid4().hex, 'date': datetime.now(timezone.utc).isoformat(timespec='seconds'),
                'operation': 'active', 'sequence': self._next_sequence(), 'project_id': project_id, 'status': 'prepared', 'origin': 'user',
                'files_before': {ident: row}, 'files_after': {ident: changed},
                'source': row['path'], 'destination': None, 'sha256': expected_sha256,
                'scope': 'tree', 'relative_path': row['path'], 'path': snapshot['path']}
            self._write_operation(record); prepared = safe_read(self._operation_path(record['id']), MAX_RECEIPT_BYTES)
            if self.file_snapshot(relative, project_id)['sha256'] != expected_sha256:
                record['status'] = 'aborted'; self._write_operation(record, prepared)
                raise ValueError('Memory file changed; reload to resolve the conflict')
            self._check_entry_identity(self._checked_storage(self._storage(relative, project_id)), expected_entry_identity)
            self._apply_registry(record); record['status'] = 'saved'; self._write_operation(record, prepared)
            return record

    @staticmethod
    def _paths_overlap(one, two):
        return one == two or one.startswith(two + '/') or two.startswith(one + '/')

    def _tree_undo_error(self, record):
        if record['status'] != 'saved':
            return 'Unconfirmed operation cannot be undone'
        identities = set(record.get('files_before', {})) | set(record.get('files_after', {}))
        paths = [record[key] for key in ('source', 'destination') if record.get(key) and not record[key].startswith('.trash/')]
        later = [row for row in self._receipt_records() if row.get('project_id') == record.get('project_id')]
        later += [row for row, raw in self._operation_records() if row.get('project_id') == record.get('project_id')]
        for row in later:
            if row.get('sequence', 0) <= record.get('sequence', 0) or row['id'] == record['id'] or row.get('status') == 'aborted':
                continue
            row_ids = set(row.get('files_before', {})) | set(row.get('files_after', {}))
            if row.get('file_identity'):
                row_ids.add(row['file_identity'])
            row_paths = [row[key] for key in ('source', 'destination', 'relative_path') if row.get(key) and not row[key].startswith('.trash/')]
            if identities & row_ids or any(self._paths_overlap(a, b) for a in paths for b in row_paths):
                return 'A later saved or unconfirmed Memory change exists; select the latest applicable change.'
        return ''

    def history(self, relative=None, project_id=None):
        self._ordered_records()
        storage = self._storage(relative, project_id) if relative is not None else None
        content = self._receipt_records()
        content = [row for row in content if row.get('project_id') == project_id and (storage is None or row['relative_path'] == storage)]
        content = self._undo_history(content)
        operations = []
        for record, raw in self._operation_records():
            if record.get('project_id') != project_id:
                continue
            if storage is not None and storage not in (record.get('source'), record.get('destination')):
                continue
            error = self._tree_undo_error(record)
            record = dict(record, is_latest=not bool(error), undo_error=error)
            operations.append(record)
        return sorted(content + operations, key=lambda row: (row.get('sequence', 0), row['date'], row['id']), reverse=True)

    def undo(self, receipt_id):
        self._ordered_records()
        path = self._operation_path(receipt_id)
        if not os.path.lexists(path):
            return super().undo(receipt_id)
        with self._operation():
            record = next((row for row, raw in self._operation_records() if row['id'] == receipt_id), None)
            if record is None or record['status'] != 'saved':
                raise ValueError('Unconfirmed operation cannot be undone')
            error = self._tree_undo_error(record)
            if error:
                raise ValueError(error)
            meta, _ = self._metadata()
            if any(meta['files'].get(ident) != row for ident, row in record.get('files_after', {}).items()):
                raise ValueError('A later Memory change exists; operation cannot be undone')
            retired = {ident for ident, row in record.get('files_after', {}).items()
                       if record['operation'] != 'delete' and row['deleted'] and
                       record['files_before'].get(ident) is not None and
                       not record['files_before'][ident]['deleted'] and
                       record['files_before'][ident]['path'] == row['path']}
            for ident, row in record.get('files_after', {}).items():
                if ident in retired:
                    continue
                self._check_old_recovery(row)
                current_path = self._checked_storage(row['path'])
                if os.path.lexists(current_path.parent):
                    recover_file(current_path)
            if record['operation'] == 'active':
                ident, row = next(iter(record['files_before'].items()))
                current = meta['files'][ident]
                prefix = f".projects/{row['project_id']}/" if row.get('project_id') else ''
                return self.set_active(current['path'][len(prefix):], row['always_active'], record['sha256'], row.get('project_id'))
            if record['operation'] == 'create':
                prefix = f".projects/{record['project_id']}/" if record.get('project_id') else ''
                return self.delete(record['destination'][len(prefix):], record['sha256'], record.get('project_id'))
            before = {ident: copy.deepcopy(row) for ident, row in record.get('files_before', {}).items()
                      if ident not in retired}
            # Preserve knowledge of every historical receipt path and retained
            # recovery location even when reversing a user move.
            for ident, row in before.items():
                current = meta['files'][ident]
                row['history_paths'] = list(dict.fromkeys(row.get('history_paths', []) + current.get('history_paths', [])))
                row['recovery_paths'] = list(dict.fromkeys(row.get('recovery_paths', []) + current.get('recovery_paths', [])))
            return self._relocate(record['destination'], record['source'], record['sha256'], record.get('project_id'),
                                  'restore' if record['operation'] == 'delete' else 'move', undo_of=receipt_id, files_after=before)
