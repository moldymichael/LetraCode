# Repository documentation cleanup implementation plan

Historical pre-execution snapshot for the original documentation commit
`98b9efd`. The unchecked boxes record the plan as written, not the current task
status. Later repository/CI maintenance is described by its PR and verification
records; the scope constraints below apply to that original documentation pass.

> For agentic workers: execute this approved plan inline, with verification before completion.

**Goal:** Make installation, contribution and verification understandable without prior project context.

**Architecture:** Separate the front-page overview, Windows user instructions and contributor setup. Index existing records rather than moving them. Keep runtime changes and release publication out of this change.

**Tech stack:** Markdown, GitHub templates, the existing Python/Qt and Windows/Fedora build workflow.

**Spec:** Maintainer-approved cleanup in the September 11, 2026 project conversation: preserve working development and historical evidence; use one documentation PR; distinguish 0.6.0 development from the published 0.3.0 release; keep issue #6 and PR #8 separate.

## Constraints

- Base: `c239fa4407f6b967be025fdf50c6aa124a296e2c` on remote `main`.
- No application, test, packaging or workflow changes; no version bump.
- Do not merge PR #8, close #6, publish a release or overwrite user data.
- Do not treat a successful packaging job as full-suite or real-model verification.
- Retain historical files and existing paths; add context where older wording could mislead.

## Tasks

- [ ] Read current source instructions, version metadata, packaging entry points, workflow, issue #6 and PR #8 status.
- [ ] Rewrite `README.md` with a short overview, release/development distinction, installation choices, permission boundaries and links to detail.
- [ ] Add `docs/WINDOWS.md` for installer/portable use, known launch limitations, updates, data isolation and a bounded smoke test.
- [ ] Add `CONTRIBUTING.md` for native Windows and Fedora development, commands, source map and evidence requirements.
- [ ] Add `docs/README.md`; annotate `docs/WINDOWS-RELEASE.md` and `docs/CURRENT-STATE.md` without removing historical findings.
- [ ] Update `AGENTS.md` so local installation paths are not universal contributor requirements; add lightweight bug-report and PR templates; ignore local virtual environments and generated egg-info in `.gitignore`.
- [ ] Validate changed Markdown links and anchors, template metadata, whitespace and unchanged historical bodies. Check commands against source; report commands not executed.
- [ ] Publish a single documentation commit and PR. Clarify PR #8 metadata without altering its code. Record branch ancestry and any administrative operations the available connection cannot perform.

## Verification boundary

This is a documentation-only change. Static documentation checks do not certify application behavior. Native Windows execution and full Windows/Fedora CI remain separate checks. Keep dated results associated with their exact commit/run; never replace a failing result with a wording change.
