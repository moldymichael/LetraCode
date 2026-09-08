# Evaluation export and Memory folders implementation plan

**Goal:** Implement the user's two-component update on the current reliability line, preserving data and existing action/recovery safeguards.

**Architecture:** A read-only evaluation module projects one SQLite conversation into a portable ZIP. A general Memory tree reuses the existing Linux descriptor-anchored save/history/recovery implementation; migrate the complete legacy root, retaining ordinary files, relative history identities, and opaque recovery data. Keep legacy scope adapters so saved receipts and drafts continue to resolve.

**Baseline:** `7863707`, isolated branch `codex/evaluation-memory`. The older anchor checkout is a separate Windows development line and is not integrated here. Inspected direction/current-state, Store schema 2 migration/backup, StrandFiles journal and Undo, source-files guarded writes, evidence and continuation modules, tool and UI flows. Baseline: 494 passed, 10 existing fork warnings.

## Constraints

- No live data migration during development; all tests use temporary data directories.
- No source/Memory/credentials/settings dump in evaluation bundles; sanitize stored tool bodies too. Export performs no recovery, autosave, or state mutation.
- Preserve all legacy files and recovery evidence. Never merge conflicting roots, overwrite migration destinations, follow links, or silently prune history.
- User chooses nested paths and always-active files. Other memory is available through bounded listing/search/reading. No embeddings, organization agent, cloud upload, scoring, or personality redesign.

## Component 1: Evaluation export

- [x] Add failing ZIP, chronological transcript/actions/errors/evidence, missing metadata, privacy and no-mutation tests in `tests/test_evaluation.py`.
- [x] Implement `letracode/evaluation.py`, Store wrapper, and **Export Evaluation…** UI with optional notes. Use one consistent read transaction; preserve saved ordering and distinguish missing historical metadata from recorded values.
- [x] Add tests and minimal future-run configuration/approval metadata in worker; retain actual runtime mode rather than current settings at export time.
- [x] Run focused exporter/worker tests and review privacy projection; commit Export Evaluation independently when practical.

## Component 2: Memory tree

- [x] Add migration, CRUD/nesting, active files/retrieval, stale conflicts, Undo/recovery, backup, project isolation and unsafe path tests.
- [x] Implement `letracode/memory.py` using guarded storage and retain complete legacy history/recovery during root migration; adapt Store initialization, backup and project deletion.
- [x] Add `letracode/memory_ui.py` with tree, explicit saves, folder/file CRUD, activation and history; retain drafts on conflicts and navigation.
- [x] Generalize tools/context integration and visible infrastructure wording. Preserve legacy reviewed-save authority; file selection does not grant new write permission.
- [x] Run focused tests, review recovery/privacy boundaries, then full offscreen suite, compilation and whitespace checks.
- [x] Document migration map, limitations, exact pre-install manual checks, and observed verification; commit Memory folders separately.

## Verification commands

```bash
QT_QPA_PLATFORM=offscreen python3 -m pytest -q tests/test_evaluation.py tests/test_evaluation_runtime.py
QT_QPA_PLATFORM=offscreen python3 -m pytest -q tests/test_evaluation.py tests/test_evaluation_runtime.py tests/test_memory.py tests/test_memory_validation.py tests/test_memory_integration.py tests/test_memory_ui.py
QT_QPA_PLATFORM=offscreen python3 -m pytest -q
python3 -m compileall -q letracode
git diff --check
```

Passing scripted-engine tests establish application behavior, not real-model quality. No installation or live assistant session is part of implementation verification.

## Observed completion verification

Export committed independently as `bc9e77a`: 16 focused tests and the staged 510-test full suite passed. Final combined feature run: 131 passed in 3.94s. Final full suite: 625 passed in 36.76s with the same 10 Python 3.14 fork deprecation warnings as the 494-test baseline. Compilation and whitespace checks passed. Independent review findings around stable identities, mixed history ordering, migration locks, parent-directory swaps and recovery were addressed with focused regressions. No live data, installation, original dirty anchor checkout, or separate Windows branch was modified.
