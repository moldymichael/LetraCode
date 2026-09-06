# Strand supervised coding proof

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

Preparation/observation scripts are retained in `.stabilization/coding-proof/prepare_acceptance.py` and `run_acceptance.py`. Model/runtime settings, attempt results, approval count, exact verification evidence and final artifact paths will be appended after the run. **No real-Strand coding result has been established yet.**

## Review and remaining limits

Independent review must evaluate both the prerequisite patch and the model-authored change/transcript. Deterministic tests establish application behavior; the actual-model run separately measures code understanding, tool choices, iteration and reporting. A model that stops early or produces a wrong patch is not automatically evidence of an application defect.

Physical power loss and every KDE interaction are outside the automated evidence. Native approval interactions will be exercised during the demonstration. Identical-byte external rewrites cannot generally be distinguished by a content hash. Unknown outcomes after interruption require inspecting current state, not an assumption of exactly-once execution. No installed app, live database, writing file or working model is modified by this milestone; no integration or installation follows the proof automatically.
