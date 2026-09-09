# Installed 0.4.0 update

On September 9, 2026, the user's desktop launcher pointed to the per-user
installation at `~/.local/share/letracode-app`, reporting 0.3.0. Its 28 Python
modules matched the unfinished `codex/multi-model-current` worktree exactly.
That installed build had two-model conversations absent from GitHub main.

Before installing the fine-tuning changes, those installed capabilities were
integrated into `codex/fine-tuning-and-thinking`. Both model selections, finite
exchanges, speaker labels, exports, Memory and single-model continuation remain
available. Thinking is retained separately for each speaker. A LoRA adapter is
scoped to Model A in the router preset; Model B does not inherit it. Adoption
and rollback preserve the complete model selection while loading only the
models required by the selected chat mode.

## Verification

- Full offscreen suite: **900 passed, 16 skipped, 6 existing Python 3.14 fork
  warnings in 57.76 seconds**.
- Independent combined-integration review found no blockers and passed its
  18 focused regression checks.
- A real local llama.cpp router loaded two generated tiny GGUF models with the
  trained adapter on Model A. Both streamed text up to the configured 16-token
  cap, and both stopped cleanly. This is workflow evidence, not useful model
  quality. Evidence: `/tmp/letracode-adapted-two-model-smoke-rbbx54ju/result.json`.
- The combined UI opened a separate copy of the existing schema-3 data, showed
  Fine-Tuning and Two models, preserved all project/chat/message/link rows, and
  started no inference.
- Existing desktop dependencies were verified before running `./install.sh
  --no-deps`. No package download or privilege escalation was needed.
- `~/.local/bin/letracode --version` reports **LetraCode 0.4.0**. Every installed
  Python module (33 files) matches the combined source byte-for-byte.
- All 375 pre-existing live data files had identical hashes immediately after
  installation. Reading the SQLite backup created an empty WAL and shared-memory
  sidecar, without changing the original files.
- The updated desktop application was then launched through the user's normal
  launcher. It stayed running from the installed module path, acquired the live
  app lock, initialized the additive training tables, and retained the existing
  project/chat/message/link rows. The startup log was empty.

The previous installation, launcher, desktop entry, copied data, manifests and
verification output were retained at
`/tmp/letracode-0.4.0-installed-upgrade-rm6h_a5p`. These temporary artifacts may
be removed by system cleanup. The original multi-model source worktree also
remains intact; no old worktree was overwritten.
