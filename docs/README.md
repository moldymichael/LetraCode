# Documentation index

Start with [the project overview](../README.md). This index separates instructions from implementation records and historical evidence. No existing document has been moved; previous file links remain valid.

## Install and use

| Document | Use / scope |
| --- | --- |
| [Windows guide](WINDOWS.md) | Installation, current test-candidate identity, engine setup, updates and safe testing. |
| [Strand user guide](STRAND-EXPERIENCE.md) | The 0.6.0 interface, permissions, examples and limitations; includes dated verification. |
| [Training guide](FINE-TUNING.md) | Optional training dependencies and workflow. Some version/UI wording predates the redesign; use the 0.6.0 guide for current navigation. |
| [Comparison workflow](COMPARISON-WORKFLOW.md) | Current candidate-review behavior and its verification record. |
| [Gemma support](GEMMA4.md) | Supported model preparation/training path and recorded evidence, not arbitrary Gemma/model support. |

## Develop and investigate

| Document | Use / scope |
| --- | --- |
| [Contributing](https://github.com/moldymichael/LetraCode/blob/main/CONTRIBUTING.md) | Windows/Fedora source setup, commands, code map, packaging and review expectations. |
| [Agent instructions](https://github.com/moldymichael/LetraCode/blob/main/AGENTS.md) | Baseline selection, preservation and verification rules. |
| [Implementation history](CURRENT-STATE.md) | Dated repository status plus earlier milestones and maintainer-local installation history. |
| [Reading recovery](READING-RECOVERY.md) | Paging, source coverage and automatic recovery behavior. |
| [Memory migration recovery](MEMORY-MIGRATION-RECOVERY.md) | A migration failure investigation and its recovery checks. |
| [Experience design](superpowers/specs/2026-09-10-strand-experience.md) | Intent for the redesign; consult code/tests for what exists. |
| [Design specifications](superpowers/specs/) and [implementation plans](superpowers/plans/) | Dated design/execution records, not independent proof of completion. |
| [Future task design](FUTURE-TASKS-DESIGN.md) | Proposed work; do not present it as implemented functionality. |

## Verification evidence

Results apply to the commits, dates, environments and test types stated in each record. A pass on Linux does not prove Windows behavior; a scripted server is not a real-model test. Use [Actions](https://github.com/moldymichael/LetraCode/actions) and the relevant PR for live results.

| Record | Subject |
| --- | --- |
| [Windows 0.3.0 release](WINDOWS-RELEASE.md) | Historical native storage, process and installer/portable verification. |
| [General verification](VERIFICATION.md) | Accumulated dated checks across earlier milestones. |
| [Reliability verification](RELIABILITY-VERIFICATION.md) | Task recovery and continuation checks. |
| [Real-model acceptance](REAL-MODEL-ACCEPTANCE.md) | Named local engine/model runs and their limits. |
| [Supervised coding proof](SUPERVISED-CODING-PROOF.md) | Observed coding-workflow evidence and unresolved outcomes. |
| [Fine-tuning verification](FINE-TUNING-VERIFICATION.md) | Original training integration checks. |
| [QLoRA verification](QLORA-VERIFICATION.md) | Recorded CUDA 4-bit training evidence. |
| [Gemma verification data](GEMMA4-VERIFICATION.json) | Structured evidence accompanying the Gemma record. |
| [Hermes template verification](HERMES-TEMPLATE-VERIFICATION.md) | A specific model/template investigation. |
| [Strand M1a review](STRAND-M1A-REVIEW.md) | Historical memory/context milestone review. |

## Historical feature and installation records

These remain useful for tracing decisions and migrations. They are not instructions to overwrite another machine's installation.

[README 0.5.0](README-0.5.0.md), [evaluation and Memory](EVALUATION-MEMORY.md), [project files](PROJECT-FILES-UPDATE.md), and [multi-model update](MULTI-MODEL-UPDATE.md) describe earlier interfaces or milestones.

Recorded maintainer installations: [0.4.0](INSTALLED-0.4.0-UPDATE.md), [0.6.0](INSTALLED-0.6.0-UPDATE.md), [comparison repair](INSTALLED-COMPARISON-UPDATE.md), and [reading repair](INSTALLED-READING-UPDATE.md).

## Keeping this useful

Update the user-facing guide when behavior changes. Link the relevant issue/PR and exact verification commit instead of copying an undated test count into several documents. Preserve historical results; annotate their scope when necessary. Add new documents to this index and use relative links inside the repository.
