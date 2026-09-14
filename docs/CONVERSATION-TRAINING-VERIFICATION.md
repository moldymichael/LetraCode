# Conversation training verification — September 14, 2026

This record covers `codex/conversation-training`, based on `8772a19`, in the
isolated `LetraCode-conversation-training` checkout. The canonical source and
installed application were not replaced during these checks. Test data and
random model artifacts used disposable directories. No user training data or
model weights were modified.

## Baseline

On Fedora with system Python 3.14 and offscreen Qt:

```bash
QT_QPA_PLATFORM=offscreen python3 -m pytest -q
```

Unchanged baseline: **1,156 passed, 22 skipped, six existing multiprocessing-fork
warnings**, 75.89 seconds. Skips include platform/dependency-specific checks.

## Native tokenization and optimization

The separate training environment contains Torch `2.11.0+cu126`, Transformers
`5.17.0`, PEFT `0.20.0`, safetensors `0.8.0`. Real local Hermes 3 Llama 3.1 8B
and Gemma 4 E2B tokenizers were exercised without loading their full model weights.
Assertions inspect complete native tokens, assistant loss labels, context-only
turns, tool results, unsafe templates and whole-example overflow.

The production-worker proof used:

```bash
QT_QPA_PLATFORM=offscreen python3 tools/verify-conversation-training.py \
  --training-python /home/miceoil/.local/share/letracode-training-qlora/bin/python \
  --tokenizer /home/miceoil/bigdrive/Jan/llamacpp/models/Hermes-3-Llama-3.1-8B-HF \
  --llama-cpp /home/miceoil/llama.cpp-cuda \
  --output /tmp/letracode-conversation-proof-20260914-v4
```

The script imported/exported complete structured examples, froze a repository
run, converted a matching base GGUF, passed production `check_readiness`,
launched the actual `TrainingWorker` and backend, verified the returned
report, and inspected adapter tensors and token labels. It used a random
one-layer Llama with the real local Hermes tokenizer, CPU float32 LoRA, two
epochs and gradient accumulation of two. The existing running chat model was
not unloaded.

Observed results:

- Preparation reported ready with the real runtime, native tokenizer, examples
  and converter.
- Two optimizer steps, finite held-out loss from **11.7685871** to **11.7647247**.
- Each of the three examples had **43 supervised tokens**; complete sequence
  lengths were **362, 362, 363**. Assistant prose, follow-up prose and tool-call
  arguments contributed labels. User text, recorded tool-result text and the
  context-only assistant text did not.
- **256 nonzero LoRA B values**; the original model weight hash stayed unchanged.
- GGUF adapter conversion succeeded. Adapter SHA-256:
  `ae7f7821d7f48bc1d4cf1e0df93ac143bd7eaa6ad83fe5085ff882ace7bdf8e6`.
- Native tool template SHA-256:
  `7ce09d55d3690e06c70b5c07228cc7e8c99c43a23cdc027762be4011efcdea6c`.
- Full structured report verification succeeded; shortening the token limit by
  one rejected each example whole. No tools were executed.

The first proof attempt failed before model loading because a field-preservation
probe generated an invalid JSON Schema type for the Hermes template. That defect
was fixed and covered by regression tests before the successful fresh run.
An intermediate verification-script run also caught a Path-to-JSON serialization
error in its readiness artifact; the helper was corrected before the final v4 run.

The generated weights establish working optimization and conversion plumbing.
They are not useful assistant weights and the small loss change is not evidence
of better Strand behavior. Full-size Gemma/Hermes optimization, native Windows
training, native Windows desktop checks and real-engine adoption/inference were
not performed for this change.

## Editor and review

Offscreen Qt checks cover simple examples, multiple function calls, collapsed
results, linking, reordering, optional strict/name metadata, context-only flags,
invalid-JSON draft recovery, saving, import/export and approval invalidation.
Screenshots of the simple editor, calls and schemas were rendered and visually
inspected at 1400 × 1050. This is offscreen visual QA, not a native desktop smoke
test.

Independent review identified and corrected stale approval on selecting a
recovered non-active draft and a comparison-version compatibility bypass.
Regressions require structured comparisons to retain their exact context and
completion evidence. Candidate comparisons issue one generation per selected
held-out target/version and retain generated calls without executing them.

During development, an initial combined full-suite run finished with **1 failed,
1,210 passed, 32 skipped and six warnings**. The failure was the source-archive
link check: this verification file had not yet been created when the archive was
assembled. After adding it, `python3 -m pytest tests/test_release.py
tests/test_release_docs.py -q` passed **all six checks**. Final whole-change checks
are recorded below when complete.


Final native-runtime command:

```bash
/home/miceoil/.local/share/letracode-training-qlora/bin/python -m pytest -q \
  tests/test_training_conversations_backend.py tests/test_training_backend.py \
  tests/test_gemma_backend.py
```

**74 passed in 18.19 seconds**, including real native Hermes/Gemma tokenizers,
actual tiny optimization, scalar/key preservation, multi-turn masks, ambiguous
linkage rejection and a bounded-metadata regression covering 10,000 examples.
Independent re-review confirmed the template/masking fixes with **15 passed**.
The final whole-change review's named-template and report-capacity findings were
also corrected and re-reviewed. Per-example metadata previews are bounded, and
totals account for every example without reducing the frozen datasets.

## Final Fedora checks

On **Fedora 44, Python 3.14.7**, as the ordinary desktop user:

```bash
QT_QPA_PLATFORM=offscreen python3 -m pytest -q
python3 -m compileall -q letracode packaging tools
bash -n install.sh uninstall.sh packaging/build-rpm.sh
git diff --check
```

Full suite: **1,216 passed, 48 skipped, six existing multiprocessing-fork
warnings**, 79.15 seconds, no failures. The system Python lacks optional training
packages; the separate 74-pass training-runtime suite above exercises those
backend/native-tokenizer tests. Native Windows-only checks remain skipped here.
Compileall, shell syntax and diff checks passed. After the final documentation
edits, the six source-archive/link/reproducibility tests also passed.

No Windows installer/RPM build, release publication, live installation or live
data migration was performed. Required remote CI and the other teammate's
approval remain separate from this local evidence.
