# Windows release of current LetraCode

Publish version 0.3.0 for Windows 10/11 x64 while retaining the current Project
files UI, schema-3 data, guarded saves, recovery history, Undo and existing Linux
behavior. Reuse the prior Windows development work selectively; never replace
the newer backend with its schema-1 predecessor.

Keep one Qt application. Isolate platform filesystem/process differences. On
Windows use handle-based, reparse-safe filesystem operations, non-overwriting
moves, cross-process locks and retained backups. Guard directory identity and
reject unsafe aliases. Normal files remain ordinary files. Windows uses
LocalAppData, PowerShell commands and owned process-tree termination. Preserve
explicit approvals, resource limits and interruption handling.

Ship an accessible per-user Windows installer/download with dependencies,
Start Menu access, clear llama.cpp setup, update/uninstall data retention and
instructions. Use the existing installer as the starting point; prefer a
self-contained executable installer if Windows CI can build it without adding
runtime complexity. No bundled model, account or administrator requirement.

Verify the Linux suite, native Windows tests and actual packaged install,
startup, update, uninstall and retained user data in Windows CI. Publish only
after checks pass, then merge GitHub main and attach Windows downloads to a
versioned release. Preserve all existing local checkouts and live user data.
