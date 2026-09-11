# Installed automatic reading recovery

Installed at the user's request from the canonical
`/home/miceoil/Projects/LetraCode` checkout using `./install.sh --no-deps`.
PySide6.QtWidgets and pypdf imports were verified before installation. No
running LetraCode application process was found, and none was stopped.

The version label remains 0.6.0. The installed application now includes
`reading.py` and the updated worker, file tools and source-reading instructions.
Module contents distinguish this repair from the previous installation. The
source changes and pre-existing uncommitted work remain in the canonical main
checkout.

Installation verification:

- `~/.local/bin/letracode --version` succeeds and reports LetraCode 0.6.0.
- All 40 installed application files match the canonical source byte for byte,
  excluding generated `__pycache__` files.
- All 421 existing entries under `~/.local/share/letracode` retain their paths,
  modes, sizes, modification times and symlink targets through installation.
- A disposable installed-package smoke test recovered mixed invalid paging
  arguments and verified exposure of all 96,008 Unicode/BOM/CRLF characters.
  It used 25 automatic reads, 26 scripted model requests, two segment boundaries
  and exactly one user turn. The final response's coverage receipt matched the
  stored evidence. Every loaded LetraCode module resolved to the installed
  package; no source-package import or actual model inference was used.
- The existing comparison workflow remains included. No live conversation,
  training run, model adoption or rollback was invoked by installation checks.

Start LetraCode from the desktop launcher or `~/.local/bin/letracode` to use the
repair. See [reading recovery and regression verification](READING-RECOVERY.md)
for its behavior and limits.
