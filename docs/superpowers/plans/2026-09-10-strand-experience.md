# Strand Experience Implementation Plan

**Goal:** Make the installed LetraCode lineage an understandable home for one persistent local Strand.
**Architecture:** Preserve storage, engine and guarded file boundaries. Reorganize Qt entry points into four purposeful pages; extend backend receipts/continuity and training orchestration through existing APIs.
**Tech Stack:** Python 3.11+, PySide6, SQLite schema 3, llama.cpp, existing local Transformers/PEFT training.
**Spec:** ../specs/2026-09-10-strand-experience.md

## Tasks and verification

- [x] Chat shell (`ui.py`, new `experience.py`, `tests/test_experience.py`): four destinations; compact growing composer; clear access options; origin-bound worker callbacks; reading/navigation/drafts while busy; per-reply details and capture; paged transcript. Verify saved drafts in two chats and render minimum window.
- [x] Knowledge (`project_files.py`, `experience.py`): show scope/inclusion status; attach ordinary files globally or to a workspace; show actual paths and open externally; preserve existing conflict-safe editor/history. Verify shared sources survive reopen and selection never changes source bytes.
- [x] Backend context/continuity (`context.py`, `tools.py`, `worker.py`, `pause_context.py`, `store.py`): account-wide reads, independent effects, request receipts, real app context/history, bounded normal reference history, explicit end-task recovery. Run focused source/worker/pause/store tests.
- [x] Setup (`dialogs.py`, `experience.py`): discover local choices without exhaustive scans; basic readiness and asynchronous local test; advanced model controls collapsed; installation/data/recovery information. Verify setup errors retain chosen paths and defaults round-trip.
- [x] Improve (`training_ui.py`, training support modules): reviewed context-aware examples, readiness before engine interruption, stage recovery, Chat-runtime comparisons, persistent judgments, explicit use/rollback. Run training and Qt integration tests.
- [x] Integration: correct release evidence inclusion and current docs; full regression suite, compilation, whitespace checks, release build, native offscreen visual inspection. Record results and limitations in STRAND-EXPERIENCE.md.

All tests use disposable data. Do not change live user data or start expensive user-model training as a side effect of development. Existing safety boundaries and backups must remain intact. Implementation runs in the current task with independent agent ownership of backend and training files; root integrates the UI and verifies the combined result.

Completed verification: 1,076 passed, 22 skipped; native offscreen layout inspection, compilation, shell syntax, whitespace checks and 0.6.0 release archive build passed. See STRAND-EXPERIENCE.md for evidence and remaining real-model/platform limits.
