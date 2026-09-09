# Two-model installed-app update

The original multi-model prototype was in the old 45092f1 checkout, carrying
version 0.2.0 and schema-1 storage. Inspection before installation showed that
every installed Python module exactly matched commit
4e138587d2c2a13cfdd1da43f5010dfc238806b7 (version 0.3.0, schema 3).

This update is therefore based on that exact installed commit, in the isolated
`codex/multi-model-current` worktree. It preserves Memory/history/Undo, Project
files, evaluation privacy, Windows filesystem/process behavior and single-model
bounded continuation. The unrelated uncommitted fine-tuning worktree and old
feature checkout remain intact. There is no database migration or version downgrade.

The two-model mode reuses one managed llama.cpp router, the existing worker
signals/cancellation, runtime request accounting and saved intent resolution.
Both models preload before use and take 1–4 alternating turns per explicit
user-started exchange. No action tools or automatic continuation run in this
mode. Required context is retained or rejected visibly; optional request copies
are bounded while original transcripts stay saved. The existing Memory rules
remain in force, including user-selected always-active files.

Speaker metadata is saved at streaming-row creation. Protocol packing preserves
row identities for intent resolution, then merges peer contributions only at
the single-model wire boundary for strict alternating templates. Markdown and
privacy-filtered evaluation exports retain saved labels without adding arbitrary
speaker metadata fields to the public projection.

The source baseline passed 722 tests, with 15 platform skips and two existing
Python 3.14 fork warnings. New regressions cover router protocols and lifecycle,
both-model readiness, token accounting by model, repeated finite exchanges,
required plan/constraint preservation, cancellation, UI preferences, exports,
and real Qt-to-local-HTTP integration. The installed engine itself has not been
replaced or automatically downloaded as part of the application update.

Final verification: 793 passed, 15 platform-dependent skips, six existing
Python 3.14 fork warnings. Independent final review found no remaining blockers.
The current per-user installer ran with existing dependencies; every installed
Python module was compared byte-for-byte to this source. All 315 live data files
were unchanged across installation. The installed GUI then opened a separate
copy of the existing schema-3 data with the new controls and no inference started.
The previous app installation was retained in a temporary rollback copy.
