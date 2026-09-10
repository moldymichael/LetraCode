# Gemma 4 in Fine-Tuning

LetraCode extends its existing approved-example, immutable-run, comparison,
adoption and rollback workflow. Only text is trained. Audio/vision towers are
omitted, the original tokenizer/template is retained, and the original model
files are never rewritten. This does not add multimodal fine-tuning.

## Models and hardware

Use Google's original instruct safetensors, not a GGUF, PEFT adapter, draft
assistant model, or QAT checkpoint:

| Target | Original source | Revision downloaded for this verification |
| --- | --- | --- |
| E2B | https://huggingface.co/google/gemma-4-E2B-it | `3e22461f65e89153144f8adb70e3b8c2cc9845a7` |
| E4B | https://huggingface.co/google/gemma-4-E4B-it | `ee0ef6023621cff504d758262d4e04895a5af4a2` |

The existing `gemma-4-E2B-it-qat-q4_0-gguf` and E4B QAT GGUF files are distinct
weights. Leave them available for their existing Chat configurations. The new
Chat GGUF is derived from the same ordinary instruct weights used for training;
a sidecar links hashes of every safetensors shard, configuration, tokenizer,
chat template and final GGUF. This records local derivation; it is not a digital
signature authenticating arbitrary files as Google originals.

Unsloth documents E2B QLoRA at 8 GB and E4B at 10 GB VRAM. Those numbers describe
its implementation and do not guarantee fit on this RTX 2060 SUPER. E2B's text
weights contain about 4.63 billion parameters, including a large per-layer
embedding (PLE) table. The table alone needs 8.75 GiB after PEFT promotes it to
FP32. LetraCode keeps that frozen table permanently in system RAM and transfers
only the requested embedding rows to CUDA. E4B's table is larger. Host RAM
therefore matters as well as VRAM; long sequences, other GPU workloads, and
larger ranks can still exhaust memory.

The RTX 2060 SUPER has compute capability 7.5 and no native BF16. The tested
path uses NF4 double-quantized frozen linear weights, FP16 CUDA computation,
FP32 trainable adapters, batch size 1, gradient accumulation, and explicitly
non-reentrant gradient checkpointing. Only attention/MLP projections
(`q_proj`, `k_proj`, `v_proj`, `o_proj`, `gate_proj`, `up_proj`, `down_proj`) are
adapted. Embeddings, output head, PLE projections, and multimodal components
remain frozen or omitted. These targets are applied by the llama.cpp Gemma
adapter execution path.

## Setup and use

Keep the desktop Qt Python separate from the training Python. The existing
training environment is reused on this machine. For a new environment, install
a compatible CUDA PyTorch wheel first, then
`packaging/gemma-training-requirements.txt` and the selected llama.cpp converter's
requirements. Recheck CUDA afterward: upstream converter requirements can alter
Torch. The installed/tested versions and real measurements are recorded below.

Download the original checkpoint with its `config.json`, safetensors,
`tokenizer.json`, `tokenizer_config.json`, `chat_template.jinja`, and
`generation_config.json`. Training itself is offline and never downloads models
or packages. Prepare a matching Chat GGUF once:

```bash
python3 tools/prepare-gemma-chat.py \
  --training-python /home/miceoil/.local/share/letracode-training-qlora/bin/python \
  --base-model /home/miceoil/bigdrive/Jan/llamacpp/models/gemma-4-E2B-it-HF \
  --llama-cpp /home/miceoil/llama.cpp-cuda \
  --output /home/miceoil/bigdrive/Jan/llamacpp/models/gemma-4-E2B-it-LetraCode-Q4_K_M.gguf
```

The command rejects an existing output and keeps conversion logs beside it in
a `.letracode-gemma-*` directory. It uses an F16 intermediate plus Q4_K_M
quantization and removes only its own intermediate after success. It requires
the built `llama-quantize` target. Keep the final `.gguf.letracode.json` sidecar.
For E4B substitute both E2B path components with E4B.

1. Open Fine-Tuning. Write/import examples, review them, and approve each desired
   training/evaluation example. Editing clears approval. Keep evaluation prompts
   separate; duplicate prompts across splits are rejected.
2. In Train, choose the Gemma E2B or E4B profile and select the original HF
   directory, its prepared matching GGUF, training Python, and llama.cpp source
   directory. Profiles preserve saved paths and are starting settings only.
3. Review and start. The app frees its inference engine before training. It
   stores immutable approved examples/configuration and checks the model pair.
   Stop cancels the owned process and preserves partial artifacts.
4. In Versions, inspect original and candidate held-out response loss and
   generated responses. Both use the same Transformers NF4 model/template,
   greedy generation and up to 64 new tokens, with thinking disabled. Outputs
   are bounded samples, not full answer-quality scores. Lower loss or successful
   training alone does not establish better answers.
5. Adopt a completed, converted version to load original Chat GGUF + adapter in
   Model A. Model B stays as configured. Hash/manifest mismatch or load failure
   prevents the settings swap. Chat records the active training version.
6. Roll back to reload the saved previous model configuration. Settings persist
   across restart. Model weights and prior adapters remain separate.

Backups of app data preserve run metadata/examples; model weights, adapters,
GGUFs and logs are separate files and need their own backup.

## Compatibility decisions and sources

- [Google model cards](https://huggingface.co/google/gemma-4-E2B-it) and
  [Transformers Gemma 4 documentation](https://huggingface.co/docs/transformers/model_doc/gemma4)
  identify the original conditional checkpoint, text model and templates.
- [Unsloth training guidance](https://unsloth.ai/docs/models/gemma-4/train)
  supplies hardware estimates and known FP16/KV-sharing caveats. Unsloth was
  evaluated as an alternative; the working local Transformers/PEFT runtime was
  retained to avoid introducing another trainer and patches.
- The old cache/no-cache error was fixed in
  [Transformers 5.5.2](https://github.com/huggingface/transformers/releases/tag/v5.5.2).
  Current [Unsloth shared-KV fixes](https://github.com/unslothai/unsloth-zoo/blob/main/unsloth_zoo/temporary_patches/gemma4.py)
  also explain why non-reentrant checkpointing is necessary. Local tiny-model
  regression tests compare logits and every trainable gradient; finite loss
  alone would miss an incorrect shared-KV gradient.
- [PEFT preparation](https://github.com/huggingface/peft/blob/main/src/peft/utils/other.py)
  promotes ordinary weights to FP32. Generic Accelerate inference offload uploads
  whole CPU modules on forward; the explicit PLE lookup avoids that VRAM spike.
- [llama.cpp adapter converter](https://github.com/ggml-org/llama.cpp/blob/master/convert_lora_to_gguf.py)
  and [Gemma execution graph](https://github.com/ggml-org/llama.cpp/blob/master/src/models/gemma4.cpp)
  support the selected decoder projections. Unrestricted all-linear targets
  would also adapt projections not applied by this inference path.

## Measured verification on this computer — September 9, 2026

Both real checkpoints completed the entire workflow in a separate scratch data
folder using the actual Qt Fine-Tuning panel and MainWindow/ConversationWorker
Chat path. Three synthetic public factual examples were approved for training
and two different prompts for evaluation. There were two optimizer updates
(one epoch, batch 1, accumulation 2, rank 4, learning rate 0.0002, seed 42,
checkpointing enabled, maximum length setting 256). The actual longest training
example was 36 tokens; the longest evaluation example was 33 tokens. These are
short operational tests, not a representative answer-quality evaluation.

| Measurement | E2B | E4B |
| --- | ---: | ---: |
| Peak PyTorch allocated VRAM, full short workflow trainer | 3.37 GiB | 5.92 GiB |
| Peak PyTorch reserved VRAM | 3.53 GiB | 6.05 GiB |
| Sampled whole-device memory during training, including desktop | 4,184 MiB | 6,751 MiB |
| Frozen PLE table in system RAM | 8.75 GiB | 10.50 GiB |
| Peak training process resident RAM | 14.31 GiB | 20.38 GiB |
| Worker wall time (hashes, loading, evaluation, training, export) | 47.77 s | 64.57 s |
| Held-out original → candidate response loss | 6.017910 → 4.260051 | 4.304399 → 3.160940 |

Every exported adapter tensor was finite and every selected LoRA B matrix had
nonzero updates. The original HF weight hashes match the publisher's pinned
revision records; source/GGUF hashes remained unchanged. Both separate GGUF
adapters loaded in llama.cpp, produced completed Chat responses, survived
application reopening, and rolled back to the original GGUF with no adapter;
that rollback also persisted across reopening.

Chat used a 2,048-token context, GPU layers 999, temperature 0, Instant mode,
a 128-token output limit and tools disabled. The prompts asked about days in a
week and France's capital. E2B Chat returned the same factual answers before and
after training. E4B changed “Paris” to “The capital of France is Paris.” on one
prompt; the answer was already correct. Neither result establishes improved
quality or generalization. The Transformers NF4 comparisons and llama.cpp
Q4_K_M comparisons are recorded separately; different runtimes/quantization can
produce different wording.

A separate real-weight E4B capacity probe used the same rank/precision/offload
and one optimizer update at each sequence length. **250 tokens succeeded** at
6.69 GiB allocated / 7.04 GiB reserved VRAM and 20.13 GiB process RAM.
**509 tokens failed with CUDA out of memory**, before a successful update.
This is an observed limit for this implementation/configuration, not proof that
all possible optimizations or example shapes fail at 509 tokens. There is little
headroom near 256 tokens. Start with short examples; batch 1 and rank 4 matter.
The extra 48 GB system RAM makes the CPU PLE approach possible but does not
replace VRAM for activations/logits.

Runtime: Torch 2.11.0+cu126, Transformers 5.17.0, PEFT 0.20.0,
bitsandbytes 0.50.2, Accelerate 1.15.0, safetensors 0.8.0;
llama.cpp checkout `304665fe7`, driver 610.57.04, RTX 2060 SUPER (SM 7.5).
No training packages were upgraded. The existing llama-quantize build target was
built from that checkout; the existing server and models were preserved.

The full desktop suite passed 1,016 tests with 22 optional-dependency skips;
50 backend/runtime tests passed in the training Python (including real tiny
Llama training and Gemma cache/checkpoint gradient and weight-loading parity).
After the memory-display wording change, 34 affected UI tests also passed.
Six existing Python 3.14 fork deprecation warnings remain. Tests cover legacy
run defaults, approval/split isolation, cancellation, hashes, mismatched model
pairs, original checkpoint validation, actual adapter updates, and rollback.

Full transcripts, reports and logs:
`/home/miceoil/Projects/artifacts/evidence/gemma4-2026-09-09/`.
The compact machine-readable result is [GEMMA4-VERIFICATION.json](GEMMA4-VERIFICATION.json).
Reproduce the full isolated workflow with `tools/verify-gemma-training.py --help`;
`--resume-run` reuses a succeeded training run if a later verification step needs
repeating without retraining.

Not verified: long Gemma training jobs, broad held-out quality, multimodal
training, full-weight fine-tuning, BF16 hardware, Windows training, or large
contexts/multimodel Chat with these adapters. GPU memory varies with actual
sequence lengths and other running applications. E4B is usable here for the
measured short-text setup; E2B remains the less constrained starting point.

The corresponding E2B capacity probes passed at both 250 and 509 tokens.
The 509-token update peaked at 5.13 GiB allocated / 5.80 GiB reserved VRAM.
This supports using E2B when the short E4B sequence budget is insufficient;
even E2B's longer-context limits have not been exhaustively established.

The existing Llama CUDA regression tool also passed: a random tiny Llama fixture
completed 16 QLoRA updates, updated every all-linear adapter, exported/loaded its
GGUF adapter, and rolled back. The fixture is an operational regression check,
not a usable chat model.

The trained E4B adapter was additionally loaded with this user's existing
53,248-token configured context and five GPU layers (Model A only), and answered
“There are seven days in a week.” in a completed request. This checks loading
and a brief prompt with those resource settings, not a filled 53k context.
The check read user settings without changing them.

## Installed result and preservation

The extension is installed under `/home/miceoil/.local/share/letracode-app`.
Both completed Gemma verification runs appear in Fine-Tuning → Versions with a
**synthetic verification** label. They can be inspected/adopted/rolled back, but
are not recommended as better-answer models. Your real example library was not
populated with synthetic examples. Existing database rows (including chats,
settings, approved examples and older runs) were compared verbatim and stayed
unchanged; the two new run records were appended. Your selected Chat model was
not switched during installation.

The original application files and a consistent database backup are under
`/home/miceoil/.local/share/letracode-repairs/gemma4-20260909/`; its
`installation.json` records the installed file hashes and new run IDs. Model
weights and earlier adapters were kept. Original E2B/E4B checkpoints, their
matching Chat GGUFs and sidecars are in the existing bigdrive model directory.

Development branch: `codex/gemma4-fine-tuning` at
`/home/miceoil/Projects/worktrees/active/LetraCode-gemma4`. It extends the current
0.5.0 fine-tuning line, carrying the already-installed Hermes tool-schema fix
intact. The older 0.1.1 anchor checkout was not replaced.
