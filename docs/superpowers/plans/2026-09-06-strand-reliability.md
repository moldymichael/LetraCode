# Strand reliability execution plan

> Use subagent-driven-development, with independent review and one owner per overlapping source file. The user explicitly authorized the supplied handoff's implementation.

**Goal:** Reliable supervised continuation, supported source reading and retained-data backups, with measured history cost and separate actual-model evidence.

**Architecture:** Retain Python/Qt/Linux, SQLite schema 2, ordinary memory files and the managed local inference runtime. No scheduler, migration, installation or permission expansion.

**Specification:** User-supplied `IMPLEMENTATION-HANDOFF.md` in the verified LetraCode-Strand-Codex-Handoff package, baseline e57b271. Its historical references are evidence only.

- [x] 0. Verify baseline ancestry/status and package hashes; isolate worktree. Read test side effects, run original suite and supplied failures separately. Record current state.
- [x] 1. Root owns worker and pause-context helper. Add failing tests for original/steered/referenced intent, repeated pause/reopen, malformed/foreign references and fit failures. Preserve authoritative rows with versioned references and reserve intact required context before optional history. Keep latest directions last; preserve approval and result pairing.
- [x] 2. Source agent owns context/tools/source reader. Add deep marker and paging failures; implement bounded character windows, compatible read cursors and snapshot/extraction provenance. Check Unicode/BOM/CRLF, limits, changes and disabled tools.
- [x] 3. Backup agent owns store/strand snapshot code. Reproduce oversized opaque archives; stream safely, include app-owned file backups and coordinate SQLite/files under Strand lock. Verify restore hashes, malformed links, destination failures and interleaving.
- [x] 4. Measure 1/20/100/1000 small revisions in fresh directories; improve exact damage diagnostics conservatively. Keep fail-closed chronology/Undo and preserve all raw history. Record a future durable ordering design.
- [ ] 5. Acceptance agent prepares portable fresh fixtures and finite runtime budgets. Run actual local inference only with existing executable/GGUF and normal human approvals. Save requests/results/patch and report coding and reading independently of deterministic tests.
- [x] 6. Rebase future orchestration design on current code with a distinct namespace and schema >2. Ship linked small docs, not raw scratch. Review diff and full suite; document concrete results and limits.

Each implementation step uses red/green focused checks with isolated XDG/data/temp roots. Final checks include all tests, supplied regressions, Python compilation, shell syntax, release documentation membership and whitespace. No installed app, live DB, source projects, model files, original evidence, unrelated changes, push or deployment is touched.
