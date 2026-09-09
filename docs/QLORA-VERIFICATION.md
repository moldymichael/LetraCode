# 4-bit QLoRA verification — 2026-09-09

LetraCode 0.5.0 extends the verified 0.4.0 Fine-Tuning and two-model app on
`codex/fine-tuning-and-thinking`. The older dirty checkout at
`/home/miceoil/Projects/LetraCode` was not changed. Implementation and release
work use `/home/miceoil/Projects/worktrees/active/LetraCode-fine-tuning-current`.

## Actual CUDA training proof

The persistent training interpreter is
`/home/miceoil/.local/share/letracode-training-qlora/bin/python`, separate from
the desktop Qt Python. CUDA availability and real tensor computation passed;
the dependency resolver reports no broken requirements.

| Component | Verified value |
| --- | --- |
| GPU | NVIDIA RTX 2060 SUPER, 8 GiB, compute capability 7.5 |
| Driver | 610.57.04 |
| PyTorch | 2.11.0+cu126, CUDA 12.6 |
| Transformers / PEFT | 5.17.0 / 0.20.0 |
| bitsandbytes / accelerate | 0.50.2 / 1.15.0 |
| safetensors | 0.8.0 |
| Compute precision | FP16; native BF16 unavailable on this GPU |

`tools/verify-local-training.py` generated random text-only Llama weights
(hidden size 256, intermediate 768, four layers), a local tokenizer, approved
training/evaluation examples and a matching GGUF. No user weights, examples,
conversations or model settings were used or trained.

```bash
python3 tools/verify-local-training.py \
  --training-python ~/.local/share/letracode-training-qlora/bin/python \
  --llama-cpp ~/llama.cpp-cuda --llama-server ~/.local/bin/llama-server \
  --output /tmp/letracode-qlora-gpu-final-20260909 \
  --training-method qlora --device cuda \
  --gradient-accumulation-steps 2 --gradient-checkpointing --fixture-size memory
```

- Actual CUDA `Linear4bit` state verified NF4 and nested/double quantization
  for all **28** linear layers. Adapters targeted all linear layers.
- Three training examples per epoch, microbatch 1, accumulation 2, eight epochs:
  **16 real optimizer updates**, including every final one-microbatch group.
- Frozen-base enforcement passed: **81,920 trainable adapter parameters** out
  of **3,520,768 total parameters**. Non-adapter gradients are prohibited.
- Every adapted layer had finite, nonzero updated B weights: **45,056 values**.
- Held-out assistant-only loss on 11 response tokens: **4.057787 → 0.817775**.
  Both evaluations used the same quantized frozen base.
- Original safetensors SHA-256 remained
  `bc61d17acf0541eabf37e4842db02c9258f310c67005b7b4de2ef29d7cd87d6e`.
- PEFT safetensors export and the local llama.cpp GGUF converter succeeded.
  The actual `ActivationWorker` loaded the adapter, streamed a bounded response,
  then rolled back and loaded the base without an adapter. Inference used CPU;
  optimization used CUDA.

Evidence is retained at `/tmp/letracode-qlora-gpu-final-20260909/proof.json`,
with per-run report, training/conversion logs and adapter checks beneath that
directory. The run ID is `eae6c7db4aa441298cf5e2baf9efc580`.

## Memory comparison

A separate CUDA full-precision LoRA proof used byte-identical generated base
weights and completed training, conversion, adoption, inference and rollback.
Evidence: `/tmp/letracode-lora-cuda-baseline-20260909/proof.json`.

| Measurement (bytes) | Full-precision LoRA | 4-bit QLoRA |
| --- | ---: | ---: |
| Reported base model footprint | 13,755,648 | 1,828,096 |
| Peak CUDA allocation | 35,238,912 | 21,481,984 |
| Peak CUDA reservation | 39,845,888 | 27,262,976 |

The model API's reported parameter/buffer footprint fell **86.7%** on this
small fixture. This is not a total VRAM estimate: allocator peaks include
training tensors, while driver/context and other processes are outside these
measurements. The two training modes also use different adapter targets and
batch settings, so their peak difference is not an isolated quantization
benchmark. Real model memory depends on architecture, sequence length, rank,
batch size and runtime. These random weights and tiny evaluation set establish
working plumbing, not a useful or smarter assistant.

## Checks and compatibility

New regression coverage verifies strict QLoRA/CUDA configuration, legacy
defaults and saved runs, setup persistence, UI evidence display, native BF16
selection, true token-weighted accumulation equivalence to a full batch,
partial final groups, and report consistency. Training remains offline with
approved immutable snapshots and original local Llama safetensors. Existing
CPU LoRA, chat thinking, two-model conversations, adoption and rollback remain
available.

The selected converter's dependency file includes CPU Torch and older version
constraints. CUDA PyTorch was installed explicitly before compatible converter
dependencies, and CUDA was verified afterwards. Setup guidance is in
[Fine-Tuning](FINE-TUNING.md). This upgrade does not expand the training
architecture boundary to Qwen, MoE or multimodal models.

FP16 gradient-scale overflow recovery was independently reviewed and tested.
The trainer retries the same examples at a lower scale, counts only applied
updates, and fails explicitly after bounded unsuccessful retries. All **27**
backend tests passed in the actual CUDA training environment; the independent
review found no remaining issues.

The final full desktop suite passed **940 tests**, with **19 skips** (native
Windows or optional training dependencies) and six existing Python 3.14 fork
warnings, in 58.29 seconds. All 14 release/installer/document checks passed.
The 0.5.0 source archives, Fedora self-extracting installer, RPM and source RPM
built successfully. Native Windows QLoRA execution was not available on this
Linux host; the GitHub workflow provides Windows app/package checks.

## Installed app update

The managed per-user launcher now reports **LetraCode 0.5.0** and loads
`~/.local/share/letracode-app`. All **33 Python modules** were verified byte for
byte against the source checkout. The app was closed before replacement. All
**375 original data files** were unchanged through installation.

The installed UI was opened against copied data: QLoRA settings, the discovered
CUDA training Python, Fine-Tuning and two-model controls were present; projects,
chats, messages and links were unchanged and no inference started. The actual
user launcher was then reopened. The prior installation, data copy, manifests,
UI screenshot and launch log are retained at
`/tmp/letracode-0.5.0-installed-upgrade-_1npk415`. The training environment is
persistent under `~/.local/share/letracode-training-qlora`.
