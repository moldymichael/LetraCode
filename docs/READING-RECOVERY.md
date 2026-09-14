# Automatic source reading recovery

The source checkout now repairs stalled ordinary file reading in the existing
bounded conversation worker. Previously `read_file` rejected mixed line and
character selectors with a generic error, leaving the model to repeat the error
until the three-stall limit. Incomplete source answers were also retried without
the application taking the missing reading step itself.

`read_file` now returns exact `next_read_file` arguments containing only `path`,
`offset`, and `max_chars`. An explicit character offset takes precedence over
leftover line selectors. A line start with a character count is converted using
the actual source line boundary. Invalid pagination has a distinct error code and
canonical retry arguments; it supplies no coverage. Missing files, denied reads,
and unsupported sources remain ordinary failures. BOM, CRLF, Unicode, cut long
lines, source hashes, and PDF/DOCX extraction provenance retain their meanings.

If the model ends with incomplete ordinary-source coverage, repeats an already
seen source page, or returns invalid pagination (including a batch), the worker
issues application-generated read calls. It selects the earliest missing
**exposed** character range of each observed source version. Reading the ending
first therefore still leaves the opening and middle to recover. Changed versions
retain separate ranges, and a file shrinking below the old cursor triggers a
read from a valid starting point.

Each automatic call and result is saved as a normal protocol pair with explicit
application provenance. A fitting page must be present in a subsequent completed
model request before it can count as exposed. The worker preflights that request,
reduces optional excerpts and page sizes when needed, and rechecks after segment
boundaries. Sizing retries consume action/time budgets but do not exhaust the
model's stall allowance before it can see the fitting page. Older recovery
boundaries can yield context while the original user request and saved evidence
remain available. No synthetic user “Continue” messages are added.

Premature final responses remain provisional while a concrete missing source
page can be recovered. If a tool-free response leaves only failed/untracked
reads or an unsupported extraction tail, the worker saves it with a **Source
limitation** status and stops automatic continuation. The failure remains in the
ledger; an alternative successful filename does not erase it. New unrelated
reads cannot extend this terminal response into another answer cycle. Final
source responses retain the coverage ledger in their payload. Full retrieval, a compacted preview, an EOF
cursor, or a model's assertion cannot establish complete exposure. Document
extraction limits and untracked sources retain their warnings. The ledger does
not verify unobserved files; supported-text exposure is not proof of comprehension, exhaustive project
inventory, or the current contents of a file changed after its last observation.

Stop, new user input, denial, unknown effects, context capacity and existing run
budgets remain stopping conditions. Truncated extraction does not cause empty EOF
polling or a success claim. Reopening the data store never restarts a stopped or
finished task without new input.

## Original reading-recovery verification (historical)

Regression tests use scripted model replies, real disposable sources and SQLite
storage. They cover a 96,008-character BOM/CRLF/Unicode file across multiple
segments, malformed and batched cursors, repeated oversized previews, tail-first
reads, source replacement/shrinkage, escaped text under small contexts, a
one-stall allowance, interrupted dispatch, denied reads, truncated DOCX extraction,
strict tool pairing and durable exposure after reopening. These establish
application behavior, not actual-model comprehension or answer quality.

Run `QT_QPA_PLATFORM=offscreen python3 -m pytest -q`,
`python3 -m compileall -q letracode`, and
`python3 packaging/build-release.py` from the canonical checkout.

The final full regression run passed: **1,150 passed, 22 skipped, 6 Python 3.14
process-fork deprecation warnings**, in 75.87 seconds. Bytecode compilation,
source/Fedora release builds, and `git diff --check` also succeeded. Independent
review found no remaining actionable issues after the recovery regressions were
added and corrected.

Installation remains separate. During this repair the desktop launcher reported
0.6.0, and its installed `tools.py`, `worker.py`, and `context.py` matched the
starting local HEAD. The user subsequently requested installation, which is now
complete; see the [installed reading update](INSTALLED-READING-UPDATE.md).

## Failed-read continuation repair — September 14, 2026

The baseline for this repair is local and remote `main` at
`8772a1916c1d65b6514aa2ffdaf024c8e6e886b8`, version 0.6.0. The installed worker,
reading recovery, evidence ledger and continuation controller matched those
source files. Work is isolated on `codex/source-read-limitations`; it does not
modify the separate conversation-training feature.

`evidence_state` correctly retained an unsuccessful guessed filename after the
correct alternative file was fully exposed. `SourceReadRecovery.next_read`
correctly returned no page for that remaining failure. The worker nevertheless
counted a provisional-answer stall and dispatched another request. `RunProgress`
correctly reset stalls when the model read novel files, permitting repeated
answer/read cycles. The repair ends that worker branch with `source_limited`,
closes its continuation context, and keeps the original exposure and failure
records. Evidence calculations, page selection, run limits, approvals and
no-progress rules are unchanged. A terminal limited answer remains eligible as
referenced context for a subsequent real user request without becoming a claim
of complete source coverage.

A matching saved conversation was inspected read-only. Its captured assistant
responses were replayed with production file tools, storage, exposure and run
control against disposable copies of five sources; all copies matched their
saved read hashes. Only the recorded read/list operations were allowed, and
paths were redirected into the disposable tree. Private conversation/source
content and the replay harness remain outside the repository.

| Captured-response replay | Baseline | Fixed worker |
| --- | ---: | ---: |
| Model requests | 15, ending with simulated Stop | 4, terminal answer |
| Tool results | 10 | 3 |
| Substantive answers | 4 provisional | 1 with source limitation |
| Additional segment boundaries | 1 | 0 |
| Failed-read issues retained | 1 | 1 |
| Synthetic user messages | 0 | 0 |
| Requests after reopening | 0 | 0 |

The fixed replay ends after the failed read, folder listing, successful alternate
read and first answer. Its tracked file is completely exposed, while overall
coverage honestly remains incomplete. Reopening changes neither evidence nor
history. This is **scripted application verification using captured responses**,
not a fresh real-model run or a claim about model answer quality.

Verification on native Fedora 44, system Python 3.14.7:

```bash
QT_QPA_PLATFORM=offscreen python3 -m pytest -q \
  tests/test_source_read_limitations.py tests/test_source_limitation_continuity.py \
  tests/test_reading_recovery.py tests/test_reading_recovery_boundaries.py \
  tests/test_evidence.py tests/test_continuation.py tests/test_automatic_continuation.py \
  tests/test_continuation_review_fixes.py tests/test_worker.py \
  tests/test_history_continuity.py tests/test_mini_coding_continuation.py tests/test_coding_loop.py
QT_QPA_PLATFORM=offscreen python3 -m pytest -q
python3 -m compileall -q letracode packaging
bash -n install.sh uninstall.sh packaging/build-rpm.sh
git diff --check
```

- Focused final run: **184 passed in 15.14 seconds**.
- Full source suite: **1,168 passed, 22 skipped, 6 Python 3.14 process-fork
  deprecation warnings in 79.04 seconds**. No failures.
- Compilation, shell syntax and diff checks passed. Independent final code
  review found no remaining actionable issues.
- Before the fix, the original six source-limitation cases produced **5 failed,
  1 passed in 0.76 seconds**; the normal partial-read case already passed. After
  the worker fix all six passed in 0.42 seconds. The continuity regression also
  failed before its reference-eligibility repair.
- The first expanded run had **1 failed, 177 passed in 14.77 seconds**: an old
  saved-result recovery test explicitly expected three repeated answers after
  historical failed reads. It now requires one answer, retained incomplete
  evidence and `source_limited`; saved-result recovery assertions remain intact.
  The final focused/full runs above include that corrected expectation.
- Native Windows, a fresh real-model run and release packaging were not run for
  this repair. Existing historical platform/model results above remain separate.

The maintainer's installed app was subsequently updated with only the three
changed modules. See the [installation and rollback record](INSTALLED-SOURCE-LIMITATIONS-UPDATE.md)
for the separate combined-candidate suite, installed replay and live-data checks.
