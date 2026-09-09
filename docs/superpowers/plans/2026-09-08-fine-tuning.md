# Local Fine-Tuning Implementation Plan

> Execute with parallel subagents and a whole-change review. Current user authorization
> covers implementation; the recovered plan delegates technical choices.

**Goal:** A separate desktop workflow for reviewed examples, real local LoRA training,
held-out comparison, explicit adapter adoption and rollback.

**Architecture:** SQLite owns reviewed examples and run snapshots. A standalone Python
backend runs in a selected training environment. A Qt worker owns its process lifecycle;
the workspace displays stored evidence and changes the engine only on user action.

**Tech Stack:** Python 3.11+, PySide6, SQLite, optional PyTorch/Transformers/PEFT,
local llama.cpp conversion and inference.

**Spec:** `docs/superpowers/specs/2026-09-08-fine-tuning-design.md`

## Global constraints

- Preserve Windows/Linux behavior, chats, Memory, and existing user work.
- No automatic downloads, live-model training, automatic adoption or cloud inference.
- Base training model and tokenizer must be local; trust_remote_code=False.
- Every run uses approved immutable data with disjoint training/evaluation prompts.
- Preserve failed runs and previous versions; stopping never reports completion.

## Task 1: Reviewed examples and run records

Files: `letracode/training.py`, `tests/test_training.py`.

Interfaces: `TrainingConfig` fields python_executable, base_model, base_gguf,
llama_cpp_dir, epochs (1), learning_rate (0.0002), rank (8), max_length (512),
batch_size (1), seed (42), device ('cpu'). `validate()` checks values/local paths.
`TrainingRepository(store)` initializes additive tables. Examples are dicts with id,
prompt, response, split ('train'/'eval'), approved, source, created/updated.
Methods: examples(), save_example(prompt,response,split='train',approved=False,
source='',example_id=None), delete_example(id), import_jsonl(path), export_jsonl(path),
create_run(config)->dict, runs(), run(id), update_run(id,status,report=None,error=''),
run_directory(id)->Path. Run dicts include id, status, config, examples, report, error,
created, updated. create_run writes config.json, train.jsonl and eval.jsonl snapshots.
Backend config.json is flat dataclass fields; JSONL rows contain prompt and response.

- [ ] Write and observe failing behavior tests for drafts, review invalidation,
  atomic import validation, duplicate prompts/split leakage, immutable snapshots.
- [ ] Implement bound validation and SQLite transactions using Store.connection().
- [ ] Test persisted runs and terminal state protections on reopen.

## Task 2: Actual local training backend

Files: `letracode/training_backend.py`, `tests/test_training_backend.py`,
`packaging/training-requirements.txt`.

Interface: selected Python executes this script with `--run-dir PATH`; no Qt imports.
Reads flat config.json and train/eval JSONL; writes adapter/, adapter.gguf (if
conversion succeeds), report.json. Emits newline JSON progress objects with a
message. report contains base_loss, candidate_loss, eval_examples, adapter_path,
adapter_gguf, conversion_error, package_versions and relevant hashes. Exit 0 means
optimization and evaluation complete; conversion_error may remain for inspection.

- [ ] Write tests for response-only labels, length rejection and offline model validation.
- [ ] Implement local-only text Llama LoRA with genuine gradient updates and finite loss.
- [ ] Measure base/candidate on identical held-out responses and retain sample outputs.
- [ ] Save adapters, provenance and report; invoke converter without a shell.
- [ ] Run tiny locally created model proof in an isolated environment if available.

## Task 3: Desktop workflow and process ownership

Files: `letracode/training_ui.py`, `letracode/training_worker.py`, `letracode/ui.py`,
`letracode/store.py`, `tests/test_training_ui.py`, `tests/test_training_worker.py`.

- [ ] Exercise real repository-backed Qt example review and stored version selection.
- [ ] Add Chat/Fine-Tuning navigation, example editing/import/export, simple configuration
  plus advanced controls, progress, Stop, results, output folder and run history.
- [ ] Start selected Python with list argv, offline environment and owned process tree;
  validate report before recording success; record errors and stop/interruption states.
- [ ] Stop chat inference before training; prevent conflicting chat/model operations.
- [ ] Preserve records in SQLite backup and explain separate output/weights backups.

## Task 4: Adapter inference and adoption

Files: `letracode/engine.py`, `letracode/dialogs.py`,
`tests/test_training_adoption.py`, existing engine tests.

- [ ] Test optional EngineConfig.lora_path launch argument and invalid-path rejection.
- [ ] Preserve optional adapter when editing Model Setup and allow explicit clearing.
- [ ] Adoption loads a selected converted adapter with its base using a temporary engine,
  saves previous/current configuration only after success, and supports rollback.
- [ ] Verify failed adoption leaves saved engine config intact and old artifacts retained.

## Task 5: Whole-change verification and documentation

- [ ] Run full offscreen pytest and git diff --check.
- [ ] Inspect workspace screenshots and fix layout/usability defects.
- [ ] Request independent review and address material findings.
- [ ] Document setup, supported model limits, actual proof, usage and remaining hardware
  validation in README and docs/FINE-TUNING.md; record exact test outcomes.

## Progress

Baseline: active authoritative worktree at 4e13858; 722 passed, 15 skipped,
one existing process-fork warning. New branch codex/assistant-fine-tuning.

## September 9 continuation

The requested GitHub version check fetched `main` at `c9f6392` (0.3.0). The
original project folder was still a divergent 0.2.0 checkout with unrelated
uncommitted work. The unfinished fine-tuning implementation above was copied
from its preserved worktree into a fresh checkout of current `main`, branch
`codex/fine-tuning-and-thinking`. Original checkouts and installed user data
remain unchanged. Source version is 0.4.0.

- [x] Complete reviewed datasets, immutable snapshots and interrupted-run recovery.
- [x] Complete backend/worker validation and training process ownership.
- [x] Add persistent per-example drafts and protect the previous model on repeat adoption.
- [x] Add live reasoning transport, separate saved metadata and safe collapsible chat rendering.
- [x] Guard chat Retry during training and capture training-editor drafts in backup.
- [x] Include the trainer script/dependency list in source and Windows package inputs.
- [x] Inspect actual Qt views at 1260×820 and 850×570.

Current test and real-model evidence is recorded in
`docs/FINE-TUNING-VERIFICATION.md`; earlier unchecked steps above describe the
recovered plan's state, not the completed implementation.
