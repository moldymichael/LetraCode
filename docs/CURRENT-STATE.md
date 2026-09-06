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

On September 6, 2026 the unchanged baseline passed **280 tests in 11.10 s**.
The separately invoked supplied audit fixtures produced **5 expected failures**
in 0.22 s: lost paused objective, deep long-line search, character paging,
and two oversized retained-memory backup cases. All used disposable data and
offscreen Qt. These are application tests, with scripted inference peers.

The historical actual-model coding trial **failed**: 19 requests, six command
approvals, about 31 minutes, an empty patch and a 300-second request timeout.
Its unchanged 280-pass suite was not model-authored work. See the preserved
[coding proof](SUPERVISED-CODING-PROOF.md). No historical evidence is rewritten.

This pass follows the supplied reliability handoff: preserve intent, make all
supported source text reachable, back up retained data, measure history costs,
then run separate bounded actual-model fixtures and reconcile the future design.
Current results and remaining boundaries are recorded in
[RELIABILITY-VERIFICATION.md](RELIABILITY-VERIFICATION.md).

Earlier [verification](VERIFICATION.md) and [M1a review](STRAND-M1A-REVIEW.md)
are historical milestone records, not the current capability specification.
The old mirrored roadmap/durable-task plan is historical and is not executed.
