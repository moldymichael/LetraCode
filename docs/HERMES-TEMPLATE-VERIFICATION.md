# Hermes template compatibility — 2026-09-09

## Actual request and cause

The installed app matches this worktree, `LetraCode-fine-tuning-current` at
`7c62e95` before this fix. The repository anchor `Projects/LetraCode` predates
the budget endpoint and was left untouched.

The saved six-question JavaScript benchmark is message 1189 in chat
`40d1ae954ba84509b15731e29478fbc3`. Its prompt says not to use tools, but Actions,
Computer and Internet were enabled. The real worker therefore sent all 14 tool
definitions to `/apply-template`, with `parallel_tool_calls: false` and
`chat_template_kwargs: {"enable_thinking": false}`. Prompt prose does not remove
tools from the request.

The selected GGUF is `Hermes-3-Llama-3.1-8B-f16.gguf`, whose metadata contains
separate default and `tool_use` templates. llama.cpp build 10868, commit
`304665fe7`, selects `tool_use` when tools are nonempty. Hermes's macro evaluates
`basic_type_map[json_spec.type]` before its union branch. The actual
`write_file.expected_sha256` schema supplied `type: ["string", "null"]`, causing
the unhashable Array error inside the parser-generation wrapper. This is an
actual request property, not an invented parser-probe schema.

Live isolation checks before changing production code:

| Request | `/apply-template` result |
| --- | --- |
| Original 14 tools | HTTP 400, unhashable Array |
| Tools omitted | HTTP 200, default template |
| Original tools plus `tool_choice: "none"` | Same HTTP 400 |
| Only nullable property rewritten with `anyOf` | HTTP 200, `tool_use` template |
| Original tools except `write_file` | HTTP 200 |

## Minimal fix

Only `write_file.expected_sha256` changes from `{"type":["string","null"]}` to
`{"anyOf":[{"type":"string"},{"type":"null"}]}`. These accept the same values
under [JSON Schema's anyOf rule](https://json-schema.org/understanding-json-schema/reference/combining#anyof).
The required argument, hash validation, null-only creation, approval, backup
and stale-file protections remain intact. Tool selection, templates, budgeting
errors, model configuration and benchmark text are unchanged.

The same small source change was applied to the matching installed
`~/.local/share/letracode-app/letracode/tools.py`, after preserving its original.

## Verification

- The real-runtime regression failed before the fix with the reported HTTP 400;
  afterward it passed in 22.75 seconds. It checks exact runtime budgeting with
  and without all tools, then a complete ordinary streamed reply with tools
  available and no tool calls.
- Full suite: **940 passed, 20 skipped, 6 warnings** in 71.67 seconds. Skips
  include the opt-in Hermes test in the normal suite; warnings concern existing
  threaded process-fork tests. Existing guarded file-write and tool-stream tests
  passed. The opt-in regression was run separately against the real model.
- The unchanged benchmark was replayed through `ConversationWorker`, with its
  saved project/Memory context in isolated data copies. Baseline: budget error
  and zero generation. Fixed with Actions on: both template requests and
  tokenization succeeded, then 171 content chunks / 543 characters streamed.
  The installed app with Actions off likewise reached generation and streamed
  32 chunks / 108 characters. Both were stopped intentionally after proving
  generation; this is not a completed or scored six-answer benchmark.
- Configuration stayed at context 53248, GPU layers 5, threads 8, maximum reply
  3072 and temperature 0.7, with no LoRA. Single-model mode clears the saved
  secondary path exactly as the UI does. No alternative model was loaded.
- The installed app also generated and parsed real Hermes `write_file` calls
  for both `expected_sha256: null` and a 64-character hash, preserving the exact
  requested arguments. These were generation-only checks: neither call was
  executed and neither target file was created.

Benchmark: 1793 bytes, SHA-256
`8fc34a242b40dc554ccc5620616a0fbbb343ae1785c758f9fd399c72541a0778`.
Model size and modification time were checked unchanged. User chat data and
saved Actions settings were not modified.

Run the opt-in regression from this worktree with an EngineConfig JSON file:

```bash
LETRACODE_HERMES_TEST_CONFIG=/path/to/engine-config.json python3 -m pytest -q tests/test_hermes_runtime.py
QT_QPA_PLATFORM=offscreen python3 -m pytest -q
```

Local request captures, observer scripts, configuration, regression logs,
benchmark streams and original installed source are retained in
`/home/miceoil/Projects/artifacts/evidence/hermes-template-2026-09-09/`.
The directory is private because the worker captures include saved context.

Review found no actionable issues. Other model families were inspected at
their template/parser boundary and covered by existing protocol tests, not
rerun with real model inference. Some templates abbreviate union types poorly;
the request schema and LetraCode's execution guards retain the nullable contract.
