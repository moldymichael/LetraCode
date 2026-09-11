# Working on LetraCode

## Source of truth

Read `README.md`, `CONTRIBUTING.md` and `docs/README.md` first. Check the current branch, commit, `git status` and `letracode/__init__.py` before editing. Start normal contributions from remote `main` unless the task names another baseline; do not assume an arbitrary local checkout is current.

Source, installed application, published release and user data are separate. When diagnosing a running app, identify its launcher/version and compare actual installed files with the intended source. A version string alone cannot distinguish all development builds.

`docs/CURRENT-STATE.md` and `docs/INSTALLED-*-UPDATE.md` preserve dated development and installation evidence. Historical branches/worktrees and their test counts are not default development targets or current verification.

## Maintainer's recorded Fedora layout

Earlier local work used `/home/miceoil/Projects/LetraCode` as the canonical checkout, `~/.local/bin/letracode` as the launcher and `~/.local/share/letracode-app` for installed modules. This is one maintainer's recorded layout, not a required path on another contributor's machine. Remote branches historically lagged local work; compare ancestry and files when that is relevant. Do not claim access to uninspected local changes.

Preserve uncommitted work. Do not overwrite or force-reset a branch to make it match documentation. Select the actual development checkout as the app's source folder, not its installed package or an obsolete worktree.

## Data and execution boundaries

Use disposable `--data-dir` directories and copied sample files. Defaults are `%LOCALAPPDATA%\letracode` on Windows and `${XDG_DATA_HOME:-~/.local/share}/letracode` on Linux. Isolated app data is not a permission sandbox. Preserve chats, ordinary Memory files, drafts, approvals, recovery history, training artifacts and rollback.

No unrelated refactors, unverified merges, branch deletion, release publication or live-data migrations as part of a documentation fix. Preserve old records and links; clearly label historical or proposed behavior rather than silently rewriting results.

## Verification

Use the native Windows or Fedora commands in `CONTRIBUTING.md`. Keep Linux and Windows process/filesystem safeguards intact. Confirm a regression test exercises the relevant production behavior rather than mocking away the failure. Report exact commands and results, including failures/skips and checks not run.

The authoritative automated commands are in `.github/workflows/windows-and-fedora.yml`. A green packaging job is not a green full suite or a real-model test. Do not publish a build as verified on a platform that was not actually tested. Training verification is separate from desktop packaging.
