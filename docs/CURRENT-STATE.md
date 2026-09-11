# Current implementation state

> **Repository snapshot: September 11, 2026.** Remote `main` at `c239fa4407f6b967be025fdf50c6aa124a296e2c` identifies as 0.6.0; the published release is 0.3.0. [PR #8](https://github.com/moldymichael/LetraCode/pull/8) tracks CI/quantizer work and [issue #6](https://github.com/moldymichael/LetraCode/issues/6) tracks unified engine invocation. A successful Windows packaging job does not establish a passing full suite. See the [Windows guide](WINDOWS.md) for the exact candidate and limitations, and the [documentation index](README.md) for navigation.
>
> Installation statements and machine paths below describe the maintainer's recorded local work. They do not mean that every contributor's installation, remote branch or published download contains those changes. Preserve the dated records; inspect the actual runtime when diagnosing another computer.

## Recorded 0.6.0 implementation and local installation

The **0.6.0 Strand experience** reworks the installed 0.5.0 Gemma lineage into
Chat, Knowledge, Improve and Settings. Workspaces focus one persistent Strand.
It adds account-wide explicit file reads with independent action controls,
private request receipts, usable long-history/task recovery, navigation and
saved drafting during jobs, guided preparation, recoverable conversion, and
Chat-runtime comparison with explicit version judgments and rollback. The
SQLite schema and ordinary-file recovery formats are retained. See the
[current user guide and verification](STRAND-EXPERIENCE.md). The live installation
is not automatically replaced by source development.

The user-requested **0.6.0 installation is now complete**, and the canonical
`/home/miceoil/Projects/LetraCode` checkout and local `main` contain this build.
The obsolete 0.1.1 local-main baseline was fast-forwarded. See the
[installation and source correction record](INSTALLED-0.6.0-UPDATE.md).

The installed comparison workflow repair adds an immediate **Compare with Strand** window,
fresh per-candidate questions, complete side-by-side answers, streaming progress,
retained partial failures and history, per-question judgments and an explicit
comparison export. It retains the original held-out questions, frozen training
snapshots, model verification, explicit adoption and rollback. See the
[repair and verification record](COMPARISON-WORKFLOW.md). The user-requested local
installation is complete; see the [installation check](INSTALLED-COMPARISON-UPDATE.md).

The current source checkout also adds [automatic source reading recovery](READING-RECOVERY.md):
canonical character cursors, deterministic paging-error repair, missing-range
continuation, context-fit checks, and final-response coverage evidence. It retains
the existing run limits. The user-requested installation is complete; see the
[installed reading update](INSTALLED-READING-UPDATE.md).

## Earlier implementation milestones

The **0.5.0 QLoRA update** adds explicit CUDA 4-bit NF4 training with double
quantization, all-linear adapters, gradient checkpointing and token-weighted
accumulation. New training setup recommends QLoRA; existing full-precision runs
retain their meaning. Reports include precision, effective batch and measured
CUDA memory. Real GPU optimization, GGUF conversion, adoption, inference and
rollback passed on a generated model. See [QLoRA verification](QLORA-VERIFICATION.md)
and [setup](FINE-TUNING.md). The in-use per-user launcher is updated to 0.5.0;
its installed modules and preserved data were checked before reopening it.

The **0.4.0 source update** adds a Fine-Tuning workspace for reviewed datasets,
offline text-only Llama LoRA optimization, held-out comparisons, GGUF adapter
conversion, explicit adoption and rollback. Regular chat preserves and displays
reasoning emitted by compatible models. It starts from GitHub `main` at
`c9f6392`, preserving the 0.3.0 Strand/Memory behavior below. The installed
0.3.0 two-model additions were then integrated before upgrading the live app.
See [setup and workflow](FINE-TUNING.md) and
[verification](FINE-TUNING-VERIFICATION.md). Original worktrees and user models
are preserved. The per-user launcher installation was updated and opened after
combined regression and copied-data checks. See the
[installed update record](INSTALLED-0.4.0-UPDATE.md).

The local two-model update adds explicit, finite model-to-model exchanges on
the 0.3.0/schema-3 baseline. See [the integration record](MULTI-MODEL-UPDATE.md)
and README for controls and verification. Existing Memory, evaluation and
single-model continuation features remain available.

The **0.3.0 Windows release** adds a self-contained Windows x64 installer and
portable download to the current app. Windows uses native file handles and
process jobs; Fedora retains its existing filesystem and process behavior.
Both keep schema 3 and the Project files interface. See the
[Windows release verification record](WINDOWS-RELEASE.md) for platform checks,
installer behavior and remaining boundaries. This release does not replace the
separately installed Linux app during development.

The September 8 **0.2.1 Project files update** was implemented and installed. It
replaces the three context tabs while preserving the schema-3 Memory recovery
backend and the reliability/evaluation features below. See
[the update and verification record](PROJECT-FILES-UPDATE.md). The latest full
suite at that milestone was **656 passed**; the earlier counts below are historical.

The evaluation export and user-controlled Memory update uses branch
`codex/evaluation-memory`, based on reliability commit `7863707`.
See [migration, privacy and pre-install checks](EVALUATION-MEMORY.md) for its
behavior and verification. A reported live migration failure involving deleted
project history has a focused correction; see
[migration recovery](MEMORY-MIGRATION-RECOVERY.md). Investigation and correction
verification left the reported live data unchanged.

The preceding reliability development used branch `codex/strand-reliability`, based on
`e57b271753b01210e913a6314bdc715b17190f02` (`codex/strand-development-loop`).
The original `45092f1` checkout is older; application version 0.1.1 does not
identify this baseline. Existing worktrees and live app data are preserved.

The stack is Python 3.11+, PySide6/Qt, SQLite schema **3**, ordinary user-owned
Memory directories and managed local llama.cpp inference. Guarded storage uses
Linux descriptor-relative operations or native Windows handles. There is one
configured primary model with Instant/Thinking modes, plus an optional second
model for explicitly bounded shared conversations.

Existing capabilities include persistent chats/projects/drafts, guarded source
edits, file hashes, retained recovery inodes, conflict-aware memory saves and
Undo, runtime token accounting, saved-result paging and automatic bounded continuation.
Memory durability is implemented. A durable background task scheduler is not.
One worker continues through ten-round segments without synthetic user turns.
Default run caps are twelve segments, 120 model requests, 240 proposed actions,
one hour including approval wait, and three consecutive failed/repeated
non-progress outcomes. Stop, denied/unknown actions, new saved user input and
exhausted limits halt dispatch. Reopening a checkpoint never restarts work;
explicit new user input is required after a stop.

The September 7 completion builds on `017fccde46096cef7b00d3de38e1a7bc0b3054ed`
in `/home/miceoil/Projects/worktrees/active/LetraCode-reliability`, preserving the
interrupted implementation. After the final whole-change review fix wave, fresh
full offscreen verification is **494 passed, 10 existing Python 3.14 process-fork
warnings, 28.26 seconds**, no failures or
skips. Source retrieval and exposure in completed requests are tracked
separately by source version. Partial responses remain provisional and receive
bounded corrective opportunity. Transcript and Markdown export distinguish
successful execution, incomplete coverage and unverified task outcomes.
Output-cap termination halts dispatch like timeout/cancellation and is labeled
as interrupted with effects requiring review. Identical returned source pages
cannot reset the stall limit by changing requested page sizes or defaults.
The 27-page synthetic whole-work reading trial spans three segments; the real
file/process coding fixture spans four, preserving staged and untracked work.
These scripted engines establish application behavior, not model quality or
independent task success. Independent review follows the local commit.

The preceding September 6 reliability implementation and portable harness were
committed locally as `12d8436960bacf7eecc3605286593335c83e5b74`. Its historical verification was
**385 passed in 19.23 seconds**, plus all **five supplied audit regressions
passing separately in 0.14 seconds**. The original paused objective, preceding
referenced context and later steering now survive supervised continuation;
source tools support character paging and deep search; backups stream retained
opaque data and include app-owned source backups; damaged history reports its
exact location and blocks unsafe Undo. The final suite retains ten documented
Python 3.14 process-fork warnings and has no failures or skips.

History scans still grow with retained revisions. Measured costs and limits are
in the [application report](RELIABILITY-VERIFICATION.md). No chronology index,
retention pruning or durable background scheduler was introduced.

On September 6, 2026 the unchanged baseline passed **280 tests in 11.10 s**.
The separately invoked supplied audit fixtures produced **5 expected failures**
in 0.22 s: lost paused objective, deep long-line search, character paging,
and two oversized retained-memory backup cases. All used disposable data and
offscreen Qt. These are application tests, with scripted inference peers.

The historical actual-model coding trial **failed**: 19 requests, six command
approvals, about 31 minutes, an empty patch and a 300-second request timeout.
Its unchanged 280-pass suite was not model-authored work. See the preserved
[coding proof](SUPERVISED-CODING-PROOF.md). No historical evidence is rewritten.

This pass completed the supplied reliability sequence: preserve intent, make
supported source text reachable, back up retained data, measure history costs,
run bounded actual-model fixtures and reconcile the future design. None of the
three real-model runs met full acceptance; those outcomes do not change the
separate application regression results.
Current application results and remaining boundaries are recorded in
[RELIABILITY-VERIFICATION.md](RELIABILITY-VERIFICATION.md). Separate bounded
real-model trials and their assessed outcomes are in
[REAL-MODEL-ACCEPTANCE.md](REAL-MODEL-ACCEPTANCE.md); application test passes
are not model acceptance. The reading harness records native automatic segment
boundaries separately from user turns. An explicit fixture Continue is supported
only for legacy manual checkpoints, never for a production denial, no-progress
stop or exhausted run.

Earlier [verification](VERIFICATION.md) and [M1a review](STRAND-M1A-REVIEW.md)
are historical milestone records, not the current capability specification.
The old mirrored roadmap/durable-task plan is historical and is not executed.
