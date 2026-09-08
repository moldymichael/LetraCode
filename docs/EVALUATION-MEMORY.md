# Evaluation export and user-controlled Memory

This update is based on the current reliability implementation, commit
`7863707`, on branch `codex/evaluation-memory`. The older repository anchor
contains separate Windows work and is preserved. This branch still requires
the Linux filesystem primitives used by the existing guarded-save system.
Development and automated verification use disposable data; your live data
folder has not been opened, migrated, or installed over.

## Evaluation bundles

Select a conversation and choose **File → Export Evaluation…**. Add optional
notes, then choose a ZIP destination outside the application data directory.
The export reads one SQLite transaction and never saves drafts, runs recovery,
reads Memory/source files, or changes conversation state.

| File | Contents |
| --- | --- |
| `README.md` | Format, ordering, evidence meanings, privacy omissions and limitations |
| `transcript.md` | Readable saved messages, action requests/results, notices and honest outcome labels |
| `conversation.json` | Structured projection of this conversation, without unsent drafts |
| `events.jsonl` | Saved-message order with requests, explicit approval decisions, results, errors, interruptions, continuation and evidence events |
| `coverage.json` | Stored source hashes, ranges, retrieval/exposure and coverage observations |
| `metadata.json` | Format/schema/app versions, optional Git state at export, and configurations actually recorded for runs |
| `notes.md` | Optional short user notes |

Message IDs define chronology; timestamps and elapsed times keep their recorded
precision. Old runs can lack model/mode, approval or coverage metadata. The bundle
reports absence instead of substituting current settings or inferring approval
from a successful action. New responses retain model/engine basenames, mode,
relevant numeric settings and capability flags. New tool results retain explicit
approval decisions and their recorded times. A result saved before an abrupt
failure can still be missing; unknown outcomes are never relabeled successful.
Git metadata describes the application checkout at export, not a linked project
or necessarily the code used by an older run. Installed copies without Git report
that information unavailable.

The exporter does not collect linked source, Memory files/history, app settings,
credentials, other conversations, model weights or engine logs. Stored source
and Memory bodies, edits, command text/output and nested saved-result pages are
replaced with byte counts, hashes and omission reasons. Structured absolute paths
become anonymous references with basenames; recognizable credentials and home
prefixes in prose are filtered. User/assistant prose and notes remain readable
and can contain private material that pattern matching cannot identify. Inspect
the ZIP before sending it. Hashes are references, not a guarantee of anonymity.
There is no scoring, cloud upload, dashboard or executable replay.

## Memory layout and migration

Schema 3 moves the complete `strand` directory to `Memory` using a guarded
non-replacing directory rename. The tree, hidden histories, receipts, recovery
inodes, deleted-project archives and unknown retained files remain present.
The older schema-1 database migration runs first when needed and preserves its
original database snapshot. Conflicting destinations stop migration; the app
does not merge or overwrite two existing roots. A durable migration record
allows interrupted migration to resume with the existing files.

A follow-up corrects migration of retained receipts for deleted projects,
including retries after the root was already renamed. See the
[failure explanation and exact recovery procedure](MEMORY-MIGRATION-RECOVERY.md).

| Existing Strand data | Location after migration, relative to the app data folder |
| --- | --- |
| Identity | `Memory/identity/strand.md` |
| Working preferences | `Memory/identity/preferences.md` |
| Global Memory | `Memory/memory/global.md` |
| Programming learning record | `Memory/learning/programming.md` |
| Project memory `strand/memory/projects/<id>.md` | `Memory/.projects/<id>/Memory.md` |
| `.history`, `.receipts`, deleted archives and recovery state | Retained inside `Memory`, with stable file identities resolving renamed paths |
| Legacy editor drafts and learning grant | Existing SQLite settings retained and honored for their original file/scope |

These are migration destinations, not required categories. Users can rename,
move or remove ordinary files/folders. Each project's tree is selected through
**This project's Memory**; its hidden storage namespace prevents one chat from
reading another project's memory. Old project recovery directories remain
retained separately. The registry maps file identities to user paths, so history
and legacy aliases follow moves without rewriting the historical receipts.

Migrated Identity and preferences remain always active to preserve existing
configuration. Global/project Memory and learning records are available for
retrieval; mark any desired files always active yourself. New data folders use
empty optional starter files, including neutral `identity/assistant.md`, with
no files active by default and no built-in Strand identity.

Always-active files are included intact for their applicable scope. If they
exceed the context budget, the app reports the problem instead of silently
truncating them. Other files are not injected automatically: the assistant can
use `list_memory`, `search_memory` and paged `read_memory` when relevant. These
saved-memory reads remain available with external computer/web tools disabled.
Model-requested Memory appends retain review and stale-hash checks. The existing
learning grant applies only to its stable legacy learning-file identity; it
does not authorize arbitrary files or identity/preferences edits.

## Safety and recovery

- Saves retain the original inode and content history, check reviewed hashes,
  and detect external edits, including later writes through old open editor
  handles. Saves and activation check the reviewed file identity; activation
  also checks its metadata revision. A stale draft cannot change a replacement
  file with the same name and bytes. Ambiguous drafts remain available for
  copying and require Reload before saving.
- Rename/move/delete/create operations use durable journals, non-replacing
  moves and sequence ordering. Deleted entries are retained in hidden trash;
  Undo checks applicable later history and current state, not just equal hashes.
  Tree actions check the selected entry's identity, and moves recheck both
  parent directories before committing.
- Conflicting editor drafts survive navigation and restart. Missing-file drafts
  remain discoverable. Tree drafts require explicit Save/Reload, including when
  the older quick pane refers to the same file. Deliberately deleting optional
  starter files does not prevent chatting; unexpected external disappearance
  still requires reconciliation.
- Ordinary nested folders are user-controlled. A folder holding retained
  recovery journals for a file now elsewhere cannot itself be moved/deleted:
  its location must stay available for late external-editor conflict detection.
  The app explains this; individual ordinary entries can still be managed.
- Project deletion retains all its Memory. Full-tree archives and the historical
  single-file archive path remain available for recovery. Deleting a project
  still deletes its chats/context as the existing confirmation states.
- Hidden control paths, traversal, absolute paths, symlinks and hardlinks cannot
  be used through Memory tools to escape scope. Files must be UTF-8 Markdown or
  text within the existing 2 MiB edit limit. Unsupported/oversized retained data
  is preserved and copied opaquely by backups rather than discarded.

Backups include `Memory/` and all retained control/history/recovery data plus
SQLite and retained source backups. Keep the full pre-upgrade backup for rollback.
Automatic migration snapshots contain SQLite only; they are not complete
old-version rollback bundles. An old app must not open the upgraded schema-3
data folder. Restore the full pre-upgrade backup into a separate folder instead.

## Exact checks before installing

1. In your current app choose **File → Back up chats, Strand and source backups…**
   (or its existing full-backup equivalent). Keep that ZIP unchanged. Verify it
   opens and contains `letracode.sqlite3`, `letracode.json`, `strand/`, hidden
   history/recovery files and `RESTORE.txt`. Close the current app.
2. Extract the entire backup into a new test folder, including hidden files.
   Remove its copied project links before launch, as instructed in `RESTORE.txt`:
   `sqlite3 /path/to/test-data/letracode.sqlite3 'DELETE FROM links;'`.
   Keep Computer and Internet off initially. From this update's source folder run
   `python3 -m letracode --data-dir /path/to/test-data`. Never use your live data
   path for this trial.
3. Open **Memory folders…**. Compare Identity, preferences, global Memory,
   learning records and each project's Memory with the old backup. Confirm
   Identity/preferences are active after migration and other files are not.
   Open existing history and any retained editor draft. Restart the test app
   and verify no files or drafts were recreated, lost or migrated twice.
4. In the test data, create `Trial/Notes/test.md`, save text, mark it active,
   rename it to `renamed.txt`, move its folder, and verify content/active state
   follow. Save another edit and Undo it. Delete the folder and Undo that
   deletion; verify nested files return. Repeat in one project and confirm
   another project and global Memory cannot see those files.
5. Open a test Memory file in an external editor. Change it while LetraCode has
   a local draft, then attempt Save and Rename/Move. Confirm a conflict appears,
   the external text remains intact, and your local draft survives restart.
   Use Reload only after copying any draft you want to keep. Try deleting an
   optional starter file and confirm normal chat remains available.
   Keep a draft open, then use a second test instance to delete/recreate that
   file with its original text. Confirm the first instance refuses to save,
   activate, rename or delete the replacement until you reload/reselect it.
6. With your local model, use a harmless test chat to check active-file context
   and `list_memory`/`search_memory`/`read_memory` for an inactive file. Request a
   Memory append, deny it, and verify no save occurred. Approve a second bounded
   append and verify history/Undo. Use only disposable linked sources for any
   computer-tool trial.
7. Export a conversation containing a failed or denied action, an interruption,
   continuation/evidence if available, and a short note. Open the ZIP and confirm
   the transcript and event order. Check that source/Memory/command bodies are
   omitted, paths/recognizable credentials are filtered, and old missing run
   metadata is labeled unavailable. Compare the app's conversation, drafts and
   Memory afterward; export should leave them unchanged.
8. Make a new full backup from the test app. Close it, restore that complete ZIP
   into a second empty folder, remove copied links again, and launch with
   `--data-dir` for that folder. Verify nested Memory, active flags, drafts,
   history and Undo. Only after these checks install from this update's source
   directory; retain the pre-upgrade backup and prior installer for rollback.

## Observed automated verification

Final verification on September 7, 2026 (America/Phoenix), using Python 3.14
and `QT_QPA_PLATFORM=offscreen`:

| Check | Observed result |
| --- | --- |
| Combined feature tests: `test_evaluation.py`, `test_evaluation_runtime.py`, `test_memory.py`, `test_memory_validation.py`, `test_memory_integration.py`, `test_memory_ui.py` | **131 passed in 3.94s** |
| Full suite: `python3 -m pytest -q` | **625 passed in 36.76s**, 10 existing fork deprecation warnings |
| Backend storage regression group | **164 passed in 3.48s** |
| Memory and existing UI regression group | **89 passed** |
| `python3 -m compileall -q letracode` and `git diff --check` | Passed |

The Export Evaluation commit was also tested independently before the Memory
commit: 16 focused tests and its complete 510-test suite passed. The original
reliability baseline passed 494 tests with the same 10 fork warnings.

Offscreen UI/scripted-engine tests do not establish real-model quality or
native desktop behavior; the manual local-model and desktop checks above
remain necessary. No installation or live-data migration was performed.
