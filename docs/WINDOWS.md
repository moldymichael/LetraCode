# Windows installation and first reply

This guide separates installing LetraCode from setting up its model engine. For editable source and packaging, see [CONTRIBUTING.md](https://github.com/moldymichael/LetraCode/blob/main/CONTRIBUTING.md). For the old release's technical findings, see the [0.3.0 verification record](WINDOWS-RELEASE.md).

## Choose a build

**Status checked September 11, 2026.** The published release is 0.3.0; source development identifies as 0.6.0. Check the [release page](https://github.com/moldymichael/LetraCode/releases) for subsequent changes. Version-specific instructions and test results apply only to their named build.

### Published download

Open the release page and download the Windows x64 `setup.exe`. The published 0.3.0 release also provides a portable ZIP. Its older interface does not contain all the 0.6.0 features described in the current user guide.

### Development test build

This is a **test candidate, not an official 0.6.0 release**:

| Item | Value |
| --- | --- |
| Source branch | `fix/windows-06-ci` |
| Exact commit | `4d901b7cafa0139a5a05ae47d3c2d7459a272454` |
| Actions run | [34562117561](https://github.com/moldymichael/LetraCode/actions/runs/34562117561) |
| Download | [LetraCode-windows-x64 artifact](https://github.com/moldymichael/LetraCode/actions/runs/34562117561/artifacts/10184843440) |
| Package/lifecycle job | Passed on the hosted Windows runner |
| Full workflow | Failed: Windows source suites and Fedora tests were not green |
| Known engine limitation | [Issue #6](https://github.com/moldymichael/LetraCode/issues/6) is not fixed in this candidate |

Sign in to GitHub, open that run, scroll to **Artifacts**, and download **LetraCode-windows-x64**. Extract the downloaded archive. It contains the installer, portable ZIP and checksum file. Do not download `windows-tests-*` expecting an installer; those are test reports.

Actions artifacts expire. If the link is unavailable, ask for a replacement candidate identified by commit and run, rather than selecting an arbitrary green job. The [GitHub artifact instructions](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/download-workflow-artifacts) explain access and download steps.

A later candidate can have the same application version and filename. Record the commit and checksum as well as the version when reporting a test.

## Install the application

1. Close any running LetraCode windows. Back up existing data before testing an update.
2. Open Downloads in File Explorer and double-click the extracted `LetraCode-0.6.0-windows-x64-setup.exe` for the candidate above. For a published release, use its matching installer instead.
3. Follow the installer prompts, then open LetraCode from Start.

The installer includes Python, Qt/PySide6 and PDF support. Normal use needs no separate Python installation or compiler. Installation is per user; its default application folder is `%LOCALAPPDATA%\Programs\LetraCode`.

The installer is unsigned. An unknown-publisher warning is not a verification result. Confirm that the file came from the intended repository release or identified Actions artifact and compare its SHA-256 with the accompanying checksum file before deciding whether to proceed. Do not disable Windows security or bypass a malware detection to make a test pass. A checksum detects a mismatch; it is not a publisher signature or a safety guarantee.

In PowerShell, from the folder containing the extracted candidate files:

```powershell
Get-FileHash .\LetraCode-0.6.0-windows-x64-setup.exe -Algorithm SHA256
Get-Content .\LetraCode-0.6.0-windows-SHA256SUMS.txt
```

Compare the hash with the line for the installer, not the portable ZIP. Letter case does not matter.

## Set up the engine and model

The installer does **not** include llama.cpp or model weights. These are separate downloads. A `.gguf` model on another local drive can be reused if compatible. A llama.cpp program built for Linux cannot run natively on Windows.

For this candidate, select a compatible Windows **`llama-server.exe`** and keep the supplied DLLs beside it in the extracted engine folder. Do not move only the executable. Consult the [official llama.cpp project](https://github.com/ggml-org/llama.cpp) for engine builds and the model publisher for requirements.

In 0.6.0, open **Settings → Choose or change model**. Select the engine file and GGUF, save, then use **Test local reply**. Start with CPU/default settings for the first check; a large model can still exceed system RAM. Verify basic inference before changing GPU settings.

### Missing engine versus issue #6

| Observation | Meaning / next action |
| --- | --- |
| You have a GGUF but neither Windows engine executable | The model is present, but the Windows runtime still needs to be obtained. This alone is not evidence of #6. |
| You have `llama-server.exe` | This is the invocation form the candidate expects. Select it, retaining its DLLs. |
| Your installation provides unified `llama.exe` requiring `serve` | This candidate does not insert that subcommand. Track #6; do not repeatedly troubleshoot the already-known mismatch. |
| Startup says `unknown command '--host'` with unified `llama.exe` selected | This matches the failure reported in #6. |

Do not append `serve` to the executable-path field: that field takes a file path, not a shell command. Do not rename binaries or make a batch-file wrapper as a substitute for verified invocation support. The fix must preserve dedicated-server setups and support the unified form explicitly.

## Test without touching existing data

**The portable ZIP uses the same default data folder as the installed app.** Extracting it elsewhere does not isolate your chats or settings. Development builds can open or migrate existing data, so use a separate directory for experiments.

Extract the portable ZIP fully. In File Explorer, open the extracted folder containing `LetraCode.exe`, then open PowerShell in that folder and run:

```powershell
$testData = Join-Path $env:TEMP ("LetraCode-test-" + [guid]::NewGuid().ToString())
.\LetraCode.exe --data-dir "$testData"
```

Keep `$testData` to reopen the same test data in that PowerShell session. A Start-menu launch will use the default data folder, not this test folder. Copy sample documents into a disposable local folder too: `--data-dir` isolates application data but does not restrict file-reading or approved-command permissions.

## First smoke test

Record the build/commit, Windows version, engine version/source, model, hardware and relevant settings. Then check:

1. The application opens and Settings/model dialogs work.
2. A saved draft or test chat remains after closing and reopening with the **same data directory**.
3. With a compatible engine, Test local reply returns an answer.
4. Cancel a generation, then start another request. Close the app and check that its engine process tree has stopped.

An app opening proves startup only. A real reply adds engine/model evidence for that specific configuration. Neither establishes answer quality, all file operations or fine-tuning support. A reproducible failure with relevant logs is a valid test result; report it without changing production data. Redact private text, credentials and sensitive paths.

## Update, uninstall and retain data

Use **Settings → Back up LetraCode** before changing builds. Keep separate copies of linked originals and large model/training files. Close the app and run the newer installer; application files and user data are separate.

Uninstall through Windows **Settings → Apps → Installed apps**. The installer is designed to retain `%LOCALAPPDATA%\letracode`, including chats and settings. Reinstalling normally reuses it. This is not a substitute for a backup, and reinstalling an older build is not a guaranteed safe data downgrade. For the portable version, extract each build into a new folder instead of overlaying files.

## Technical boundaries

The installer targets Windows x64-compatible systems with Windows 10 build 17763 or later, including Windows 11. Its configured CI host is Windows Server 2022, not a manual Windows 11 desktop test. These are distinct facts, not a claim that every edition/hardware combination has been verified.

Guarded data storage and edits require supported local paths. Network shares, reparse points such as junctions/symlinks, ambiguous aliases and certain ACL configurations are deliberately refused. A clean refusal is different from a crash or a silent write to the wrong file. See the [storage and process design record](WINDOWS-RELEASE.md#storage-and-process-behavior).

Implementation references: [installer definition](https://github.com/moldymichael/LetraCode/blob/main/packaging/windows.iss), [Windows builder](https://github.com/moldymichael/LetraCode/blob/main/packaging/build-windows.py), [entry point and --data-dir](https://github.com/moldymichael/LetraCode/blob/main/letracode/app.py), and [workflow](https://github.com/moldymichael/LetraCode/blob/main/.github/workflows/windows-and-fedora.yml). Fine-tuning has a separate environment and validation path described in the [training guide](FINE-TUNING.md).
