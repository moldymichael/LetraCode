# Project files update — September 8, 2026

The installed application came from the evaluation/Memory worktree with SQLite
schema 3. The initial file-first redesign was in a different checkout with schema
1 and was never installed. This update brings the interface into the newer
backend; installing the older checkout would be an incompatible downgrade.

## Scope and implementation

- Replace the three autosave context editors with a native Project files tree.
  Keep project instructions in a separate optional dialog.
- Keep existing Memory paths, metadata, selected active flags, durable drafts,
  guarded saves, history, Undo, project archives and backup support.
- Export the remaining Current Context database text to an ordinary Markdown
  file on first use, retaining original SQLite text. Never replace an existing
  file. Once exported, retrieval uses files instead of stale database injection.
- Preserve evaluation exports, continuation, source evidence and approval flows.
- Fix launcher import precedence so the installed build is selected even when
  launched from an unrelated checkout. Package this compatible build as 0.2.1.

No new memory database, file relocation, scheduler, platform port or dependency
is part of this update. The app's hidden history controls remain internal.

## Verification and installation plan

Exercise legacy-text export, restart, collisions, interrupted import, preserved
external edits, backup inclusion and Undo using temporary data directories.
Exercise the new panel, explicit saves, old drafts, conflict handling, busy
controls and project selection. Run the full suite and inspect the rendered Qt
window. Before installing, back up the installed build and snapshot live data
under the app lock. Validate the new source against a copy of that snapshot.
Install using the existing installer and verify the launcher and installed UI.

## Verified result

The final full offscreen suite passed **656 tests** in 38.52 seconds, with the
ten existing Python 3.14 process-fork warnings. Compilation, whitespace checks,
release archive construction and independent final review passed. Native Qt
windows were rendered at normal and compact sizes, including the simple editor.

The installed 0.2.1 launcher was verified from both `/tmp` and the older checkout.
All 156 original persistent data files were unchanged by installation. The
installed UI opened a copy of the live schema-3 data; all 145 existing Memory
files remained identical, eight history records validated (including the
previously reported receipt), and explicit Save followed by Undo restored the
exact text. The updated desktop app was then opened normally.

The pre-install application and consistent data snapshot are retained locally
under the user’s data directory in `letracode-repairs/project-files-<timestamp>`.
Implementation source is the evaluation-memory worktree, not the older schema-1
checkout. No source checkout was reset or replaced.
