# Teach Strand and try a trained version

Open **Improve** and follow **1. Examples → 2. Set up and train →
3. Compare and choose**. You write examples of the responses you want, train a
trial version, then compare its answers before deciding whether to use it.
Saving examples does not start training or change your current Strand.

A **teaching example** is used to learn. A **comparison example** is kept out of
training so you can test the result; technical reports call it *held out*. The
trained trial version is also called a *candidate*.

## Try a first lesson

1. In **1. Examples**, write both fields. For **You say**, try “Explain a metaphor
   to someone new to poetry.” For **Strand should respond**, write “A metaphor
   describes one thing as another. ‘Time is a river’ suggests it keeps moving
   forward.” Strand does not generate the answer in this editor. **Show an
   example** opens a read-only illustration without replacing your work.
2. Leave **Use this to teach Strand** selected, review your wording, then choose
   **Save and approve**. Use **Save draft** instead when you are still editing;
   drafts are excluded from training and testing.
3. Choose **New example**. Write a different request, such as “Explain a simile
   to someone new to poetry,” and the reference answer you want, such as “A simile
   compares things using ‘like’ or ‘as.’ ‘The moon is like a lantern’ compares
   their light.” Select **Use this to test Strand**, then **Save and approve**.
4. Choose **Next: set up training →**. Setup shows the approved counts; it needs
   at least one teaching example and one different comparison example. This
   small lesson demonstrates the workflow.

**Add a follow-up** adds another request-and-response pair to the conversation.
**More conversation options** reveals individual messages, background-only
assistant turns, system instructions and recorded tools. See
[conversation training](CONVERSATION-TRAINING.md) for those options and JSONL.

Training needs local model files and a separate Python environment. If yours
are already installed, go to [set up and train](#set-up-and-train); otherwise
follow the environment instructions below.

## Training and regular Chat

CUDA 4-bit QLoRA was introduced in 0.5.0, following the 0.4.0 Fine-Tuning workspace.
The older verification records retain those version and interface names.
Trained adapters apply to the configured local chat model, including conversations
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
requirements in that environment. New configurations recommend **NVIDIA GPU ·
use less memory (QLoRA)**; previously saved full-precision settings retain their meaning.
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
The tokenizer must provide a verifiable native chat/tool template.
Remote custom code is disabled. Besides the supported original Gemma 4 E2B/E4B
path, other Gemma, Qwen, Mistral, quantized and adapter-only training folders are rejected. They may
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

**CPU or GPU · full precision (LoRA)** retains float32 base weights and q_proj/v_proj adapters.
Base weights alone use approximately four bytes per parameter. In QLoRA,
quantized linear weights use about half a byte per parameter plus quantization
metadata; embeddings, adapters, activations and runtime buffers also need memory.
Actual whole-model savings depend on architecture and settings. Start with
microbatch 1, accumulation 4, length 512 and rank 8. Running a large GGUF in chat
does not establish that its training weights fit this GPU. A 35B model's nominal
4-bit weights alone exceed 8 GiB. Failed runs and logs remain available.

## Review and import examples

Use **Save and approve** after reviewing both sides of an example. Editing its
content or changing whether it teaches or tests Strand requires approval again.
Chat's **Create example** action can prepare a draft from a reply for correction;
review it before using it as a lesson.

Keep comparison examples distinct from teaching examples. Repeated context
leading up to a learned response is refused across or within the approved sets. For a simple exchange,
that means the same question, ignoring case and repeated whitespace. For a
conversation, it includes the prior messages and available tools before the
first assistant turn selected for learning.

Import JSONL files up to 16 MiB and 10,000 rows. Each row may contain prompt and
response, or a [structured conversation with tools](CONVERSATION-TRAINING.md#import-and-export). All
imports start as drafts, including files that contain an approved flag.

```json
{"prompt":"How should you handle an uncertain fact?","response":"State the uncertainty and check an appropriate source.","split":"train"}
{"prompt":"What should you do when evidence conflicts?","response":"Explain the disagreement and compare the sources.","split":"eval"}
```

Use conversation cards or the structured format for multiple turns and tool
exchanges. System/user/tool messages and context-only assistant turns supply
context; selected assistant text and tool calls are optimized. A copied chat
reply is a starting point for review, not proof that the answer is correct.

## Set up and train

In **2. Set up and train**, check **Your examples** and the names shown under
**Model and tools · set up once**. The location fields open automatically when
a required location is missing. Use **Choose or change model and tool locations**
to inspect or change them at any time; these basic choices are separate from
optional training settings.

| Setup field | What to select |
| --- | --- |
| **Original model folder** | The folder with the original `.safetensors` weights, `config.json`, tokenizer files and native chat template. A chat `.gguf` file alone cannot be trained. |
| **Matching chat model** | A `.gguf` made from those same original weights, used to try the trained version in Chat. |
| **Training environment** | The environment's Python executable: for example, `~/letracode-training/bin/python` on Linux or `letracode-training\Scripts\python.exe` on Windows. |
| **Conversion tools folder** | A local llama.cpp source checkout containing `convert_lora_to_gguf.py`; the chat engine executable alone is insufficient. |

Install that checkout's conversion requirements in the training environment;
use its own requirements documentation.
The GGUF must derive from the same original model weights. A compatible shape
alone cannot establish that two models have matching weights.

For supported original Gemma 4 E2B/E4B weights, **Create matching chat model**
can create the matching GGUF locally. This helper is only for that Gemma path;
for Llama, select a matching GGUF. It does not download a model.

Choose **Check setup**. This checks the selected files, training packages,
conversion tools and complete examples without starting training. If a check
fails, its reason appears in the setup result; **Show full check details** opens
the technical details. Correct the reported problem and check again.

After setup passes, review the counts and select **I have reviewed the approved
examples shown above**. This enables **Train a trial version**. Starting training
rechecks setup before optimization; changing the examples or settings requires
another successful check. **Adjust training settings (optional)** contains
CPU/GPU choices, example length, batch size and learning rate.

Each run freezes its approved teaching/comparison examples and configuration. Overlong examples
fail with an explanation rather than silently dropping response tokens.
Inference is unloaded during training, and chat/model changes wait for the job.
**Stop** terminates the owned training process tree. Reopening marks unfinished
runs interrupted; it never automatically restarts training.

## Compare and choose

**3. Compare and choose → Show technical training report** shows status, base and candidate response loss,
sample outputs and provenance. Loss measures the selected assistant turns;
user/system messages, tool results and context-only assistant turns are excluded.
It uses the same held-out conversations before and after optimization. Lower loss on that
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
current Chat settings, without shared notes or project retrieval. Structured
held-out conversations retain their recorded context and tool definitions;
generated calls are displayed without execution. Fresh questions are tool-free. Select a
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
