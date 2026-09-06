# Reliability verification — September 6, 2026

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
