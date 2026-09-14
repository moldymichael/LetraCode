# Source scope and continuation verification — September 14, 2026

The continuation repair separates encountered source passages from required
whole-file reading and counts returned evidence rather than changed search
arguments. A narrow answer can finish while its source ledger remains partial.
Explicit complete reading still requires actual exposure of the necessary
pages. This record distinguishes production-path scripted tests from fresh
local-model runs; neither constitutes native Windows or training verification.

## Inspected baseline

The supplied ZIP investigation was treated as evidence to check against the
machine, not as instructions to execute. Its source-scope and search-progress
findings were confirmed against actual installed modules and saved runs.

| Inspected component | Observed baseline |
| --- | --- |
| Canonical checkout and local `main` | `8772a1916c1d65b6514aa2ffdaf024c8e6e886b8` |
| Existing source-limitation branch | `48856173b6872675cf95b41b3bf7bacd9e79181b`, containing implementation `42c5f30` |
| Installed application | Earlier failed-read repair, separate conversation-training work at `b2ba108`, and a local scrollbar change |
| Application version | 0.6.0 in these different builds; insufficient to identify their contents |

Installed `worker.py`, `store.py`, and `pause_context.py` matched the actual
hashes recorded by the [previous installation](INSTALLED-SOURCE-LIMITATIONS-UPDATE.md).
The other installed files were compared separately. GitHub `main` was not used
as a replacement for the newer installed combination. The isolated repair
branch was retained, with existing training and other local changes preserved.

Live SQLite records were opened read-only, without constructing a live `Store`.
Private exports and model-trial artifacts remain outside the repository. No
private source titles, prompts, manuscript text, chat exports, or source paths
are included here.

## What the saved runs established

The newest saved run explicitly requested twelve chapters in full. Its eighteen
completed model requests were sequential source reads or a directory listing;
the nineteenth request was interrupted. It had no substantive final answer and
no application-generated recovery reads. Ten of eleven encountered chapters
were fully exposed when the user stopped during the eleventh. That remaining
reading was real work, not evidence of an incidental-search loop.

An earlier targeted analysis question was the clean broader-loop reproduction.
Search returned six passages. The application rejected four substantive
tool-free answers as `source_incomplete`, issued four recovery reads, crossed a
segment boundary, and requested inference again. Its recovery selections
included an index tail, a three-character chapter tail, a larger chapter gap,
and the beginning of an index initially encountered through search. Seven
other tool calls came from the model. The twelfth model request was manually
stopped. Saved context receipts confirmed that earlier provisional answers
were omitted from later ordinary conversation input.

A more recent follow-up explicitly required complete reading, so its paging
was not used as the narrow-task acceptance case. It nevertheless demonstrated
the separate progress defect: five different memory-search queries returned
the identical empty result body while every subsequent request still recorded
zero stalls. No inspected run showed a terminal `source_limited` response
automatically restarting without new user input.

## Root cause and final behavior

The previous failed-path fix stopped only when no recoverable page existed.
Search passages still entered the truthful file ledger, whose completeness is
measured against each full file. Recovery treated every gap as an obligation,
even when the question only needed a passage. Separately, search fingerprints
included arguments, allowing another query for the same evidence to reset the
consecutive no-progress count.

| Production module | Change |
| --- | --- |
| `reading.py` | Derives required whole-file paths separately from coverage and filters automatic recovery to those paths. Search hits and application-generated recovery cannot create obligations by themselves. |
| `tools.py` | Adds validated `read_file` scope: `passage` or the compatible `whole_file` default; continuation/retry arguments preserve an explicit scope. |
| `context.py` | Explains passage versus complete-reading intent to the model without asserting that partial exposure is complete. |
| `evidence.py` | Presents required reads and passage evidence distinctly. Retrieval, exposure, source identity, failures, and incomplete coverage accounting remain intact. |
| `worker.py` | Saves reading obligations, uses them for recovery, and supplies actual project-source range novelty to progress accounting. A final limited answer closes the existing continuation context. |
| `continuation.py` | Tracks returned search range unions by source/version and normalized result observations. Query wording, ranking, ordering, subsets, empty results, and changing wrapper statistics do not manufacture progress. |

Successful direct whole-file reads retain their existing completion contract.
A later model-selected passage cannot cancel that obligation. Explicit user
complete-reading directions can also bind observed named files or an explicit
all-read-files policy. This is conservative instruction binding, not a general
relevance classifier: quoted or fenced instructions, questions discussing
instructions, mixed search/read clauses, negative directions, and ambiguous
same-basename paths must not broaden recovery accidentally. Actual later user
directions can narrow scope.

New ranges and changed source versions can still advance work. Failed reads
remain failures; incidental files remain partial. Existing limits were not
lowered. Stop, approvals, unknown outcomes, saved-result recovery, continuation
boundaries, and the prohibition on synthetic user turns remain in force. A
model that proposes tool calls is exercised separately from recovery after a
tool-free answer. Required missing pages may still cause a provisional answer;
this change does not certify model comprehension or eliminate every premature
answer a model could emit.

## Automated application tests

Environment: Fedora, Python 3.14.7, Qt offscreen. The new worker regressions use
real disposable `Store` data, file/search tools, evidence, packing, and run
control with scripted model responses. They do not mock away the source or
continuation failure.

The initial scope regression command was:

```bash
QT_QPA_PLATFORM=offscreen python3 -m pytest -q tests/test_source_reading_scope.py
```

Before the fix: **5 failed, 1 passed in 4.13 seconds**. Failures included a
narrow task making nineteen requests instead of two and recovery completing an
incidental archive. Initial search-progress regressions were run with:

```bash
QT_QPA_PLATFORM=offscreen python3 -m pytest -q tests/test_search_progress.py
```

Before the search fix: **13 failed, 4 passed in 64.46 seconds**, including cases
that continued until existing global limits rather than stopping repeated
unproductive searches. The expanded final search suite contains twenty-one
passing cases.

Final focused command:

```bash
QT_QPA_PLATFORM=offscreen python3 -m pytest -q \
  tests/test_source_reading_scope.py tests/test_search_progress.py \
  tests/test_source_read_limitations.py tests/test_reading_recovery.py \
  tests/test_reading_recovery_boundaries.py
```

Result: **72 passed in 12.89 seconds**. Coverage includes narrow search hits,
explicit complete reading, passage scope, retained prior obligations, changed
sources, failed/untracked reads, repeated unchanged searches and interleaved
failures, paging, Stop/recovery boundaries, and no synthetic user messages.

The combined candidate retains the actual installed application, including
conversation training and the local UI change, and overlays only the six
production modules above. Its full command was:

```bash
QT_QPA_PLATFORM=offscreen python3 -m pytest -q
```

Candidate result: **1,285 passed, 48 skipped, 6 warnings in 90.21 seconds**.
This candidate's test composition differs from the source branch; its count
must not be substituted for the source full-suite result.

Source full-suite result with the same command: **1,213 passed, 22 skipped,
6 warnings in 84.80 seconds**. The preceding run had **1 failed, 1,212 passed,
22 skipped, 6 warnings in 88.46 seconds**. One UI routing/retry
fixture initially exceeded its synthetic context budget: the baseline measured
8,159 of 8,192 fake tokens and the expanded tool/guidance text measured 8,222.
The installed candidate's shorter provenance measured 8,177. This fixture tests
routing and retries, not a context threshold, so its configured test window was
raised to 16,384; production budgets were unchanged. The UI module then passed
**12 tests in 0.93 seconds**; the candidate also passed the updated UI module
with **12 tests in 1.04 seconds**. The final production hashes were unchanged
through this fixture correction and matched the real-model runs.

`python3 -m compileall -q letracode packaging`,
`bash -n install.sh uninstall.sh packaging/build-rpm.sh`, and `git diff --check`
passed. The six warnings in each full suite are the existing Python 3.14
multi-threaded process-fork deprecations in crash/recovery tests. An initial
broader-test command named nonexistent `tests/test_context.py` and collected no
tests (exit 4); the corrected commands and full suite ran the existing modules.
Independent agent review found no remaining actionable defects in the scoped
recovery, search progress, persistence, Stop, approval and replay paths.

An earlier PR Windows CI run (`34893492334`) had two failures in
`test_real_missing_exposure_is_paged_before_the_terminal_answer[False/True]`.
The fixture expected 15,213 characters but Windows newline conversion wrote
16,013. Its `write_text` now explicitly uses UTF-8 and `newline=''`; production
newline/exposure handling was not relaxed. The corrected fixture was exercised
on Fedora. Fresh native Windows results remain a separate CI requirement.

## Fresh real-model comparison

These runs used newly generated responses through the real `LocalEngine` and
production worker, tools, evidence, and native template/tokenizer. They were
not captured-response replay or scripted inference.

| Scenario | Model requests | Model-origin tools | Application recovery reads | Provisional answers | Terminal answers | Elapsed |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Installed baseline, exact saved narrow question | 12 | 4 | 7 | 7 | 1 | 447.968 s |
| Final six-module fix, identical narrow question | 2 | 1 | 0 | 0 | 1 | 31.853 s |
| Final fix, explicit entire representative chapter | 4 | 3 | 0 | 0 | 1 | 38.395 s |

Both narrow runs independently selected the same first search query and
received byte-for-byte identical 30,262-character result bodies. The baseline
eventually exposed all ten files it encountered, producing seven provisional
answers along the way. The final run saved its first substantive answer as
`source_limited`: six search-hit files remained recorded, only one fully
exposed, with no forced reading of the other five.

The explicit complete-reading trial used one representative 15,772-character
chapter, not all twelve chapters. Its three real pages covered [0,8207),
[8207,12207), and [12207,15772). All supported text reached completed model
requests before the single terminal acknowledgment (`response_unverified`).

Both final chats reopened with zero further model requests and unchanged
history. Each persisted chat and every model input contained exactly one real
user message. Every tool call had one uniquely matching result. Neither trial
guard fired. These observations establish the named cases, not deterministic
behavior for every model or question; the timing comparison is not a general
performance benchmark.

### Runtime and budgets

- Model: Qwen3.5-9B-Q4_K_M.gguf; full-file SHA-256
  `148ffb97ac1d4cbbaef95ff36dbc02948b9c25746d6df3bc86533b859060380a`.
- Native qwen35 chat/tool template: 7,756 characters; SHA-256
  `a4aee8afcf2e0711942cf848899be66016f8d14a889ff9ede07bca099c28f715`.
- llama-server 0.4.0-dev, build 10868, commit `304665fe7`, GNU 15.3.1,
  Linux x86_64, native `--jinja` template handling.
- Instant, thinking disabled; 53,248-token context; 3,072-token reply reserve;
  temperature 0.7; 30 GPU layers; 8 threads; RTX 2060 SUPER.

All measured token accounting used the native runtime tokenizer. Baseline
prompt counts ranged from 6,549 to 29,241; final prompt counts ranged from
6,582 to 15,013. These loops were not context-overflow artifacts. Baseline
inference took 436.773 seconds and context builds 4.535 seconds. Across both
final cases, inference took 65.722 seconds, context builds 1.833 seconds, token
counting 0.225 seconds, and source-tool execution 0.128 seconds. These separate
measurements do not justify an unmeasured caching or scanning performance claim.

### Isolation and retained evidence

The trial used a frozen copy of all forty-one installed Python modules. Final
code overlaid only the six modified production modules; their hashes still
matched the repair worktree after inference. Training code was preserved.

The complete linked source tree (214 files) and Memory tree (368 files) were
copied into private disposable storage. All four Memory files selected in the
saved narrow context matched their captured hashes. Nine observed sources
still matched current bytes. One source had changed since the saved run: its
private historical copy was reconstructed from saved numbered-line text plus
its raw continuation and verified against the recorded complete-file hash.
The current live original was preserved. After the trials, all 214 live source
files and 368 live Memory files retained their pretrial content hashes.

The disposable database contained the relevant project and fresh trial chat;
older chats and training artifacts were omitted. The unused configured
secondary model was disabled. Actions and web were disabled, approvals denied,
and a test-only path guard confined ordinary file tools to the copied trees.
Actual model responses and tool results were not preprogrammed. These controlled
differences are disclosed rather than claiming an exact clone of the complete
live environment.

The retained private harness ran the baseline with `--scenario narrow` and the
final code with `--scenario both`, each with `--seconds 600 --requests 16` under
`QT_QPA_PLATFORM=offscreen`. Those were external per-scenario trial bounds;
application limits were unchanged and all reported runs finished naturally.
A preliminary source-hash preflight aborted before model startup when it found
the changed source; it was resolved by the verified private reconstruction.
Intermediate model runs are retained separately and are not presented as
verification of the final production hashes.

Private evidence includes request/response JSON, saved conversations, source
ledgers, disposable databases, native engine timing logs, model/template hashes,
code manifests, first-search result comparison, and unchanged-live-file checks.
No private dataset or transcript is committed. Native Windows, installer/RPM
packaging, and new training runs were not performed by this verification.
The [installed update](INSTALLED-CONTINUATION-SCOPE-UPDATE.md) records rollback,
unchanged application/data checks and verification importing the actual installed modules.
