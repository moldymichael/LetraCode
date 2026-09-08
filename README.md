# LetraCode

LetraCode is a private desktop chat app for a local AI model. It runs on Windows
10/11 x64 and Fedora KDE, uses native Qt windows, and keeps conversations on your
computer. There is no cloud inference, account, or telemetry.

For the development baseline, current capabilities and verification boundaries,
see [Current implementation state](docs/CURRENT-STATE.md).

## Evaluation export and Memory folders

**File → Export Evaluation…** saves the selected conversation as a portable ZIP
with a readable transcript, ordered action/evidence records, recorded run
settings, optional notes, and an explanatory README. Private source/Memory tool
bodies are replaced by hashes and omission markers. Review conversation prose
and notes before sharing; free-text secrets cannot all be detected automatically.
An evaluation bundle is not a restorable backup.

**Project files** puts your notes, folders and linked originals in one visible
tree. Create notes and folders, attach existing files, or open them in your usual
application. Text edits use explicit Save; external changes are checked before
saving, and unsaved drafts and saved-change history remain recoverable.
Project instructions live in a separate optional dialog.

Existing Memory files stay in place. The former Current Context field is copied
once to `Current Context.md`, preserving the original database value for recovery.
A name collision gets a separate legacy filename. These notes become ordinary
files available to the assistant's file tools. Existing always-active choices
remain unchanged; advanced file settings and history remain accessible.

Existing Strand files and their hidden recovery/history data migrate to `Memory`;
your Strand identity remains your own configuration. Read the [migration and
pre-install checks](docs/EVALUATION-MEMORY.md) before upgrading an existing data
folder. Version 0.3.0 includes native Windows filesystem and process support while
retaining the current Project files interface, schema-3 data and recovery history.

## Install on Windows 10/11 (64-bit)

1. Open the [LetraCode 0.3.0 downloads](https://github.com/moldymichael/LetraCode/releases/tag/v0.3.0).
2. Download **LetraCode-0.3.0-windows-x64-setup.exe** and double-click it.
3. Follow the installer, then open **LetraCode** from the Start menu.

Python, Qt/PySide6 and PDF support are included. You do not need to install
Python, use a terminal, or enter an administrator password. The app installs
for your Windows account. Windows may show an **Unknown publisher** or
SmartScreen prompt because this release is unsigned; check that the download
came from the release page above before continuing.

For a portable copy, download **LetraCode-0.3.0-windows-x64-portable.zip**,
right-click it and choose **Extract All**, then open **LetraCode.exe** inside
the extracted folder. Keep its `_internal` folder alongside the executable.
The portable copy saves data in the same per-user location as the installer;
it does not put chats on a USB drive automatically.

Close LetraCode before updating. Run the new setup file to update; your chats,
settings, Project files and recovery history stay in place. To remove the app,
open Windows **Settings → Apps → Installed apps → LetraCode → Uninstall**.
On Windows 10, use **Apps & features**. Your data remains available for reinstall.

## Install on Fedora KDE

1. Download `LetraCode-0.3.0.run`.
2. Open Dolphin, go to Downloads, right-click an empty area, and choose
   **Open Terminal Here** (Konsole).
3. Run:

   ```bash
   chmod +x LetraCode-0.3.0.run
   ./LetraCode-0.3.0.run
   ```

The installer shows the Fedora packages it needs, then uses `sudo dnf install`.
It installs only for your user. Start **LetraCode** from KDE's application
launcher, or run `~/.local/bin/letracode` in Konsole.

If you downloaded the source archive instead, extract it, open Konsole in the
extracted `LetraCode-0.3.0` folder, and run `./install.sh`.

Version 0.1.1 fixes the Fedora 44 installation failure caused by the unavailable
`python3-docx` package. It requires only `python3`, `python3-pyside6`, and
`python3-pypdf` from Fedora. DOCX paragraph and table text is read by the app
using Python's standard library, so no pip installation is needed. If 0.1.0
stopped with that dependency error, run this installer normally; no cleanup
or dependency-skipping option is needed. Existing chats and projects are kept.

## First chat

LetraCode needs two local files that are distributed separately from the app:

- a `llama-server` executable from [llama.cpp](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)
- an instruction/chat model in GGUF format

On Windows, open the official [llama.cpp releases](https://github.com/ggml-org/llama.cpp/releases)
and download a **Windows x64 CPU** build to start. Extract its entire ZIP to a
permanent folder, keeping `llama-server.exe` together with the included DLLs.
Open LetraCode, choose **Model setup**, select that `llama-server.exe` and your
`.gguf` file, and save. A GPU build is optional; choose one that supports your
hardware and drivers once CPU mode works.

On Fedora, install a provided engine with `sudo dnf install llama-cpp` when that
package is available for your Fedora release, or choose a compatible local
llama.cpp build. Select its `llama-server` executable and your `.gguf` file in
**Model setup**. Model weights can be several gigabytes and are not included. Pick a model whose publisher documents
the RAM, license, and llama.cpp compatibility you need.

Create a project, link only the files or folders you want the conversation to
use, and start chatting. LetraCode previews commands and sensitive file or web
access. Read each request and choose **Approve once** only when the proposed
action is correct.

The system `llama-cpp` package can lag models that use a new architecture or
chat template. GPU offload also requires a llama.cpp build compatible with your
GPU and driver; begin with **GPU layers** set to 0 if unsure.

## Data, backup, and removal

On Windows, chats, settings and app-managed Project files live in
`%LOCALAPPDATA%\letracode`. Paste that path into File Explorer's address bar
to open it. The default application installation is separate, in
`%LOCALAPPDATA%\Programs\LetraCode`.

On Fedora, chats and settings live in `${XDG_DATA_HOME:-~/.local/share}/letracode`.
Installed application files live separately in
`${XDG_DATA_HOME:-~/.local/share}/letracode-app`, so updates and uninstall do not
delete your conversations.

Choose **File → Back up chats, Memory and source backups** while the app is open. Save the
snapshot ZIP to your backup drive. It contains a SQLite snapshot, human-readable JSON, Memory files and retained
recovery history, app-owned pre-edit source backups, and a restore guide.
Linked originals, model weights, logs and migration snapshots are excluded.
See the consistency boundary in [the reliability report](docs/RELIABILITY-VERIFICATION.md). To remove the application while
retaining its data folder, run:

```bash
"${XDG_DATA_HOME:-$HOME/.local/share}/letracode-app/uninstall.sh"
```

To reinstall or update, run the new `.run` installer in the same way as the
first installation.

## Developer and RPM packaging

`./install.sh --no-deps` skips `dnf` only for a development/test system where
Python 3.11+, PySide6, and pypdf are already installed. Set
`LETRACODE_PYTHON` explicitly to test with a chosen interpreter. Fedora uses its
system Qt packages.

Windows source development requires native Python 3.11 or newer. From the
source directory, run `python -m pip install .` and `python -m letracode`.
The optional `install.ps1` source installer creates a private virtual environment
in `%LOCALAPPDATA%\letracode-app`; it requires Python with pip/venv and internet
access. Use its `uninstall.ps1` before switching from that development installation
to the packaged installer. Both preserve `%LOCALAPPDATA%\letracode`.
The downloadable setup executable is the normal installation path.

Build the Fedora `.run` and reproducible source `.tar.gz`/`.zip` archives with:

```bash
python3 packaging/build-release.py
```

To build Windows downloads, use native Windows x64 Python 3.11 and
[Inno Setup 6](https://jrsoftware.org/isinfo.php):

```powershell
python -m pip install -r packaging/windows-requirements.txt .
python packaging/build-windows.py
```

The build creates a setup executable, portable ZIP and SHA-256 checksums in
`dist`. Its Python/Qt dependencies are bundled; llama.cpp and model weights are
separate. The [Windows and Fedora workflow](https://github.com/moldymichael/LetraCode/actions/workflows/windows-and-fedora.yml)
runs source tests on Windows Python 3.11, 3.13 and 3.14, builds the downloads,
and checks actual native-window startup, reinstall/update, uninstall and data
retention on a clean Windows account. It saves desktop screenshots and the
lifecycle report with its artifacts. **Run workflow** can validate a release
branch before publishing downloads.

On Fedora, install `rpm-build` and run `packaging/build-rpm.sh` to build the
included spec. See Fedora's [RPM packaging
guidelines](https://docs.fedoraproject.org/en-US/packaging-guidelines/),
[Qt for Python documentation](https://doc.qt.io/qtforpython-6/), and the
[llama.cpp server documentation](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)
for upstream details.

## License

LetraCode is available under the MIT License. See `LICENSE`.
