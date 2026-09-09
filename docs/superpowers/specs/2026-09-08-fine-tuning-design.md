# Local assistant fine-tuning

The September 9 request additionally requires visible thinking in regular chat.
The engine accepts streamed reasoning fields and leading think blocks, persists
the emitted text separately from answers, and displays it in a collapsible
Thinking block. Partial reasoning is retained on interruption. Reasoning is
excluded from later prompts and answer copying/exports; no reasoning is invented
for models that do not emit it. Existing action authority and Markdown safety
boundaries apply unchanged to the separate display.

Implements the previously requested separate Fine-Tuning area and local training
loop described in `LetraCode-Direction/MANIFESTO.md` and `MILESTONES.md`.
The user delegated architecture, libraries, model strategy and implementation
order in “Recommend Strand Architecture”; the current request authorizes this upgrade.

## Scope and behavior

Keep the existing Chat workspace and add a separate Fine-Tuning workspace.
Users prepare prompt/desired-response examples, import/export JSONL, and explicitly
review examples for either training or held-out evaluation. Chat replies may be
copied into draft examples; chats and Memory are never automatically training truth.
Duplicate prompts cannot occur across the two splits. Edits clear prior approval.

One local backend uses PyTorch, Transformers and PEFT for actual LoRA optimization.
It loads a local Hugging Face model directory and local tokenizer, without remote
code or implicit downloads. This first backend targets text-only Llama-compatible
causal language models, with CPU or CUDA execution. Training dependencies live in
a separately selected Python environment, never in the desktop app's Qt runtime.
Training a quantized inference GGUF directly is unsupported. Configuration explains
the need for matching original training weights and sufficient RAM/VRAM.

Each run snapshots approved data and configuration, preserves a unique output
directory, emits progress, supports cancellation of the owned process tree, and
records failure/interruption honestly. Closing the app cannot leave an unmanaged
trainer. Reopening never silently restarts training. Base weights are read-only.

Evaluate base and candidate on the identical held-out desired responses, excluding
prompt tokens from loss. Save token-weighted loss and example outputs for comparison.
A lower loss is narrow evidence, not a claim of overall assistant improvement.
Keep dataset hashes, model/config identity, package versions and adapter hashes.

Convert the PEFT adapter to GGUF using a selected local llama.cpp checkout and the
same training Python. Preserve the PEFT output if conversion fails. Adoption requires
a completed evaluation, converted adapter, explicit user choice and a successful
local engine load with the selected base GGUF. Keep the complete previous engine
configuration for rollback. Never replace the base GGUF or older versions.

## Boundaries

No cloud API, automatic live-data capture, automatic adoption, model download, or
claim that the current large Qwen model can be trained on this hardware. The backend
will reject unsupported architectures clearly. The app can manage this workflow on
Windows/Linux; platform and model quality claims require corresponding evidence.
Current assistant tools can prepare ordinary JSONL and inspect exported reports
under existing approval rules. Dedicated autonomous training tool calls are outside
this initial explicit desktop training workflow.

## Persistence and integration

TrainingRepository uses additive SQLite tables for reviewed examples, immutable run
snapshots, states and results. App backups retain these records; separately stored
large weights/output directories require their own backup and are explicitly listed
as excluded. No existing Memory/schema migration is repurposed for model training.
EngineConfig gains an optional LoRA path. Existing configurations remain valid.

## Verification

Regression tests cover approval invalidation, split leakage, import atomicity,
snapshot immutability, run lifecycle, cancellation, failed conversion, adoption and
rollback. Qt checks exercise the real workspace using temporary data. A tiny local
random Llama fixture establishes actual optimization, held-out evaluation and GGUF
conversion without touching the user's model or data. The full existing suite is
run after integration. This fixture proves plumbing, not useful model quality.
