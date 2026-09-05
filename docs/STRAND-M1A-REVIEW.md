# Strand M1a — local review

September 5, 2026. This is a development branch for review, not an installed update.

## Where the work lives

- Review checkout: `/home/miceoil/Projects/LetraCode-strand-m1a`, branch `codex/strand-m1a`.
- Starting commit: `45092f1b1ed2f3a6f3361864f7d8587a87fd82c6` (the context-overflow fix was already present).
- Original checkout remains `/home/miceoil/Projects/LetraCode`, branch `fix/bound-tool-result-context`, at that same commit. Its untracked `letracode/__pycache__/` and `tests/__pycache__/` were preserved.
- Isolated review data and logs: `/home/miceoil/Projects/strand-m1a-review-PGYug9/`.
- No installation, live database migration, model replacement, model download, training, push, or merge into the original branch was performed. The live database still reports schema version 1; its context/GPU settings remain 32768/12.

## What M1a changes

Strand loads an editable identity and working preferences in every global or project chat. Authoritative memory lives in ordinary files beneath the selected app data directory:

```text
strand/
  identity/strand.md
  identity/preferences.md
  memory/global.md
  memory/projects/<project-id>.md
  learning/programming.md
  .history/                    previous file contents for Undo
  .receipts/                   saved-change records
```

A project chat receives global memory, its own project memory and the programming record. A global chat receives no project memory. External corrections are read again on the next user turn. Large memories are selected within a budget and labeled partial; `read_memory` retrieves further pages. Ordinary Strand files have a 2 MiB supported-size limit; oversized external files are left intact and produce an explicit error.

`remember` appends an entry with an ID, date, origin and scope. The app chooses the destination from the active chat rather than accepting a model-supplied path or project ID. By default, a dialog shows the scope, destination, text and append preview for approval. Saved text/location and Undo appear in the conversation. The user can optionally grant automatic appends to **only** `learning/programming.md`; that grant permits no source edits, identity changes, commands, other memories or training.

Memory editor saves check the file version before replacement. Conflicting editor text is retained separately in SQLite so closing the window does not lose it. Reload uses the external file; copy any local draft you want to keep before reloading. The same check applies to Send and Retry. Native internal action links are removed from model-written Markdown, so a model cannot disguise an Undo button as a documentation link.

The database migration is versioned and backs up version 1 before moving legacy project memory into files. The old editable database memory column is cleared only after successful migration. Migration/recovery, Undo and ZIP restoration are tested on disposable fixtures. Backups include Strand files, receipts and previous versions. Linked originals and model weights remain separate. A copied database retains linked paths: follow its `RESTORE.txt` and remove/retarget links before opening a test restoration.

Request accounting includes enabled tool definitions, the actual chat template, the current request, core instructions and reserved response tokens. This runtime supports `/apply-template` plus `/tokenize`; unsupported builds use an explicitly labeled conservative estimate. Optional retrieved text and saved-result previews can shrink; core instructions and the current request are never silently cut. A request that still cannot fit pauses with its evidence saved.

Oversized action batches execute nothing and save an outcome for every call. The model gets one bounded correction opportunity when context permits. Repeated oversized batches, true context exhaustion and the ten-round action limit save a pause checkpoint. A new user message can continue from saved results. `read_tool_result` reads pages from the current chat's saved outcomes instead of rerunning commands. “Complete saved result” means the full bounded tool response; this does not remove existing source-file/read/search limits.

## Deliberate engineering choices

- Kept the existing Fedora/PySide6/SQLite/llama.cpp implementation and working GGUF. No new dependency was needed.
- Extended the already-present overflow fix instead of cherry-picking or rebuilding it.
- Chose `<data-dir>/strand` for the ordinary folder in M1a. This keeps isolated launches and backup restoration straightforward; selecting an arbitrary external Strand root is deferred.
- Kept default review for model-proposed memory saves. Interpreting “remember this” with a word-matching shortcut would let unrelated source text or an ambiguous scope authorize a write. The exact learning-file grant is an explicit user control.
- Added locking before migration, bounded/no-follow file access and conflict-safe editor drafts because the actual persistence paths needed them.
- Preserved LetraCode application branding and existing navigation. Separate Chat/Fine-Tuning areas, reply/export relabeling, indexed source discovery, sustained reading jobs and VS Code navigation belong to M1b. Training/adoption/rollback belong to M2.

## Verification evidence

Environment: Fedora Linux 44 KDE Plasma, system `/usr/bin/python3` 3.14.7, PySide6 6.11.2, pytest 8.4.2, SQLite 3.51.2 with FTS5. The checkout's interpreter and system Qt were used; no virtual environment or system packages were changed.

Baseline command, before code changes:

```bash
QT_QPA_PLATFORM=offscreen PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -p no:cacheprovider
```

**Baseline: 78 passed in 5.80 seconds.** **Final: 132 passed in 7.40 seconds**, with zero failures/skips. Full results and commands are recorded in [VERIFICATION.md](VERIFICATION.md). New tests exercise identity/scope/external edits, guarded writes, receipts/Undo, missing/oversized files, symlink and FIFO swaps, simultaneous writes, interrupted migration/retry, safe restoration, memory permissions, UI drafts/retry/links, template accounting, complete paired outcomes, pause/resume and saved-result pagination. Scripted model/HTTP peers establish application behavior, not model quality.

### Actual local model

Observed GGUF metadata: `Qwen3.6-35B-A3B`, architecture `qwen35moe`, file type 15 (`MOSTLY_Q4_K_M` in the installed llama.cpp constants), GGUF v3, 22,134,528,992 bytes. Path: `/home/miceoil/bigdrive/Jan/llamacpp/models/Qwen3_6-35B-A3B-UD-Q4_K_M/model.gguf`. This identifies observed metadata; no publisher revision or full-file hash was established.

Engine: `/home/miceoil/src/llama.cpp/build/bin/llama-server`, build `0.4.0-dev` / build 1 / commit `4d91760` (source `4d9176092d00586775af140581bb0b558ddc4389`). Actual configured profile: context 32768, GPU layers 12, threads 8, reply reserve 3072, temperature 0.7. A second isolated profile used context 8192 with the other settings retained. Instant mode was used; no claim about Thinking-mode quality.

The API probe included Unicode/code and all tool schemas. Template/tokenizer counts matched `/v1/chat/completions/input_tokens`: **1185 = 1185** in the first probe and **1180 = 1180** in the later probe with different text. The local implementation uses the same parser for templating and completions, and the probe explicitly sets `add_special=true, parse_special=true`. See [official server APIs](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md).

Observed results on synthetic data:

| Check | Actual observation |
|---|---|
| Global identity | “I am Strand, running in LetraCode.” (4.88 s at 32768; later 8192 check also correct) |
| Project memory | Model called `remember`, saved cobalt only to its project file, then confirmed (16.45 s; later 16.24 s at 8192) |
| External correction | A new chat used amber after direct file correction (4.62 s; later 4.13 s) |
| Other project | Answered green from the other project's conflicting memory (3.11 s) |
| Context-heavy continuation, 32768 | Correct ALPHA-17/BETA-29 markers; 18368 prompt + 3072 reply + 128 safety tokens; 80.66 s |
| Same continuation, 8192 | Correct markers after compaction; 4992 + 3072 + 128 = 8192 tokens; 26.88 s |
| Saved-result retrieval | Actually called `read_tool_result` and returned DELTA-41 with computer/web tools off; archived output unchanged (11.14 s) |
| Cancellation | Cancelled actual generation, stopped its owned engine, returned in 2.40 s |
| Long intact core, final code | Preserved all 7000 instruction characters; 1794 prompt + 3072 reply + 128 safety tokens at context 8192; answered Strand (10.42 s) |

The reading probes seed real file-tool outcomes from synthetic text; the saved-command-output probe seeds an archived synthetic outcome. No real command was executed to create or recover that output. These are real model continuations/tool calls, not a test of autonomous whole-book reading. Small successful probes do not establish general teaching/literary quality or reliable model behavior on all prompts.

The RTX 2060 SUPER had about 7.1 GiB GPU memory in use while the 32768 profile was loaded (7198 MiB measured after load; later 7237 MiB observed), with roughly 0.5 GiB free. Load took about 3 seconds. The server logged an auto-fit warning because GPU layers were explicitly set to 12; it nevertheless loaded and completed the checks. Every test-owned engine was stopped; the working model/configuration was retained.

Raw local evidence: `model-probe.log`, `real-model-results.json`, `model-metadata.json`, `final-model-check.log`, `final-model-results.json`, `core-budget-model-results.json` and each isolated data folder's `engine.log`, under the review folder above. The scripts used are retained beside them and specify synthetic destinations explicitly. The first probe tested code at `93a270f`; saved-result/cancellation checks used `7d6a376`; the final long-core check used `9496958`, the final behavior revision. Later changes only remove trailing whitespace and document verification.

### Fedora display versus manual review

Offscreen Qt behavior tests passed. The main window and Strand settings dialog were rendered and their images inspected. The actual development entry point also launched on **Wayland**, using `ui-demo`, and closed its own window after a bounded capture. The live widget image was inspected and inherited the desktop's dark style. Logs: `live-launch.log`; captures: `strand-live-widget.png`, `strand-main-offscreen.png`, `strand-settings-offscreen.png`.

This verifies display launch and captured rendering. It does not certify every interactive KDE/dialog/keyboard flow; the short checklist below remains for user review. The `code` CLI was unavailable in this session; no editor installation or VS Code integration was attempted.

## Safe review launch

In Konsole, run these two lines together:

```bash
cd /home/miceoil/Projects/LetraCode-strand-m1a
python3 -m letracode --data-dir /home/miceoil/Projects/strand-m1a-review-PGYug9/ui-demo
```

This launches development source with synthetic review data. It already has two synthetic projects and an Undo receipt, Internet is off, and the model configuration points to the existing working GGUF. Model weights load only when you send a request. Avoid simultaneously loading the everyday model in another app while testing; the GPU has little spare memory at this setting.

1. Open **Strand identity & memory…**. Inspect identity, working preferences, global memory and the learning record. Leave the learning grant off unless you want to test it.
2. In the synthetic Draft A chat, try `Remember this in this project: the bridge is closed.` Review the text/path, approve, and use the receipt's **Undo this save**.
3. Edit that project's memory using the ordinary file shown in the pane, then send a new question. Confirm the correction is used and Draft B remains separate. To test a conflict, make an unsaved pane edit too; the file should remain intact and the pane should explain its retained draft.
4. Check scrolling, dialogs, keyboard input, Stop during a reply, and reopening the test app. **Close the test window** to unload its own engine; **Stop** cancels the current turn. Do not use the installed launcher for this review.

M1a stops here for review. The next bounded task, if requested, is M1b source discovery and sustained reading. Installation/live-data migration require a separate request.
