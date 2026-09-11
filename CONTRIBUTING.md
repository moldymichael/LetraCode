# Contributing to LetraCode

For installation without development tools, use the [Windows guide](docs/WINDOWS.md) or [Fedora installation](README.md#fedora-kde). This document is for running source, making changes and checking them.

A **checkout** is a local copy of the repository. A **branch** keeps a change separate until review. A **pull request (PR)** proposes merging that change. **CI** is the automated test/build workflow on GitHub; its results are evidence for an exact commit, not every copy of the application.

## Establish the baseline

The repository is `moldymichael/LetraCode`. Start ordinary contributions from `main`, unless a task explicitly names another branch. Read [README](README.md), [AGENTS.md](AGENTS.md) and the relevant section of the [documentation index](docs/README.md).

```text
git status --short
git branch --show-current
git rev-parse HEAD
```

Check these before editing an existing checkout. Do not reset, clean or discard uncommitted work. Source, installed application, model engine and user data are separate. A maintainer's dated installation record does not identify your local runtime.

As checked September 11, 2026, source is 0.6.0 while the latest published release is 0.3.0. [Issue #6](https://github.com/moldymichael/LetraCode/issues/6) and [PR #8](https://github.com/moldymichael/LetraCode/pull/8) track different unresolved work. Check their live state and coordinate before editing the same area. Do not use a `*-red*` diagnostic branch as your normal baseline.

## Windows development

Use native Windows x64 Python, not WSL, for Windows compatibility work. Python 3.13 x64 matches the packaging workflow. Source CI also covers 3.11 and 3.14; packaging and training dependencies may have narrower requirements than the application's `>=3.11` metadata.

Install [Git](https://git-scm.com/downloads) and [Python](https://www.python.org/downloads/windows/) first. In PowerShell, from a folder where you keep source repositories:

```powershell
git clone https://github.com/moldymichael/LetraCode.git
cd LetraCode
git switch -c fix/my-change
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e . pytest pytest-timeout pytest-instafail
.\.venv\Scripts\python.exe -m letracode --version
$testData = Join-Path $env:TEMP ("LetraCode-dev-" + [guid]::NewGuid().ToString())
.\.venv\Scripts\python.exe -m letracode --data-dir "$testData"
```

Run each command only after the previous one succeeds. `-e .` installs the local source in editable mode: source edits are used without rebuilding an installer. If `py -3.13` is unavailable, select an installed compatible x64 Python explicitly to create the environment. Do not copy a Linux virtual environment to Windows.

The commands call the environment's interpreter directly, so activation and execution-policy changes are not needed. See [Python's virtual-environment documentation](https://docs.python.org/3.13/library/venv.html). The application can open without a model; inference still needs a compatible Windows engine and GGUF. The [known #6 limitation](docs/WINDOWS.md#missing-engine-versus-issue-6) also applies to source until fixed.

### Windows tests

Close the development window. From the repository root:

```powershell
$env:QT_QPA_PLATFORM = 'offscreen'
$env:PYTHONUTF8 = '1'
.\.venv\Scripts\python.exe -m pytest -q --instafail --timeout=300 --timeout-method=thread
.\.venv\Scripts\python.exe -m compileall -q letracode packaging
```

`offscreen` lets Qt tests run without opening normal desktop windows. Remove it before manual UI testing in that session:

```powershell
Remove-Item Env:QT_QPA_PLATFORM -ErrorAction SilentlyContinue
.\.venv\Scripts\python.exe -m letracode --data-dir "$testData"
```

For a focused test, replace the pytest command's arguments with the relevant test path. The workflow uses an additional native filesystem/migration preflight; see the [exact CI commands](.github/workflows/windows-and-fedora.yml).

## Fedora development

The maintained Linux environment is Fedora KDE. Other Linux distributions are not covered by this workflow. Install the distribution's dependencies, then run source without replacing the installed app:

```bash
sudo dnf install git python3 python3-pyside6 python3-pypdf python3-pytest
git clone https://github.com/moldymichael/LetraCode.git
cd LetraCode
git switch -c fix/my-change
python3 -m letracode --version
test_data="$(mktemp -d -t letracode-dev-XXXXXXXX)"
python3 -m letracode --data-dir "$test_data"
```

These commands use Fedora's system Qt bindings; no pip install is needed to run from the repository root. The project's pip dependencies are conditional on Windows, so `pip install .` alone does not supply the Fedora dependencies.

Run tests as an ordinary user, not root:

```bash
QT_QPA_PLATFORM=offscreen python3 -m pytest -q
python3 -m compileall -q letracode packaging
bash -n install.sh uninstall.sh packaging/build-rpm.sh
```

## Source map

| Area | Start reading |
| --- | --- |
| Startup and data-directory selection | `letracode/app.py`, `letracode/store.py` |
| Main UI and model setup | `letracode/ui.py`, `letracode/dialogs.py`, `letracode/experience.py` |
| Engine arguments, local protocol and process lifecycle | `letracode/engine.py`, `letracode/processes.py`, `letracode/platform.py` |
| Conversation execution and continuation | `letracode/worker.py`, `letracode/continuation.py`, `letracode/pause_context.py` |
| Context, receipts and source reading | `letracode/context.py`, `letracode/evidence.py`, `letracode/source_files.py` |
| Guarded files, memory and approvals | `letracode/filesystem.py`, `letracode/_windows_filesystem.py`, `letracode/memory.py`, `letracode/tools.py` |
| Training and candidate comparison | `letracode/training*.py`, [training guide](docs/FINE-TUNING.md), [comparison record](docs/COMPARISON-WORKFLOW.md) |
| Distribution and validation | `packaging/`, `tests/`, `.github/workflows/windows-and-fedora.yml` |

## Changes and evidence

Keep a PR focused on one problem. For a bug fix, add or identify a regression test that fails for the relevant reason before the fix and passes afterward. Exercise the real production entry point. Do not remove a safety check or skip a platform merely to turn CI green. When mocking a boundary, say what the mock cannot establish.

Run the relevant tests first, then the full suite where feasible. Report exact commands, results, environment, commit and untested areas. A failing baseline should be identified, not silently attributed to your change or hidden. Local Linux tests do not prove Windows behavior. Scripted peers can test protocol handling without establishing compatibility with real llama.cpp binaries or models.

For runtime and packaging changes, distinguish these checks:

| Check | What it establishes |
| --- | --- |
| Unit/integration suite | The behavior actually exercised by its tests and fixtures |
| Package/lifecycle smoke test | The built application installs, opens and follows tested lifecycle paths |
| Native desktop/manual test | The observed behavior on the named Windows/Fedora machine |
| Real-model test | The named engine/model/settings can complete the tested operation |

Do not describe all four as passing unless each was checked. The existing workflow's Windows host is Windows Server 2022. Its separate packaging job can pass even when source test jobs fail. Use the [Windows smoke test](docs/WINDOWS.md#first-smoke-test) for a small manual starting point.

## Data and privacy rules

Use disposable application data with `--data-dir` and copied sample files. This isolates storage; it does not sandbox read permissions or approved shell commands. Never run destructive tests on a live notes vault, model directory or production app data.

Keep chats, drafts, approvals, recovery history and training artifacts intact. Do not commit local virtual environments, private logs, database/receipt exports, model weights or build outputs. Check `git status` and the staged diff before committing. Redact secrets and private prose from bug reports.

## Build distributables

Running source does not require building an installer. Build only when validating distribution or preparing a candidate. The scripts create files; they do not publish a GitHub release.

### Windows installer and portable ZIP

Use the Windows environment above. Install Inno Setup 6 using the compiler version/source and checksum in the [packaging workflow](.github/workflows/windows-and-fedora.yml), then run:

```powershell
.\.venv\Scripts\python.exe -m pip install -r packaging/windows-requirements.txt .
.\.venv\Scripts\python.exe packaging/build-windows.py
```

This requires native Windows x64 Python. If the compiler is in a custom location, `build-windows.py --iscc` accepts its path. The outputs go to `dist/`: a setup executable, portable ZIP and SHA-256 checksum file. Keep the entire portable folder together. Engine binaries and model weights are not bundled.

To exercise the generated package lifecycle with disposable data:

```powershell
$version = .\.venv\Scripts\python.exe -c "from letracode import __version__; print(__version__)"
$smokeDir = Join-Path $env:TEMP ("LetraCode-package-" + [guid]::NewGuid().ToString())
.\.venv\Scripts\python.exe packaging/smoke-windows.py --installer "dist/LetraCode-$version-windows-x64-setup.exe" --portable "dist/LetraCode-$version-windows-x64-portable.zip" --work-dir "$smokeDir"
```

The smoke script performs install/update/uninstall operations. Use a disposable Windows account or VM for package-lifecycle work, not an account whose live installation you need. Preserve its logs/screenshots as evidence, and perform a separate real-model test.

### Fedora/source artifacts

```bash
python3 packaging/build-release.py
```

For native RPMs, install the additional RPM build dependencies listed in the workflow and run `packaging/build-rpm.sh`. Outputs are in `dist/`. These commands do not certify a release.

## Before a release

Keep the commit, version metadata, build outputs, checksums and release notes aligned. Separate a test candidate from a published release. Do not call 0.6.0 release-ready while its required CI and launch-compatibility work remain unresolved. Verify real Windows inference, update/data retention and the Linux regression baseline; state remaining limitations explicitly.

For an issue report, a reproducible failure is a completed contribution. Include the environment, steps, expected/actual behavior and relevant sanitized logs. A fix is not required. For a PR, include scope, tests, limitations and data/recovery implications. The repository templates provide a short starting point, not a requirement to audit unrelated code.
