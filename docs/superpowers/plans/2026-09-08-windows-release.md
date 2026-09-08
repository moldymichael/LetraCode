# Windows release implementation plan

> Use superpowers:subagent-driven-development for independent tasks and review.

**Goal:** Publish an installable Windows build of current LetraCode 0.3.0.
**Architecture:** Keep the current app and schema; isolate OS file/process APIs.
**Tech stack:** Python 3.11+, Qt/PySide6, Win32 filesystem/job APIs, GitHub Actions.
**Spec:** ../specs/2026-09-08-windows-release.md

- [x] Filesystem: provide native Windows safe directories/files, no-replace
  moves, file identity and interprocess locks behind current Memory/Strand
  algorithms. Port Store initialization/archive and evaluation export calls.
  Preserve Linux algorithms. Add native regression tests for save/restart,
  external edits, reparse paths, collision, backup, Undo and process locking.
- [x] Runtime/UI: adapt existing platform/process modules to current engine,
  command execution, source reads, application paths and setup text. Preserve
  resource limits, cancellation and approvals; verify process descendants.
- [x] Distribution: reuse/refine Windows per-user installer, package dependencies,
  shortcuts and uninstall; build version0.3.0 downloadable Windows artifacts.
  Add Windows/Linux CI and native installer lifecycle smoke tests.
- [x] Integration: run full Linux suite and Windows CI, fix all platform failures,
  review the whole change and inspect startup. Update user docs with real results.
- Publication: merge verified code into main, tag0.3.0 and publish a GitHub release
  with Windows download and installation instructions. Verify private downloads
  using an authenticated account; preserve the repository's private visibility.
  Publication status and assets are recorded in the
  [versioned GitHub release](https://github.com/moldymichael/LetraCode/releases/tag/v0.3.0).

Implementation and native validation completed September 8, 2026. Results and
remaining platform boundaries: [Windows release record](../../WINDOWS-RELEASE.md).

Baseline: main b1d4e82,656tests passed on Linux. Prior Windows work resides in
another dirty checkout and has not received native execution validation.
