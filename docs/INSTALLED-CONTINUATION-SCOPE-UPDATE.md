# Installed continuation scope repair — September 14, 2026

This update separates required source reading from incidental search passages
and measures search progress from returned evidence. The [investigation and
verification record](CONTINUATION-SCOPE-VERIFICATION.md) distinguishes scripted
application tests from fresh Qwen model runs. It follows the [initial failed-read
repair](INSTALLED-SOURCE-LIMITATIONS-UPDATE.md), whose dated record remains intact.

The candidate preserves the actual installed application and overlays only
`context.py`, `continuation.py`, `evidence.py`, `reading.py`, `tools.py` and
`worker.py`. Training, the local scrollbar repair, saved-data code, launcher
and previous rollback files are retained. The update is installed and verified
on the maintainer's Fedora host. The live-data app was left closed.

The managed launcher is still `~/.local/bin/letracode`, executing
`/usr/bin/python3 -P -m letracode` from `~/.local/share/letracode-app`.
It reports 0.6.0. These six hashes identify the actual update:

| Module | SHA-256 |
| --- | --- |
| `context.py` | `32d21cce0c78f8eb74ef1c27d6a4f88f1ad93beacd5d3580f95db48832d14b74` |
| `continuation.py` | `ea9f2fbac489cb10fe0dd3cc99f0d81919d9add6155c9a2e75c8406dffc58b9b` |
| `evidence.py` | `3c7b91aae61c74445e2e88bb4696d4c696543f99662146c820a4b7f7b4eb8c0a` |
| `reading.py` | `f714e65f15f65e642f3a3d45ee955ab3af4f8e2e25e5e75ef1d7adebe0305e0b` |
| `tools.py` | `dd459acbc1402f0969a4c6738d40611b2d4e73e0a2a2fb0ce6ea583f0353fa00` |
| `worker.py` | `c3015ce73e06266e306e5fdbb327b4b4c7e3ad5c71a6939137b5d03e0c3e95b6` |

The application and model server were closed before replacement. A complete
copy of the previous installed app and launcher was retained, then each of the
six verified modules was replaced atomically. All **43 package files** match
the tested candidate; the **37 other package files** remain byte-identical,
including conversation training, the scrollbar repair and its backup,
`store.py` and `pause_context.py`. The launcher hash is unchanged.

The candidate combines the actual installed package with the separate training
branch's tests and the source repair regressions. Its full offscreen suite
passed **1,285 tests, 48 skipped, 6 warnings in 90.21 seconds**. The source
repair branch independently passed **1,213 tests, 22 skipped, 6 warnings in
84.80 seconds**. Compilation, shell syntax and diff checks passed. The later
explicit-budget UI fixture passed twelve tests in each build without changing
any production module. These counts apply to different test compositions.

After replacement, the five focused source/reading/search test modules were
copied into the private verification directory and run with
`PYTHONPATH=~/.local/share/letracode-app`, `PYTHONDONTWRITEBYTECODE=1` and
`QT_QPA_PLATFORM=offscreen`. The harness asserted that imports came from the
actual installed package before starting pytest: **72 passed in 12.69 seconds**.
An offscreen check used the actual `app.main()` entry point with a disposable
`--data-dir`, observed its visible idle window, then closed it successfully
(exit 0). The managed launcher's `--version` check also passed. This is automated
application verification, not a manual native desktop or packaging claim.

The separate fresh Qwen trials used exactly the six installed hashes above.
For the identical saved narrow question, the baseline made twelve model
requests and seven provisional answers; the final build made two requests,
one search and one terminal answer with honest source limitations. An explicit
complete-chapter trial read all 15,772 characters before its terminal
acknowledgment. Neither final run continued on reopening. Full configuration,
source-copy boundaries and model-quality limits are in the linked verification
record. No new training run or native Windows verification was performed.

Rollback and private verification records are retained at
`~/.local/share/letracode-continuation-scope-20260914-tpa7pdma/`, including
`previous-app`, `launcher`, manifests, test logs, real-model observations and
the installed verification scripts. The old failed-read rollback remains.
The live database SHA-256 and all **601 live-data filesystem metadata entries**
were unchanged through installation and subsequent verification. Chats,
drafts, approvals, Memory, recovery records and training artifacts were
preserved. No live-data migration was run. This is a local update, separate
from a reviewed merge or published release.
