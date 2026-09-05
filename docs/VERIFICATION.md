# LetraCode 0.1.1 — verification and limits

Verified on September 5, 2026 in an Ubuntu 24.04 x86_64 environment, using Python 3.12 and Qt/PySide6 6.11.2. The installed Fedora application uses Fedora's system Qt, not the development Qt wheel.

## Fedora 44 installer correction

The 0.1.0 installer incorrectly required `python3-docx`, which the user's Fedora 44 repositories could not resolve. Version 0.1.1 removes that requirement from both the installer and RPM specification. DOCX body paragraphs and table text are now read with Python's standard library, with bounded ZIP/XML input, rejection of document type declarations, and no external resource loading. This does not require pip or a separate Python environment and leaves Fedora's native Qt integration intact.

The regression test reproduced the reported `No match for argument: python3-docx` error before the fix. It now runs both the source installer and the built `.run` normally against a simulated Fedora package manager that rejects unavailable packages, with `docx` imports explicitly disabled. The application launcher succeeds afterward. Install/update/uninstall tests also verify preservation of user data and handling of paths containing spaces.

Fedora's official [python3-docx listing](https://packages.fedoraproject.org/pkgs/python-docx/python3-docx/) has no Fedora 44 build. The remaining PDF dependency has an official [Fedora 44 python3-pypdf build](https://packages.fedoraproject.org/pkgs/python-pypdf/python3-pypdf/fedora-44.html) targeting Python 3.14. Package-index verification and a simulated package manager are not a live Fedora installation test.

A real DOCX produced with python-docx was read successfully in a separate Python process with site-packages disabled. The automated document tests cover paragraphs, tables, Unicode, hyperlinks, real tabs versus formatting tab stops, line breaks, strict/transitional namespaces, malformed ZIP data, expanded-size limits, text limits, and UTF-8/UTF-16 entity-declaration rejection. Headers, footers, embedded objects, and visual layout are not extracted.

## Implemented

- Native Qt desktop window, standard menus/dialogs/controls, theme icons, system palette and font inheritance. No application stylesheet or forced light/dark theme.
- Persistent global chats and projects with multiple chats, linked ordinary files/folders, and shared editable Memory, Current Context and Instructions. Context and unfinished prompts autosave.
- Managed local llama.cpp process, local GGUF selection, CPU/GPU-layer settings, Instant/Thinking template control, streamed Markdown replies, Stop, retry, search, rename/delete confirmation, copy and export.
- Bounded fresh retrieval from UTF-8/source files and optional PDF/DOCX extraction; visible editable context, source inventory and evidence sent only to the local model.
- Approval dialogs for every model-requested file write and command, sensitive/out-of-project reads, and every public web query/URL/redirect. Commands require an additional acknowledgement of their unsandboxed authority. Denial is the default.
- File-write diffs, old-byte backups, and changed-during-approval detection. Commands have timeouts, output limits and process-group cancellation. Public web retrieval checks/pins public DNS addresses and refuses private networks, credentials, nonstandard ports and unapproved redirects.
- Local SQLite persistence with private filesystem permissions, consistent ZIP backups including JSON and restoration instructions, recovery of interrupted messages and tool outcomes, and one active app per data folder.
- A per-user installer, KDE application-menu entry, scalable icon, update/uninstall scripts retaining user data, source archive, and RPM build inputs.

## Behavioral verification

Final automated result: **73 tests passed**. Python compilation, shell syntax and whitespace checks also passed.

The automated suite exercises real SQLite databases, temporary local files, harmless real subprocesses, real loopback HTTP peers, Qt widgets and threads, and the actual installer/uninstaller. It checks persistence across reopen, project isolation, backup recovery, denied-action side effects, source changes, sensitive symlink ancestry, file backup/diff behavior, command timeout, public/private DNS handling, redirect denial, SSE/tool-call parsing, incomplete tool-history recovery, model cancellation, wall-clock deadlines, Qt chat streaming, draft/context persistence, and install/update/uninstall under paths containing spaces.

During the original 0.1.0 build, the UI was rendered offscreen and visually inspected. A separate backend review found three issues (hidden symlink ancestry, incomplete action history, and slow-stream deadline overrun); each was reproduced in a failing regression test, fixed, and re-reviewed. No blockers remained in that scoped review.

The final whole-app review also caught welcome-screen draft persistence and cancellation from a modal approval. Both were fixed, including a delete-transition regression, and verified with seven Qt tests and a real worker/dialog cancellation check. The final review reported no remaining blockers.

Real inference smoke check: the official llama.cpp build `b10816`, commit `427291b5b`, loaded the tiny `ggml-org/models/tinyllamas/stories260K.gguf` on CPU. Its SHA-256 matched the publisher's `270cba1bd5109f42d03350f60406024560464db173c0e387d91f0426d3bd256d`. The engine streamed 584 characters, reported the requested 256-token output limit honestly, and stopped its child process. This is a process/inference check, not a usable-model recommendation or a test of answer quality. Full successful reply/tool sequences are tested with the scripted local HTTP peer.

## Not verified here

- Fedora package installation through dnf, KDE menu/theme integration in a live Plasma session, NVIDIA/CUDA/Vulkan offload, and this user's actual GGUF models and hardware.
- An RPM build: rpmbuild is unavailable here. The delivered `.run` installs the app; the source includes an RPM spec and Fedora build command.
- Live public-page/search retrieval from the app's direct network transport: this environment blocks its direct DNS lookup. The HTTP transport, parsers and approval boundaries pass loopback tests. Public search availability depends on DuckDuckGo's HTML service; websites may block automation, require JavaScript or require sign-in. No authenticated browser automation is implemented.
- Model reasoning quality, reliable autonomous tool selection, or model-specific Thinking behavior. These depend on the selected model and chat template. Turning off Actions enables plain chat with models that do not support tool calls.

## Practical limits

- No model weights are bundled. Select a local chat/instruction GGUF and compatible llama-server. Model downloads and GPU builds are separate setup steps.
- Reading is bounded: ordinary UTF-8 files up to 2 MiB, PDF/DOCX files up to 20 MiB, PDFs up to 500 pages, inventories up to 600 files, and limited retrieved excerpts. There is no OCR, image/audio/video understanding, ZIP ingestion, embeddings, or guaranteed exhaustive whole-project analysis. Use narrower links/questions when a project is large.
- Large contexts use more RAM/VRAM. Context packing uses a conservative character estimate; the model's actual tokenizer may require a larger setting or a shorter prompt. Older turns remain saved when excluded from model context.
- Commands run with your desktop account's permissions and may access the network. They are not a sandbox. Review the exact command before approving; there are no automatic privilege escalations.
- Web tools send only approved URLs/queries, but the destination sees your IP address. Check each request for private information. The app has no cloud inference endpoint or synchronization.
- Data is private by filesystem permissions, not encrypted independently of the disk. Backups of linked originals and model weights are your separate responsibility.

## Reproduce local checks

With Python 3.11+, PySide6, pytest and pypdf available:

```bash
QT_QPA_PLATFORM=offscreen python3 -m pytest -q
python3 -m compileall -q letracode
python3 packaging/build-release.py
```

On Fedora, prefer distribution packages for Qt. See README.md for installation, first chat, backup and removal.
