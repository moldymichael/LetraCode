# Known issues and current boundaries

This page records limitations that can affect a new installation or development decision. It is a snapshot, not a replacement for the live [issue list](https://github.com/moldymichael/LetraCode/issues), [pull requests](https://github.com/moldymichael/LetraCode/pulls) or [Actions results](https://github.com/moldymichael/LetraCode/actions). Check the exact commit and date before relying on a result.

## Release and source are different builds

**Status checked September 11, 2026:** the latest published release is 0.3.0, while current source identifies as 0.6.0. The 0.6.0 source contains later interface, training, comparison and source-reading work, but no 0.6.0 release has been published. Cloning source or downloading an Actions artifact does not update an installed copy.

Use the [Windows guide](WINDOWS.md) for the published download and for selecting an explicitly identified development artifact. Treat every development artifact as a test candidate tied to one commit and workflow run.

## Windows engine invocation

[Issue #6](https://github.com/moldymichael/LetraCode/issues/6) remains open. Current source starts a dedicated `llama-server.exe` by passing server options directly. It does not insert the `serve` subcommand required by a unified `llama.exe serve` installation. Selecting that unified binary can fail with `unknown command '--host'`.

A compatible dedicated `llama-server.exe`, with its accompanying DLLs, remains the expected Windows engine form until the issue is resolved and verified. The LetraCode installer does not include llama.cpp or model weights.

## CI and platform evidence

[PR #8](https://github.com/moldymichael/LetraCode/pull/8) records Windows/Fedora fixture, native quantizer and model-file verification repairs. It is separate from issue #6. Read the checks on the exact candidate commit; do not infer a green application suite from a successful packaging job. Windows model verification rehashes files because its creation-time metadata cannot prove unchanged contents after a same-size rewrite; verification of large model files can therefore take longer.

The hosted Windows workflow uses Windows Server 2022. Package lifecycle checks can establish that the generated app installs, opens, updates, uninstalls and retains the tested data. They do not establish manual Windows 11 behavior, real llama.cpp/model compatibility, answer quality or all hardware combinations. The installer is unsigned.

## Runtime and training boundaries

- One model job runs at a time. Work is bounded and recoverable, but there is no durable background scheduler that continues after the desktop application exits.
- Retained history scans can grow with accumulated revisions. There is no chronology index or retention-pruning feature.
- Earlier real-model acceptance and coding trials include incomplete or failed outcomes. Passing scripted-engine tests is application evidence, not proof that a local model can complete arbitrary work. See [real-model acceptance](REAL-MODEL-ACCEPTANCE.md) and the [supervised coding record](SUPERVISED-CODING-PROOF.md).
- Local training supports specific text-only Llama and Gemma paths and requires compatible original weights and separate dependencies. An arbitrary GGUF file alone cannot be trained. See the [training guide](FINE-TUNING.md).
- Guarded Windows file operations deliberately refuse unsupported network paths, reparse points, ambiguous aliases and some ACL configurations. A refusal can be the expected safety behavior.

## Reporting a new failure

Search the live issues first, then report the exact LetraCode commit/build, operating system, engine and model where relevant, steps, expected and observed behavior, and sanitized logs. A reproducible report is useful even without a fix. Use disposable application data and sample files when possible; do not attach private chats, full databases, receipts or model weights.
