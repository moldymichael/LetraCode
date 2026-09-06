# LetraCode

LetraCode is a private desktop chat app for a local AI model. It uses Fedora's
native Qt/PySide6 packages, follows your KDE style, and keeps conversations on
your computer. There is no cloud inference, account, or telemetry.

For the development baseline, current capabilities and verification boundaries,
see [Current implementation state](docs/CURRENT-STATE.md).

## Install on Fedora KDE

1. Download `LetraCode-0.1.1.run`.
2. Open Dolphin, go to Downloads, right-click an empty area, and choose
   **Open Terminal Here** (Konsole).
3. Run:

   ```bash
   chmod +x LetraCode-0.1.1.run
   ./LetraCode-0.1.1.run
   ```

The installer shows the Fedora packages it needs, then uses `sudo dnf install`.
It installs only for your user. Start **LetraCode** from KDE's application
launcher, or run `~/.local/bin/letracode` in Konsole.

If you downloaded the source archive instead, extract it, open Konsole in the
extracted `LetraCode-0.1.1` folder, and run `./install.sh`.

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

Install a Fedora-provided engine with `sudo dnf install llama-cpp` when that
package is available for your Fedora release, or choose a compatible local
llama.cpp build. Open LetraCode, choose **Model setup**, select the
`llama-server` executable and your `.gguf` file, and save. Model weights can be
several gigabytes and are not included. Pick a model whose publisher documents
the RAM, license, and llama.cpp compatibility you need.

Create a project, link only the files or folders you want the conversation to
use, and start chatting. LetraCode previews commands and sensitive file or web
access. Read each request and choose **Approve once** only when the proposed
action is correct.

The system `llama-cpp` package can lag models that use a new architecture or
chat template. GPU offload also requires a llama.cpp build compatible with your
GPU and driver; begin with **GPU layers** set to 0 if unsure.

## Data, backup, and removal

Chats and settings live in `${XDG_DATA_HOME:-~/.local/share}/letracode`.
Installed application files live separately in
`${XDG_DATA_HOME:-~/.local/share}/letracode-app`, so updates and uninstall do not
delete your conversations.

Choose **File → Back up chats, Strand and source backups** while the app is open. Save the
snapshot ZIP to your backup drive. It contains a SQLite snapshot, human-readable JSON, Strand notes and retained
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
`LETRACODE_PYTHON` explicitly to test with a chosen interpreter. This project
does not ask pip to install a bundled Qt.

Build the `.run` and source archive with:

```bash
python3 packaging/build-release.py
```

On Fedora, install `rpm-build` and run `packaging/build-rpm.sh` to build the
included spec. See Fedora's [RPM packaging
guidelines](https://docs.fedoraproject.org/en-US/packaging-guidelines/),
[Qt for Python documentation](https://doc.qt.io/qtforpython-6/), and the
[llama.cpp server documentation](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)
for upstream details.

## License

LetraCode is available under the MIT License. See `LICENSE`.
