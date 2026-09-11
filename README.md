# LetraCode

LetraCode is the home of **Strand**, one persistent, general-purpose local
assistant for writing, learning, files and ongoing projects. It uses native Qt
on Fedora KDE and Windows. Conversations, notes, model inference and training
stay on your computer. There is no account, telemetry or cloud inference.

This checkout is **0.6.0**, the Strand experience redesign, based on the installed
0.5.0 Gemma work. It preserves existing data, guarded file editing, approvals,
training artifacts and model rollback. See the [user guide and verification
record](docs/STRAND-EXPERIENCE.md), [design](docs/superpowers/specs/2026-09-10-strand-experience.md)
and [development history](docs/CURRENT-STATE.md). Older public downloads do not
include these changes; build this checkout for the current application.

## Start with Strand

1. Open **Settings → Choose or change model**. Choose a local `llama-server`
   program and a GGUF chat model. Existing settings are retained. **Find local
   models** checks common folders; Browse can select a model anywhere.
2. Leave Advanced settings at their defaults if unsure. **Test local reply**
   checks loading and a real reply without changing saved chats. It does not
   certify answer quality or tool support.
3. Open **Chat**, type a question and press Ctrl+Enter. Enter makes a new line.
   Your draft saves automatically. Read other chats and prepare your next
   message while Strand works; one model job runs at a time.

**Workspaces** focus the same Strand on particular work. They are optional.
Shared notes remain available across workspaces; workspace notes and instructions
supply the current focus. Conversations and saved knowledge are not model training.

## Four places, four purposes

- **Chat:** everyday work, saved conversations, per-reply Copy/Create example,
  and **Used for this reply** receipts. Conversation options contain advanced
  tool compatibility and optional bounded two-model exchanges. **Ask again**
  repeats the question as a new turn and keeps the previous answer.
- **Knowledge:** see saved notes and useful source files. **Automatic** notes
  are included when local reading is enabled; **Available** files can be found
  and read when relevant. Originals stay in their current folders and open in
  Dolphin, Obsidian or their usual application. Note settings provide explicit
  saves, retained drafts, history and Undo.
- **Improve:** create and approve self-contained examples; check preparation;
  train a candidate; compare every held-out question in the actual Chat runtime;
  save your judgment; choose a version or return to the previous one.
- **Settings:** model readiness and testing, permission explanations,
  LetraCode source-checkout selection, data location, backup and diagnostics.

## Reading and action permissions

**Read local files** allows Strand to read supported ordinary files anywhere
your operating-system account can read, including hidden paths. Selected sources
prioritize relevance; they are not an access boundary. Turning this off excludes
automatic note/source text and file-reading tools. Saved conversation text and
workspace instructions remain available.

**Edits & commands** allows proposals for changes, with exact approval previews,
conflict checks and retained previous bytes. Commands require a separate
acknowledgment and run with your account, including network authority. A legacy
grant for automatic additions to one saved learning note remains explicit in
Note settings; it is saved information, not model training.

**Web research** asks before every outgoing query or URL. Turning it off disables
web tools; it does not sandbox separately approved shell commands. **Model tools**,
in Conversation options, is a compatibility setting for models without tool use.

The reply receipt shows recorded request contents, source excerpts, permissions
and limits. “Available,” “retrieved,” “included” and “understood” are different
things. Old replies without receipts remain readable and are labeled honestly.

## Improving Strand

Use **Create example** on a reply, or write one in Improve. Make the question
understandable on its own; correct the desired answer and remove irrelevant
background. Captured earlier dialogue is editable, and files/tool output are not
silently copied. Keep different questions for comparison. Only approved teaching
examples go into optimization; comparison examples are held out.

LetraCode checks preparation before unloading Chat, reuses saved local training
configuration, and can prepare the supported matching Gemma model. Numeric
settings remain available under preparation details. If conversion fails after
training, retry it from the retained adapter. **Compare with Strand** opens a
comparison window. Add fresh questions, run both versions, and read their full
answers side by side. Original held-out questions remain included. Progress,
partial answers, errors, previous attempts and your judgments are retained.
**Export comparison…** saves this evidence separately from the short optimizer
evaluation in the run folder. Comparison uses the Chat runtime and settings
and does not automatically adopt.
A lower training loss is not proof of useful improvement. Record regressions,
uncertain outcomes and your own judgment before choosing a version.

Supported optimization remains local LoRA/QLoRA for text-only Llama and the
supported Gemma 4 E2B/E4B path. Training requires original compatible weights and
local training/conversion dependencies; an arbitrary GGUF alone cannot be
trained. No model or package is downloaded automatically. Generic Llama pairing
has less provenance assurance than app-prepared Gemma pairing. See the
[training guide](docs/FINE-TUNING.md) and [Gemma record](docs/GEMMA4.md).

## Install or build

From this source checkout on Fedora KDE:

```bash
./install.sh
```

The installer explains its distribution dependencies and requests `sudo` for
those packages. It installs LetraCode for your user and keeps application files
separate from your data. A compatible local `llama-server` and model weights are
separate; use the instructions in Model setup and the engine/model publisher’s
requirements. GPU support depends on the engine build and driver.

Build source and Fedora self-extracting archives:

```bash
python3 packaging/build-release.py
```

Run the resulting `.run` from Konsole, or build native Windows artifacts using
the included workflow/build scripts. Windows packaging details are in the
[Windows release guide](docs/WINDOWS-RELEASE.md). Development systems with the
required packages already installed may use `./install.sh --no-deps`.

## Ownership and recovery

On Linux data lives in `${XDG_DATA_HOME:-~/.local/share}/letracode`; installed app
files live separately in `letracode-app`. On Windows data uses the account’s
LocalAppData LetraCode directory. **Settings → Back up LetraCode** creates a
restorable snapshot with a guide. Keep separate backups of linked originals and
large training/model artifacts. Export Evaluation makes a privacy-filtered
review bundle, not a restorable backup; inspect its conversation prose before
sharing. Automatic request receipts are private and excluded from that export.

Stopping keeps saved output. A paused task offers **Start a fresh task in this
chat**, retaining history and unknown-action records without replaying effects.
Long chats display recent messages with **Load earlier messages** and search;
model context remains bounded, with reduction reported in receipts.

Uninstalling retains the data folder. The [historical README](docs/README-0.5.0.md)
and dated verification records preserve earlier development findings.

## Development checks

```bash
QT_QPA_PLATFORM=offscreen python3 -m pytest -q
python3 -m compileall -q letracode
python3 packaging/build-release.py
```

License: [MIT](LICENSE).
