# Installed source-limitation repair — September 14, 2026

The maintainer's Fedora installation now includes the source-reading loop repair
from commit `42c5f30` on `codex/source-read-limitations`. Source behavior, the
captured reproduction and regression results are recorded in
[reading recovery](READING-RECOVERY.md#failed-read-continuation-repair--september-14-2026).
This local installation is separate from a reviewed merge or published release.

The managed launcher remains `~/.local/bin/letracode`, using `/usr/bin/python3`
and `~/.local/share/letracode-app`; the version remains 0.6.0. Actual installed
module hashes, rather than that shared version string, identify this update.

Only `worker.py`, `store.py` and `pause_context.py` were replaced. Their prior
contents matched local `main` at `8772a19` and the separate conversation-training
branch at `b2ba108`. Every other installed application file was preserved,
including the training feature and the local scrollbar repair. No training code
was changed. The application and model server were closed before replacement.

| Installed module | SHA-256 |
| --- | --- |
| `worker.py` | `36339213e27226760007d4d460aaa3ce2e48858c4accfc83f860e853d18624a3` |
| `store.py` | `475cb5636956b6d27fa22aa785d9ee40912915042f844925c74aa2b5eedef1f8` |
| `pause_context.py` | `84b101520748fb60889c60e3d733915ce0fecb862bd379cee85fc54742fce5dd` |

A disposable candidate used the installed application files, the separate
training branch's tests and this repair's regressions. From that candidate:

```bash
QT_QPA_PLATFORM=offscreen python3 -m pytest -q
python3 -m compileall -q letracode
```

The combined suite passed: **1,240 passed, 48 skipped, 6 Python 3.14 process-fork
deprecation warnings in 80.80 seconds**. Compilation passed. Hash checks confirmed
that exactly the three intended modules differed from the previous installation.
This count includes the existing training branch's tests and skips; it is
separate from the focused source branch's **1,168 passed, 22 skipped** suite.

After installation, the captured-response replay imported the installed modules
and again stopped after **4 requests, 3 tool results and 1 source-limited answer**.
The failed read remained in the evidence ledger. There were no extra segment
boundaries or synthetic user messages, and reopening dispatched no request or
changed evidence. An offscreen check of the installed application entry point
opened and closed its window successfully with disposable data. The launcher
version check also passed. These are automated application checks; no fresh
real-model inference, native Windows run or release-package build was performed.

Rollback and verification records are retained at
`~/.local/share/letracode-source-limitations-20260914-pn5gqc0a/`, including
`previous-app`, the launcher copy, module manifests, full candidate test log and
installed replay summary. The launcher hash and live database hash were unchanged
by installation. All **600 live-data filesystem metadata entries** were unchanged
through installation and subsequent verification. Chats, drafts, approvals,
ordinary Memory files, recovery history, training artifacts and prior rollback
copies were preserved. The normal live-data app was left closed.
