# Recovering a prepared Memory migration with deleted-project history

## Cause and correction

The original Memory-folder update (`4c1d52d`) discovered legacy project identities
from files still present in `strand/memory/projects/`. Earlier project deletion
moved a note into `.deleted-projects/` while retaining its receipt, history and
recovery data. That valid history had no identity in the new registry and failed
destination validation.

The root rename and registry creation precede history validation. This failure
therefore leaves `Memory/` present, `strand/` absent, SQLite schema 2, and the
migration marker at `status: prepared`. The receipt is not corrupt merely because
migration reported `Invalid receipt destination`.

The correction registers missing canonical project receipt destinations as
inactive, deleted historical identities before validation. It also handles the
partial registry left by a failed attempt, retaining existing IDs and flags. It
does not restore a deleted project, repoint its receipt at an archive, or edit
receipt/history/archive bytes. Original receipt validation still rejects malformed
paths, IDs, hashes and other invalid metadata. Prepared receipts for deleted files
cannot replay a recovery image into their old path; unfinished evidence remains
available and Undo stays blocked.

## Verification against the reported data

Installed migration modules matched `4c1d52d`. Live data was read without
instantiating Store or running recovery. A private full snapshot captured 64
regular files. Subsequent comparison found identical contents, file identities,
modification times and change times for every live file.

Two disposable cases failed with the original code and succeeded with the fix:

- A copy of the observed schema-2/prepared-marker/partial-registry state.
- A reconstructed pre-upgrade copy using the actual saved schema-2 database and
  retained ordinary/hidden Strand tree. Only migration-created control state was
  removed from this disposable reconstruction. It is not an independently captured
  full pre-upgrade backup.

The saved schema-2 database and observed database had identical logical contents.
Both corrected runs reached schema 3 and completed the marker, retained the saved
receipt and all five archived project notes, and preserved every original Memory
file except the registry being extended. Existing registry rows and database
logical contents were unchanged. Restart retained the historical identity; backup
and restoration preserved receipt, history and recovery bytes.

`tests/test_memory_migration.py` uses synthetic notes with the same legacy receipt
shape, not personal data. Its eight cases cover both starting states, stable IDs,
preserved bytes, history/Undo, subsequent saves, backup, unfinished recovery without
resurrection, and rejection of malformed receipts.

Observed final verification:

- Focused migration/storage tests: **172 passed in 3.74s**, including all eight
  new migration regressions (`test_memory_migration`, `test_memory`,
  `test_memory_validation`, `test_strand`, `test_store`, `test_reliable_backup`).
- Full offscreen suite: **633 passed in 37.34s**, with the same 10 existing
  Python 3.14 fork deprecation warnings as the preceding update.
- Compilation and whitespace checks passed. Independent code review found no
  blocking issue in the correction. No installation or live migration was run.

## Exact recovery procedure

These commands are for the owner to run after reviewing the verified correction.
They have not been run against live data during this investigation.

1. Close LetraCode and editors holding Memory files open. Keep the investigation
   snapshot and any genuine pre-upgrade full backup. Make another complete copy
   of the current partial state:

   ```bash
   migration_backup="$(mktemp -d "$HOME/.local/state/letracode-before-migration-retry.XXXXXX")" &&
   cp -a -- "$HOME/.local/share/letracode" "$migration_backup/data" &&
   diff -qr -- "$HOME/.local/share/letracode" "$migration_backup/data"
   ```

2. Install the corrected code from this checkout:

   ```bash
   cd /home/miceoil/Projects/worktrees/active/LetraCode-evaluation-memory
   ./install.sh --no-deps
   ```

   Dependencies are already installed on the investigated machine. The installer
   updates managed application code; it does not perform the data migration.

3. From the same checkout, finish migration without starting a model or opening
   the UI. This step intentionally updates the live registry, schema and marker,
   and checks that the original receipts remain identical:

   ```bash
   python3 - <<'PY'
   import hashlib
   import json
   from pathlib import Path
   from letracode.store import Store

   data = Path.home() / '.local/share/letracode'
   receipt_dir = data / 'Memory/.receipts'
   original_receipts = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                        for p in receipt_dir.glob('*.json')}
   assert original_receipts, 'Expected retained receipts; stop and inspect this data path.'
   store = Store(data)
   with store.connection() as db:
       assert db.execute('PRAGMA user_version').fetchone()[0] == 3
   assert json.loads((data / 'memory-tree-migration.json').read_text())['status'] == 'complete'
   for name, expected in original_receipts.items():
       assert hashlib.sha256((receipt_dir / name).read_bytes()).hexdigest() == expected
       store.memory.receipt(Path(name).stem)
   print('Migration complete: schema 3; original receipts preserved and readable.')
   PY
   ```

4. Start the corrected launcher:

   ```bash
   "$HOME/.local/bin/letracode" --data-dir "$HOME/.local/share/letracode"
   ```

   Check Identity/preferences, ordinary Memory and retained project archives.
   Deleted projects should remain deleted. Make a new full backup after inspection.

If any step fails, stop and retain the complete data folder and exact error. Do not
delete receipts, rename `Memory` back, recreate `strand`, remove `.memory.json`, or
manually set `PRAGMA user_version=3`. The corrected migration resumes from the
prepared state itself. For rollback to an older app, restore a genuine full
pre-upgrade backup into a separate directory; SQLite-only migration snapshots are
not complete rollback bundles.
