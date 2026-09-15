# LetraCode development roadmap

This document defines the development direction for LetraCode. It is not a release checklist or a chronological implementation record. Use [CURRENT-STATE.md](CURRENT-STATE.md) for dated implementation history, issues and pull requests for live work, and this roadmap for deciding what should come next.

## North star

LetraCode is a local environment for one persistent assistant, **Strand**, that can work with a person over time.

The central goal is not to accumulate AI features. The goal is for Strand to be useful in ordinary work: understand the current task, find and use the right local information, use tools reliably, complete the task, preserve appropriate knowledge for later, and improve through deliberately reviewed experience.

A successful everyday interaction should feel like this:

> Open LetraCode, tell Strand what you need, and Strand can determine what information is required, retrieve it, work through the task, give a trustworthy answer, preserve useful knowledge when appropriate, and learn from reviewed corrections without the application getting in the way.

Model launching, Memory, source tools, training, multi-model chat, packaging and interface work support that goal. They are not separate end goals.

## Development priorities

### 1. Make ordinary Strand work dependable

This is the current highest priority.

Strand must be able to finish normal tasks without LetraCode introducing avoidable failures. Work in this layer includes:

- reliable file reading and search;
- correct source-coverage tracking;
- sensible termination after enough evidence has been gathered;
- continuation that preserves the task, evidence and tool results;
- context management that does not silently remove required information;
- prevention of repeated answers, repeated non-progress tool calls and accidental loops;
- understandable stops, failures and partial results;
- a final answer when the task is actually complete.

Current related work includes [PR #16](https://github.com/moldymichael/LetraCode/pull/16), which targets incidental source-reading loops and evidence progress. Reliability work should be evaluated first as application behavior, then with real models when the claim depends on model/runtime interaction.

Do not try to fine-tune around an execution, continuation, context-packing or tool-delivery bug.

### 2. Make Strand's knowledge use trustworthy

Once task execution is dependable, Strand should be able to find the right information and understand its authority.

Important distinctions include:

- current source material versus summaries;
- established facts versus plans;
- project-specific information versus shared information;
- user-authored material versus Strand interpretation;
- durable reference information versus temporary conversation context;
- current evidence versus stale or conflicting evidence.

Project facts that can change should normally stay in files or Memory and be retrieved when needed. Training weights are not a substitute for current project state.

The desired result is a knowledge system that is predictable enough to become boring: Strand knows where to look, retrieves evidence when needed, exposes uncertainty and conflicts, and does not invent authority the source does not provide.

### 3. Define and measure a good Strand

Before expanding training, maintain a representative evaluation set for the work Strand is actually expected to do.

The set should cover different abilities rather than many near-duplicate prompts. Representative categories include:

- source-grounded factual questions;
- project continuity and cross-file reasoning;
- evidence versus interpretation;
- writing and literary analysis;
- correction after an unsupported or mistaken claim;
- beginner programming explanations;
- repository reading and debugging;
- choosing whether to use a tool;
- choosing whether clarification is actually necessary;
- appropriate Memory behavior.

Evaluation should include tasks that were not used as teaching examples. Record enough context to reproduce the task and distinguish model quality from application failure.

The purpose is to replace "this answer feels better" with evidence about which Strand abilities improved, regressed or stayed unchanged.

### 4. Use training to improve judgment and behavior

Conversation training is a means to improve Strand, not the product goal.

Current conversation-training work is tracked in [PR #15](https://github.com/moldymichael/LetraCode/pull/15). The useful target for training is durable behavior such as:

- grounding claims before asserting them;
- deciding when a source or tool should be consulted;
- distinguishing evidence from interpretation;
- responding to corrections by rechecking rather than defending or merely agreeing;
- analyzing writing on its own terms instead of applying generic rules automatically;
- explaining technical material at an appropriate level;
- choosing between acting and asking a necessary clarification;
- communicating uncertainty proportionately.

Do not use fine-tuning as the primary storage mechanism for current manuscript facts, file layouts, codebase state or changing user/project information.

Training runs should be judged against held-out and fresh evaluation tasks, not only training loss.

### 5. Make Strand capable of substantial coding work

A longer-term goal is for Strand to understand and improve LetraCode itself, but this depends on the previous priorities.

The capability should develop in stages:

1. read repository files and explain them accurately;
2. trace behavior across multiple modules;
3. investigate a bug using repository evidence;
4. propose a change and appropriate tests;
5. make guarded edits, run verification, inspect failures and iterate;
6. participate safely in improving LetraCode itself.

Do not skip directly to autonomous editing while source use and task completion are still unreliable.

### 6. Improve everyday usability, performance and platform quality

Interface and platform work remain important when they remove friction from the core Strand workflow.

Examples include:

- responsive and correct chat scrolling;
- obvious Thinking controls;
- understandable progress and failure states;
- simple model setup and model switching;
- lower inference overhead where LetraCode is slower than the underlying engine;
- safe installation, updates and backups;
- Windows and Fedora compatibility;
- documentation that works for both ordinary users and contributors.

Treat these as support for daily use rather than a reason to postpone core assistant reliability indefinitely.

## Failure classification: fix the right layer

Before starting a fix, classify the observed failure. A single symptom can involve more than one layer, but the classification prevents unrelated mechanisms from being used as substitutes for each other.

| Layer | Typical problem | Preferred response |
| --- | --- | --- |
| **Application/runtime** | loops, lost tool results, broken continuation, bad context packing, UI state bugs | code and regression tests |
| **Retrieval / Memory** | wrong source selected, stale project fact, missing authoritative file, poor source hierarchy | retrieval, indexing, Memory or project-file design |
| **Instructions** | a stable rule is clear but not being supplied or expressed well | prompts/instructions, then evaluate |
| **Training** | recurring judgment/style/tool-choice behavior remains weak across tasks | reviewed teaching conversations plus held-out evaluation |
| **Base model** | the model cannot reliably perform the reasoning/language task even with good context and instructions | select a better compatible model or accept the capability boundary |

The development loop should be:

> **Use Strand → observe a failure → classify the layer → fix the correct layer → rerun representative evaluation.**

## Current focus

The immediate development sequence is:

1. finish and verify the source-reading / continuation reliability work;
2. stabilize the reviewed conversation-training workflow without expanding its scope unnecessarily;
3. create a Strand capability and evaluation set based on real intended use;
4. run the current Strand through that set to establish a baseline;
5. choose the next work from the measured failures rather than from feature novelty;
6. create a first deliberate training curriculum only after the behavioral target and evaluation are clear.

This sequence can change when evidence exposes a more fundamental blocker, but a new feature should not displace it merely because the feature is interesting.

## Later, not current priorities

The following ideas may become valuable, but they should not compete with the current core work unless a concrete use case makes them necessary:

- elaborate multi-model conversations;
- networks of shared/local models;
- nano-model orchestration;
- autonomous self-training or automatic approval of training data;
- large theme systems;
- broad model-backend expansion without a demonstrated need;
- hardware-control integrations;
- distributed or shared Strand deployments.

Existing versions of these capabilities do not need to be removed. The point is to avoid expanding them while more fundamental Strand behavior remains unreliable or unevaluated.

## Decision rule for new work

Before adding a feature, ask:

1. Which core Strand job does this improve?
2. Is there evidence that this job is currently failing or unnecessarily difficult?
3. Which layer actually owns that failure?
4. How will the change be evaluated?
5. What more fundamental work would this displace?

If those questions do not have clear answers, the work should usually remain an idea rather than become the next implementation task.

## Relationship to other documents

- [README.md](../README.md) explains what LetraCode is and how to start.
- [STRAND-EXPERIENCE.md](STRAND-EXPERIENCE.md) explains current user-facing behavior.
- [CURRENT-STATE.md](CURRENT-STATE.md) preserves dated implementation history.
- [KNOWN-ISSUES.md](KNOWN-ISSUES.md) records current boundaries and active problems.
- [FINE-TUNING.md](FINE-TUNING.md) documents the supported training workflow.
- [CONTRIBUTING.md](../CONTRIBUTING.md) defines development and review procedure.

This roadmap should stay comparatively stable. Change it when the product direction or priority order changes, not for every implementation milestone.