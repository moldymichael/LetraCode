# Compare with Strand workflow repair

Baseline: canonical local main `b4a20560169369df1e242cc8c06e6aa2924b0e00`,
LetraCode 0.6.0. The installed Python modules matched that source before this work.

The original button was connected to the comparison worker. It started expensive
candidate verification and file hashing without changing the visible status first,
opened no comparison window, and offered no new prompt input. Results appeared only
after completion in a long text field. This explains the apparent lack of response;
it was not an unconnected button. Comparisons used only the run's frozen held-out
questions. The optimizer report contained at most three short generated samples,
while later Chat comparison records lived separately in the application database.
Opening/exporting the run folder therefore did not include them.

The button now opens a dedicated window before starting inference. Add fresh
questions, then run every original held-out question and the fresh questions on the
current Strand and candidate with the same standalone Chat settings. The window
shows verification, loading and answer progress, full independently scrollable
answers, separate Thinking text, each question's outcome and an overall judgment.
The prompt editor collapses after completion to give the answers more room.

Stop and failure preserve available answers and explicit incomplete statuses.
Closing the comparison window does not cancel its job; reopen it or use Improve's
Stop button. Reopening an interrupted app never restarts comparison automatically.
Reruns archive the previous attempt and its saved review. Removed questions' prior
answers remain accessible before rerunning. Export comparison creates a separate
new JSON file with the selected immutable run, effective conversion report, full
saved comparison/review/history and queued fresh prompts. It excludes unrelated
chats/settings and model binaries, but contains full prose and local paths.

Fresh questions are validated against the frozen run rather than later edits to
the example library. Comparison never changes training examples, active model or
rollback settings. New attempts reset adoption eligibility before model checks.
Adoption still requires complete answers for the original held-out and selected
fresh questions, an explicit saved judgment, matching runtime settings/system
prompt, and reverified model/adapter fingerprints. Training preparation, conversion
provenance, load-before-adoption checks and rollback are retained.

Verification uses disposable data and scripted inference peers, including the real
managed llama.cpp-compatible HTTP transport. This establishes workflow behavior;
it does not demonstrate improvement of a user's trained model. No real optimization
or user-model adoption is performed by these tests.

Final verification on September 11, 2026 (UTC):

- Full offscreen suite: **1,111 passed, 22 skipped, six existing Python 3.14
  process-fork warnings**, 68.95 seconds. Baseline: 1,076 passed with the same
  skip/warning counts.
- The six comparison-window regressions passed again after the final layout and
  review-save adjustments. Export, runtime, cancellation, artifact/currentness,
  training and adoption regression coverage is included in the full suite.
- Independent review passed an additional disposable Qt smoke test: switching
  candidates while a comparison is running preserves the originating run and
  adapter identity, with no cross-candidate results or QObject lifecycle errors.
- `python3 -m compileall -q letracode`, `git diff --check` and
  `python3 packaging/build-release.py` passed. Source tar/zip and Fedora `.run`
  artifacts are in `dist/` with the existing 0.6.0 package version.
- The 1,100 × 760 comparison layout was rendered and visually inspected with
  full sample answers. Real model quality and native Windows behavior were not
  tested in this pass.

The canonical checkout contains the repair. Source development initially left the
installed application unchanged. The subsequent user-requested installation with
`./install.sh --no-deps` is now complete, with matching installed modules and an
installed-build comparison-window smoke test. Existing user data was preserved.
See [the installation record](INSTALLED-COMPARISON-UPDATE.md).
