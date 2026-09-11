## Change and reason

Describe the problem, scope and related issue. Use a closing keyword only when this PR actually resolves the issue.

## Verification

- Commands run and results:
- Operating system, Python and tested commit/build:
- Regression test: how does it fail without the fix?
- Native process/installer or real-model checks, where relevant:
- Checks not run, known failures and limitations:

For docs-only changes, report link/command/source checks instead of inventing a runtime test result. A packaging pass is not a full-suite pass.

## Safety and compatibility

Describe data/migration/recovery effects and Windows/Linux differences, or state that none are introduced. Note anything mocked that still needs native verification. Confirm that no private data, model weights or unrelated changes are included.
