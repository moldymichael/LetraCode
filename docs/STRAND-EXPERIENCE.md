# Getting started with Strand — LetraCode 0.6.0

Strand is your one local assistant. Chat history records what happened; saved
notes supply reusable information; training creates a different model version.
A workspace focuses Strand on something you return to, such as a novel, course
or LetraCode development. You can start in Everyday without creating one.

## Your first conversation

Open Settings and choose the local engine and model. A GGUF is a model file;
llama-server is the program that runs it. Existing settings are preserved.
Find local models checks the selected model folder, Models, models and Downloads
at bounded depth. Browse can choose other locations. Advanced settings remain
collapsed; CPU defaults are a conservative starting point, not a memory-fit
promise for every model.

Test local reply checks a real response without adding a chat. A successful check
does not certify factual accuracy, reasoning or tool selection. If it fails,
your choices are kept and the error is visible beside the next action. The
engine log remains available for detailed diagnosis.

In Chat, type normally. Ctrl+Enter sends; Enter adds a line. Drafts save
automatically. While a job runs, you can switch chats, search, copy, browse files
and prepare drafts. The activity indicator returns to the running chat. Sending
another request or changing the engine waits until the current job ends.

## Messenger appearance and scrolling

The messenger keeps the window, buttons, sidebar and chat canvas neutral. Only
message bubbles and their color-picker previews use your chosen colors. You is
on the right; Strand (or a named model in a two-model conversation) is on the
left. Sender names and saved-message times stay outside the bubbles.

Use **Bubble colors** in the Chat sidebar or Settings. Click a bubble to open
the native color picker, or edit its six-digit hex value. **Swap** exchanges the
two colors; **Reset** restores the default blue and pink without changing the
background mode. **Paper** is light neutral and **Graphite** is dark neutral.
Changes apply immediately and persist across restarts. Text and links use black
or white to stay readable on the selected bubble color. Theme preferences do
not change saved messages, exports, model settings or permissions.

While a reply is generating, scroll normally or drag the scrollbar to reach
the actual bottom. Once there, new output follows. Scrolling up stops following;
it does not stop the model. Dragging the scrollbar or selecting text temporarily
defers visible replacements so they do not move the drag target or destroy the
selection. Output continues to be saved. Releasing the drag or clearing the
selection displays pending output. **Latest message**, or **View → Jump to
latest message** (Ctrl+End), clears the reading selection and returns to the
latest saved text. The contact header reports local model state, not internet
presence.

## Files and saved information

Knowledge shows shared notes, workspace notes and added sources. Shared notes
follow Strand across workspaces. Workspace notes and instructions focus the
current conversation. “Automatic” means the note is included when Read local
files is enabled. “Available” means Strand can retrieve it; it does not mean the
file was read. The old learning folder contains saved notes, not trained weights.

Add ordinary files or folders where they already live. Work on originals in
Dolphin and Obsidian as usual. Removing a source link never deletes its original.
The current focus determines where a new source is listed. In Everyday, it
becomes shared; in a workspace, it focuses that workspace. Shared sources remain
visible in every workspace. The file path is visible when selected.

Editing a note uses Save. Unfinished note edits remain separate drafts, including
when another application changes the file. A conflicting save does not overwrite
the external change. Note settings, drafts and history provides Reload and Undo.
Use automatically included notes sparingly: short preferences, facts and decisions
are more useful than making a large reference library mandatory for every reply.

## What Strand actually used

Each new assistant reply provides Used for this reply. The summary lists included
notes, source excerpts, capabilities and reductions. Technical details show the
saved context receipt: system text, note/source snapshots, tool-result text and
conversation identifiers. It is not a complete serialized inference request.
Snapshots preserve the supplied contents at that time, not whatever a file
contains today. They cannot prove comprehension. Old messages without receipts
do not gain invented provenance.

Source lookup has limits. Some file formats cannot be fully extracted; very large
folders and documents are bounded. Failed extraction and inventory limits are
reported. Images, scanned-document OCR, audio and video remain unsupported.
Strand should state gaps and inspect more evidence where possible.

## Permission controls

Read local files permits supported ordinary file reads anywhere your OS account
can read, including hidden paths. Source selections identify useful material,
not access limits. Turning it off removes automatic file text and file-reading
tools; saved conversation/result text and workspace instructions remain available.

Edits & commands permits proposals, not automatic execution. Review the exact
change or command and choose Approve once when appropriate. Commands run with your
account and can access the network. Web research has a separate per-request
approval. Disabling it does not sandbox separately approved commands. A legacy
automatic-additions grant for one saved note is explicit in Note settings and
can be turned off there. Model tools is an advanced compatibility switch.

## Teaching with examples

An example should contain enough context to understand the question and the
answer you want. For instance:

Question: Explain what a metaphor is to someone new to poetry. Use one everyday
example and explain what the comparison suggests.

Desired answer: A metaphor describes one thing as another to help you imagine
it. “My desk is a jungle” means the desk is crowded and hard to navigate; it does
not mean plants are growing there.

“Make that clearer” by itself is not a useful teaching question. Include the
passage and explain what clearer means. Prefer correct, representative answers
over many near-duplicates. Correct mistakes before approval. Do not include
private facts just to teach a response style; use saved notes for those facts.

Create example captures the chosen reply and request into a draft, with bounded
editable preceding dialogue and workspace instructions. Review what was captured;
source files and tool output are not silently imported, and longer history may be
omitted. Approval means you chose this example for its selected purpose.

Keep different questions for comparison. Those examples are not trained on. They
help you notice whether the candidate applies the desired behavior to new work.

## Preparation, training and choosing

Improve separates Examples, Prepare and train, and Compare and choose. Check
preparation before training. The check verifies local prerequisites, examples,
token lengths, compatibility information and conversion support before unloading
Strand. Existing settings and known local pairing information are reused. Show
preparation details to resolve a missing path or adjust advanced defaults.

The supported Gemma path can prepare a matching local model. This requires local
original weights, training dependencies and conversion tools; there is no hidden
download. Generic Llama pairing reports its lower provenance assurance honestly.
Unsupported models remain usable for Chat but cannot use this trainer.

Training produces a candidate; it never silently replaces Strand. Saved examples,
completed optimization, successful conversion, and useful improvement are separate
states. Retry conversion can use the retained adapter without repeating training.
**Compare with Strand** opens a comparison window immediately. Add fresh questions
with an optional reference answer, then choose **Run comparison**. These questions
are saved for this candidate separately from teaching examples. Questions matching
the run's frozen teaching data are rejected; the original held-out questions always
remain included. Changing the question set requires another comparison for adoption.

The window shows verification/loading progress and independently scrollable full
answers for Strand now and the trained candidate. Select each question to inspect
both responses; Thinking text, when recorded, has its own tab. Each request uses
the configured Chat answer budget. Standalone comparison excludes shared notes,
project retrieval and action tools. Cut-off, stopped, empty or failed answers remain
incomplete. Stop retains partial output; closing the window keeps the job running
with Stop still available in Improve. A base-model change is disclosed. Earlier
attempts remain selectable, including their saved judgments and errors.

Read both answers. Check accuracy, completeness, clarity, useful style and whether
anything got worse. Lower loss alone cannot answer these questions. Save a name,
your judgment (including same, worse or mixed), and notes. The comparison window
also records a judgment for each question. A changed configuration
or model artifact invalidates the comparison used for adoption. Use this version
is explicit and guarded. Return to previous version restores the saved prior
configuration; model artifacts are retained.

**Export comparison…** creates a new JSON file containing the selected training
run, full saved comparisons and history, prompt drafts, judgments and conversion
receipts. The optimizer's `report.json` stays immutable and only contains its
limited short built-in evaluation. This explicit comparison export includes local
paths and full prompts, answers and notes; inspect it before sharing. It is separate
from Chat's privacy-filtered Export Evaluation and from a restorable backup.

## Continuing and recovering work

Long chats keep full saved history but initially display a recent page. Load
earlier messages or Find in conversation to read older work. Model context is
bounded independently from saved history. Its receipt says when earlier material
was reduced; paused task intent and steering remain protected.

A stopped/paused task never restarts itself. Inspect saved actions when an outcome
is unknown. Start a fresh task in this chat preserves prior messages, sources and
unknown outcomes, without rerunning actions or marking them successful. It gives
an unrelated request a clear new starting point. Back up before repairing damaged
saved context; reading the conversation remains available.

Settings shows the actual installation and data folder. Select the LetraCode
checkout there so Strand can inspect actual source, documentation and bounded Git
history. A configured checkout is not automatically asserted to match the running
build. Reading source does not grant permission to change it or run commands.

Use Back up LetraCode for restorable data and retain separate copies of linked
originals and large model/training artifacts. Export Evaluation intentionally
omits private request receipts and structured source bodies; inspect remaining
conversation prose before sharing.

## Implementation evidence

The redesign starts from dcf8d56, the source matching the assessed installed
0.5.0 build. It retains SQLite schema 3 and the existing memory/file recovery
format. Changes are implemented on codex/strand-experience. The baseline suite
reproduced 1,015 passes, 22 skips and the assessment’s missing packaged JSON
failure. The release builder now includes referenced JSON verification records;
the Fedora installer also retains documentation for installed help/self-reading.

Verification on September 10, 2026 (Linux, Python 3.14.7, PySide6 6.11.2):

- Full regression suite: **1,076 passed, 22 skipped** in 68.86 seconds. Skips
  cover platform-specific and opt-in model-runtime checks. Six existing Python
  fork deprecation warnings remain in deliberate process-crash tests.
- Python compilation, installer shell syntax and Git whitespace checks passed.
- Native Qt renders were inspected at 850×570 and 1000×700, plus 15-point text
  at 1000×760. The compact Chat transcript has 221 pixels of height beside a
  52-pixel composer; the large-text view retains a 351-pixel transcript and a
  72-pixel composer. Knowledge, setup and all Improve stages were inspected.
- Independent reviews covered context/continuity, busy navigation, source scope,
  training conversion recovery and adoption. Regression tests cover selection
  refresh, older-message search, draft preservation and hidden adapter changes.

The release builder produces 0.6.0 source archives and a Fedora per-user
installer. Native Windows execution and a fresh real-model training/quality
trial were not run for this redesign. Scripted local inference peers exercise
the real loading/comparison protocol; they do not establish model quality,
GPU capacity or that a particular user's examples improve Strand. Earlier
hardware evidence stays in the dated training verification documents.

No live user chats, documents, models or installation were modified during tests.
The subsequent user-requested deployment is recorded separately in
[Installed 0.6.0 update](INSTALLED-0.6.0-UPDATE.md).
