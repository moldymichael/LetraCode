# LetraCode

LetraCode is a local desktop AI application for writing, learning, files and ongoing projects. It uses a model on your computer rather than a hosted chat service. The current interface calls the assistant **Strand**.

The application uses Python and native Qt widgets on Fedora KDE and Windows. Chat history and saved notes stay in your local data folder. No LetraCode account or cloud inference service is required. Optional web research and approved commands can access the network; see [permissions and privacy](#permissions-and-privacy).

## Choose where to start

| Your goal | Start here |
| --- | --- |
| Install on Windows | [Windows installation and first reply](docs/WINDOWS.md) |
| Use the current interface | [User guide](docs/STRAND-EXPERIENCE.md) |
| Run or modify the source | [Contributor setup: Windows and Fedora](https://github.com/moldymichael/LetraCode/blob/main/CONTRIBUTING.md) |
| Find technical records or older documentation | [Documentation index](docs/README.md) |

## Development version versus published download

**Status checked September 11, 2026:** this source tree identifies as **0.6.0**. The latest published [GitHub release](https://github.com/moldymichael/LetraCode/releases) is **0.3.0**. They are not the same build. Cloning the repository does not update an installed copy, and a newer version number in source does not mean a newer release has been published.

The 0.6.0 development line contains Chat, Knowledge, Improve and Settings, plus the comparison and source-reading updates. A Windows **test build** is available from a specific Actions run; the [Windows guide](docs/WINDOWS.md#development-test-build) identifies its commit and limitations. Its packaging job passed, but the full run did not. It is not a validated 0.6.0 release.

Two separate items remain under investigation:

- [Issue #6](https://github.com/moldymichael/LetraCode/issues/6): launching installations that use `llama.exe serve` instead of a dedicated `llama-server.exe`.
- [PR #8](https://github.com/moldymichael/LetraCode/pull/8): Windows/Fedora test portability and quantizer selection. It does **not** fix issue #6.

Use those threads for live status. Earlier test counts and installation records are evidence for the builds they name, not guarantees about every later checkout.

## What you need

There are three separate pieces:

1. **LetraCode:** the desktop application. The Windows installer includes Python, Qt/PySide6 and PDF support.
2. **llama.cpp:** the engine program that loads the model. This checkout expects a dedicated `llama-server` executable, or `llama-server.exe` on Windows. Unified-binary support is tracked in #6.
3. **A compatible GGUF model:** the model weights. Existing compatible `.gguf` files can be reused; the engine executable must match the operating system. A Linux `llama-server` is not a Windows executable.

Neither llama.cpp nor model weights are bundled with LetraCode. Model size, context length, engine build and available RAM/VRAM determine what your machine can run. Finding a model file does not establish compatibility.

<a id="install-or-build"></a>

## Install and start

### Windows

Follow the [Windows guide](docs/WINDOWS.md) to choose a published download or the explicitly labeled development test build. Ordinary installation does not require Git, Python, a compiler or a source checkout. Contributors who need editable source should use [CONTRIBUTING.md](https://github.com/moldymichael/LetraCode/blob/main/CONTRIBUTING.md#windows-development).

### Fedora KDE

From the root of a source checkout, run in Konsole:

```bash
./install.sh
```

The script explains its distribution dependencies and requests `sudo` for those packages. Application files are installed for your user, separately from data. `./install.sh --no-deps` is intended for systems whose required dependencies have already been checked. To run source without changing the installed application, use the [Fedora development instructions](https://github.com/moldymichael/LetraCode/blob/main/CONTRIBUTING.md#fedora-development).

<a id="start-with-strand"></a>

### First reply in 0.6.0

Open **Settings → Choose or change model** and select the engine and GGUF. Start with the default settings; CPU mode avoids GPU setup but still requires enough RAM for the model. **Test local reply** checks that the selected engine can load the model and answer. It does not certify answer quality or tool support.

Open **Chat**, enter a question and press **Ctrl+Enter**. Enter adds a line. Drafts save automatically. You can read other chats and prepare drafts while a job runs; only one model job runs at a time.

<a id="four-places-four-purposes"></a>

## The four areas

| Area | Purpose |
| --- | --- |
| **Chat** | Saved conversations, optional bounded two-model exchanges, Copy/Create example, and **Used for this reply** receipts. |
| **Knowledge** | Shared and workspace notes, useful source files, explicit saves, retained drafts, history and Undo. Originals stay where they are. |
| **Improve** | Review examples, prepare training, compare candidate answers and explicitly adopt or roll back a model version. |
| **Settings** | Choose/test the model, control permissions, locate data, create backups and inspect diagnostics. |

Workspaces are optional ways to focus ongoing work. Shared notes can follow you across workspaces. Saved notes are not model training. The [user guide](docs/STRAND-EXPERIENCE.md) explains everyday use; [comparison workflow](docs/COMPARISON-WORKFLOW.md) describes the current candidate-review interface.

<a id="reading-and-action-permissions"></a>

## Permissions and privacy

**Read local files** permits supported ordinary file reads anywhere your operating-system account can read, including hidden paths. Selected sources prioritize useful material; they are **not a sandbox**. Turning this permission off removes automatic file text and file-reading tools, but saved conversation/result text and workspace instructions remain available.

**Edits & commands** permits proposals with approval previews and conflict checks. Commands require a separate acknowledgment and run with your account's authority, including network access. A legacy grant for automatic additions to one saved note is visible in Note settings; it is not a general editing grant.

**Web research** asks for approval before each outgoing query or URL. Turning it off disables web tools, not the network access of separately approved commands. **Model tools** is a compatibility option for models without tool use, not a security boundary.

Receipts record what was supplied to a request, including private excerpts. They cannot prove comprehension. Review logs, screenshots and exports before sharing them. See the [permission details](docs/STRAND-EXPERIENCE.md#permission-controls).

<a id="improving-strand"></a>

## Training is optional

Normal chat does not require a training environment. Improve supports local LoRA/QLoRA for compatible text-only Llama models and the supported Gemma 4 E2B/E4B path. It requires compatible original weights and separate training/conversion dependencies; an arbitrary GGUF alone cannot be trained.

Only approved teaching examples are optimized on. Comparison examples are held out, and adopting a candidate is explicit. Lower training loss is not proof of better answers. See the [training guide](docs/FINE-TUNING.md) and [Gemma support record](docs/GEMMA4.md); do not infer native Windows training validation from an installer smoke test.

<a id="ownership-and-recovery"></a>

## Data, backups and recovery

| Platform | Default user data |
| --- | --- |
| Windows | `%LOCALAPPDATA%\letracode` |
| Linux | `${XDG_DATA_HOME:-~/.local/share}/letracode` |

Application files are separate. **Settings → Back up LetraCode** creates a restorable snapshot with a guide. Back up linked originals and large model/training artifacts separately. Evaluation export is a review bundle, not a restorable backup.

Uninstalling retains the data folder. On Windows, the portable app uses that same folder by default; portable does not mean isolated. Use an explicit `--data-dir` for tests. See [Windows data isolation](docs/WINDOWS.md#test-without-touching-existing-data).

Stopping retains saved output. A paused chat offers **Start a fresh task in this chat**, preserving history and unknown-action records without replaying their effects. Long conversations remain searchable even though the model's context is bounded.

<a id="development-checks"></a>

## For developers

[CONTRIBUTING.md](https://github.com/moldymichael/LetraCode/blob/main/CONTRIBUTING.md) contains runnable setup and test commands, a source map, packaging instructions and expected verification evidence. [AGENTS.md](https://github.com/moldymichael/LetraCode/blob/main/AGENTS.md) records source/data handling rules. The project is licensed under [MIT](LICENSE); bundled dependencies and model weights have their own licenses.
