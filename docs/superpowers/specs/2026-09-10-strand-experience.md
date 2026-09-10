# Strand's home in LetraCode

The implementation starts from dcf8d56 (installed 0.5.0), not the stale 0.1.1 main checkout. The September 10 design assessment and CURRENT-STATE.md are the evidence baseline. This is a functional redesign with native Qt widgets, not a visual theme change.

## Product structure

Strand is one persistent local assistant. A workspace focuses the same assistant on relevant work; it does not create a separate personality. Existing global and workspace ordinary Memory files remain canonical. Existing chats, drafts, file revisions, receipts, archives and adapter versions remain intact.

Four destinations have distinct jobs: Chat is for working with Strand; Knowledge explains and manages saved information and useful ordinary files; Improve guides reviewed examples, readiness, training, comparison, use and rollback; Settings explains current model readiness, configuration, access and recovery. The primary daily view is Chat. Workspace management remains beside conversation navigation. File-management controls occupy Knowledge rather than permanently reducing the transcript. An optional chat context panel summarizes the current focus and permissions.

## Daily use and continuity

A compact composer grows with its text. Reading, searching, copying, visiting other chats and writing saved drafts stay available during inference/training. One model job still owns execution at a time. Model changes, sending another task and destructive operations remain unavailable while it owns the engine. Worker callbacks address their originating chat, never whichever chat happens to be selected. A persistent activity indicator returns to the running chat.

Normal old history is bounded reference context, not an unbounded mandatory active objective. Paused task intent and user steering remain authoritative. Ending a paused task retains messages and unknown-effect records, never certifies success and never repeats actions. Users can inspect results before deciding their next request. History rendering is paged; older messages remain searchable and loadable.

## Knowledge and authority

Selected source files/folders prioritize retrieval. Strand can explicitly read any supported ordinary file the OS account can read, including hidden paths. Read permission is independent from changing files, running commands and web requests. UI switches describe actual capability classes. Turning off file reading excludes automatic file text and file tools; saved conversation text is still visible as conversation. Approved arbitrary commands retain their existing account authority and can use the network; this is stated with the command permission.

Knowledge shows shared versus workspace scope and whether notes are automatically included or only available for retrieval. External files stay at their paths and open in their usual app. Sources may be missing, unreadable, partial or outside a bounded inventory; those limitations must be visible. Strand can consult actual installation metadata and a verified configured development checkout/history without inventing provenance.

Every new reply has an inspectable receipt for its constructed request: included instructions/notes, automatic source excerpts, capabilities, history reduction and saved tool results. Availability and retrieval do not prove understanding. No receipt is fabricated for old messages.

## Improve

Saving a training example changes neither saved knowledge nor model weights. Capture opens an editable draft with the request, desired answer and necessary preceding context, and explains excluded files/tools. Review requires a self-contained example. Held-out examples are for judging generalization and never optimization.

Readiness runs before releasing a chat model. Reuse verified saved configuration and known model pairing where possible; keep numeric settings in Advanced. Report the next missing prerequisite in ordinary language. Existing Llama/Gemma training safeguards remain. Training, conversion and validation are distinct stages; retry conversion from saved adapters without repeating optimization. Real comparison uses the eventual Chat runtime, complete requested evaluation answers, and persistent user judgments. A lower loss does not certify improvement. Adoption remains explicit, artifact-validated and reversible; regressions and unanswered comparisons remain visible.

## Validation

Regression tests cover switching/drafting while a worker belongs to another chat; account-wide reads with separate effect approvals; receipt provenance and context exclusion; long histories and end-task recovery; context-aware training capture; readiness, conversion recovery, actual-runtime comparison and rollback. Native offscreen renders at 850x570, 1000x700 and large text inspect reading room and accessible labels. Existing storage, source-write, migration, protocol, installer and training tests remain part of the full suite. Real inference/training quality claims require actual trials, distinct from scripted-peer contract tests.
