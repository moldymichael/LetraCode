# LetraCode source of truth

The canonical development repository is `/home/miceoil/Projects/LetraCode`.
Its local `main` is the integrated application baseline. Start by reading
`README.md` and `docs/CURRENT-STATE.md`, then check `git status`, the current
commit and `letracode/__init__.py` before choosing a baseline.

Newer work previously lived only in separate worktrees while local `main`
remained the 0.1.1 import. This was corrected with the Strand experience
update. Do not use a historical worktree, an old version label, or
`origin/main` as evidence of the newest local application. Remote branches
can lag local development; compare ancestry and actual files. Preserve
uncommitted work. Keep local `main` current when integrating completed work.

The desktop launcher is `~/.local/bin/letracode`; installed Python modules
are in `~/.local/share/letracode-app`. Installation is separate from source.
Confirm the launcher version and compare installed modules when diagnosing
the running app. Set Strand's development folder to the canonical repository,
not the installed package or an older worktree.

User data lives separately in `~/.local/share/letracode` (or `XDG_DATA_HOME`).
Use disposable data for tests; preserve chats, ordinary Memory files, drafts,
training artifacts, approvals and rollback. Installation records are in
`docs/INSTALLED-*-UPDATE.md`. Historical worktrees and dated documents are
evidence, not default development targets.

Run `QT_QPA_PLATFORM=offscreen python3 -m pytest -q` for the regression suite.
Build distributable source and Fedora installers with
`python3 packaging/build-release.py`. Local installation can use
`./install.sh --no-deps` when dependencies have already been verified.
