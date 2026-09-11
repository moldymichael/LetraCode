# Current 0.6.0 workflow

The interface is now **Improve → Examples → Prepare and train → Compare and choose**.
Start with the [beginner guide](STRAND-EXPERIENCE.md). Preparation runs before Chat
is unloaded, advanced configuration is collapsed, failed conversion can be
retried from retained artifacts, and adoption requires a current Chat-runtime
comparison and saved judgment. The technical training requirements below still
apply; older names and measured runs describe the previous implementation.

# Fine-Tuning and chat thinking

LetraCode 0.5.0 adds CUDA 4-bit QLoRA to the Fine-Tuning workspace introduced in 0.4.0.
Its adapters apply to the configured local chat model, including conversations
using Strand identity, Memory and action tools. Model training and file-based
Memory are separate: saving a note never trains weights.

The installed two-model workflow is retained. An adopted adapter applies to
the primary model (Model A) only; Model B remains unadapted. Each speaker's
emitted thinking stays with its own saved reply. Training blocks both Send and
Continue exchange until the job ends. Single-model adoption checks the primary
model without loading the optional second model, while preserving that selection.

## Prepare a training environment

Training requires a separate Python environment with PyTorch, Transformers,
PEFT, accelerate, bitsandbytes, safetensors and sentencepiece. The desktop app
does not install these packages or download models when training starts. Create
an environment from an extracted source tree:

```bash
python3 -m venv ~/letracode-training
# First install the CUDA PyTorch build appropriate for your GPU and driver.
~/letracode-training/bin/python -m pip install -r packaging/training-requirements.txt
```

On Windows, install a native Python supported by the chosen PyTorch build, then
use PowerShell from the source tree or extracted portable app:

```powershell
py -m venv "$env:USERPROFILE\letracode-training"
& "$env:USERPROFILE\letracode-training\Scripts\python.exe" -m pip install -r packaging/training-requirements.txt
```

Package installation requires internet access. Training itself uses local files
and offline mode. Follow the [PyTorch installer](https://pytorch.org/get-started/locally/)
for a CUDA build compatible with your GPU and driver, then install the remaining
requirements in that environment. New configurations recommend **4-bit QLoRA
(NVIDIA GPU)**; previously saved full-precision settings retain their meaning.
Ordinary LoRA supports CPU or CUDA. QLoRA requires CUDA and fails with a clear
message if unavailable; it never silently changes training method.

The packaged desktop runtime remains separate. On Linux, an existing
`~/.local/share/letracode-training-qlora/bin/python` is offered automatically
when no training Python was saved. Some llama.cpp conversion requirements select
a CPU Torch build or incompatible older dependencies. Review their constraints
before installation and verify `torch.cuda.is_available()` afterwards.

Choose the environment's Python executable, not LetraCode.exe or pythonw.exe.
The training model folder must contain original **unquantized, text-only Llama**
weights in safetensors format, config.json, tokenizer files and a chat template.
The tokenizer must support a consistent user/assistant generation prefix.
Remote custom code is disabled. Qwen, Mistral, Gemma, multimodal, quantized and
adapter-only training folders are rejected by this first backend. They may
still be used as normal GGUF chat models when supported by llama.cpp.

**4-bit QLoRA** loads those original weights into NF4 with double quantization,
freezes the base, and trains adapters on the model's linear layers. Native BF16
compute is used when the GPU supports it; otherwise FP16 with gradient scaling
is used, including on the RTX 2060 SUPER. Gradient checkpointing trades extra
computation for lower activation memory. Accumulation combines several small
batches into an optimizer update, weighted by supervised response tokens.
The final partial group is included. These choices follow the
[Transformers bitsandbytes guide](https://huggingface.co/docs/transformers/quantization/bitsandbytes)
and [PEFT QLoRA guide](https://huggingface.co/docs/peft/developer_guides/quantization).

**LoRA (full precision)** retains float32 base weights and q_proj/v_proj adapters.
Base weights alone use approximately four bytes per parameter. In QLoRA,
quantized linear weights use about half a byte per parameter plus quantization
metadata; embeddings, adapters, activations and runtime buffers also need memory.
Actual whole-model savings depend on architecture and settings. Start with
microbatch 1, accumulation 4, length 512 and rank 8. Running a large GGUF in chat
does not establish that its training weights fit this GPU. A 35B model's nominal
4-bit weights alone exceed 8 GiB. Failed runs and logs remain available.

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

In **Prepare and train**, select the training Python and model folder. For conversion and
adoption, also select the matching base GGUF and a local llama.cpp source folder
containing `convert_lora_to_gguf.py`. Install that checkout's conversion
requirements in the training environment; use its own requirements documentation.
The GGUF must derive from the same original model weights. A compatible shape
alone cannot establish that two models have matching weights.

Review method, epochs, rank, length, microbatch, accumulation, checkpointing,
seed, learning rate and device, select
the review checkbox, and choose **Check preparation and train**. Each run snapshots
approved training/evaluation examples and its configuration. Overlong examples
fail with an explanation rather than silently dropping response tokens.
Inference is unloaded during training, and chat/model changes wait for the job.
**Stop** terminates the owned training process tree. Reopening marks unfinished
runs interrupted; it never automatically restarts training.

**Compare and choose → Show technical training report** shows status, base and candidate response loss,
sample outputs and provenance. Loss excludes prompt tokens and is measured on
the same held-out responses before and after optimization. Lower loss on that
set is narrow evidence; compare sample answers and test real Strand tasks.
In QLoRA both baseline and candidate use the same quantized base; the baseline
is not an unquantized-model benchmark. The report includes training precision,
effective batch, model footprint, peak PyTorch CUDA allocated/reserved memory,
package versions, dataset/model/adapter hashes and optimization steps. CUDA
figures cover this training process's allocator, not total system GPU usage.
QLoRA makes training more memory efficient; better answers still depend on the
base model, reviewed examples and evaluation. **Open run folder** exposes logs
and artifacts. The optimizer's immutable `report.json` contains only the first
three held-out generated samples with a short output budget. It is not the record
of later comparisons against the currently configured Strand.

After conversion, choose **Compare with Strand**. The window opens before any model
job starts. Add fresh, self-contained questions (optional reference answers), then
choose **Run comparison**. Original held-out questions remain included; fresh
questions are checked against the run's frozen teaching data and never added to
training examples. Both versions receive identical independent prompts using the
current Chat settings, without shared notes, project context or tools. Select a
question to read the full responses side by side, including separately recorded
Thinking. Progress and errors are visible; **Stop** preserves partial answers.

Save per-question ratings and your overall judgment and notes. Reruns retain earlier
attempts, and editing the question set requires a new comparison for adoption.
**Export comparison…** writes the full saved answers, partial failures, history,
review and run information to a new JSON file. It contains full text and local paths,
so inspect it before sharing. It does not alter the frozen training report.

Conversion failure keeps the PEFT adapter and comparison report, but disables
adoption. After a complete current comparison and saved judgment, choose **Use this version**.
LetraCode verifies the recorded base/adapter hashes and loads them with the
configured llama-server before saving the new engine settings. A failed load
leaves the previous saved settings intact. **Restore previous version**
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
See [QLoRA verification](QLORA-VERIFICATION.md) for the CUDA upgrade evidence.
