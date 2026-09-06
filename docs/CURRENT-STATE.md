# Current implementation state

Reliability development uses branch `codex/strand-reliability`, based on
`e57b271753b01210e913a6314bdc715b17190f02` (`codex/strand-development-loop`).
The original `45092f1` checkout is older; application version 0.1.1 does not
identify this baseline. Existing worktrees and live app data are preserved.

The stack is Python 3.11+, PySide6/Qt, SQLite schema **2**, ordinary Strand
memory files and managed local llama.cpp inference. Linux filesystem primitives
are required. Windows support is not implemented. There is one configured model
with Instant/Thinking modes; model roles and platform changes remain decisions.

Existing capabilities include persistent chats/projects/drafts, guarded source
edits, file hashes, retained recovery inodes, conflict-aware memory saves and
Undo, runtime token accounting, saved-result paging and supervised continuation.
Memory durability is implemented. A durable background task scheduler is not.
Continuation still requires a new user message after the ten-round pause.

The reliability implementation and portable harness are committed locally as
`12d8436960bacf7eecc3605286593335c83e5b74`. Final application verification is
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
are not model acceptance. The reading harness can record one explicit fixture
Continue, separately labelled from native human actions and tool approvals.

Earlier [verification](VERIFICATION.md) and [M1a review](STRAND-M1A-REVIEW.md)
are historical milestone records, not the current capability specification.
The old mirrored roadmap/durable-task plan is historical and is not executed.
