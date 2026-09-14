# Teaching Strand with conversations

Improve → Examples now edits a complete text conversation. Start with the two
cards for a user request and the desired assistant answer. Add messages for
follow-up questions, assistant answers, function calls and recorded tool results.
Use the arrows to reorder messages. A tool result starts collapsed; expand it to
edit the full text. Available function definitions are editable below the messages.

An assistant card marked **Learn this turn** contributes training targets,
including its function calls. Select **Context only — do not learn this turn**
for an assistant turn that should remain in the context without teaching its
answer or calls. User messages, system instructions, tool definitions and tool
results provide context and never contribute target tokens. At least one
assistant turn must be a teaching target. The same selection determines held-out
loss for conversations kept for comparison.

Save, review, then approve the example. Changing a message, its position, a call,
a result, a tool definition, a target checkbox or the teaching/comparison split
revokes approval. An unfinished edit remains a draft, including incomplete JSON
arguments and schemas, when switching examples or restarting. Collapsing a result
does not change the example. Approval never starts training by itself.

## Tool exchanges

Add an available function with its name, description and JSON parameter schema.
On an assistant card, add a function call, select/write its function name and
supply its arguments as a JSON object. Each call has an editable ID. Add a tool
message and select that ID in **Result for call ID**. Its body is the recorded
result, including an error result if that is the example you want to teach.

Multiple calls in one assistant message are supported. Each call needs exactly
one result, linked by ID, before the conversation continues. Results can arrive
in a different order from the calls in storage and the editor. This encoder requires the result cards in call
order for training so native formats that omit call IDs preserve the connection; reorder the results explicitly when
prompted. Duplicate call IDs, unknown functions,
missing results, repeated results and orphan results are rejected. Ordinary user
and assistant messages need not alternate. An optional system message comes
first. The selected model's own template may impose additional sequence limits;
preparation reports those rather than rewriting the conversation.

These definitions and records are data. Creating examples, preparation, training
and candidate comparison do not execute recorded or generated functions.

## Import and export

JSONL imports retain their existing 16 MiB / 10,000-row limits and are atomic:
one invalid row prevents the entire import. Every imported example starts
unapproved. Legacy `prompt` / `response` rows still work. Existing examples,
saved pair drafts and frozen historical runs remain readable.

Structured rows use `schema_version: 2`, `messages` and `tools`. The `train` flag
is optional on assistant messages and defaults to `true`; it is not allowed on
other roles. Function arguments may be an object or a JSON object string on
input; they are saved/exported as objects. Text is preserved, including multiline
results. Unsupported fields and non-text message content are rejected.

The following is one JSONL row, formatted across lines here for readability:

```json
{
  "schema_version": 2,
  "split": "train",
  "tools": [{
    "type": "function",
    "function": {
      "name": "read_file",
      "description": "Read a file from the example workspace.",
      "parameters": {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"]
      }
    }
  }],
  "messages": [
    {"role": "user", "content": "Read the title in poem.txt."},
    {"role": "assistant", "content": "", "tool_calls": [{
      "id": "read_1", "type": "function",
      "function": {"name": "read_file", "arguments": {"path": "poem.txt"}}
    }]},
    {"role": "tool", "tool_call_id": "read_1", "content": "Title: Rain at Dawn"},
    {"role": "assistant", "content": "The title is Rain at Dawn."},
    {"role": "user", "content": "Remember that the title is provisional."},
    {"role": "assistant", "content": "Understood: it is provisional.", "train": false},
    {"role": "user", "content": "How should I refer to it?"},
    {"role": "assistant", "content": "Use Rain at Dawn as the working title."}
  ]
}
```

`source` may carry a short provenance note. Optional tool-result `name`, function
`description` and function `strict` metadata survive editing and export. Function
parameters must be an object schema. This version does not support images,
audio, arbitrary message metadata or reasoning-channel annotations.

The existing Chat **Create example** action still creates a simple draft from
the selected reply and editable background. It does not reconstruct exact tool
schemas from old chat receipts. Use the conversation editor or structured JSONL
for complete recorded tool conversations.

## Preparation and real training

Use **Prepare and train → Check preparation** with the matching local original
model and tokenizer. The model family restrictions remain those in the
[training guide](FINE-TUNING.md): text-only Llama and the supported Gemma 4
E2B/E4B path. Training dependencies and model files are separate from the desktop
application. No model download is performed when a run starts.

Preparation and optimization share the same encoder. It renders the full native
chat/tool template, including available functions and all recorded turns. It
verifies assistant token boundaries and excludes tool results even when a native
template embeds them in an assistant block. The native template determines which
transport metadata is rendered; stored call/result IDs are always retained and
validated. Template identity and the masking strategy are recorded as evidence.

Supported native boundary checks include ChatML-style templates such as Hermes,
Gemma's embedded tool exchanges, and templates with provably stable generation
prefixes. A template that ignores tool data, uses ambiguous boundaries, encounters
literal role delimiters in supplied text, or changes earlier tokens in an
unverifiable way is refused. There is no generic replacement template or silent
conversion to a prompt/response pair. Change the example or choose a matching
supported tokenizer when preparation reports a format error.

A conversation exceeding the configured sequence length or the model's position
limit fails whole, with its measured length. Increase the limit within the model
and memory constraints, or edit and review the complete example. Training never
silently slices off a result, answer or end marker. Native assistant end-of-turn
and call terminators contribute loss where they belong to a teaching turn.

## Review the candidate

Runs freeze full structured teaching and held-out datasets. Held-out loss covers
all teaching-target assistant tokens. The short optimizer previews and **Compare
with Strand** generate the first teaching-target assistant turn of each held-out
conversation using its full preceding context and tool definitions. Later targets
contribute to loss but are not a simulated interactive tool rollout. Generated
calls are visible for review, and no calls are executed. Fresh comparison
questions remain independent, tool-free questions.

Comparison records and exports retain the target, prior messages, definitions
and generated calls. Changes to that context invalidate comparison eligibility.
Normal explicit adoption, conversion recovery and rollback checks still apply.
A lower loss on a small set does not establish better answers or tool selection.

## Verification

[Conversation training verification](CONVERSATION-TRAINING-VERIFICATION.md)
records the exact local commands and their scope. The reusable
[`verify-conversation-training.py`](../tools/verify-conversation-training.py)
creates random tiny Llama weights with an existing local tokenizer, imports and
exports structured examples, trains through the production worker, verifies
assistant-only supervision and checks that LoRA weights changed while the base
weights stayed fixed. This establishes training plumbing, not useful model
quality or full-size model capacity.
