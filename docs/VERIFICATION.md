# LetraCode — verification and limits

## Strand M1a stabilization — September 5, 2026 (current)

Started from a clean `codex/strand-m1a` checkout at `886ff6632969ba141cfe867faa5039df15cb747e`. The original context-overflow fix remains present. The baseline suite passed again: **132 passed in 7.73 s**. A newer independent review nevertheless reproduced five P2 bugs; the earlier 132-pass results below are historical evidence, not proof that those bugs were absent.

| Finding | Reproduced failure and regression coverage | Stabilized behavior |
|---|---|---|
| Commit-time external edit loss | Six initial regressions failed: in-place/atomic editor saves at the final boundary for Save/Undo, create race, and late writes through an open descriptor. | Capture and retain the actual original file, check its bytes, then publish using Linux `RENAME_NOREPLACE`. Never replace a competing name, even during rollback. Later edits to a retained original surface a conflict on read/reopen. |
| Unsent first message lost | Global/project first sends emptied the composer on conflict. Also tested memory disappearing after chat creation. | Save checks precede chat creation; transfer the draft durably before selecting the new chat. Failed Save/Reload preserves drafts through reopening. |
| One unavailable project blocks startup | Missing, unreadable, malformed and oversized memory failed project access; Qt startup/reopen cases reproduced failures. | Project lists use metadata only. Affected memory has an explicit unavailable state, path and Reload control; unrelated projects/chats remain usable. Bounded context marks unavailable project memory explicitly. |
| Deleted project leaves memory | Existing deletion left the active memory path behind. | Archive the original inode and recovery record before deleting database ownership. Test raw malformed/oversized bytes, missing/unsafe paths, late descriptor writes, DB rollback, concurrent recreation, crash recovery and backup inclusion. Resolve pending saves first so receipt inspection cannot resurrect deleted memory. |
| Earlier evidence references disappear | Three-cycle pause regression lost the first ID on cycle two; oversized legacy checkpoint overflowed a short continuation. Catalog pagination initially chased its own saved output. | Keep 20 recent chat-wide IDs and a count. `list_tool_results` discovers older results with bounded, chat-scoped pages and a stable upper cursor. `read_tool_result` retrieves their original bounded bodies. Test 24 pauses/reopen/compaction, 500 saved legacy results, scope/bounds and pagination completion. |

Additional save regressions kill a disposable child process immediately after capture/publication and halfway through journal/completion-record writes. Recovery control records are published atomically; newly created directories are synced before capture. Backup/restore retains journals and old inodes and still supports Undo. All five reproductions and the integration regressions now pass.

Fresh commands, run from `/home/miceoil/Projects/LetraCode-strand-m1a`:

```bash
QT_QPA_PLATFORM=offscreen PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -p no:cacheprovider tests/test_strand.py tests/test_store.py tests/test_strand_ui.py tests/test_ui.py tests/test_worker.py tests/test_tools.py --basetemp=stabilization-test-data/targeted
# 122 passed in 36.12 s

QT_QPA_PLATFORM=offscreen PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -p no:cacheprovider --basetemp=stabilization-test-data/full
# 183 passed in 42.29 s; zero failures/skips

python3 - <<'PY'
from pathlib import Path
paths = sorted(Path('letracode').rglob('*.py')) + sorted(Path('tests').rglob('*.py')) + sorted(Path('packaging').glob('*.py'))
for path in paths:
    compile(path.read_bytes(), str(path), 'exec')
print(f'Compiled {len(paths)} Python files without writing bytecode')
PY
# Compiled 27 Python files without writing bytecode
bash -n install.sh uninstall.sh packaging/build-rpm.sh
git diff --check
# Both exit 0
```

The temporary-data parent `stabilization-test-data/` and evidence directory `.stabilization/` are ignored and inside the worktree. Create the parent with `mkdir -p stabilization-test-data` before reproducing in a fresh checkout. An initial full run used hidden `.stabilization/pytest-full`: two source-reading tests correctly hit sensitive-path restrictions, and a worker waited for approval; that run was interrupted after 159 passes/two failures. Moving **test data** to a visible directory fixed the setup. No application permission rule was weakened. Its log is retained as `.stabilization/full-tests.log`; the successful logs are `full-tests-final.log` and `targeted-tests-final.log`.

**Fresh real-Qwen check:** one actual Worker continuation after 25 **seeded** pause cycles, reopening and compaction. The oldest result ID was absent from the 20 recent checkpoint references, its marker absent from the initial prompt, and older turns omitted. With Computer/Web disabled, Qwen called `list_tool_results`, then `read_tool_result`, and correctly reported `ZETA-73` and saved exit code `7`. The original synthetic command was never executed or rerun; its saved row remained unchanged. Actual request totals were 4692, 4912 and 5268 tokens out of 8192, including 3072 reply and 128 safety tokens. Load 3.41 s; continuation 41.50 s. The owned engine stopped and no `llama-server` process remained.

```bash
QT_QPA_PLATFORM=offscreen PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python3 .stabilization/qwen-saved-recovery/probe.py > .stabilization/qwen-saved-recovery/probe.log 2>&1
# exit 0; results.json: passed=true, engine_stopped=true
```

This uses the same existing Qwen3.6 GGUF/llama.cpp paths documented below, with an isolated 8192 context, 12 GPU layers, eight threads and temperature 0.7. The probe script, `results.json`, log and engine log remain in `.stabilization/qwen-saved-recovery/`. Subsequent fixes touched file recovery only; the verified worker/tool continuation code did not change. This is a small real-model recovery check, not evidence of general model reliability or 25 autonomous model runs.

**Limits and manual checks:** offscreen Qt tests cover the changed draft, startup, Reload and deletion flows. Interactive KDE keyboard/dialog behavior and reconciliation with the user's preferred external editor still need manual review. Crash tests interrupt processes; no physical power-loss, filesystem corruption or disk-full experiment was performed. Linux/filesystem `renameat2(RENAME_NOREPLACE)` support is required; unsupported operations fail without an overwrite fallback. A save briefly removes the active pathname while retaining its original inode and journal. An editor holding that inode can write later: the data is preserved and the conflict is detected at the next read/reopen, requiring manual reconciliation. Recovery history is retained and grows with saves; no automatic pruning was added. An archived external file beyond the existing 2 MiB limit remains intact, but backup explicitly refuses that unsupported file rather than truncating or omitting it.

Both documented M1a departures were retained: `<data-dir>/strand` keeps isolation/restore straightforward; default review for model memory saves, except the explicit learning-file grant, keeps scope/authorization unambiguous. No evidence linked either choice to these defects. No installation, live-data migration, source-note edit, model replacement, training, push, merge or M1b work occurred. A fresh synthetic manual-review fixture and launch instructions are in [STRAND-M1A-REVIEW.md](STRAND-M1A-REVIEW.md).

## Historical Strand M1a verification on Fedora — September 5, 2026

Historical results from `/home/miceoil/Projects/LetraCode-strand-m1a`, branch `codex/strand-m1a`, based on `45092f1`. At that stage, code was verified at `9496958`; changes through `886ff66` only removed trailing whitespace and documented those results. The five defects described above were discovered afterward. The original checkout, installed app and live data were not updated.

Environment: Fedora 44 KDE Plasma, `/usr/bin/python3` 3.14.7, system PySide6 6.11.2, pytest 8.4.2, SQLite 3.51.2; FTS5 available. No new dependencies or system changes.

- Baseline: **78 passed in 5.80 s**.
- Final: **132 passed in 7.40 s**, zero failures/skips, using the command below.
- All 15 application Python files compiled successfully using Python's `compile()` with no bytecode writes. Shell syntax and `git diff --check` passed.
- Independent review findings were corrected and regression-tested: protected native Undo controls, stable editor cursor/drafts, Retry save checks, bounded/no-follow memory reads, concurrent writes/migration, and token-authoritative core budgeting with explicit omitted-context coverage. Scoped final review reported no remaining findings.

```bash
QT_QPA_PLATFORM=offscreen PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -p no:cacheprovider
```

The tests use temporary files/databases and scripted HTTP/model peers. Installer checks redirect all install/update/uninstall destinations into temporary homes; they do not install into the user's account. These tests establish deterministic behavior, not model intelligence.

**Real local inference:** existing GGUF metadata reports Qwen3.6-35B-A3B / qwen35moe / MOSTLY_Q4_K_M; installed llama.cpp reports `0.4.0-dev`, commit `4d91760`. Tested with the configured context 32768, 12 GPU layers, 8 threads, reply reserve 3072 and temperature 0.7, plus an isolated 8192 profile. Actual model checks passed global identity, scoped remember, external correction, conflicting projects, context-heavy continuations at both sizes, and saved-result tool retrieval with external tools disabled. Prompt counts matched the independent input-token endpoint. Actual cancellation stopped the owned engine in 2.40 s. The final core-budget regression also passed on the real model: all 7000 instruction characters were preserved, with 1794 prompt + 3072 reply + 128 safety tokens at context 8192; answer “Strand”, 10.42 s. Every test-owned engine was stopped. No training was attempted.

The context-heavy fixtures contain synthetic file reads; the archived command-output fixture is synthetic and its command was never executed. Small real-model checks do not prove whole-book comprehension, teaching quality or reliable tool use in general.

**Fedora interface:** offscreen Qt tests and inspected renders passed. The actual development entry point opened on Wayland with isolated `ui-demo` data and closed cleanly after capturing its own widget. The captured live widget inherited the desktop dark style and was inspected. Full interactive KDE/dialog/keyboard behavior still requires user review.

Detailed configuration, observations, limitations, safe launch command and manual checklist: [Strand M1a review guide](STRAND-M1A-REVIEW.md). Logs and isolated fixtures are retained at `/home/miceoil/Projects/strand-m1a-review-PGYug9/`, including `automated-tests.log`, `real-model-results.json`, `final-model-results.json`, `core-budget-model-results.json`, `live-launch.log` and engine logs. M1b and M2 were not implemented.

---

## Historical LetraCode 0.1.1 verification (retained attribution)

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
