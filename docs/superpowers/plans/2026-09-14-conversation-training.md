# Conversation training implementation plan

**Goal:** Create, edit, review, save, prepare, and actually optimize complete conversations with tool use.

**Architecture:** Share a dependency-free versioned conversation validator between the repository and standalone training backend. Use native tokenizer templates to preserve tool structure and prove assistant-only supervision. Replace the pair editor with editable Qt conversation cards.

**Spec:** `docs/superpowers/specs/2026-09-14-conversation-training.md`

**Constraints:** Python 3.11+, native Qt; preserve existing examples, drafts, immutable runs and recovery. Local offline training, no tool execution, no truncation or template fallback. Focused branch from 8772a19; no release or live installation changes.

- [x] Data contract and storage: create `training_examples.py` with `normalize_example(row)`, `example_summary(row)`, `example_identity(row)`, `comparison_example(row)`; update `TrainingRepository` to accept `messages=` and `tools=` in `save_example`, retain pair API and summaries, migrate additively, preserve legacy snapshots; test malformed call graphs, imports, round trips and approval invalidation before implementation.
- [x] Native training: update dataset loading and encoder in `training_backend.py`; structured records reach native templates with tools, verify each assistant span, reject unsupported or lossy templates and overlength examples. Keep masked context and report full structured examples; test real tokenizer behavior and run actual tiny-model optimization.
- [x] Editor: create `training_conversation_ui.py` and update `training_ui.py` with message cards, call/result linking, editable tools, target toggles, collapse and reorder. Preserve old simple prompt/response access for callers and legacy drafts. Test save/reload, invalid draft recovery, approval revocation, and controls.
- [x] Integration: update preparation duplicate checks, full-context generated comparison previews and review display; document new format and limitations; add cross-layer regressions.
- [ ] Verify and review: run focused tests then Fedora full suite, compileall and shell syntax checks. Inspect native Qt screenshots with disposable data. Independent whole-change code review; fix findings and rerun affected checks. Commit and open draft PR with exact evidence and remaining native Windows/large-model checks.

## Shared interfaces

`normalize_example(row: dict) -> dict` returns only schema_version/messages/tools, upgrading legacy pairs. Assistant `train` defaults true; explicit false means context only. `example_summary(row) -> tuple[str, str]` returns display summaries (first user, last assistant text/calls). `example_identity(row) -> str` keys conditioning context before the first trained assistant, including tool schemas, with normalized text. `comparison_example(row) -> dict` provides `prompt`, `response`, `messages`, `tools` for the first trained assistant; messages are the full prefix preceding that target. All structured fields remain in training snapshots; summaries are never training input. Legacy frozen run bytes remain stable.

## Work allocation and review

Data agent owns training_examples.py, training.py, corresponding tests. Backend agent owns training_backend.py and backend tests. UI agent owns training_conversation_ui.py, training_ui.py and UI tests. Root owns integration in training_experience.py, training_comparison_ui.py, docs, end-to-end evidence and final review. Shared interfaces above are fixed before parallel edits; coordinate interface changes explicitly.

## Completion evidence

Data/editor/integration and native-template implementation passed independent
review and fix re-review. Final Fedora suite: 1216 passed, 48 skipped, 6 existing
fork warnings. Optional training runtime suite: 74 passed. Production readiness,
real CPU optimization and GGUF conversion passed on a fresh tiny random Llama
fixture with the actual local Hermes tool template. Evidence and exact commands
are in `docs/CONVERSATION-TRAINING-VERIFICATION.md`. Team review/required remote CI
remain the integration gate; the worktree is retained for that review.
