"""Versioned source reads and guarded saves; callers own approval.

Unfinished adjacent recovery records are untrusted and require explicit manual
reconciliation. Reading a linked source never restores or rewrites its path.
"""
from __future__ import annotations

import re
import stat
from pathlib import Path

from .strand import MAX_FILE_BYTES, digest, safe_snapshot, safe_write
from .filesystem import IS_WINDOWS

RECOVERY_NAMESPACE = '.letracode-recovery'


def _path(path):
    path = Path(path).expanduser()
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('Use an absolute source path without parent traversal.')
    return path


def _text(raw):
    if b'\x00' in raw:
        raise ValueError('Not a supported UTF-8 text source (contains NUL bytes).')
    try:
        # Keep BOM and every newline byte in the edit representation. Display
        # callers may remove the leading BOM, but the saved source must not.
        return raw.decode('utf-8')
    except UnicodeDecodeError:
        raise ValueError('This file is not UTF-8 text. Convert it or link a text export.') from None


def snapshot(path, *, allow_missing=False):
    path = _path(path)
    observed = safe_snapshot(path, namespace=RECOVERY_NAMESPACE)
    if observed is None:
        if not allow_missing:
            raise FileNotFoundError(f'Source file is missing: {path}')
        return {'path': str(path), 'raw': None, 'text': None, 'sha256': None,
                'mode': 0o666 if IS_WINDOWS else 0o600}
    raw, info = observed
    return {'path': str(path), 'raw': raw, 'text': _text(raw),
            'sha256': digest(raw), 'mode': stat.S_IMODE(info.st_mode)}


def publish(path, content: bytes, expected_sha256: str | None, *, mode=None, cancel=None):
    if cancel is not None and cancel.is_set():
        raise InterruptedError('Cancelled before saving.')
    path = _path(path)
    if not isinstance(content, bytes) or len(content) > MAX_FILE_BYTES:
        raise ValueError(f'Source content must be bytes within the {MAX_FILE_BYTES} byte limit.')
    _text(content)
    if expected_sha256 is not None and (not isinstance(expected_sha256, str) or
                                        not re.fullmatch(r'[a-f0-9]{64}', expected_sha256)):
        raise ValueError('An expected source SHA-256 hash or None for creation is required.')
    # Snapshot is also a mode precondition when a caller does not supply one.
    # safe_write repeats the byte/mode check while holding the recovery lock.
    if mode is None:
        mode = snapshot(path, allow_missing=True)['mode']
    safe_write(path, content, expected_sha256, namespace=RECOVERY_NAMESPACE, mode=mode, cancel=cancel)
    saved = snapshot(path)
    if saved['raw'] != content or saved['mode'] != mode:
        raise ValueError(f'Source file changed immediately after publication; inspect the current file: {path}')
    return saved
