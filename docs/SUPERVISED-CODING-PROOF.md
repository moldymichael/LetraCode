# Strand supervised coding proof

Historical milestone record. Its original results and limits are preserved below.
For the current reliability branch, see [Current implementation state](CURRENT-STATE.md)
and [Reliability verification](RELIABILITY-VERIFICATION.md).


This milestone builds on accepted M1a commit `cdfdf9d19d9e19149d168a0b4942564de10e0f54`. Prerequisite work lives in `/home/miceoil/Projects/LetraCode-strand-development`, branch `codex/strand-development-loop`. The accepted M1a checkout and the original LetraCode checkout are preserved.

## Purpose and boundaries

The intended proof is that Strand, using the existing local model through LetraCode, can inspect code, author a regression, observe a failing test, implement a small improvement, verify it and leave a reviewable Git diff. Codex's prerequisite implementation is not that proof.

Existing linked-file reads, searches, approval dialogs, command execution, result storage and continuation provide the loop. This milestone adds no IDE, Git UI, source index, task-wide command grant, training or deployment. Each edit and command still requires its own approval; commands are unsandboxed. Linking a project directory does not confine a command to that directory or authorize a write.

## Prerequisite behavior

- `read_file` returns the whole-file SHA-256 from the same byte snapshot as its source text. It identifies PDF/DOCX extraction as read-only, with no editable source hash.
- `edit_file(path, expected_sha256, old_text, new_text)` requires one exact, nonempty old fragment. Stale hashes, absent matches and multiple matches fail without editing. Small edits preserve the remaining bytes and file mode. Empty replacement text is allowed.
- `write_file` requires `expected_sha256`: a current source hash for replacement, or JSON null for create-only behavior. Previously saved tool calls are history, not requests to execute against the new schema.
- Both editing tools show the proposed diff, including visible markers for newline/BOM differences, and retain old bytes. Publication uses the existing guarded-write protocol with a separate adjacent `.letracode-recovery` namespace. Competing files and original inodes are retained rather than overwritten, including later writes through an already-open descriptor.
- The worker rebuilds bounded source context before each model request, including after commands that fail after changing files. Token accounting, intact task instructions and historical tool results remain in place.
- Command results include the exact command, working directory and timeout alongside exit/output/cancellation information, so paged recovery can identify what actually ran.

Source recovery metadata is inside an untrusted repository. An unfinished record therefore reports the retained versions and requires deliberate reconciliation; reading source cannot authorize an automatic pathname restoration. In-flight rollback belongs to the already-approved operation. App-owned Strand memory keeps its existing automatic interruption recovery.

Source recovery artifacts remain adjacent to their source for filesystem-safe moves. The disposable clone excludes them locally from Git status, while retaining them for inspection. No automatic pruning is added. Ordinary source backups in the app's `file-backups` directory are not included in the existing app ZIP; preserve the source checkout and recovery files separately.

## Automated verification

Fresh baseline on Fedora: **220 passed in 86.52 seconds**, using offscreen Qt and disposable fixtures. The full suite from accepted M1a remains the baseline; historical results are not substituted for new verification.

Final prerequisite verification: **280 passed in 85.19 seconds**, with zero failures/skips. All 30 Python files compiled without bytecode writes; each of the three shell scripts passed its own syntax check; whitespace checks passed. Exact commands and regression evidence are recorded in `VERIFICATION.md`. Raw red/green evidence remains under `.stabilization/coding-proof/`; visible disposable fixtures are under `stabilization-test-data/coding-proof/`.

## Real-Strand acceptance

The coding task is deliberately separate from the prerequisites: add accurate `truncated` reporting to `list_files`, covering exactly 300 eligible entries, additional eligible entries, and hidden-only overflow while preserving ordering, existing fields and returned entries. Neither the task implementation nor its regression is supplied by Codex.

The preparation script creates a separate local clone from the verified prerequisite commit, removes remotes, disables hooks locally, stages an unrelated comment in the source file and creates an unrelated untracked sentinel. A separate disposable app data folder pins the task, allowed files, test commands and correction limit in project Instructions. The running app uses the prerequisite checkout, not the model's target clone.

The observation script uses the actual MainWindow, ConversationWorker, ToolExecutor and LocalEngine. It records actual model requests/replies, native approval decisions, transcript and final Git state. It never approves an action or supplies code/test outcomes. A restrictive observer guard denies a fourth source implementation attempt; it cannot grant permissions. The model must author the test, observe its failure, implement, verify and report. At most two correction attempts after the initial implementation are permitted; an uncompleted task is recorded honestly.

Preparation/observation scripts are retained in `.stabilization/coding-proof/prepare_acceptance.py` and `run_acceptance.py`. The final read-only audit is `audit_acceptance.py` in that directory.

### Observed outcome: coding proof did not pass

The native Wayland run used prerequisite commit **`483fb00083adf5f5b7a2890ca3bae4d3af4d2d2b`**, with the target clone on `strand/coding-proof` at the same commit. Two worker turns produced 19 model requests, 18 completed tool-call replies/results and six native command approvals, all approved. The first turn paused after ten action rounds; Codex explicitly continued the saved task with the original Instructions and correction limit. The second turn ended at the existing **300-second model completion deadline** while generating a proposed regression-test edit. There was no completed edit/write proposal, authored regression on disk or implementation attempt. The two permitted correction attempts were consequently not reached. No target patch was repaired or supplied by Codex.

The partial final assistant message says it will append a regression; the next persisted notice reports `Local model completion timed out at its request deadline`. The runtime was still generating at about 3.38 tokens/second immediately before cancellation (657 generated tokens observed). That request spent about 104 seconds processing its prompt. This is a runtime/deadline limitation of the exercised setup, not evidence that a completed test or patch was wrong. The deadline predates this milestone and is unchanged from accepted M1a. All recorded request totals, including reply/safety reserves, were within 32,768 tokens: 9,095–26,861 overall. No context-overflow failure occurred.

The model repeatedly inspected already-read source and tests and chose a shell `cat` for an ordinarily readable sentinel. This adds real latency and approval overhead. Its coding accuracy, correction ability and truthful final completion remain unverified because it never reached those stages. Increasing the deadline, changing runtime settings or coaching the implementation was not used to turn this recorded run into a pass. The run is retained as failed acceptance for independent review.

Model/runtime settings: existing `/home/miceoil/src/llama.cpp/build/bin/llama-server`, freshly reporting `0.4.0-dev (build 1, commit 4d91760)`, GNU 15.3.1/Linux x86_64; existing `/home/miceoil/bigdrive/Jan/llamacpp/models/Qwen3_6-35B-A3B-UD-Q4_K_M/model.gguf`; context 32,768; 12 GPU layers; eight threads; 3,072 reply tokens; temperature 0.7; **Instant** mode. No model or dependency replacement occurred. Observation durations were 418.50 seconds and 1,435.17 seconds, including model work, approvals and commands (about 31 minutes in total). Both owned engines stopped; a final process check found no remaining `llama-server`.

### Actual command evidence and attribution

All six commands ran in the disposable target checkout through the real ToolExecutor. Each saved outcome retains its exact command, resolved working directory, timeout, execution flag, exit code, output and termination flags. All had `timed_out=false`, `cancelled=false` and `output_limit_reached=false`.

| Saved result | Operation (summarized here) | Observed result |
|---|---|---|
| 17 | `git status` | Exit 0; staged source fixture and untracked sentinel visible. |
| 19 | `git diff --cached -- letracode/tools.py` | Exit 0; unrelated staged comment inspected. |
| 21 | `cat` of the sentinel | Exit 0; expected sentinel text. Individually approved, but outside the literal pinned Git/Python command list; an ordinary read tool would have sufficed. |
| 25 | Focused `tests/test_tools.py` baseline | Exit 1; **35 setup errors in 0.48 s**, before tests ran. Codex's preparation script had omitted the parent of pytest's external `--basetemp` directory. This is a fixture error, not a failing feature regression. |
| 27 | `mkdir -p` of disposable focused/full fixture directories | Exit 0; the model correctly diagnosed the setup issue and requested this separately approved command. Like `cat`, this exceeded the literal pinned command list and was reviewed individually. |
| 29 | Full baseline suite | Exit 0; **280 passed in 92.96 s**. This verifies unchanged baseline code, not the requested `truncated` improvement. |

After observing result 25, Codex created only the missing disposable parent and corrected the preparation script; the operator event is recorded. The model then independently requested result 27's directory creation. No test implementation, expected answer or feature code was provided. The existing pinned commands stayed unchanged. Test subprocesses used offscreen Qt, disabled bytecode/cache writes and visible fixtures outside the linked target.

The actual model did **not** run a newly authored regression, perform a source edit, rerun focused tests successfully, run verification after an edit, inspect a final unstaged patch, compile Python, check shell syntax or report successful completion. Those missing acceptance steps cannot be filled in by attributing Codex's prerequisite checks to Strand.

### Review artifacts and isolation checks

Local evidence root: `/home/miceoil/Projects/LetraCode-strand-development/stabilization-test-data/coding-proof/acceptance-01/`.

- `run-01.json` and `run-02.json`: complete recorded requests/replies, native approvals, statuses and archived SQLite rows. All 41 final archived rows match the persisted database, including the unchanged first-turn rows.
- `transcript-02.md`: human-readable conversation. Keep the JSON and database too: the Markdown export does not contain all structured checkpoint IDs or project Instructions.
- `command-evidence.md`: **exact commands and full actual saved outcomes**, including the setup error. `codex-evidence-audit.json` records the independent read-only checks.
- `manifest.json`, `task-instructions.txt`, `operator-events.json`, `app-data/engine.log` and `app-data/letracode.sqlite3`: settings, task boundary, fixture correction, runtime and persisted evidence.
- `strand.patch`: **empty**, because Strand published no change. `preserved-staged.patch`: the original seeded unrelated comment. `window-01.png` / `window-02.png`: final native app views.

The final audit verifies no target remotes, unchanged target HEAD, byte-for-byte unchanged index entries and staged diff, preserved staged source comment, and unchanged/untracked sentinel. There are no other target changes. Recovery artifacts were locally excluded from the source review diff; none were needed for a model edit in this run. The original and accepted M1a checkouts remain preserved. Raw local evidence and scripts are deliberately ignored test artifacts; retain this workspace for independent review, since a fresh Git clone will not include them.

The actual continuation reopened the saved data and ran only newly proposed, individually reviewed commands. It did not replay an action automatically. Bounded-history compaction occurred in turn two; no `read_tool_result`/`list_tool_results` recovery was attempted by this model. Exact recovered-command attribution is established by the separate scripted worker regression, not by an unperformed real-model recovery step.

## Review and remaining limits

Independent review must evaluate the prerequisite patch and failed model transcript. No model-authored change exists. Deterministic tests establish application behavior; the actual-model run separately measures code understanding, tool choices, iteration and reporting. A model that stops early or produces a wrong patch is not automatically evidence of an application defect.

Physical power loss and every KDE interaction are outside the automated evidence. Native **command** approvals were exercised during the demonstration; edit approvals, denial/Stop interactions and broader KDE behavior remain manual checks (their deterministic regressions pass). Identical-byte external rewrites cannot generally be distinguished by a content hash. Unknown outcomes after interruption require inspecting current state, not an assumption of exactly-once execution. No installed app, live database, writing file or working model is modified by this milestone; no integration or installation follows the proof automatically.
