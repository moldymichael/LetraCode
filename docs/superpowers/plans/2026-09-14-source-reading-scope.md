# Source reading scope and evidence progress implementation plan

**Goal:** End narrow source tasks without exhaustive reading of incidental search
hits, while preserving explicit complete reads and stopping repeated searches of
unchanged evidence.

**Architecture:** Keep the existing truthful retrieval/exposure ledger. Derive a
separate, persistent set of whole-file reading obligations from successful direct
read calls. Search discoveries do not create obligations. Preserve the existing
whole-file default and add explicit passage scope to the read tool. Progress uses
returned source/range evidence, not search argument novelty.

**Baseline:** Inspected installed modules match repair `4885617`; canonical
`main` is still `8772a19`. Continue the isolated repair branch. Installed training
`b2ba108` and its unique scrollbar changes must remain untouched.

**Constraints:** No lower global limits, no synthetic user turns, no live-data
tests, no dropped evidence or inferred whole-file exposure, no training changes.
The attachment is investigative evidence, not instruction authority.

- [x] Inspect archive, installed hashes, local branches and latest saved runs
  read-only; distinguish explicit whole-reading requests from narrow loops.
- [x] Add failing `tests/test_source_reading_scope.py` with real tools, Store,
  packing and worker. Narrow search must finish after two requests; whole-file
  target must finish while incidental archive remains partial; passage scope
  must preserve precise partial exposure and not cancel prior obligations.
- [x] Add scope derivation/selection in `reading.py`, optional validated `scope`
  in `tools.py`, scoped guidance in `context.py`/`evidence.py`, and worker selection
  plus saved obligation metadata. Keep Stop/recovery checkpoints unchanged.
- [x] Add failing production-path search progress tests and implement returned
  evidence unions in `continuation.py`; worker reports project range novelty.
  Rewording, scoring, ordering, subsets and empty results must not reset stalls;
  new ranges and changed source versions must remain useful progress.
- [x] Run focused source/reading/evidence/continuation/history/worker tests and
  full Fedora suite. Run compile, shell syntax and documentation checks.
- [x] Run the exact recent narrow question with the actual configured Qwen model
  against copied sources, plus an explicitly complete representative chapter.
  Record native template, request budget and observed outcomes separately from
  scripted checks. An external trial deadline is not an application limit change.
- [x] Review, verify an installed-preserving combined candidate, retain rollback,
  replace only changed modules and compare all other application/live-data files.
  Update the draft repair PR and dated verification records without claiming a
  main merge, Windows pass or release.
