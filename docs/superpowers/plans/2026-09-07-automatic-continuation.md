# Automatic bounded continuation implementation plan

> Execute with subagent-driven-development; one owner per production module,
> regression-first checks and independent final review.

**Goal:** Continue a Strand conversation through ten-action segment boundaries
without synthetic user messages, with saved intent/evidence, honest coverage and
finite safety stops.

**Architecture:** Extend the existing ConversationWorker and chat payloads.
One cancellable QThread owns a run and iterates bounded segments. Each boundary
is a saved checkpoint; no recursive worker calls, background queue, schema
migration or competing task system. Completed source exposure and progress are
application records, not model assertions. Existing Qt approval dialogs remain
individual human decisions. A denied/unknown action blocks further dispatch.

**Specification:** The September 7 user request in this task; repository baseline
017fccde46096cef7b00d3de38e1a7bc0b3054ed. Existing reports remain historical evidence.

## Decisions and constraints

- Work directly in the requested clean reliability worktree/branch. Preserve
  original worktrees, live data and runtime/model configuration. No install,
  training, merge or push. Commit locally and stop for independent review.
- Keep ten rounds per segment. Default whole-run caps: twelve segments, 120
  model requests, 240 proposed actions, one hour including approval wait, and
  three consecutive failed/repeated non-progress outcomes. All count limits
  reserve before dispatch; runtime cancellation enforces the wall deadline.
- A run continues only while the worker owns its original chat and cancellation
  token. No delayed UI callback can resurrect a stopped run. Reopen requires
  explicit user input; an old checkpoint is evidence, never startup authority.
- Preserve user text, same-chat origin/references and all later steering. Split
  request packing at trusted segment notices, keeping required intent plus
  bounded deterministic coverage/action summaries and saved-result IDs. Raw
  results and working notes remain in SQLite and can be paged without replay.
- Source coverage describes exact supported source text provided in completed
  requests. Track retrieval separately; compacted previews and internal search
  scans cannot establish full exposure. Source version changes invalidate union
  with older text. Whole-work scope and understanding are not inferred from
  visited-file counts; untracked automatic excerpts are conservative unknowns.
- A tool-free model answer is a response, never independent proof of task success.
  When tracked source coverage remains incomplete, retain it as provisional,
  give bounded corrective opportunity, then stop accurately if no progress.
- Successful repeated effectful actions require new source evidence/change to
  justify rerun; otherwise reference the saved outcome. Failed commands may have
  effects. Unknown writes/commands and denied approval stop rather than replay.
- Grey Area is not present in repository-owned fixtures. Use a named synthetic
  whole-work regression; do not claim evaluation of a user's actual manuscript
  or local-model quality from scripted inference.

## Work sequence

Recovery note: the interrupted Tasks 2–6 were completed together in the active
reliability worktree, preserving their dirty implementation. The September 6
baseline evidence is historical; the September 7 full suite is fresh. The three
review reproductions were rerun RED before production repair, and the missing UI
behavior was tested RED before implementation. The implementation owner did not
dispatch agents; the controller owns the subsequent independent reviews.

- [x] Reproduce baseline ten-round/manual-resume behavior, record clean baseline
  and focused tests using fresh visible temporary XDG/data/runtime roots.
- [x] Add `continuation.py` and pure regression tests for segment-spanning
  request/action/time reservations, source-version-aware repetition, error/A-B
  cycles and saved action outcomes. No Qt or persistence dependency.
- [x] Add `evidence.py`, source-tool range metadata and regression tests for
  interval gaps/overlap, source version changes, truncated extraction, request
  compaction, saved JSON recovery, forged/legacy metadata and scope isolation.
- [x] Integrate the worker: trusted boundary checkpoints, run-wide cancellation,
  blockers/denial pairing, source exposure sidecars, bounded model progress
  context, preserved original objective and provisional incomplete responses.
- [x] Update UI rendering to distinguish successful action execution, partial
  source coverage and unverified task outcome. Keep notices in exported history.
- [x] Demonstrate three-segment whole-work reading and bounded real-tool coding
  with scripted inference. Cover Stop at a boundary/pending approval, reopen,
  no-progress loops and overall limits. Update legacy manual-pause fixtures and
  acceptance observer only where intentional behavior changed.
- [ ] Run focused checks, full suite and release/document checks, review the diff
  independently, repair concrete findings, document actual results/limits and
  create one clean local implementation commit. Stop for independent review.
