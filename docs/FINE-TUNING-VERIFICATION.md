# Local fine-tuning verification

Verified on September 9, 2026 in the current development worktree. These checks
exercise actual optimization and local inference using a generated random model.
They do not demonstrate a useful assistant or an improvement in task performance.
No user models, conversations or training data were used. No packages or models
were downloaded during verification.

## Complete application verification

On the final integrated 0.4.0 source tree:

- `QT_QPA_PLATFORM=offscreen python3 -m pytest -q`: **812 passed, 16 skipped,
  6 warnings in 49.45 seconds**. Skips cover platform-specific tests and the
  optional real-training test separately exercised below. The six warnings
  concern Python 3.14 forking a process after Qt threads were initialized.
- `python3 -m compileall -q letracode packaging tools`, shell syntax checks and
  `git diff --check`: passed.
- `packaging/build-rpm.sh`: built the 0.4.0 Fedora noarch RPM, source RPM,
  source ZIP/tarball and self-extracting installer; packaged version check passed.
- Actual Qt screenshots inspected at 1260×820 and 850×570: chat reasoning,
  example editor and scrollable training settings fit without clipped controls.
- Independent whole-change review and a scoped re-review of shutdown/metadata
  fixes found no remaining important or critical issues. Scoped checks passed
  23 regressions; the broader independent check passed 125 tests with one optional
  dependency skip, then all 16 backend tests in the separate training environment.

Native Windows packaging is configured to include the standalone training script
and requirements, and the installer smoke checks execute that script's help
entry point with a separate Python. Native Windows CI is distinct from these
local Fedora results; local success does not establish a Windows run.

## Reproduce the real pipeline

Run [tools/verify-local-training.py](../tools/verify-local-training.py) with the
desktop Python, an existing separate training environment and a compatible local
llama.cpp checkout. The output path must not exist. The script creates the tiny
model and tokenizer, converts its base to GGUF, creates approved examples in a
temporary app database, runs the real `TrainingWorker`, verifies updated adapter
weights, converts to GGUF, loads the adapter through `ActivationWorker`, streams
inference, and loads the original model again without the adapter. Owned engines
are stopped afterwards. The selected training environment needs the dependencies
described in [Fine-Tuning setup](FINE-TUNING.md), including SentencePiece and the
llama.cpp conversion requirements.

The exact successful command was:

```bash
python3 tools/verify-local-training.py \
  --training-python /tmp/letracode-training-proof-env/bin/python \
  --llama-cpp /home/miceoil/llama.cpp-cuda \
  --llama-server /home/miceoil/.local/bin/llama-server \
  --output /tmp/letracode-fine-tuning-current-proof-20260909-2
```

The script exited successfully. Evidence is retained under the output directory:
`proof.json`, `fixture.log`, `base-conversion.log`, `adapter-check.json`, and the
app run folder `data/training/runs/40928514120948a5a5cea9170d104378/`. That run folder
contains immutable input snapshots, `training.log`, `conversion.log`, `report.json`,
the PEFT adapter and the converted `adapter.gguf`. These temporary artifacts may
be removed by normal system cleanup; the script remains in the repository.

| Observation | Result |
| --- | --- |
| Actual optimization steps | 8 |
| Approved training / held-out examples | 2 / 2 |
| Held-out assistant response tokens | 11 |
| Base held-out response loss | 4.091680526733398 |
| Candidate held-out response loss | 3.8879194259643555 |
| Original safetensors weights | Unchanged |
| PEFT adapter | Preserved; nonzero LoRA B weights verified |
| GGUF adapter conversion | Completed |
| Candidate inference | Loaded with `--lora` and returned text |
| Rollback | Base model loaded without the adapter |

The converted adapter SHA-256 was
`223fbf17fab486f6debe5082f3e989cf971e5fde736983f24bb35fc2e474cdf3`.
The output text was meaningless, as expected for random tiny weights. Other
runs can reach the output token limit; the proof records that explicitly when
streamed text was produced. Loss values concern only this fixture's held-out
responses and do not establish general model quality.

## Runtime

- Fedora, Python 3.14.7; the desktop Qt runtime remained separate.
- Training: PyTorch `2.14.0+cpu`, Transformers `5.16.1`, PEFT `0.20.0`,
  safetensors `0.8.0`.
- Inference/conversion: local llama.cpp build 10868, commit `304665fe7`,
  `llama-server` version `0.4.0-dev`.
- Training and inference used CPU. The machine also has an NVIDIA RTX 2060 SUPER
  with 8 GiB VRAM, but the selected training Python has CPU-only PyTorch.
  CUDA training and native Windows execution are not established by this proof.

## Focused regression checks

```bash
QT_QPA_PLATFORM=offscreen python3 -m pytest -q \
  tests/test_training.py tests/test_training_worker.py \
  tests/test_training_backend.py tests/test_training_adoption.py
```

Result: **60 passed, 1 skipped in 1.70 seconds**. The skipped check requires the
optional training dependencies absent from the desktop Python. Running the
backend suite in the separate training environment exercised that check:

```bash
/tmp/letracode-training-proof-env/bin/python -m pytest -q tests/test_training_backend.py
```

Result: **16 passed in 6.71 seconds**. Coverage includes response-only loss,
token limits, normalized duplicate prompts, supported local model validation,
real adapter updates, unchanged base weights, input snapshot integrity,
configuration bounds, report correspondence with approved evaluation data,
cancellation, descendant cleanup, changed model rejection and rollback.

Regression failures were observed before the corresponding fixes. In particular,
the worker previously waited indefinitely when an exited trainer left a child
holding stdout; bounded output draining now monitors the trainer and stops its
remaining owned process group. Edited snapshots are rejected before launch and
before a completed report can be accepted. Reopening marks queued or running
runs as interrupted and never restarts optimization automatically.
