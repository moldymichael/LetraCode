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

Premature final responses remain provisional. Final source responses retain the
coverage ledger in their payload. Full retrieval, a compacted preview, an EOF
cursor, or a model's assertion cannot establish complete exposure. Document
extraction limits and untracked sources retain their warnings. The ledger does
not verify unobserved files; supported-text exposure is not proof of comprehension, exhaustive project
inventory, or the current contents of a file changed after its last observation.

Stop, new user input, denial, unknown effects, context capacity and existing run
budgets remain stopping conditions. Truncated extraction does not cause empty EOF
polling or a success claim. Reopening the data store never restarts a stopped or
finished task without new input.

## Verification

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
