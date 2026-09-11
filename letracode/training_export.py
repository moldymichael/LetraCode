"""Explicit local exports of one training version and its saved comparisons."""
from __future__ import annotations

import json
import os
from pathlib import Path
import uuid

from . import filesystem as fs
from .store import now
from .training_experience import effective_report, version_review


DESCRIPTION = (
    'Local review export of one training version. The immutable run report contains '
    'the optimizer measurements and limited short built-in evaluation outputs. '
    'The review contains separately saved standalone Chat comparisons, including '
    'full current/candidate answers, partial results, errors, earlier comparisons, '
    'prompt drafts and user judgments when recorded. Missing review fields mean '
    'not recorded; this export does not run comparisons, verify current artifacts '
    'or certify improvement. The effective report includes any saved conversion '
    'retry receipt. This file includes local paths, prompts, answers and review '
    'notes without privacy filtering. Inspect it before sharing. No chats, '
    'unrelated settings, model binaries or adapter binaries are included.'
)


def export_version_review(repo, ident, destination: Path):
    """Create a new UTF-8 JSON review file without replacing existing files."""
    destination = Path(destination).absolute()

    def check_destination():
        if destination.resolve().is_relative_to(repo.store.directory.resolve()):
            raise ValueError('Choose an export destination outside LetraCode data.')
        if os.path.lexists(destination):
            raise FileExistsError('This export destination already exists. Choose a new filename.')

    check_destination()
    run = repo.run(ident)
    if run is None:
        raise ValueError('This training version does not exist.')
    snapshot = {
        'format': 'letracode-version-review', 'format_version': 1,
        'exported_at': now(), 'description': DESCRIPTION,
        'run': run, 'effective_report': effective_report(repo, ident),
        'review': version_review(repo, ident),
    }
    content = (json.dumps(snapshot, ensure_ascii=False, allow_nan=False, indent=2) + '\n').encode('utf-8')
    # Stage through a retained directory descriptor; publication is atomic and
    # refuses replacement even if another writer creates the chosen filename.
    with fs.safe_directory(destination.parent) as parent:
        check_destination()
        temporary = '.letracode-version-review-' + uuid.uuid4().hex + '.json'
        fd = fs.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | fs.O_NOFOLLOW,
                     0o600, dir_fd=parent)
        try:
            with os.fdopen(fd, 'wb') as output:
                output.write(content)
                output.flush()
                fs.fsync(output.fileno())
            check_destination()
            with fs.safe_directory(destination.parent) as current:
                before, after = fs.fstat(parent), fs.fstat(current)
                if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
                    raise ValueError('Export destination folder changed while saving.')
            fs.rename_noreplace(parent, temporary, parent, destination.name)
            fs.fsync(parent)
        finally:
            try:
                fs.unlink(temporary, dir_fd=parent)
            except FileNotFoundError:
                pass
    return destination
