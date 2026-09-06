# Strand M1a — local review

September 5, 2026. This is a development branch for review, not an installed update.

M1a at `cdfdf9d` has since received independent technical acceptance; broader manual KDE checks were deferred. The subsequent, separately developed source-editing milestone is documented in [Supervised coding proof](SUPERVISED-CODING-PROOF.md). Its prerequisite changes and real-model evidence are separate from the historical M1a results below.

Current supervised-coding result: prerequisite commit `483fb00` passes **280 automated tests**, but the actual coding proof **did not pass**. After inspection and a passing unchanged baseline, Qwen reached the existing 300-second completion deadline before producing its first edit request. No model-authored regression or patch was published. Six native command approvals, the saved continuation, a separately attributed fixture-setup correction and the failed outcome are retained in the proof document. The staged source fixture and untracked sentinel are unchanged. Review the prerequisite diff and failed transcript independently; broader KDE checks and real coding completion remain outstanding.

## Undo lifecycle stabilization (current)

This focused pass starts from clean `932996ce27554647ea29471cc7af93bc5a47f0ae` on `codex/strand-m1a`. The 197-test baseline passed again, but new reproductions confirmed the same-second ordering defect and connected Undo safety/interface problems. The results below from earlier passes remain historical evidence.

New saved-change receipts now carry a persisted increasing sequence, allocated under the existing shared write lock. The selected file's latest confirmed change is selected independently of clock precision, clock rollback or random receipt IDs. Filtering still happens before the scope limit, so unrelated saves do not hide project history. Undo verifies this latest-change rule in the backend as well as in the menu: an older receipt cannot restore over a later save merely because the file's bytes happen to match again.

Prepared receipts now require their own durable write-completion record before they can authorize Undo. An unresolved later write blocks selection of an older change. Older receipts without sequences remain visible, but only a sole receipt or an explicit chain of Undo references can prove their order. Matching content hashes cannot prove chronology across external edits. Ambiguous legacy history has no automatic selection and refuses Undo; a fresh confirmed Save establishes an ordered point for subsequent changes. Existing receipts and previous contents are retained for deliberate recovery.

Clicking Undo now keeps an unsaved editor draft separately and asks for Save or Reload first. Failed Undo preserves the selected receipt and its conflict message. Reload refreshes history; an unavailable-file error after Undo stays visible. A conversation's Undo link no longer saves unrelated editor drafts. Each new menu entry displays its sequence, and an Undo receipt is identified as “Undo of …”. Undoing that new receipt restores the change again; this remains a selected-change operation, not a cascading undo stack.

Fresh results: **220 automated tests passed**, including **23 new cases** and all prior M1a regression coverage; the focused storage/UI suite passed **95 tests**. All 27 Python files compiled, shell syntax and whitespace checks passed. No real-model run was needed because the changed behavior is entirely in storage and Qt controls. Exact commands, regression failures before repair and fresh results are in [VERIFICATION.md](VERIFICATION.md). This pass is a separate local commit for independent review. No installed app, live data, writing files, dependency or working model was changed; no push, merge, M1b or training was performed.

## Historical second stabilization (`932996c`)

The second stabilization pass starts from clean `cc7b48d` on `codex/strand-m1a` and fixes the three remaining findings: migration rollback losing external corrections, context packing stopping before reaching a fitting allowance, and unrelated saves hiding a project's Undo history. The latest independent review confirmed the previous five scenarios were resolved; these three defects predate `cc7b48d`, with no confirmed regression attributed to that commit.

Fresh verification: **197 automated tests passed**, including 14 new regression cases; all 27 Python files compiled, shell syntax and whitespace checks passed. The supplied context reproduction now generates with **32073 / 32768** estimated tokens. Migration retains external corrections and refuses conflicting retries. The actual Undo menu remains usable after 51 unrelated global saves. No new real-model run was needed for this pass; the Qwen checks below remain historical. Exact commands and observed results are in [VERIFICATION.md](VERIFICATION.md).

The three requested repairs are ready for independent re-review. A separate pre-existing Undo ordering issue remains: same-second receipts can appear out of order, so the default selection can fail safely with a conflict. It is documented in [VERIFICATION.md](VERIFICATION.md) and was not bundled into this pass. The fixed Strand directory and default memory-save review remain unchanged. Nothing was installed, pushed or merged; real data, writing and the working model remain untouched. M1b and training were not started.

## Historical first stabilization (`cc7b48d`)

The newer review reproduced five P2 defects after the earlier 132-test verification. This stabilization started from clean commit `886ff66` on the same branch and corrects all five: commit-time external memory edits, unsent first-message loss, startup failure from one unavailable project memory, orphaned memory after project deletion, and missing evidence references after repeated pauses.

Results at that stage: **183 automated tests passed**, including **122 focused storage/UI/tool/continuation tests**; all 27 Python files compiled, shell syntax and whitespace checks passed. One real-Qwen continuation discovered and retrieved the oldest saved result after 25 seeded pauses and context compaction, without rerunning the original action. The owned engine stopped. Exact commands, failed-then-passing regression evidence and limitations appear in [VERIFICATION.md](VERIFICATION.md). The 132-pass figures below are preserved as history.

The fixed Strand root and default review for model-proposed memory saves remain appropriate for M1a. No installed application, live database, real writing files or working model was changed. No M1b work began. This is ready for independent re-review; interactive desktop checks remain below.

## Where the work lives

- Review checkout: `/home/miceoil/Projects/LetraCode-strand-m1a`, branch `codex/strand-m1a`.
- Starting commit: `45092f1b1ed2f3a6f3361864f7d8587a87fd82c6` (the context-overflow fix was already present).
- First stabilization base: `886ff6632969ba141cfe867faa5039df15cb747e`; resulting commit `cc7b48d`.
- Second stabilization base: `cc7b48db0434bd86ba35b4c5c35dfbd5f9c62e04`; resulting commit `932996c`.
- Undo lifecycle stabilization base: `932996ce27554647ea29471cc7af93bc5a47f0ae`; a separate commit on the same branch.
- Original checkout remains `/home/miceoil/Projects/LetraCode`, branch `fix/bound-tool-result-context`, at `45092f1`. Its untracked `letracode/__pycache__/` and `tests/__pycache__/` were preserved.
- Current isolated logs: `.stabilization/undo-lifecycle/`; test data: `stabilization-test-data/undo-lifecycle*`. Previous pass logs/data remain in `.stabilization/pass2/` and `stabilization-test-data/pass2*`. Earlier `.stabilization/` evidence and `/home/miceoil/Projects/strand-m1a-review-PGYug9/` are retained.
- No installation, live database migration, model replacement, model download, training, push, or merge into the original branch was performed. The earlier read-only live-data check reported schema version 1 and context/GPU settings 32768/12; this Undo pass did not reopen live data.

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

The Undo menu now selects up to 50 receipts **for the chosen scope/project**, so global activity or another project cannot hide that project's entries. New receipts sort by their durable save sequence, with the latest confirmed change selected. Dates and IDs are only presentation details for uncertain legacy entries. Older receipts remain on disk. Only the latest established change can be undone; later saves, changed contents or uncertain history produce an explicit refusal. Scope validation and existing write-conflict protections still apply; choosing a history entry does not grant model write permission.

Memory editor saves check the file version before replacement. Conflicting editor text is retained separately in SQLite so closing the window does not lose it. Reload uses the external file; copy any local draft you want to keep before reloading. The same check applies to Send and Retry. Native internal action links are removed from model-written Markdown, so a model cannot disguise an Undo button as a documentation link.

At commit time the actual original file is moved into a recovery directory, its bytes are checked, and the new file is published only if the active name is still absent. Every move uses Linux's no-replacement operation, including rollback. An ordinary editor's intervening save wins or produces an explicit conflict with preserved versions. This avoids relying on an advisory lock that other editors can ignore. The [Linux rename documentation](https://man7.org/linux/man-pages/man2/rename.2.html) specifies the no-replacement behavior and that open descriptors survive renames; [fsync documentation](https://man7.org/linux/man-pages/man2/fsync.2.html) explains why directory entries also need syncing.

Recovery records and retained original files live in `<memory-file-parent>/.strand-recovery/<filename>/`, are included in backups, and allow interrupted saves to recover before an empty default could be created. An editor may still write through an already-open descriptor after a save returns. Its edits remain in the retained original and cause an explicit conflict on the next read/reopen. To resolve that rare case: close external editors, copy both versions to a safe place, reconcile the active file with the `.before` file named by the error, then move the named `.json` recovery record out of that recovery directory and Reload. Retain the copies until satisfied. Recovery history grows with saves; no automatic cleanup or merge UI is included. The active pathname can be briefly absent during a save; interruption and racing recreation are regression-tested. Physical power-loss behavior was not tested.

A missing, inaccessible, malformed or oversized project memory now disables only its memory editor and sending from that affected scope until repaired. The error shows the path; an unsuccessful Reload keeps its draft. Other projects, existing conversations and editable project fields remain usable. Context explicitly marks unavailable memory rather than treating it as authoritative empty text.

Deleting a project now explains and archives its memory under `strand/.deleted-projects/<project-id>/`, with title, date, original path and operation status. The original inode is preserved, including malformed bytes and late writes from open editors. Database failures restore into an absent path only; concurrent versions are retained with a recovery message. Interrupted deletion can leave the project present with unavailable memory and a prepared archive record; recover from the path in that record before retrying. Pending save journals are resolved before deletion so later receipt inspection cannot recreate orphaned memory. Linked source files remain untouched. An archive over the supported 2 MiB size is preserved, but backup explicitly refuses it rather than silently dropping it.

The database migration is versioned and backs up version 1 before moving legacy project memory into files. The old editable database memory column is cleared only after successful migration. Migration/recovery, Undo and ZIP restoration are tested on disposable fixtures. Backups include Strand files, receipts and previous versions. Linked originals and model weights remain separate. A copied database retains linked paths: follow its `RESTORE.txt` and remove/retarget links before opening a test restoration.

If a later migration step fails, SQLite rolls back and **already-created memory files stay in place**. Removing them after a contents check could lose an intervening edit or a later save through an open editor descriptor. Retaining them avoids that deletion entirely. Retry reuses matching files without replacing their inodes; a file conflicting with nonempty legacy text stops migration with its path shown. Both the retained external correction and backed-up legacy text remain available for deliberate reconciliation. No automatic overwrite or automatic choice between divergent versions is made.

Request accounting includes enabled tool definitions, the actual chat template, the current request, core instructions and reserved response tokens. This runtime supports `/apply-template` plus `/tokenize`; unsupported builds use an explicitly labeled conservative estimate. Optional retrieved text and saved-result previews can shrink; core instructions and the current request are never silently cut. A request that still cannot fit pauses with its evidence saved.

Several optional-context allowances can produce the same text. The worker now continues reducing the allowance through zero instead of mistaking an unchanged excerpt for proof that the request cannot fit. This permits the reviewed fallback-counter request to generate while preserving the stop for truly oversized core instructions or user input.

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

## Safe Undo review launch (fresh disposable fixture)

Run these two lines together in Konsole:

```bash
cd /home/miceoil/Projects/LetraCode-strand-m1a
python3 -m letracode --data-dir /home/miceoil/Projects/LetraCode-strand-m1a/stabilization-test-data/undo-lifecycle/manual-review
```

This fixture has one synthetic project with two tied-timestamp saves followed by 51 unrelated global saves. No model is configured or needed, no real sources are linked, and Computer/Internet/Actions are off. Fixture details and the seed script are in `.stabilization/undo-lifecycle/`.

1. Open **Settings → Strand identity & memory…**, then **This project’s memory**. Receipt **#2** should be selected; Undo should restore **First synthetic save**. Global memory should stay at **Unrelated global save 51**.
2. Make an editor draft and click Undo. It should keep the draft separately and request Save or Reload. Save explicitly, then Undo; close and reopen to check the selected receipt and restored contents. An entry labeled **Undo of …** represents a new change; undoing it restores the change again.
3. Edit only this fixture's memory file externally, using the path displayed in the dialog. With no pending editor draft, Undo should report a conflict and preserve that correction. Check keyboard selection, messages, and reopening in KDE. Reload discards the separate draft only after successfully reading the file, so copy any draft you want to retain first.

Close the development window when finished. The prior fixture below is retained for historical review; this fresh fixture is for the remaining interactive Undo checks.

## Historical first-stabilization review fixture (retained)

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
