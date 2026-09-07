# Reliability verification — September 6, 2026

## September 7 independent-review fix round 1

Review of `1296e258cde67ffa698f48c36b12e69c015ed5ee` found five additional
boundary failures. Each was reproduced before production repair in
`tests/test_continuation_review_fixes.py`: **9 failed in 0.29 s**, covering
steering during approval, Stop before/during a run, provisional request exposure,
smaller-page recovery after compaction, and equivalent commands whose rationale,
default timeout or directory alias differed. A final-response reopen case also
failed separately before the admission fix; an already-saved provisional reply
without the new completion marker failed before compatibility support.

Approval now rechecks the worker's saved input cursor after the wait and before
returning authority to the tool. Stale approval yields paired not-executed
`new_input` outcomes for the current and later calls. Stop and final response
records retain a separate terminal input cursor even when their context closes;
startup requires later explicit user input. Successful inference records request
completion separately from a provisional task outcome. Existing provisional
proofs remain valid through their application outcome metadata and source-proof
validation; interrupted and streaming requests still receive no exposure credit.

New completed source exposure resets a stall streak independently of retrieved
ranges, but does not change the source epoch that protects effectful actions
from replay. The smaller-page regression starts with a compacted 16000-character
read, then exposes four complete 4000-character pages across six requests. Every
smaller page is checked as actually present in the request. Disabling only the
exposure-progress update reproduces the four-request false stop.

Command identity uses exact shell text, resolved working directory and effective
timeout. The command parser is shared between normalization and execution;
descriptive `reason` remains in the approval but cannot bypass duplicate checks.
Omitted timeout and explicit 60 seconds are equivalent. Execution uses the same
normalized parameters checked against prior saved effects, and the controller
records actual command-result parameters. Different command text, directory or
timeout remain distinct; exposure alone does not unlock reruns.

Fresh verification on the same Python 3.14.7 / pytest 8.4.2 / PySide6 6.11.2
offscreen environment:

- Broad focused lifecycle/evidence/tool/UI group: **219 passed in 18.87 s**.
- Final focused review/evidence/continuation/tool group: **122 passed in 2.68 s**.
- Fresh complete suite: **488 passed, 10 warnings in 27.74 s**, no failures or
  skips. Existing fork-warning locations/counts are unchanged.
- **49 Python files compiled** without bytecode; `git diff --check` passed.

The full suite used the exact isolated invocation shown in the initial completion
record below, with `LETRACODE_CHECK_ROOT=/tmp/letracode-continuation-fix1-0XS3YA`.
Its full output remains in that root's `full.log`, with separate runtime, cache,
config, data and scratch directories. No existing test was weakened or changed
in this fix round. No real model, manuscript, live data or installed application
was used. The local completion report records RED/GREEN diagnostics, final
release checks and the separate fix commit. Controller review follows the fix.

## September 7 automatic bounded continuation completion

The recovered implementation was preserved and completed directly in
`/home/miceoil/Projects/worktrees/active/LetraCode-reliability`, on
`codex/strand-reliability`, above `017fccde46096cef7b00d3de38e1a7bc0b3054ed`.
The sections below this September 7 addition are historical September 6 evidence.
No live data, model/runtime configuration, installed app, original worktree or
historical model evidence was changed. No install, training, merge or push was
performed. Installer tests use disposable simulated destinations.

One cancellable worker owns the run across ten-round segments. Defaults remain
twelve segments, 120 completion requests, 240 proposed actions, one hour including
approval wait, and three consecutive failed/repeated non-progress outcomes.
Reservations precede dispatch. New saved input is fenced before each action in
a batch; undispatched calls receive paired `executed: false`, `code: new_input`
results. Denial, unknown effects, Stop and budget exhaustion stop the run. A
checkpoint is saved evidence, not startup authority. No synthetic user turn is
inserted by native continuation, and no schema migration was added.

Ordinary-source coverage is keyed by path, hash and extractor version, and counts
text exposed in completed model requests separately from retrieval. Compacted
previews and internal search scans earn no full-exposure credit. Saved JSON pages
can recover old source evidence without replaying actions. A successful modern
read resolves an earlier failed attempt for the same normalized requested path,
including a saved alias; historical failures remain visible. Legacy metadata,
unresolved other paths, a later failed attempt, current-version gaps and truncated
extraction remain incomplete. Rebuilding history normalizes paths lexically and
does not follow today's symlinks to reinterpret old source identities.

Stop launches engine cancellation once without blocking Qt. Worker completion
joins its timer and engine cancellation thread. Completion and token-budget
operations also join their internal cancellation watchers before releasing
operation ownership. Cancellation between token counting and generation is
reported as cancellation rather than a missing server. No stale cancellation
thread can be handed to the next worker by the finished signal.

Transcript and Markdown export share application status labels: successful
execution, denied/failed/not-executed/unknown outcomes, partial source retrieval,
provisional incomplete responses and unverified task outcomes. Ordinary assistant
completion claims do not grant verified status. Trusted notices remain visible
and exported. Offscreen transcript/export behavior was tested, and an observer
window capture was visually checked for readable wrapping. Interactive KDE
keyboard/dialog behavior was not assessed in this pass.

### Observed regression and final results

All commands ran from the active reliability worktree with system Python
**3.14.7**, pytest **8.4.2**, PySide6 **6.11.2**, on Linux
`7.1.13-200.fc44.x86_64`. Initial focused checks used
`QT_QPA_PLATFORM=offscreen python3 -m pytest -q`; no real model was loaded.

| Check | Observed result |
| --- | --- |
| Recovered `tests/test_automatic_review.py`, before production edits | **3 failed in 0.13 s**: batch steering wrote a forbidden file; recovered read used five requests instead of three; old cancellation interrupted a new worker. |
| Additional cancellation/evidence regressions | Four expected failures, three conservative evidence cases already passed; deterministic completion/budget watcher RED rerun: **2 failed in 0.58 s**. Cancellation after counting separately failed before repair. |
| New transcript/export behavior, before UI edits | **3 failed in 0.24 s**: absent action-success, partial/provisional and unverified-response labels. Unknown legacy command status also failed before repair. |
| Worker/pause/UI/evidence focused group after repairs | **103 passed in 8.11 s**. |
| Observer/UI/engine/review focused group | **67 passed in 15.73 s**. |
| Coding/compaction/Strand/source/tool focused group | **103 passed in 1.96 s**. |
| One fresh complete offscreen suite | **477 passed, 10 warnings in 27.05 s**; no failures or skips. |
| Release/document/installer checks | **9 passed in 1.10 s**. |
| Compilation, shell syntax, version and whitespace | **48 Python files compiled** without bytecode; shell syntax passed; `LetraCode 0.1.1`; `git diff --check` passed. |

The ten full-suite warnings are the existing Python 3.14 warnings for process
crash fixtures forking after Qt started threads: four source-file, one store,
five Strand tests. No new warnings remain. The detailed completion report is
local controller evidence under `.superpowers/sdd/2026-09-07-automatic-continuation/`;
it records the individual RED/GREEN commands, fixture rulings and local commit.

The exact full-suite invocation was:

```bash
set -o pipefail
export LETRACODE_CHECK_ROOT=/tmp/letracode-continuation-final-BXwLWZ
mkdir -p "$LETRACODE_CHECK_ROOT"/{runtime,cache,config,data,scratch}
chmod 700 "$LETRACODE_CHECK_ROOT/runtime"
export QT_QPA_PLATFORM=offscreen PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
export XDG_RUNTIME_DIR="$LETRACODE_CHECK_ROOT/runtime"
export XDG_CACHE_HOME="$LETRACODE_CHECK_ROOT/cache" XDG_CONFIG_HOME="$LETRACODE_CHECK_ROOT/config"
export XDG_DATA_HOME="$LETRACODE_CHECK_ROOT/data" TMPDIR="$LETRACODE_CHECK_ROOT/scratch"
python3 -m pytest -q -p no:cacheprovider --basetemp="$LETRACODE_CHECK_ROOT/full" 2>&1 | tee "$LETRACODE_CHECK_ROOT/full.log"
```

For a new run, create a new root with `mktemp -d`; pytest clears its basetemp.
The existing root retains the complete log and disposable fixture evidence.
Additional verification commands:

```bash
python3 -B - <<'PY'
from pathlib import Path
files = sorted(path for directory in ('letracode', 'tests', 'tools', 'packaging') for path in Path(directory).rglob('*.py'))
for path in files:
    compile(path.read_bytes(), str(path), 'exec')
print(f'Compiled {len(files)} Python files without writing bytecode')
PY
bash -n install.sh uninstall.sh packaging/build-rpm.sh
QT_QPA_PLATFORM=offscreen PYTHONDONTWRITEBYTECODE=1 python3 -m letracode --version
QT_QPA_PLATFORM=offscreen PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q -p no:cacheprovider tests/test_release_docs.py tests/test_install.py
PYTHONDONTWRITEBYTECODE=1 python3 packaging/build-release.py --output-dir /tmp/letracode-continuation-release-Fo9n2f
git diff --check
```

The builder produced `LetraCode-0.1.1.tar.gz` and `LetraCode-0.1.1.run` in a
fresh temporary directory; neither installer was run. Final documentation is
packaged again into a separate fresh directory, recorded in the completion
report. The release test validates linked current/historical documents, scripts
and exclusion of model/database artifacts.

### Fixture changes and limits

Legacy fixtures were updated only where they encoded superseded behavior:
denials now terminate before another request; repeated catalogs stop for no
progress; capped/compacted first-page claims remain provisional; a run-wide
request limit supplies the saved-history reopen checkpoint; synthetic modern
source rows now supply the required provenance fields. Compaction tests retain
their original size, exact saved-body, pairing, all-steering and no-reexecution
assertions. The coding loop retains its two failing real checks and final diff,
and adds two reads covering the edited source version before the final answer.
Its staged and untracked preservation checks remain intact.

The fallback plateau fixture now uses 5200 words for the fitting case because
mandatory continuation/evidence context increased request size. At 5400 words,
measured requests were 36345/34137 tokens with optional excerpts and 33605 at
minimum; 5200 words fits only after optional excerpts are omitted. The 6000-word
case still exhausts every optional reduction and stops without cutting user or
core instructions. The legacy acceptance fixture supplies a new source revision
for its second segment so it exercises a manual checkpoint, not a repeated-read
stall. Native observer results separate automatic segments, user continuations
and the explicitly opted-in legacy fixture turn; production stops cannot trigger
the fixture continuation callback.

The named synthetic whole-work regression reads **27 actual pages from six
synthetic chapters**, retrieves older saved evidence, crosses two boundaries
(three segments), and retains the question across **30 requests** with one user
turn. The bounded coding demonstration crosses three boundaries (four segments)
in **32 requests**, using 31 real tool actions, two source edits, three actual
verification invocations with exit codes **1, 1, 0**, final diff and saved failed
output recovery. Approval callbacks are scripted fixture decisions, not human
approvals. Stop at a boundary/pending approval, reopen-without-dispatch,
no-progress cycles and count/time limits are separately exercised.

Grey Area is absent from repository fixtures. These tests did not evaluate the
user's actual manuscript, local-model reasoning quality or independent task
completion. Coverage remains bounded ordinary-source evidence, not a whole-work
inventory, fresh disk verification or understanding score. Historical untracked
reads can conservatively keep a resumed response provisional. Cancellation
ownership can delay worker completion while the engine finishes teardown, but
does not block Qt. Independent review is the controller's next step.

## Historical September 6 record

Implementation branch: `codex/strand-reliability`, isolated at
`/home/miceoil/Projects/LetraCode-reliability`, based on
`e57b271753b01210e913a6314bdc715b17190f02`. The older original checkout and all
existing worktrees are preserved. The handoff package's SHA256SUMS all verified.
No installed app, live database, original source project, model file or saved
historical evidence was changed. No installation, migration of live data, push,
publication, download or training was performed.

## Deterministic application results

The original suite passed **280 tests in 11.10 s** on the untouched baseline.
The separately invoked supplied audit fixtures produced **five expected
failures in 0.22 s**. They reproduced lost paused intent, missed long-line search,
unavailable character paging and two oversized archived-memory backup failures.
These failures were present before implementation.

Focused results during implementation:

| Area | Observed failure before repair | Focused verification |
| --- | --- | --- |
| Pause intent/reference lifecycle | 11 failed, 1 passed initially; independent malformed-reference cases also reproduced | 56 worker, pause and coding-loop tests passed in 1.97 s |
| Source paging/search/extraction | 13 failed, 10 passed; separate DOCX separator failure | 106 passed in 1.61 s |
| Retained-data backups | 7 failed, 6 passed | 96 passed in 1.29 s |
| History diagnostics | 10 failed, 3 passed | 114 passed in 2.93 s |
| Measurement CLI duplicate counts | 1 failed | 1 passed in 0.20 s |
| Portable acceptance harness | Scripted peer only, never a model-quality result | 15 passed in 3.95 s after provenance expansion |

The final combined suite passed **385 tests in 19.23 s**, with zero failures or
skips. Ten Python 3.14 warnings come from existing process-crash fixtures forking
after Qt created threads. The five supplied audit regressions also passed
separately in **0.14 s**. Packaging/install checks passed **9 tests in 1.08 s**;
41 Python files compiled without bytecode, shell syntax checks and
`git diff --check` passed. Intermediate combined runs were 371/379 passes;
these are development checkpoints, not additional acceptance results.
The final documentation/release recheck passed **9 tests in 1.11 s**, retained
under `documentation-final/` in the evidence directory.

The tested application and harness are committed as
`12d8436960bacf7eecc3605286593335c83e5b74`. Later report changes do not alter that
application. Durable logs, exact invocations and model evidence are retained at
`/home/miceoil/Projects/LetraCode-reliability-evidence-6v5gmg4f`:
`baseline/`, `audit-before/`, `audit-after/`, `application-final/`, and
`history-measurement/`. The directory also retains the original verified handoff
ZIP and the isolated check wrapper. Original scratch paths in copied logs are
provenance, not instructions to execute.

Root-led full-suite and audit checks use fresh visible `/tmp/letracode-check-*`
roots with separate runtime,
cache, config, data and scratch directories. The actual invocations and full
output remain in those roots' `invocation.json` and log files. Source-focused
checks used `/tmp/letracode-pages-final-SANHSp` and related disposable roots;
their transcript-extracted commands and results are retained under
`source-focused/`, clearly distinguished from original wrapper logs. Backup and
history focused command/output excerpts are likewise labelled under
`backup-history-focused/`.
Baseline evidence
is `/tmp/letracode-check-baseline-c569o8j0`; supplied red fixtures are
`/tmp/letracode-check-audit-6d8saw_s`. To reproduce from this repository:

```bash
export LETRACODE_CHECK_ROOT="$(mktemp -d /tmp/letracode-check-XXXXXX)"
mkdir -p "$LETRACODE_CHECK_ROOT"/{runtime,cache,config,data,scratch}
chmod 700 "$LETRACODE_CHECK_ROOT/runtime"
export QT_QPA_PLATFORM=offscreen PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
export XDG_RUNTIME_DIR="$LETRACODE_CHECK_ROOT/runtime"
export XDG_CACHE_HOME="$LETRACODE_CHECK_ROOT/cache" XDG_CONFIG_HOME="$LETRACODE_CHECK_ROOT/config"
export XDG_DATA_HOME="$LETRACODE_CHECK_ROOT/data" TMPDIR="$LETRACODE_CHECK_ROOT/scratch"
python3 -m pytest -q -p no:cacheprovider --basetemp="$LETRACODE_CHECK_ROOT/full"
```

Use a new root for each invocation; pytest clears its selected base directory.
No installer should be run. Installer tests use isolated simulated destinations.

## Changes and boundaries

**Continuation:** pause-context format 1 retains original same-chat user
provenance, a bounded objective preview for inspection, original row references
and every later user correction. The original anchor survives repeated pauses;
the resume cursor advances independently. Malformed/missing/foreign references
and invalid cursors pause without generation. Archived tool rows are unchanged;
unknown actions remain unknown and paired, with no automatic retry.

Because the current UI cannot select an exact earlier plan, the complete
preceding conversation (up to 128 referenced rows) is conservatively retained.
User and assistant text is never sliced; tool bulk remains pageable by saved
result ID. A required span that cannot fit causes a visible pause. Start a new
chat with a self-contained request to narrow this context. Later user directions
take precedence over historical intent. A final ordinary reply or Stop closes
the supervised pause lifecycle; a new chat is fully isolated. This is not a
durable scheduler or a claim that a model actually completed its objective.

**Source reading:** `read_file(path, offset, max_chars)` pages raw Python Unicode
characters, preserving BOM/CRLF and whole-snapshot SHA-256. The 16,000-character
return bound remains. Line-mode output keeps its fields and numbering and now
has `next_offset`, including mid-line cuts. Reject mixed modes, booleans, invalid
bounds and types. Changed source hashes/extractor versions are visible between
pages. PDF/DOCX expose read-only binary provenance and extraction coverage;
terminal paging means supported-text exhaustion, not full-document coverage.
PDF's former 30,000-character per-page cutoff is removed within the existing
2 MiB extraction bound. Search uses overlapping 5,000-character windows, at most
12 MiB of searched characters, accurate lines/offsets and file diversity.
20 MiB binary input, 500 PDF pages, bounded inventory, no OCR and the supported
DOCX body-only extraction remain explicit limits.

**Backups:** stream opaque retained data in 128 KiB chunks without raising active
memory parsing limits. Include `strand/` (deleted archives, history, receipts,
recovery files) and app-owned `file-backups/`, SQLite, JSON and RESTORE.txt.
No-follow, opened-descriptor identity, regular-file/single-link and change checks
must pass before atomic ZIP publication. Failed replacements preserve the old
ZIP. Linked originals, model weights, logs, migration snapshots and runtime
temporary files are excluded; the menu and restore guide name that scope.

The Strand operation lock precedes SQLite backup, serializing cooperating
memory saves/project deletion. A completed memory receipt may precede its chat
tool-result row: restore can inspect it, but must not replay the action. Source
backups/external editors do not share that lock; changes detected during copying
abort the archive, while edits after a file was copied belong to a later backup.
Finish active tools and external edits for a quiescent snapshot. Retarget copied
links/settings before opening restored test data. All tests restore to fresh
directories and compare retained bytes/hashes.

**History damage:** exact receipt paths and malformed schema/sequence diagnostics
replace opaque exceptions. Duplicate sequences block allocation and Undo.
The memory dialog remains usable with an explicit unresolved-history warning,
disabled Undo and preserved drafts. It does not skip corrupt records or present
a stale head as safe. Unrelated saves can still be blocked by damaged chronology.

## History cost measurements

Measured before diagnostic changes, using the portable
[measure_history.py](../tools/measure_history.py), 256-byte current revisions,
counts 1/20/100/1000, 600-second and 128-MiB budgets, fresh directories. The run
completed in 207.07 s with 37,191,680 allocated bytes across preserved fixtures.
Report: `/tmp/letracode-history-measure-S5kHrw/baseline/report.json`.

| Revisions | Historical bytes per snapshot | Metadata bytes | File opens | Snapshot ms | Save ms | Undo ms | Allocated bytes before probes |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 0 | 189 | 5 | 0.269 | 2.410 | 4.204 | 81,920 |
| 20 | 4,864 | 3,780 | 62 | 1.194 | 10.668 | 18.948 | 704,512 |
| 100 | 25,344 | 18,900 | 302 | 5.077 | 45.094 | 80.001 | 3,325,952 |
| 1,000 | 255,744 | 189,000 | 3,002 | 50.522 | 433.542 | 796.663 | 32,817,152 |

Every current file was 256 bytes. Counts measure successful `_read_at` bytes and
`os.open` calls on a warm local filesystem, including instrumentation overhead;
they are not physical device I/O or production latency guarantees. Save and Undo
probes add two preserved revisions after each sample. Limits are checked between
operations/every ten seed saves; a single operation can cross the soft deadline.
Duplicate counts are rejected before creating any output.

No retention/chronology optimization is claimed. Scans still grow with history.
The [future design](FUTURE-TASKS-DESIGN.md) describes a recoverable reservation
ledger/index and independent retained-descriptor validation; no pruning or
metadata-only cache is introduced.

## Acceptance-driven cursor follow-up

The first genuine reading trial exposed an additional application ambiguity:
a compacted read_file result combined saved `result_id=5` with original source
`next_offset=15997`, while its guidance directed saved-result paging. The model
then called read_tool_result at 15997 (the saved JSON tail), not the source tail.
The original request/evidence remains preserved for inspection.

Compacted receipts now put original offsets/totals under `source_page`, retain
source hashes/binary provenance and explicitly start saved-result recovery at
`offset=0`. Three new deterministic cases failed before this repair. The focused
worker/cursor/acceptance suite then passed **77 tests in 8.56 s**. A fresh final
full suite passed **385 tests in 19.23 s**, with the same ten process-fork warnings,
zero failures and no skips, at `/tmp/letracode-check-9x3ij0rz`.

One older assertion required the entire optional retrieval excerpt to stay
identical across 32 reads. It now verifies that the complete system, identity
and project core remain intact while optional excerpts can shrink to fit the
versioned receipts. Saved bodies and tool-pairing assertions are unchanged.
Other updated baseline fixtures explicitly retain all user steering and use a
real user anchor for legacy checkpoint recovery; the batch-limit fixture uses
16,384 context to isolate batch behavior from schema/path-length growth.

## Actual local-model results are separate

The actual local-model configurations, requests, approvals, timings, final
artifacts and assessed outcomes are in
[REAL-MODEL-ACCEPTANCE.md](REAL-MODEL-ACCEPTANCE.md). Application pass counts above
use scripted/loopback inference and are not evidence of model-authored work or
successful autonomous task completion.
