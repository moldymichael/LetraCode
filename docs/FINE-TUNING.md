# Fine-Tuning and chat thinking

LetraCode 0.4.0 adds a separate Fine-Tuning workspace to the current Strand app.
Its adapters apply to the configured local chat model, including conversations
using Strand identity, Memory and action tools. Model training and file-based
Memory are separate: saving a note never trains weights.

## Prepare a training environment

Training requires a separate Python environment with PyTorch, Transformers,
PEFT, safetensors and sentencepiece. The desktop app does not install these
packages or download models when training starts. For a CPU environment, from
an extracted source tree:

```bash
python3 -m venv ~/letracode-training
~/letracode-training/bin/python -m pip install -r packaging/training-requirements.txt
```

On Windows, install a native Python supported by the chosen PyTorch build, then
use PowerShell from the source tree or extracted portable app:

```powershell
py -m venv "$env:USERPROFILE\letracode-training"
& "$env:USERPROFILE\letracode-training\Scripts\python.exe" -m pip install -r packaging/training-requirements.txt
```

Package installation requires internet access. Training itself uses local files
and offline mode. For CUDA, install a PyTorch build compatible with your GPU and
driver in that same environment before selecting `cuda`. The default is CPU.
The packaged desktop runtime remains separate from the training environment.

Choose the environment's Python executable, not LetraCode.exe or pythonw.exe.
The training model folder must contain original **unquantized, text-only Llama**
weights in safetensors format, config.json, tokenizer files and a chat template.
The tokenizer must support a consistent user/assistant generation prefix.
Remote custom code is disabled. Qwen, Mistral, Gemma, multimodal, quantized and
adapter-only training folders are rejected by this first backend. They may
still be used as normal GGUF chat models when supported by llama.cpp.

The trainer uses float32 base weights and LoRA adapters on q_proj/v_proj. Base
weights alone need approximately four bytes per parameter, plus activations,
optimizer state and runtime memory. Start with a small model. Running a large
quantized GGUF in chat does not establish that its original weights fit for
training. Out-of-memory failures preserve the failed run and logs.

## Review examples

1. Open **Fine-Tuning → Examples**. Write a prompt and the desired response,
   or choose **Learn from reply…** in Chat to prepare a draft for correction.
2. Choose **Training example** or **Held-out evaluation**. Save and review each
   example, then choose **Approve example**. Editing approved text requires
   review again. Drafts are excluded from runs.
3. Keep evaluation prompts separate from training prompts. Duplicate prompts,
   ignoring case and repeated whitespace, are refused when creating a run.

Import JSONL files up to 16 MiB and 10,000 rows. Each row may contain prompt and
response, or exactly one user message followed by one assistant message. All
imports start as drafts, including files that contain an approved flag.

```json
{"prompt":"How should you handle an uncertain fact?","response":"State the uncertainty and check an appropriate source.","split":"train"}
{"prompt":"What should you do when evidence conflicts?","response":"Explain the disagreement and compare the sources.","split":"eval"}
```

The simple dataset format does not train multi-turn conversations, system
instructions or tool-call records. A copied chat reply is a starting point for
review, not proof that the answer is correct.

## Train, compare and adopt

In **Train**, select the training Python and model folder. For conversion and
adoption, also select the matching base GGUF and a local llama.cpp source folder
containing `convert_lora_to_gguf.py`. Install that checkout's conversion
requirements in the training environment; use its own requirements documentation.
The GGUF must derive from the same original model weights. A compatible shape
alone cannot establish that two models have matching weights.

Review epochs, rank, length, batch size, seed, learning rate and device, select
the review checkbox, and choose **Start local fine-tuning**. Each run snapshots
approved training/evaluation examples and its configuration. Overlong examples
fail with an explanation rather than silently dropping response tokens.
Inference is unloaded during training, and chat/model changes wait for the job.
**Stop** terminates the owned training process tree. Reopening marks unfinished
runs interrupted; it never automatically restarts training.

**Versions & evaluation** shows status, base and candidate response loss,
sample outputs and provenance. Loss excludes prompt tokens and is measured on
the same held-out responses before and after optimization. Lower loss on that
set is narrow evidence; compare sample answers and test real Strand tasks.
The report includes package versions, dataset/model/adapter hashes and the
number of optimization steps. **Open run folder** exposes logs and artifacts.

Conversion failure keeps the PEFT adapter and comparison report, but disables
adoption. After a successful conversion, choose **Adopt selected version**.
LetraCode verifies the recorded base/adapter hashes and loads them with the
configured llama-server before saving the new engine settings. A failed load
leaves the previous saved settings intact. **Roll back to previous model**
restores the last configuration. Previous weights and adapters remain on disk.
Model Setup also supports selecting or clearing an optional GGUF LoRA adapter.

## Thinking in regular chat

Select **Thinking** to request reasoning from a model whose chat template
supports it. When llama-server emits `reasoning_content` or `reasoning`, it
appears live in a separate Thinking block. Leading `<think>…</think>` content
is also separated. Completed thinking is collapsed and can be expanded with
**Show thinking**. It survives app restart, including partial text on interruption.

This displays text actually returned by the local model. It cannot reveal
internal computation that the model does not emit. Thinking does not enable
tools or change action approval rules. Plain answers remain usable when the
model has no reasoning support. Answer copying, Markdown/evaluation exports
and subsequent prompts exclude the separate reasoning text; full backups keep
it as saved local message metadata.

See the upstream [llama.cpp server documentation](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)
for model-template and reasoning behavior, and the
[PEFT LoRA documentation](https://huggingface.co/docs/peft/en/conceptual_guides/lora)
for the adapter method.

## Data and verification

Application backups include training examples, saved editor drafts, frozen run
datasets, configuration and results in SQLite/JSON. Back up the data folder's
`training/` directory separately for adapters, converted files and logs; original
training weights and base GGUF files are also separate. Restoring database
records does not restore missing weights or restart jobs.

Read the [verification record](FINE-TUNING-VERIFICATION.md) for automated tests
and the tiny local model proof. That fixture establishes working optimization,
conversion and inference plumbing; it is not evidence of useful model quality.
