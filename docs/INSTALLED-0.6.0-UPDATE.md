# Installed 0.6.0 update

On September 10, 2026, the user requested installation of the Strand experience
redesign and correction of the stale source baseline.

## Installation and verification

- Integrated application commit: `55f36ad` (version 0.6.0), based on `dcf8d56`.
- Verified existing Python, PySide6 and PDF dependencies, then used
  `./install.sh --no-deps`. No dependency download was necessary.
- The normal `~/.local/bin/letracode` launcher reports **LetraCode 0.6.0**.
  All 36 installed Python modules match the source byte-for-byte.
- Before installation, retained a complete copy of the previous app, launcher,
  desktop entry and data, including local training outputs. Compared copied
  files by SHA-256 and verified the copied database's integrity.
- All 267 pre-existing data entries matched their pre-installation hashes
  immediately after installation. The only deliberate configuration change
  afterward was setting `development_root` to the canonical source repository.
- The installed Qt interface opened a disposable copy of existing data and
  verified Chat, Knowledge, Improve and Settings with no model job started.
- Launched the normal installed app. It remained running, acquired the live
  data lock and produced an empty startup log. Existing chat, message, workspace
  and training records, model configuration and active version were preserved;
  database integrity passed.
- Release/installer regression checks after adding source guidance: **13 passed**.
  The application regression result remains **1,076 passed, 22 skipped**; see
  [the experience verification](STRAND-EXPERIENCE.md) for its scope and limits.

Rollback material is retained outside temporary storage at:

`~/.local/share/letracode-upgrade-backups/2026-09-10-to-0.6.0-142224/`

Its `RESTORE.txt` explains the previous app/data copy. Original linked files
and original model folders were not moved or changed. Training quality was
not reevaluated as part of installing the interface update.

## Source baseline correction

The saved Codex project already pointed to `/home/miceoil/Projects/LetraCode`,
but its local `main` remained at `3b7ab1b`, the 0.1.1 import. Later development
was in other branches/worktrees. This caused the canonical project directory
to present an obsolete starting point.

Committed the verified redesign and fast-forwarded local `main` to it. This
canonical directory now checks out `main` with 0.6.0. Added root `AGENTS.md`
to identify the canonical source, distinguish the installed package, require
actual branch/file checks, and prevent historical worktrees from silently
becoming the development baseline. Source archives include that guidance.

Strand's saved development folder now points to the canonical repository.
Historical branches and worktrees remain intact. No GitHub publication was
performed: after fetching, `origin/main` was still `c9f6392` (0.3.0), so remote
clones must not be described as this installed build. Local `main` contains
the integrated application and is the starting point for further local work.
