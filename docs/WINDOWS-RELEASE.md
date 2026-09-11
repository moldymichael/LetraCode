# Windows release 0.3.0

> **Historical verification record.** These findings apply to the September 8, 2026 release and the commits named below, not to all later 0.6.0 development builds. For installation today, use the [Windows guide](WINDOWS.md). For document status and navigation, use the [index](README.md).

This release brings the current Project files interface and schema-3 backend
to Windows 10 version 1809 or newer and Windows 11 x64. The Windows setup executable includes Python, Qt/PySide6
and PDF support. The publication build bundles Python 3.13. Installation is per user, with a Start Menu shortcut and
standard Windows uninstall. The portable ZIP uses the same per-user data
location. Neither distribution includes llama.cpp or model weights.

Application files and user data have separate locations. Updating replaces the
application; uninstall removes the application and shortcut. Both retain chats,
settings, ordinary project notes and hidden recovery history. User data defaults
to `%LOCALAPPDATA%\letracode`; an explicit `--data-dir` selects another local
folder. The existing Linux installation and live data were preserved during
development and release verification.

## Storage and process behavior

The existing save, history, conflict, backup and Undo algorithms use an explicit
filesystem boundary. Linux keeps its descriptor-relative implementation.
Windows retains native handles for directory ancestors, rejects reparse points,
hardlinks and ambiguous Windows path aliases, and uses non-overwriting moves
and cross-process locks. Native identities match Python's file identities
across supported versions. Read snapshots refuse a conflicting open writer;
retained recovery files still detect edits after a save.

Windows data and guarded editable files must be on a local drive. UNC/network
shares, device paths, junctions, symbolic links and directories explicitly
configured for case-sensitive Windows filenames are refused by guarded storage.
Read-only files must be made writable before saving. Files with custom,
protected or differing inherited access-control lists remain readable, but
saving is refused when replacement cannot retain the same owner and permissions.
The empty staged file is checked before writing content. File data and recovery
records are flushed. Windows has no supported
unprivileged equivalent of Linux directory fsync, so power-loss durability of
directory entries follows Windows/filesystem guarantees. Existing collision,
interruption and recovery checks remain in place.

Approved commands use PowerShell on Windows and Bash on Linux. The approval
shows the shell and command. Windows Job Objects own the engine and command
process trees, stop descendants on cancellation/timeout, and close them if the
app exits. A packaged app temporarily restores the normal Windows DLL lookup
when launching external programs so llama.cpp can load its own DLLs.

## Verification

Release verification runs in the repository's Windows and Fedora workflow.
Windows source jobs exercise Python 3.11, 3.13 and 3.14. A separate native
Windows job builds the actual installer and portable ZIP, opens a real Qt
window from Unicode paths containing spaces, and checks updates, uninstall,
retained chats/notes/history and portable startup without Python on PATH.
Its artifacts contain desktop screenshots and a lifecycle report.

The application source at `8d4149c9937312a3c5d5c2c9957d88733847eea3` passed
[the complete Windows and Fedora verification run](https://github.com/moldymichael/LetraCode/actions/runs/34277778599)
on September 8, 2026:

- Windows Python 3.11: **704 passed, 20 skipped**, 451.55 seconds.
- Windows Python 3.13: **704 passed, 20 skipped**, 468.65 seconds.
- Windows Python 3.14: **704 passed, 20 skipped**, 469.85 seconds.
- Fedora 44: **709 passed, 15 skipped**, 83.10 seconds; RPM/source builds passed.
- Local Fedora offscreen run: **709 passed, 15 skipped**, 45.12 seconds,
  with six existing Python 3.14 process-fork deprecation warnings.
- Native installer/portable lifecycle: **passed**. Screenshots and the report
  confirm startup, same-version update, uninstall, schema-3 chats/notes/history
  retention, running-app protection and Unicode paths without Python on PATH.

Skips cover host-specific behavior; native Windows filesystem, ACL, process and
legacy migration checks ran on Windows. Independent review found no remaining
blocking issues. Compilation and diff checks passed. Release documentation was
then finalized; the publication build runs the same required checks again.

The Windows CI host is Windows Server 2022; these tests do not claim a manual
run on every Windows 10/11 edition or real-model quality. Inference behavior is
tested with scripted local peers, while model and hardware compatibility depend
on the separately selected llama.cpp build and GGUF model.

The installer is unsigned. Download it from the repository's versioned GitHub
release and compare its SHA-256 checksum if Windows asks about its publisher.
At the time of the original release verification, access was private. As checked September 11, 2026, the repository and published releases are public; Actions artifact downloads still require GitHub sign-in and read access.
