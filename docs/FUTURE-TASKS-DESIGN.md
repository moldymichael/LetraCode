# Future durable tasks, rebased on reliability development

Status: design only, September 6, 2026. No scheduler is implemented by this
reliability pass. The historical proposal based on `45092f1` remains unchanged
in the handoff package. Its claimed approval/defaults are historical context,
not new authorization or product decisions for this pass.

## Existing foundation

Start from `e57b271` plus the fixes described in
[CURRENT-STATE.md](CURRENT-STATE.md) and
[RELIABILITY-VERIFICATION.md](RELIABILITY-VERIFICATION.md). Python/Qt/Linux,
SQLite schema **2**, managed llama.cpp, ordinary Strand memory, retained recovery
inodes, guarded source hashes, conflict-aware Undo, formatted runtime token
counts, saved-result paging, and app locking before Store initialization already
exist. Do not implement these again from the old plan's assumptions.

`letracode/strand.py` owns memory durability. Reserve a distinct future namespace
such as `letracode/tasking/` for orchestration. Any refactor of memory needs an
explicit compatibility layer; a colliding `strand/` package is unsuitable.

Current `pause_context` version 1 references the original saved user row and
all subsequent user steering in the same chat. It conservatively reserves the
preceding completed conversation (up to 128 referenced rows) to avoid guessing
which plan was selected. User/assistant intent is intact; old tool bulk can be
recovered by saved-result ID. Missing, malformed or oversized required context
pauses visibly. A final ordinary reply or Stop closes this supervised lifecycle;
that marker is not evidence that an objective was achieved. A future explicit
starting-context picker should replace the conservative broad selection and
allow an unrelated new objective without carrying unnecessary prior context.

## Durable state and user control

Use a Qt controller with one managed execution slot shared by chats and tasks.
Its queue is derived from persisted state. A segment must return a typed outcome
after its worker and engine teardown settle; do not recursively call the current
worker or raise its ten-round limit to simulate a scheduler.

Persist original objective, criteria, selected input references, ordered user
steering, exact model configuration, capabilities, checkpoints, budgets and
action state. Earlier context is historical evidence; the latest user steering
controls the next safe dispatch. Starting context and original user rows remain
inspectable after compaction. Preserve provenance and a bounded derived summary,
with a visible pause if required authoritative material cannot be recovered.

Keep desired user control (`run`, `pause`, `cancel`) distinct from observed
execution state (`ready`, `running`, `waiting_approval`, `waiting_input`,
`needs_reconciliation`, `paused_limit`, `completed`, `failed`). A late callback
cannot erase a newer Pause/Cancel. Stop, application close/reopen, explicit
replacement and ordinary chat behavior need separate documented semantics.
Whether safe ready tasks resume automatically after reopening remains a product
decision; this pass grants no background authority.

Completion requires explicit criteria and actual evidence, not an empty tool
list, worker exit, successful baseline tests or a model's assertion. Store the
final answer reference and distinguish observations from model assessments.

## Transactions, actions and migration

A future task migration must advance **beyond schema 2** (provisionally 3 after
checking the then-current schema). Preserve the existing v1-to-v2 memory
migration and backups. Test v1-to-latest and v2-to-latest upgrades, interruption,
rollback from the preserved pre-migration snapshot, and rejection of newer
unsupported schemas. App locking remains before Store/migration/recovery.

Task creation, checkpoint advancement, input revision and budget reservations
use short SQLite transactions. Never hold one during inference, a filesystem
action, a network request or a human approval dialog. Saved source files and
SQLite cannot form one atomic external-effect transaction.

Record exact action intent, input/scope version, preconditions and approval;
persist `started` before dispatch. Consume only an approval matching that exact
action and current scope. Record full outcome and paired protocol messages
durably. A crash between dispatch and result leaves `outcome_unknown`, even if
the command might not have started. Resume, retry and cancellation must retain
that warning; none can replay writes/commands to discover their result.

Pending approvals survive as decisions about exact actions, never task-wide
grants. New steering invalidates affected undispatched work and its approvals;
already dispatched work may save its outcome but may not launch obsolete later
actions. Per-command acknowledgement and web/read/write boundaries remain.

## Backups and restoration

Extend the current Strand-before-SQLite snapshot order. Include task tables and
their event cursor in both copied SQLite and JSON. Continue bounded streaming
of retained opaque memory and app-owned source backups. The current recoverable
boundary allows a completed memory receipt to precede its chat tool-result row;
restore must inspect the receipt and never infer that an absent outcome means
the action did not run. Preserve all current recovery bytes and original paths.

Restored tasks require an explicit restore hold until linked roots, writable
destinations and local runtime settings have been reviewed. Unknown actions stay
blocked even after that hold is cleared. Tests must cover restored approved but
undispatched actions, stale source hashes, unknown dispatched effects, concurrent
memory saves and project deletion, with no automatic replay.

## Budgets and acceptance dependencies

The old 180-second segment default is not supported by the measured runtime.
The historical failed coding request spent about 104 seconds processing its
prompt and generated about 3.38 tokens/second before a 300-second timeout.
The old trial took about 31 minutes with no patch. These observations justify
profiling prompt/generation/read repetition before selecting segment and total
task budgets; extending a deadline alone demonstrates no improvement.

The new bounded coding trial also ended without a patch after 900 seconds:
about 303 seconds prompt processing, 343 seconds generation and 248 seconds
native approval wait. One completed request took 209 seconds. The separate
[actual-model report](REAL-MODEL-ACCEPTANCE.md) records this wall-budget stop;
it is not evidence for adopting a 180-second segment or extending engine limits.

Keep finite persisted request/action/time/correction budgets, cancellation,
true timeout classification, and a no-progress stop. Measure engine startup,
prompt processing and generation separately from human wait time. Select
defaults only after the portable [acceptance runner](../tools/run_acceptance.py)
produces reviewed coding and reading evidence for the intended local profile.

Memory chronology is a separate prerequisite. At 1,000 small revisions, the
current measured save took about 434 ms and Undo 797 ms. A separately versioned,
durable reservation/completion ledger could reserve global sequence, destination
and write ID before publication. Interrupted reservations stay occupied. A
derived per-scope index must rebuild from a validated ledger and receipts;
never allocate `max(valid)+1` after skipping damage. Upgrade/reopen/concurrency/
restore and crash tests must preserve ambiguous legacy history. This would
address sequence scans only: retained-descriptor checks still need independent
proof before any caching, and no history/inode pruning is authorized.

Remaining decisions: Windows support, model-role selection, task-mode UI and
starting-context selection, while-open versus reopen continuation policy,
resource budgets and evidence-based completion criteria. Retain the current
Linux stack and configured model until those decisions are made.
