# Current implementation state

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
Memory directories and managed local llama.cpp inference. Linux filesystem primitives
are required. Windows support is not implemented. There is one configured model
with Instant/Thinking modes; model roles and platform changes remain decisions.

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
