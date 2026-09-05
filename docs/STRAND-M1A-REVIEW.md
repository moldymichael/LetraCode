# Strand M1a — local review

September 5, 2026. This is a development branch for review, not an installed update.

## Stabilization status (current)

The newer review reproduced five P2 defects after the earlier 132-test verification. This stabilization started from clean commit `886ff66` on the same branch and corrects all five: commit-time external memory edits, unsent first-message loss, startup failure from one unavailable project memory, orphaned memory after project deletion, and missing evidence references after repeated pauses.

Fresh results: **183 automated tests passed**, including **122 focused storage/UI/tool/continuation tests**; all 27 Python files compiled, shell syntax and whitespace checks passed. One real-Qwen continuation discovered and retrieved the oldest saved result after 25 seeded pauses and context compaction, without rerunning the original action. The owned engine stopped. Exact commands, failed-then-passing regression evidence and limitations appear in [VERIFICATION.md](VERIFICATION.md). The 132-pass figures below are preserved as history.

The fixed Strand root and default review for model-proposed memory saves remain appropriate for M1a. No installed application, live database, real writing files or working model was changed. No M1b work began. This is ready for independent re-review; interactive desktop checks remain below.

## Where the work lives

- Review checkout: `/home/miceoil/Projects/LetraCode-strand-m1a`, branch `codex/strand-m1a`.
- Starting commit: `45092f1b1ed2f3a6f3361864f7d8587a87fd82c6` (the context-overflow fix was already present).
- Stabilization base: `886ff6632969ba141cfe867faa5039df15cb747e`; stabilization is a separate commit on the same M1a branch.
- Original checkout remains `/home/miceoil/Projects/LetraCode`, branch `fix/bound-tool-result-context`, at that same commit. Its untracked `letracode/__pycache__/` and `tests/__pycache__/` were preserved.
- Current isolated data/logs: this checkout's `.stabilization/` and `stabilization-test-data/`. Earlier historical evidence remains at `/home/miceoil/Projects/strand-m1a-review-PGYug9/`; it was not changed during stabilization.
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

At commit time the actual original file is moved into a recovery directory, its bytes are checked, and the new file is published only if the active name is still absent. Every move uses Linux's no-replacement operation, including rollback. An ordinary editor's intervening save wins or produces an explicit conflict with preserved versions. This avoids relying on an advisory lock that other editors can ignore. The [Linux rename documentation](https://man7.org/linux/man-pages/man2/rename.2.html) specifies the no-replacement behavior and that open descriptors survive renames; [fsync documentation](https://man7.org/linux/man-pages/man2/fsync.2.html) explains why directory entries also need syncing.

Recovery records and retained original files live in `<memory-file-parent>/.strand-recovery/<filename>/`, are included in backups, and allow interrupted saves to recover before an empty default could be created. An editor may still write through an already-open descriptor after a save returns. Its edits remain in the retained original and cause an explicit conflict on the next read/reopen. To resolve that rare case: close external editors, copy both versions to a safe place, reconcile the active file with the `.before` file named by the error, then move the named `.json` recovery record out of that recovery directory and Reload. Retain the copies until satisfied. Recovery history grows with saves; no automatic cleanup or merge UI is included. The active pathname can be briefly absent during a save; interruption and racing recreation are regression-tested. Physical power-loss behavior was not tested.

A missing, inaccessible, malformed or oversized project memory now disables only its memory editor and sending from that affected scope until repaired. The error shows the path; an unsuccessful Reload keeps its draft. Other projects, existing conversations and editable project fields remain usable. Context explicitly marks unavailable memory rather than treating it as authoritative empty text.

Deleting a project now explains and archives its memory under `strand/.deleted-projects/<project-id>/`, with title, date, original path and operation status. The original inode is preserved, including malformed bytes and late writes from open editors. Database failures restore into an absent path only; concurrent versions are retained with a recovery message. Interrupted deletion can leave the project present with unavailable memory and a prepared archive record; recover from the path in that record before retrying. Pending save journals are resolved before deletion so later receipt inspection cannot recreate orphaned memory. Linked source files remain untouched. An archive over the supported 2 MiB size is preserved, but backup explicitly refuses it rather than silently dropping it.

The database migration is versioned and backs up version 1 before moving legacy project memory into files. The old editable database memory column is cleared only after successful migration. Migration/recovery, Undo and ZIP restoration are tested on disposable fixtures. Backups include Strand files, receipts and previous versions. Linked originals and model weights remain separate. A copied database retains linked paths: follow its `RESTORE.txt` and remove/retarget links before opening a test restoration.

Request accounting includes enabled tool definitions, the actual chat template, the current request, core instructions and reserved response tokens. This runtime supports `/apply-template` plus `/tokenize`; unsupported builds use an explicitly labeled conservative estimate. Optional retrieved text and saved-result previews can shrink; core instructions and the current request are never silently cut. A request that still cannot fit pauses with its evidence saved.

Oversized action batches execute nothing and save an outcome for every call. The model gets one bounded correction opportunity when context permits. Repeated oversized batches, true context exhaustion and the ten-round action limit save a pause checkpoint. A new user message can continue from saved results. `read_tool_result` reads pages from the current chat's saved outcomes instead of rerunning commands. “Complete saved result” means the full bounded tool response; this does not remove existing source-file/read/search limits.

Repeated checkpoints retain the latest 20 result IDs across the chat and its total result count. `list_tool_results` discovers older IDs in bounded pages, scoped to the current chat; keep its `through_id` while following `next_after_id` so pagination finishes even as the app saves the catalog responses themselves. This discovery works with Computer/Web off when Actions is on. Full result bodies remain in SQLite and are retrieved with `read_tool_result`.

## Deliberate engineering choices

- Kept the existing Fedora/PySide6/SQLite/llama.cpp implementation and working GGUF. No new dependency was needed.
- Extended the already-present overflow fix instead of cherry-picking or rebuilding it.
- Chose `<data-dir>/strand` for the ordinary folder in M1a. This keeps isolated launches and backup restoration straightforward; selecting an arbitrary external Strand root is deferred.
- Kept default review for model-proposed memory saves. Interpreting “remember this” with a word-matching shortcut would let unrelated source text or an ambiguous scope authorize a write. The exact learning-file grant is an explicit user control.
- Added locking before migration, bounded/no-follow file access and conflict-safe editor drafts because the actual persistence paths needed them.
- Preserved LetraCode application branding and existing navigation. Separate Chat/Fine-Tuning areas, reply/export relabeling, indexed source discovery, sustained reading jobs and VS Code navigation belong to M1b. Training/adoption/rollback belong to M2.

## Historical M1a verification evidence (before stabilization)

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

## Safe review launch (fresh stabilization fixture)

In Konsole, run these two lines together:

```bash
cd /home/miceoil/Projects/LetraCode-strand-m1a
python3 -m letracode --data-dir /home/miceoil/Projects/LetraCode-strand-m1a/.stabilization/review-data
```

This launches development source with two fresh synthetic projects, an unsent first-message draft in Stabilization Draft A, and memory Undo receipts. Computer/Internet are off; Actions is on. The isolated configuration points to the existing GGUF at context 8192/12 GPU layers. Model weights load only when you send a request. Avoid simultaneously loading the everyday model in another app while testing. Fixture IDs/paths are in `.stabilization/review-fixture.json`; no real source files are linked.

1. Open **Strand identity & memory…**. Inspect identity, working preferences, global memory and the learning record. Leave the learning grant off unless you want to test it.
2. Select **Stabilization Draft A**, which initially has no chat. Make a pane memory edit, then externally edit the ordinary memory file shown there. Send the existing composer draft. Confirm the conflict preserves the external file and unsent message through reopening. Copy the pane draft before using Reload.
3. On this synthetic project only, temporarily rename its memory file. Reopen: Draft B and existing conversations should remain accessible; Draft A should show unavailable memory. Restore the file and Reload. Test project deletion after creating another disposable project: confirmation should explain archiving and completion should identify the recovery path.
4. Check scrolling, dialogs, keyboard input, Stop during a reply, and reopening the test app. **Close the test window** to unload its own engine; **Stop** cancels the current turn. Do not use the installed launcher for this review.

M1a stops here for review. The next bounded task, if requested, is M1b source discovery and sustained reading. Installation/live-data migration require a separate request.
