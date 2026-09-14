"""Offline LoRA and 4-bit QLoRA trainer for a separate Python environment.

The local tokenizer renders complete recorded conversations and tool schemas.
Verified native boundaries ensure assistant-only loss; no truncation or tool replay.
"""
from __future__ import annotations

import argparse
from contextlib import nullcontext
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import random
import re
import subprocess
import sys

# Direct execution must not let sibling platform.py shadow Python's stdlib.
if __name__ == '__main__':
    script_directory = str(Path(__file__).resolve().parent)
    sys.path[:] = [entry for entry in sys.path if str(Path(entry).resolve()) != script_directory]
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from letracode.training_examples import normalize_example, example_identity, comparison_example


def progress(message):
    print(json.dumps({'message': message}), flush=True)


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def validate_model_directory(directory):
    directory = Path(directory).expanduser().resolve()
    if not directory.is_dir() or not (directory / 'config.json').is_file():
        raise ValueError('Select a local Hugging Face model directory with config.json; downloads are disabled.')
    if (directory / 'adapter_config.json').exists():
        raise ValueError('Select original base weights, not an existing adapter directory.')
    config = json.loads((directory / 'config.json').read_text(encoding='utf-8'))
    gemma = config.get('model_type') == 'gemma4'
    text_config = config.get('text_config', {})
    if gemma:
        # Keep the standalone backend's original-weight boundary aligned with
        # desktop pairing without importing Qt or changing script import paths.
        def quantized(value):
            if isinstance(value, dict):
                return any((key in ('quantization_config', 'quantization_status', 'quant_method') and bool(item))
                           or quantized(item) for key, item in value.items())
            if isinstance(value, list):
                return any(quantized(item) for item in value)
            return isinstance(value, str) and bool(re.search(r'(^|[^a-z])qat([^a-z]|$)', value.lower()))

        if (config.get('architectures') != ['Gemma4ForConditionalGeneration']
                or text_config.get('model_type') != 'gemma4_text'
                or (text_config.get('hidden_size'), text_config.get('num_hidden_layers')) not in ((1536, 35), (2560, 42))
                or text_config.get('hidden_size_per_layer_input') != 256
                or text_config.get('enable_moe_block') or quantized(config)
                or re.search(r'(^|[^a-z])qat([^a-z]|$)', str(directory).lower())):
            raise ValueError('Select original unquantized Gemma 4 E2B-it or E4B-it weights; QAT, draft, MoE and other architectures are unsupported.')
    elif config.get('model_type') != 'llama' or config.get('vision_config') or config.get('quantization_config'):
        raise ValueError('Only unquantized text-only Llama or original Gemma 4 E2B/E4B instruct training weights are supported.')
    if not list(directory.glob('*.safetensors')):
        raise ValueError('Local safetensors model weights are required; GGUF and pickle weights cannot be trained.')
    return config


def load_datasets(run_dir):
    datasets = []
    seen = set()
    for split in ('train', 'eval'):
        rows = []
        for index, line in enumerate((Path(run_dir) / f'{split}.jsonl').read_text(encoding='utf-8').splitlines(), 1):
            if not line.strip():
                continue
            try:
                row = normalize_example(json.loads(line))
            except (ValueError, TypeError) as error:
                raise ValueError(f'{split} line {index}: invalid prompt/response conversation: {error}') from error
            identity = example_identity(row)
            if identity in seen:
                raise ValueError('Duplicate prompt/context across training/evaluation examples; review the split.')
            seen.add(identity)
            rows.append(row)
        if not rows:
            raise ValueError(f'{split} dataset must contain approved examples.')
        datasets.append(rows)
    return datasets


_NATIVE_MARKERS = ('<|im_start|>', '<|im_end|>', '<|turn>', '<turn|>',
                   '<|tool_call>', '<tool_call|>', '<|tool_response>', '<tool_response|>')


def _template_metadata(tokenizer, tools, strategy):
    template = (tokenizer.get_chat_template(tools=tools or None)
                if hasattr(tokenizer, 'get_chat_template') else tokenizer.chat_template)
    return {'boundary_strategy': strategy,
            'chat_template_sha256': hashlib.sha256(str(template).encode('utf-8')).hexdigest()}


def _check_template_fields(render, messages, tools, rendered):
    """Reject silent payload loss by probing the selected native template.

    Probes are rendered only; they never become model input. IDs and train flags
    are linkage/supervision metadata, not text the model must reproduce.
    """
    import copy
    content_ranges = {}
    tool_ranges = []

    def changed(path, replacement, location):
        candidate = copy.deepcopy({'messages': messages, 'tools': tools})
        parent = candidate
        for key in path[:-1]:
            parent = parent[key]
        parent[path[-1]] = replacement
        try:
            value = render(candidate['messages'], candidate['tools'], False)
        except Exception as error:
            raise ValueError(f'The native template cannot verify {location}: {error}') from error
        if value == rendered:
            raise ValueError(f'The native template ignored {location}; choose a tokenizer that preserves this conversation and its tools.')
        first = 0
        while first < min(len(value), len(rendered)) and value[first] == rendered[first]:
            first += 1
        tail = 0
        while tail < min(len(value), len(rendered)) - first and value[-1 - tail] == rendered[-1 - tail]:
            tail += 1
        return first, len(rendered) - tail

    def complete_string(path, original, location):
        marker = hashlib.sha256((rendered + location).encode()).hexdigest()
        opening, closing = 'LETRACODE_FIELD_START_' + marker, 'LETRACODE_FIELD_END_' + marker
        for source in dict.fromkeys((original, original.strip())):
            candidate = copy.deepcopy({'messages': messages, 'tools': tools})
            parent = candidate
            for key in path[:-1]:
                parent = parent[key]
            parent[path[-1]] = opening + source + closing
            try:
                marked = render(candidate['messages'], candidate['tools'], False)
            except Exception as error:
                raise ValueError(f'The native template cannot verify complete {location}: {error}') from error
            allowed = {source}
            for ascii_only in (False, True):
                escaped = json.dumps(source, ensure_ascii=ascii_only)[1:-1]
                allowed.add(escaped)
                allowed.add(escaped.replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026').replace("'", '\\u0027'))
            pieces = re.findall(re.escape(opening) + '(.*?)' + re.escape(closing), marked, re.DOTALL)
            if (pieces and len(pieces) == marked.count(opening) == marked.count(closing)
                    and all(piece in allowed for piece in pieces)
                    and marked.replace(opening, '').replace(closing, '') == rendered):
                return
        raise ValueError(f'The native template did not preserve complete {location}.')

    def complete_scalar(path, original, location):
        marker = 'LETRACODE_SCALAR_' + hashlib.sha256((rendered + location).encode()).hexdigest()
        candidate = copy.deepcopy({'messages': messages, 'tools': tools})
        parent = candidate
        for key in path[:-1]:
            parent = parent[key]
        parent[path[-1]] = marker
        try:
            marked = render(candidate['messages'], candidate['tools'], False)
        except Exception as error:
            raise ValueError(f'The native template cannot verify complete {location}: {error}') from error
        # Native JSON/Gemma string delimiters are removed before comparing the
        # exact original scalar rendering. Arithmetic, boolean inversion, null
        # omission, and rounding therefore cannot pass a mere influence probe.
        candidates = (json.dumps(marker), '<|"|>' + marker + '<|"|>', marker)
        values = (json.dumps(original, allow_nan=False), str(original))
        if not any(quoted in marked and marked.replace(quoted, value) == rendered
                   for quoted in candidates for value in values):
            raise ValueError(f'The native template did not preserve complete {location}.')

    def complete_key(path, original, replacement, location):
        key = next(key for key in original if key not in replacement)
        marker = hashlib.sha256((rendered + location).encode()).hexdigest()
        opening, closing = 'LETRACODE_KEY_START_' + marker, 'LETRACODE_KEY_END_' + marker
        candidate = copy.deepcopy({'messages': messages, 'tools': tools})
        parent = candidate
        for item in path[:-1]:
            parent = parent[item]
        parent[path[-1]] = {opening + name + closing if name == key else name: value
                           for name, value in original.items()}
        try:
            marked = render(candidate['messages'], candidate['tools'], False)
        except Exception as error:
            raise ValueError(f'The native template cannot verify complete {location}: {error}') from error
        allowed = {key, json.dumps(key, ensure_ascii=False)[1:-1], json.dumps(key, ensure_ascii=True)[1:-1]}
        pieces = re.findall(re.escape(opening) + '(.*?)' + re.escape(closing), marked, re.DOTALL)
        # Native dictsort may move the marked key, so field placement is checked
        # separately by its ordinary key-change probe against assistant spans.
        if (not pieces or len(pieces) != marked.count(opening) or len(pieces) != marked.count(closing)
                or any(piece not in allowed for piece in pieces)):
            raise ValueError(f'The native template did not preserve complete {location}.')

    def leaves(value, path, location):
        if isinstance(value, dict):
            # Probe user-defined argument/property keys, retaining schema keywords.
            data_keys = 'arguments' in path or path[-1] in ('properties', '$defs', 'definitions')
            if data_keys:
                if not value:
                    item = {'type': 'string'} if path[-1] == 'properties' else None
                    yield path, {'letracode_probe_key': item}, location
                for key in value:
                    replacement = dict(value)
                    replacement[key + '_letracode_probe'] = replacement.pop(key)
                    yield path, replacement, location + '.' + key + ' key'
            for key, item in value.items():
                if key == 'strict' and path[-1] == 'function':
                    continue
                yield from leaves(item, path + [key], location + '.' + key)
        elif isinstance(value, list):
            if not value:
                yield path, ['letracode_probe_value'], location
            for index, item in enumerate(value):
                yield from leaves(item, path + [index], location + f'[{index}]')
        else:
            # Use the native value type where template filters depend on it.
            replacement = (not value if isinstance(value, bool) else
                           value + 1 if isinstance(value, (int, float)) else
                           'letracode_probe_value' if value is None else
                           ('integer' if value == 'string' else 'string') if path[-1] == 'type' and isinstance(value, str) else
                           str(value) + '_letracode_probe')
            yield path, replacement, location

    for index, message in enumerate(messages):
        candidate = copy.deepcopy(messages)
        candidate[index]['role'] = 'user' if message['role'] != 'user' else 'assistant'
        try:
            role_probe = render(candidate, tools, False)
        except Exception:
            # A native template rejecting the changed role demonstrably uses it.
            role_probe = None
        if role_probe == rendered:
            raise ValueError(f'The native template ignored message {index + 1} role.')
        if message.get('content', '').strip():
            # A prefix/suffix probe catches templates that retain only a substring.
            # Native whitespace trimming is allowed; internal text must survive.
            start = 'LETRACODE_CONTENT_START_' + hashlib.sha256(rendered.encode()).hexdigest()
            end = 'LETRACODE_CONTENT_END_' + hashlib.sha256(rendered.encode()).hexdigest()
            for content in dict.fromkeys((message['content'], message['content'].strip())):
                candidate = copy.deepcopy(messages)
                candidate[index]['content'] = start + content + end
                marked = render(candidate, tools, False)
                if (marked.count(start) == 1 and marked.count(end) == 1
                        and marked.split(start, 1)[1].split(end, 1)[0] == content
                        and marked.replace(start, '').replace(end, '') == rendered):
                    left = marked.index(start)
                    content_ranges[index] = (left, left + len(content))
                    break
            else:
                raise ValueError(f'The native template did not preserve complete message {index + 1} content.')
        for number, call in enumerate(message.get('tool_calls', [])):
            function_path = ['messages', index, 'tool_calls', number, 'function']
            for path, replacement, location in leaves(call['function'], function_path, f'message {index + 1} tool call {number + 1}'):
                changed(path, replacement, location)
                original = {'messages': messages, 'tools': tools}
                for key in path:
                    original = original[key]
                if isinstance(original, str):
                    complete_string(path, original, location)
                elif location.endswith(' key'):
                    complete_key(path, original, replacement, location)
                elif not isinstance(original, (dict, list)):
                    complete_scalar(path, original, location)
    for index, tool in enumerate(tools):
        # Function type is transport metadata; name/description/schema are input.
        for path, replacement, location in leaves(tool['function'], ['tools', index, 'function'], f'tool {index + 1}'):
            tool_ranges.append(changed(path, replacement, location))
            original = {'messages': messages, 'tools': tools}
            for key in path:
                original = original[key]
            if isinstance(original, str) and path[-1] != 'type':
                complete_string(path, original, location)
            elif location.endswith(' key'):
                complete_key(path, original, replacement, location)
            elif not isinstance(original, (dict, list, str)):
                complete_scalar(path, original, location)
    return content_ranges, tool_ranges


def _verify_field_spans(fields, messages, spans):
    content_ranges, tool_ranges = fields
    for index, (left, right) in content_ranges.items():
        if messages[index]['role'] == 'assistant':
            if not any(owner == index and start <= left <= right <= end for owner, start, end in spans):
                raise ValueError(f'Native template moved message {index + 1} content outside its assistant boundary.')
        elif any(start < right and left < end for _, start, end in spans):
            raise ValueError(f'Native template moved message {index + 1} context content into an assistant boundary.')
    if any(start < right and left < end for left, right in tool_ranges for _, start, end in spans):
        raise ValueError('Native template moved tool definitions into an assistant boundary.')


def _chatml_spans(rendered, messages):
    matches = list(re.finditer(r'<\|im_start\|>([^\n]+)\n(.*?)<\|im_end\|>', rendered, re.DOTALL))
    if rendered.count('<|im_start|>') != len(matches) or rendered.count('<|im_end|>') != len(matches):
        raise ValueError('The native ChatML template has ambiguous role delimiters.')
    assistants = [match for match in matches if match.group(1) == 'assistant']
    expected = [(index, message) for index, message in enumerate(messages) if message['role'] == 'assistant']
    # Verify the role sequence, permitting a native injected system header and
    # native grouping of consecutive tool results only.
    def compressed(roles):
        result = []
        for role in roles:
            if role == 'tool' and result and result[-1] == role:
                continue
            result.append(role)
        return result
    actual_roles = compressed([match.group(1) for match in matches])
    source_roles = compressed([message['role'] for message in messages])
    while len(actual_roles) > len(source_roles) and actual_roles[0] == 'system':
        actual_roles.pop(0)
    if actual_roles != source_roles or len(assistants) != len(expected):
        raise ValueError('The native ChatML template did not preserve the conversation role sequence.')
    return [(index, match.start(2), match.end()) for (index, _), match in zip(expected, assistants)]


def _gemma_spans(rendered, messages):
    """Map Gemma's native merged model turns, excluding embedded tool results."""
    turns = list(re.finditer(r'<\|turn>([^\n]+)\n(.*?)<turn\|>', rendered, re.DOTALL))
    if rendered.count('<|turn>') != len(turns) or rendered.count('<turn|>') != len(turns):
        raise ValueError('The native Gemma template has incomplete or ambiguous turn delimiters.')
    groups = []
    for index, message in enumerate(messages):
        if message['role'] == 'tool':
            continue
        role = 'model' if message['role'] == 'assistant' else message['role']
        if role == 'model' and groups and groups[-1][0] == role:
            groups[-1][1].append(index)
        else:
            groups.append((role, [index]))
    while len(turns) > len(groups) and turns[0].group(1) == 'system':
        turns.pop(0)
    if [turn.group(1) for turn in turns] != [role for role, _ in groups]:
        raise ValueError('The native Gemma template did not preserve the conversation role sequence.')
    spans = []
    for turn, (role, indices) in zip(turns, groups):
        if role != 'model':
            continue
        cursor = turn.start(2)
        for index in indices:
            message = messages[index]
            for call in message.get('tool_calls', []):
                prefix = '<|tool_call>call:' + call['function']['name'] + '{'
                if not rendered.startswith(prefix, cursor):
                    raise ValueError('The native Gemma template did not preserve an assistant tool call.')
                end = rendered.find('<tool_call|>', cursor)
                if end < 0 or end >= turn.end(2):
                    raise ValueError('The native Gemma template has an incomplete tool call.')
                end += len('<tool_call|>')
                spans.append((index, cursor, end))
                cursor = end
            following = index + 1
            while following < len(messages) and messages[following]['role'] == 'tool':
                if not rendered.startswith('<|tool_response>', cursor):
                    raise ValueError('The native Gemma template did not preserve a tool result.')
                end = rendered.find('<tool_response|>', cursor)
                if end < 0 or end >= turn.end(2):
                    raise ValueError('The native Gemma template has an incomplete tool result.')
                cursor = end + len('<tool_response|>')
                following += 1
            content = message.get('content', '').strip()
            if not rendered.startswith(content, cursor):
                raise ValueError('The native Gemma template changed or discarded assistant content.')
            if content:
                spans.append((index, cursor, cursor + len(content)))
                cursor += len(content)
        if cursor != turn.end(2):
            raise ValueError('The native Gemma template has unverified assistant content or tool boundaries.')
        spans.append((indices[-1], cursor, turn.end()))
    return spans


def encode_example(tokenizer, row, max_length, *, gemma=False):
    if not getattr(tokenizer, 'chat_template', None):
        raise ValueError('A local tokenizer chat template is required for assistant fine-tuning; select matching instruct weights/tokenizer.')
    example = normalize_example(row)
    tools = example['tools']
    messages = [{key: value for key, value in message.items() if key != 'train'} for message in example['messages']]
    for index, message in enumerate(messages):
        calls = message.get('tool_calls', [])
        if calls:
            results = messages[index + 1:index + 1 + len(calls)]
            if [result.get('tool_call_id') for result in results] != [call['id'] for call in calls]:
                raise ValueError(f'Message {index + 1}: this training encoder requires tool results in matching call order to preserve associations in native positional tool formats; reorder the linked result messages.')
    options = {'enable_thinking': False} if gemma else {}
    if tools:
        options['tools'] = tools
    try:
        tokens = tokenizer.apply_chat_template(messages, tokenize=True, add_generation_prompt=False, return_dict=False, **options)
    except Exception as error:
        raise ValueError(f'The native tokenizer template cannot render this conversation: {error}') from error
    if len(tokens) > max_length:
        raise ValueError(f'Example token length {len(tokens)} exceeds max_length {max_length}; shorten it or increase the limit. No tokens were truncated.')
    labels = [-100] * len(tokens)
    target_index = next(index for index, message in enumerate(example['messages']) if message['role'] == 'assistant' and message['train'])
    def render(current_messages, current_tools, tokenize):
        kwargs = {'enable_thinking': False} if gemma else {}
        if current_tools:
            kwargs['tools'] = current_tools
        return tokenizer.apply_chat_template(current_messages, tokenize=tokenize, add_generation_prompt=False, return_dict=False, **kwargs)

    rendered = render(messages, tools, False)
    serialized = json.dumps({'messages': messages, 'tools': tools}, ensure_ascii=False)
    if any(marker in serialized for marker in _NATIVE_MARKERS):
        raise ValueError('Example text contains a native role/tool delimiter marker; remove the literal marker before training.')
    fields = _check_template_fields(render, messages, tools, rendered) if isinstance(rendered, str) else None
    simple = not tools and len(messages) == 2 and [message['role'] for message in messages] == ['user', 'assistant']
    if simple:
        prompt = tokenizer.apply_chat_template(messages[:1], tokenize=True, add_generation_prompt=True, return_dict=False, **options)
        if not prompt or tokens[:len(prompt)] != prompt:
            raise ValueError('Tokenizer chat template does not preserve the exact generation prefix; this template is unsupported for response-only training.')
        if len(tokens) == len(prompt):
            raise ValueError('Response must produce tokens after the generation prefix.')
        if fields is not None:
            prompt_text = tokenizer.apply_chat_template(messages[:1], tokenize=False, add_generation_prompt=True, return_dict=False, **options)
            if not isinstance(prompt_text, str) or not rendered.startswith(prompt_text):
                raise ValueError('Native template text does not preserve the generation prefix.')
            _verify_field_spans(fields, messages, [(1, len(prompt_text), len(rendered))])
        labels[len(prompt):] = tokens[len(prompt):]
        return {'input_ids': tokens, 'labels': labels, 'prompt_ids': prompt, 'target_message_index': target_index,
                **_template_metadata(tokenizer, tools, 'exact-generation-prefix')}

    if fields is None:
        raise ValueError('The native template must expose its rendered text to verify conversation boundaries.')
    if gemma and '<|turn>model\n' in rendered:
        spans = _gemma_spans(rendered, messages)
        strategy = 'native-gemma-turns-and-tool-results'
    elif '<|im_start|>assistant\n' in rendered:
        spans = _chatml_spans(rendered, messages)
        strategy = 'native-chatml-role-boundaries'
    else:
        # Unknown dialects remain supported when each native generation prefix
        # and completed assistant span is an exact prefix of the full sequence.
        spans = None
        strategy = 'exact-generation-prefixes'
    first_target = None
    if spans is not None:
        _verify_field_spans(fields, messages, spans)
        if tokenizer.encode(rendered, add_special_tokens=False) != tokens:
            raise ValueError('Native rendered text and native tokenization disagree; no loss boundaries can be verified.')
        boundaries = {}
        for _, start, end in spans:
            for offset in (start, end):
                if offset not in boundaries:
                    prefix = tokenizer.encode(rendered[:offset], add_special_tokens=False)
                    if tokens[:len(prefix)] != prefix:
                        raise ValueError('Native template boundary crosses a token or rewrites its prefix; this example cannot be masked safely.')
                    boundaries[offset] = len(prefix)
        for index, start, end in spans:
            if example['messages'][index]['train']:
                left, right = boundaries[start], boundaries[end]
                if first_target is None:
                    first_target = left
                labels[left:right] = tokens[left:right]
    else:
        verified_spans = []
        for index, message in enumerate(example['messages']):
            if message['role'] != 'assistant':
                continue
            try:
                prompt = tokenizer.apply_chat_template(messages[:index], tokenize=True, add_generation_prompt=True, return_dict=False, **options)
                completed = render(messages[:index + 1], tools, True)
            except Exception as error:
                raise ValueError(f'The native template cannot verify message {index + 1}: {error}') from error
            if (not prompt or completed[:len(prompt)] != prompt or tokens[:len(completed)] != completed):
                raise ValueError('Native template does not preserve exact generation prefixes for this conversation.')
            prompt_text = tokenizer.apply_chat_template(messages[:index], tokenize=False, add_generation_prompt=True, return_dict=False, **options)
            completed_text = render(messages[:index + 1], tools, False)
            if not completed_text.startswith(prompt_text) or not rendered.startswith(completed_text):
                raise ValueError('Native template text does not preserve exact generation prefixes.')
            verified_spans.append((index, len(prompt_text), len(completed_text)))
            if message['train']:
                if first_target is None:
                    first_target = len(prompt)
                labels[len(prompt):len(completed)] = tokens[len(prompt):len(completed)]
        _verify_field_spans(fields, messages, verified_spans)
    if first_target is None or not any(label != -100 for label in labels[1:]):
        raise ValueError('Example has no verified assistant target tokens.')
    return {'input_ids': tokens, 'labels': labels, 'prompt_ids': tokens[:first_target],
            'target_message_index': target_index, **_template_metadata(tokenizer, tools, strategy)}


def _encoding_evidence(row):
    return {key: row[key] for key in ('boundary_strategy', 'chat_template_sha256', 'target_message_index')} | {
        'input_tokens': len(row['input_ids']),
        'supervised_tokens': sum(label != -100 for label in row['labels'][1:]),
        'prompt_tokens': len(row['prompt_ids'])}


def _tokenization_metadata(splits):
    """Keep bounded previews and exact aggregates for every encoded example."""
    previews, counts, summaries = {}, {}, {}
    for split, rows in splits.items():
        preview = []
        count = total_input = total_supervised = maximum = 0
        strategies, templates = set(), set()
        for row in rows:
            if len(preview) < 3:
                preview.append(dict(row))
            count += 1
            total_input += row['input_tokens']
            total_supervised += row['supervised_tokens']
            maximum = max(maximum, row['input_tokens'])
            strategies.add(row['boundary_strategy'])
            templates.add(row['chat_template_sha256'])
        previews[split] = preview
        counts[split] = len(preview)
        summaries[split] = {'example_count': count, 'total_input_tokens': total_input,
                            'total_supervised_tokens': total_supervised, 'max_input_tokens': maximum,
                            'boundary_strategies': sorted(strategies),
                            'chat_template_sha256s': sorted(templates)}
    return {'tokenization_examples': previews, 'tokenization_preview_counts': counts,
            'tokenization_summary': summaries}


def convert_adapter(run_dir, base_model, llama_cpp_dir):
    output = Path(run_dir) / 'adapter.gguf'
    converter = Path(llama_cpp_dir) / 'convert_lora_to_gguf.py'
    if not converter.is_file():
        return '', 'Select a local llama.cpp checkout containing convert_lora_to_gguf.py; PEFT adapter and evaluation are preserved.'
    env = dict(os.environ, HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1')
    try:
        result = subprocess.run([sys.executable, str(converter), '--base', str(base_model), '--outfile', str(output), str(Path(run_dir) / 'adapter')], env=env, capture_output=True, text=True, check=False)
        (Path(run_dir) / 'conversion.log').write_text(result.stdout + '\n' + result.stderr, encoding='utf-8')
        if result.returncode or not output.is_file() or output.stat().st_size == 0:
            return '', f'GGUF conversion failed ({result.returncode}). Install the selected llama.cpp conversion requirements into the training environment. See conversion.log. ' + (result.stderr or result.stdout)[-3000:]
        return str(output), ''
    except OSError as exc:
        return '', f'GGUF converter could not start: {exc}. PEFT adapter is preserved.'


def cuda_compute_dtype(torch):
    # BF16 emulation is reported as supported on some Turing installations.
    native_bf16 = torch.cuda.get_device_capability(0)[0] >= 8 and torch.cuda.is_bf16_supported()
    return torch.bfloat16 if native_bf16 else torch.float16


GEMMA_TARGETS = ['q_proj', 'k_proj', 'v_proj', 'o_proj', 'gate_proj', 'up_proj', 'down_proj']


def offload_gemma_ple(model, torch):
    """Keep the frozen PLE table on CPU; transfer selected rows, never the table.

    Accelerate's inference offload would upload this multi-GB table on every
    forward. Remove those hooks after loading and retain its original scaled
    embedding implementation with only input/output device transfers.
    """
    from accelerate.hooks import remove_hook_from_module
    remove_hook_from_module(model, recurse=True)
    ple = model.model.embed_tokens_per_layer
    if ple.weight.device.type != 'cpu' or ple.weight.is_meta:
        raise RuntimeError('Gemma per-layer embeddings were not retained in system RAM.')
    ple.to('cpu')
    ple.requires_grad_(False)
    destination = model.model.embed_tokens.weight.device
    ple.register_forward_pre_hook(lambda module, args: (args[0].to('cpu'),))
    ple.register_forward_hook(lambda module, args, output: output.to(destination))


def load_gemma_model(base, config, options, quantization_config, torch):
    from transformers import Gemma4ForCausalLM, Gemma4TextConfig
    # Explicit mapping is required: the original multimodal checkpoint stores
    # decoder weights under model.language_model, the causal class under model.
    model, info = Gemma4ForCausalLM.from_pretrained(
        str(base), config=Gemma4TextConfig(**config['text_config']),
        key_mapping={r'^model\.language_model\.': 'model.'},
        output_loading_info=True, **options, quantization_config=quantization_config,
        device_map={'model.embed_tokens_per_layer': 'cpu', 'model.embed_tokens': 0,
                    'model.layers': 0, 'model.norm': 0, 'model.rotary_emb': 0,
                    'model.per_layer_model_projection': 0, 'model.per_layer_projection_norm': 0,
                    'lm_head': 0}, attn_implementation='sdpa')
    if info.get('missing_keys') or info.get('mismatched_keys') or info.get('error_msgs'):
        raise RuntimeError(f'Gemma original decoder weights were not loaded completely: {info}')
    offload_gemma_ple(model, torch)
    return model


def verify_adapter_parameters(model):
    trainable = [name for name, parameter in model.named_parameters() if parameter.requires_grad]
    if not trainable or any('.lora_A.' not in name and '.lora_B.' not in name for name in trainable):
        raise RuntimeError('Training must update only LoRA adapters; unexpectedly trainable base weights were detected.')
    trainable_count, total_count = model.get_nb_trainable_parameters()
    if not 0 < trainable_count < total_count:
        raise RuntimeError('Training did not preserve a frozen base and trainable adapters.')
    return int(trainable_count), int(total_count)


def compute_response_loss(torch, model, inputs, *, gemma=False):
    """Keep full decoder context but project only supervised Gemma positions.

    The native Gemma head still applies its logit softcap and FP32 causal loss.
    Explicit shifted labels align selected logits with the next response token;
    a batch uses the union of supervised columns and retains each row's masks.
    """
    if not gemma:
        return model(**inputs).loss
    shifted = torch.nn.functional.pad(inputs['labels'], (0, 1), value=-100)[:, 1:]
    positions = (shifted != -100).any(dim=0).nonzero(as_tuple=True)[0]
    if positions.numel() == 0:
        raise ValueError('Training/evaluation batch has no assistant response tokens.')
    return model(**inputs, logits_to_keep=positions, shift_labels=shifted[:, positions]).loss


def train_epoch(torch, model, rows, batch, optimizer, scaler, autocast,
                batch_size, accumulation, on_step=None, *, gemma=False):
    """Normalize each optimizer update by its actual assistant-token count."""
    steps = 0
    group_size = batch_size * accumulation
    for group_start in range(0, len(rows), group_size):
        group = rows[group_start:group_start + group_size]
        token_count = sum(sum(label != -100 for label in row['labels'][1:]) for row in group)
        if not token_count:
            raise ValueError('Training group has no assistant response tokens.')
        for attempt in range(13):
            optimizer.zero_grad(set_to_none=True)
            response_loss = 0.0
            for start in range(0, len(group), batch_size):
                microbatch = group[start:start + batch_size]
                tokens = sum(sum(label != -100 for label in row['labels'][1:]) for row in microbatch)
                with autocast():
                    loss = compute_response_loss(torch, model, batch(microbatch), gemma=gemma)
                    if not torch.isfinite(loss):
                        raise RuntimeError('Nonfinite training loss; reduce learning rate or review examples.')
                    weighted_loss = loss * (tokens / token_count)
                scaler.scale(weighted_loss).backward()
                response_loss += float(loss.detach().item()) * tokens / token_count
            scaler.unscale_(optimizer)
            parameters = [p for p in model.parameters() if p.requires_grad and p.grad is not None]
            if not parameters:
                raise RuntimeError('Training produced no adapter gradients.')
            finite = bool(torch.stack([torch.isfinite(p.grad).all() for p in parameters]).all().item())
            if not finite:
                if not scaler.is_enabled() or attempt == 12:
                    raise RuntimeError('Nonfinite adapter gradients persisted after loss-scale adjustment; reduce the learning rate or use a smaller training sequence.')
                # unscale_ recorded these nonfinite gradients, so step skips the
                # update. Recompute the same group at a lower scale: no examples
                # or optimizer updates are silently lost to normal AMP overflow.
                previous_scale = scaler.get_scale()
                scaler.step(optimizer)
                scaler.update()
                if not scaler.get_scale() < previous_scale:
                    raise RuntimeError('The training loss scaler could not recover nonfinite gradients.')
                progress('Adjusting FP16 loss scale and retrying the same training examples')
                continue
            try:
                torch.nn.utils.clip_grad_norm_(parameters, 1.0, error_if_nonfinite=True)
            except RuntimeError as exc:
                raise RuntimeError('Nonfinite adapter gradient norm; reduce the learning rate or use a smaller training sequence.') from exc
            scaler.step(optimizer)
            scaler.update()
            break
        steps += 1
        if on_step is not None:
            on_step(steps, response_loss)
    return steps


def run_training(run_dir):
    run_dir = Path(run_dir).expanduser().resolve()
    config = json.loads((run_dir / 'config.json').read_text(encoding='utf-8'))
    base = Path(config['base_model']).expanduser().resolve()
    model_config = validate_model_directory(base)
    gemma = model_config.get('model_type') == 'gemma4'
    train_rows, eval_rows = load_datasets(run_dir)
    for key, low, high in [('epochs', 1, 100), ('rank', 1, 256), ('max_length', 8, 8192), ('batch_size', 1, 64), ('seed', 0, 2**31 - 1), ('gradient_accumulation_steps', 1, 128)]:
        value = config.get(key, {'epochs': 1, 'rank': 8, 'max_length': 512, 'batch_size': 1, 'seed': 42, 'gradient_accumulation_steps': 1}[key])
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise ValueError(f'{key} must be an integer from {low} to {high}.')
        config[key] = value
    lr = config.get('learning_rate', 0.0002)
    if isinstance(lr, bool) or not isinstance(lr, (int, float)) or not math.isfinite(lr) or not 0 < lr <= .1:
        raise ValueError('learning_rate must be finite, greater than 0 and at most 0.1.')
    device = config.get('device', 'cpu')
    if device not in ('cpu', 'cuda'):
        raise ValueError('device must be cpu or cuda.')
    method = config.get('training_method', 'lora')
    if method not in ('lora', 'qlora'):
        raise ValueError('training_method must be lora or qlora.')
    checkpointing = config.get('gradient_checkpointing', False)
    if type(checkpointing) is not bool:
        raise ValueError('gradient_checkpointing must be true or false.')
    if method == 'qlora' and device != 'cuda':
        raise ValueError('4-bit QLoRA requires an NVIDIA CUDA GPU; select CUDA or choose regular LoRA for CPU training.')
    if gemma and (method != 'qlora' or device != 'cuda'):
        raise ValueError('Gemma 4 text training requires CUDA 4-bit QLoRA with frozen per-layer embeddings in system RAM.')
    if any((run_dir / name).exists() for name in ('adapter', 'adapter.gguf', 'report.json')):
        raise ValueError('Run already contains output; create a fresh run to preserve earlier artifacts.')
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1')
    try:
        import torch
        from transformers import AutoTokenizer, LlamaForCausalLM
        from peft import LoraConfig, TaskType, get_peft_model
    except ImportError as exc:
        raise RuntimeError('Install packaging/training-requirements.txt into the selected separate training Python environment.') from exc
    if device == 'cuda' and not torch.cuda.is_available():
        raise ValueError('CUDA is unavailable in the selected training Python environment. Install a CUDA-enabled PyTorch build and a compatible NVIDIA driver, or choose regular LoRA on CPU.')
    compute_dtype = cuda_compute_dtype(torch) if method == 'qlora' else torch.float32
    if gemma:
        from packaging.version import Version
        if Version(importlib.metadata.version('transformers')) < Version('5.17.0'):
            raise RuntimeError('Gemma 4 requires Transformers 5.17.0 or newer for verified text loading and shared-KV checkpointing; use a separate training environment.')
    quantization_config = None
    if method == 'qlora':
        if torch.cuda.get_device_capability(0)[0] < 6:
            raise ValueError('4-bit QLoRA requires an NVIDIA Pascal or newer GPU (compute capability 6.0+).')
        try:
            import bitsandbytes as bnb
            import accelerate  # noqa: F401 - verify the selected training environment
            from transformers import BitsAndBytesConfig
            from peft import prepare_model_for_kbit_training
            quantization_config = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type='nf4',
                                                     bnb_4bit_use_double_quant=True,
                                                     bnb_4bit_compute_dtype=compute_dtype,
                                                     llm_int8_enable_fp32_cpu_offload=gemma)
        except (ImportError, OSError, RuntimeError) as exc:
            raise RuntimeError('4-bit QLoRA needs working bitsandbytes and accelerate CUDA packages. Install packaging/training-requirements.txt into the selected training Python environment and check its CUDA compatibility.') from exc
    seed = config.get('seed', 42)
    random.seed(seed)
    torch.manual_seed(seed)
    if device == 'cuda':
        torch.cuda.manual_seed_all(seed)
        torch.cuda.reset_peak_memory_stats(0)
    progress('Hashing local model weights and approved dataset snapshots')
    provenance = {'config_sha256': sha256(run_dir / 'config.json'), 'train_sha256': sha256(run_dir / 'train.jsonl'), 'eval_sha256': sha256(run_dir / 'eval.jsonl'), 'base_model': str(base), 'model_config_sha256': sha256(base / 'config.json'), 'model_weight_manifest': {p.name: {'sha256': sha256(p), 'bytes': p.stat().st_size} for p in sorted(base.glob('*.safetensors'))}, 'tokenizer_manifest': {p.relative_to(base).as_posix(): sha256(p) for p in sorted(list(base.iterdir()) + list((base / 'chat_templates').glob('*.jinja')) + list((base / 'additional_chat_templates').glob('*.jinja'))) if p.is_file() and (p.name.startswith('tokenizer') or p.name in ('special_tokens_map.json', 'added_tokens.json', 'chat_template.jinja') or p.parent in (base / 'chat_templates', base / 'additional_chat_templates'))}, 'tokenization': 'complete local native chat/tool template; verified assistant targets and native terminators contribute loss; user/system/tool/context-only assistant tokens masked; no truncation or tool execution'}
    progress('Loading local tokenizer and ' + ('Gemma 4 text decoder' if gemma else 'Llama') + ' safetensors weights')
    tokenizer = AutoTokenizer.from_pretrained(str(base), local_files_only=True, trust_remote_code=False)
    text_config = model_config['text_config'] if gemma else model_config
    limit = min(config['max_length'], text_config.get('max_position_embeddings', config['max_length']))
    train = [encode_example(tokenizer, row, limit, gemma=gemma) for row in train_rows]
    evaluation = [encode_example(tokenizer, row, limit, gemma=gemma) for row in eval_rows]
    provenance.update(_tokenization_metadata({'train': (_encoding_evidence(row) for row in train),
                                               'eval': (_encoding_evidence(row) for row in evaluation)}))
    load_options = dict(local_files_only=True, trust_remote_code=False, use_safetensors=True,
                        torch_dtype=compute_dtype)
    quantized_layers = 0
    if method == 'qlora':
        progress(f'Loading 4-bit NF4 weights with double quantization and {str(compute_dtype).split(".")[-1]} computation')
        if gemma:
            progress('Keeping frozen Gemma per-layer embeddings in system RAM; training only text decoder projections on CUDA')
            model = load_gemma_model(base, model_config, load_options, quantization_config, torch)
        else:
            model = LlamaForCausalLM.from_pretrained(str(base), **load_options,
                                                   quantization_config=quantization_config, device_map={'': 0})
        for module in model.modules():
            if isinstance(module, bnb.nn.Linear4bit):
                state = getattr(module.weight, 'quant_state', None)
                if (module.weight.device.type != 'cuda' or state is None
                        or state.quant_type != 'nf4' or not state.nested):
                    raise RuntimeError('The training runtime did not load CUDA 4-bit NF4 weights with double quantization; QLoRA was stopped.')
                quantized_layers += 1
        if not quantized_layers:
            raise RuntimeError('No 4-bit layers were loaded; QLoRA was stopped instead of falling back to full weights.')
        model = prepare_model_for_kbit_training(model, use_gradient_checkpointing=checkpointing,
                                                gradient_checkpointing_kwargs={'use_reentrant': False})
    else:
        model = LlamaForCausalLM.from_pretrained(str(base), **load_options).to(device)
        if checkpointing:
            model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={'use_reentrant': False})
    model.config.use_cache = False
    base_model_bytes = int(model.get_memory_footprint())
    autocast = (lambda: torch.autocast(device_type='cuda', dtype=compute_dtype)) if method == 'qlora' else nullcontext
    pad = tokenizer.pad_token_id if tokenizer.pad_token_id is not None else tokenizer.eos_token_id
    if pad is None:
        pad = 0

    def batch(rows):
        width = max(len(row['input_ids']) for row in rows)
        return {key: torch.tensor(values, dtype=torch.long, device=device) for key, values in {
            'input_ids': [r['input_ids'] + [pad] * (width - len(r['input_ids'])) for r in rows],
            'labels': [r['labels'] + [-100] * (width - len(r['labels'])) for r in rows],
            'attention_mask': [[1] * len(r['input_ids']) + [0] * (width - len(r['input_ids'])) for r in rows],
        }.items()}

    def evaluate(current):
        current.eval()
        total, count = 0.0, 0
        with torch.no_grad(), autocast():
            for start in range(0, len(evaluation), config['batch_size']):
                inputs = batch(evaluation[start:start + config['batch_size']])
                tokens = int((inputs['labels'][:, 1:] != -100).sum().item())
                loss = float(compute_response_loss(torch, current, inputs, gemma=gemma).item())
                if not math.isfinite(loss):
                    raise RuntimeError('Nonfinite evaluation loss; no completed report produced.')
                total += loss * tokens
                count += tokens
        return total / count, count

    def generate(current):
        current.eval()
        result = []
        with torch.no_grad(), autocast():
            for row in evaluation[:3]:
                ids = torch.tensor([row['prompt_ids']], dtype=torch.long, device=device)
                eos = current.generation_config.eos_token_id if gemma else tokenizer.eos_token_id
                output = current.generate(input_ids=ids, attention_mask=torch.ones_like(ids), do_sample=False, max_new_tokens=min(64, limit - ids.shape[1]), pad_token_id=pad, eos_token_id=eos)
                result.append(tokenizer.decode(output[0, ids.shape[1]:], skip_special_tokens=True))
        return result

    progress('Evaluating unchanged base on held-out responses')
    base_loss, eval_tokens = evaluate(model)
    base_examples = generate(model)
    target_modules = GEMMA_TARGETS if gemma else ('all-linear' if method == 'qlora' else ['q_proj', 'v_proj'])
    model = get_peft_model(model, LoraConfig(task_type=TaskType.CAUSAL_LM, r=config['rank'], lora_alpha=2 * config['rank'], lora_dropout=0.0, target_modules=target_modules, bias='none'))
    trainable_count, total_count = verify_adapter_parameters(model)
    optimizer = torch.optim.AdamW((p for p in model.parameters() if p.requires_grad), lr=lr)
    scaler = torch.amp.GradScaler('cuda', enabled=method == 'qlora' and compute_dtype == torch.float16,
                                 init_scale=1.0 if gemma else 1024.0)
    steps = 0
    for epoch in range(config['epochs']):
        model.train()
        order = list(train)
        random.shuffle(order)
        def on_step(epoch_step, loss):
            progress(f'Epoch {epoch + 1}/{config["epochs"]}, step {steps + epoch_step}, response loss {loss:.6f}')
        steps += train_epoch(torch, model, order, batch, optimizer, scaler, autocast,
                             config['batch_size'], config['gradient_accumulation_steps'], on_step, gemma=gemma)
    progress('Evaluating candidate on identical held-out responses')
    candidate_loss, _ = evaluate(model)
    candidate_examples = generate(model)
    changed_b = 0
    for name, parameter in model.named_parameters():
        if parameter.requires_grad:
            if not bool(torch.isfinite(parameter).all().item()):
                raise RuntimeError('Training produced nonfinite adapter weights; candidate cannot be exported.')
            if '.lora_B.' in name:
                changed_b += int(torch.count_nonzero(parameter).item())
    if not changed_b:
        raise RuntimeError('Training produced no nonzero LoRA B weights; candidate cannot be exported.')
    adapter = run_dir / 'adapter'
    model.save_pretrained(str(adapter), safe_serialization=True)
    provenance['adapter_manifest'] = {p.name: sha256(p) for p in sorted(adapter.iterdir()) if p.is_file()}
    progress('Converting preserved PEFT adapter to GGUF')
    gguf, conversion_error = convert_adapter(run_dir, base, config.get('llama_cpp_dir', ''))
    if gguf:
        provenance['adapter_gguf_sha256'] = sha256(gguf)
    packages = ('torch', 'transformers', 'peft', 'safetensors') + (('bitsandbytes', 'accelerate') if method == 'qlora' else ())
    details = {'training_method': method, 'quantization': 'nf4-double' if method == 'qlora' else 'none',
               'compute_dtype': str(compute_dtype).split('.')[-1], 'target_modules': target_modules,
               'gradient_checkpointing': bool(model.is_gradient_checkpointing),
               'gradient_accumulation_steps': config['gradient_accumulation_steps'],
               'effective_batch_size': config['batch_size'] * config['gradient_accumulation_steps'],
               'device': device, 'device_name': torch.cuda.get_device_name(0) if device == 'cuda' else 'CPU',
               'quantized_layer_count': quantized_layers, 'base_frozen': True,
               'trainable_parameter_count': trainable_count, 'total_parameter_count': total_count}
    if gemma:
        details.update(model_family='gemma4', text_only=True, frozen_ple_cpu=True,
                       loss_strategy='native-softcapped-response-position-logits',
                       variant='E2B' if text_config['hidden_size'] == 1536 else 'E4B',
                       nonzero_lora_b_values=changed_b, generation_max_new_tokens=64,
                       max_train_example_tokens=max(len(row['input_ids']) for row in train),
                       max_eval_example_tokens=max(len(row['input_ids']) for row in evaluation),
                       comparison_runtime='Transformers NF4 original vs NF4 + adapter; greedy, thinking disabled')
    memory = {'base_model_bytes': base_model_bytes,
              'peak_allocated_bytes': int(torch.cuda.max_memory_allocated(0)) if device == 'cuda' else 0,
              'peak_reserved_bytes': int(torch.cuda.max_memory_reserved(0)) if device == 'cuda' else 0}
    if gemma:
        ple = model.get_base_model().model.embed_tokens_per_layer.weight
        memory['frozen_ple_cpu_bytes'] = ple.numel() * ple.element_size()
        try:
            import resource
            memory['peak_process_rss_bytes'] = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss) * (1 if sys.platform == 'darwin' else 1024)
        except ImportError:
            pass
    comparisons = []
    for row, encoded, before, after in zip(eval_rows[:3], evaluation[:3], base_examples, candidate_examples):
        view = comparison_example(row)
        comparisons.append(dict(row, prompt=view['prompt'], response=view['response'],
                                target_message_index=encoded['target_message_index'],
                                base_output=before, candidate_output=after))
    report = {'base_loss': base_loss, 'candidate_loss': candidate_loss, 'eval_response_tokens': eval_tokens, 'eval_examples': comparisons, 'adapter_path': str(adapter), 'adapter_gguf': gguf, 'conversion_error': conversion_error, 'package_versions': {name: importlib.metadata.version(name) for name in packages}, 'provenance': provenance, 'optimization_steps': steps, 'training_details': details, 'memory': memory}
    temporary = run_dir / 'report.json.tmp'
    temporary.write_text(json.dumps(report, indent=2, allow_nan=False), encoding='utf-8')
    temporary.replace(run_dir / 'report.json')
    progress('Training and held-out evaluation complete' + ('; GGUF conversion requires attention' if conversion_error else ''))
    return report


def check_training(run_dir):
    """Check the selected runtime and every example without loading model weights."""
    run_dir = Path(run_dir)
    config = json.loads((run_dir / 'config.json').read_text(encoding='utf-8'))
    base = Path(config['base_model'])
    architecture = validate_model_directory(base)
    gemma = architecture.get('model_type') == 'gemma4'
    train, evaluation = load_datasets(run_dir)
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1')
    try:
        import torch
        import peft  # noqa: F401 - verify this separate environment before Chat unloads
        import safetensors  # noqa: F401
        from transformers import AutoTokenizer
    except ImportError as exc:
        raise RuntimeError('The selected training Python is missing PyTorch, Transformers, PEFT or safetensors. Use the separate training environment described in Help; examples remain saved.') from exc
    if config.get('device') == 'cuda':
        if not torch.cuda.is_available():
            raise ValueError('The selected training Python cannot use CUDA. Check its PyTorch build and NVIDIA driver, or choose CPU LoRA for a supported Llama model.')
    if config.get('training_method') == 'qlora':
        if config.get('device') != 'cuda' or torch.cuda.get_device_capability(0)[0] < 6:
            raise ValueError('QLoRA requires an available NVIDIA Pascal or newer CUDA GPU.')
        try:
            import bitsandbytes  # noqa: F401
            import accelerate  # noqa: F401
        except (ImportError, OSError, RuntimeError) as exc:
            raise RuntimeError('The selected training Python needs working bitsandbytes and Accelerate packages for QLoRA.') from exc
    if gemma:
        if config.get('training_method') != 'qlora' or config.get('device') != 'cuda':
            raise ValueError('Gemma 4 training uses CUDA QLoRA. Apply the Gemma starting settings in preparation details.')
        from packaging.version import Version
        if Version(importlib.metadata.version('transformers')) < Version('5.17.0'):
            raise ValueError('Gemma 4 needs Transformers 5.17 or newer in the separate training environment.')
    tokenizer = AutoTokenizer.from_pretrained(str(base), local_files_only=True, trust_remote_code=False)
    text_config = architecture.get('text_config', {}) if gemma else architecture
    limit = min(config['max_length'], text_config.get('max_position_embeddings', config['max_length']))
    lengths = []
    encodings = {'teaching': [], 'comparison': []}
    for split, rows in (('teaching', train), ('comparison', evaluation)):
        for index, row in enumerate(rows):
            try:
                encoded = encode_example(tokenizer, row, limit, gemma=gemma)
                lengths.append(len(encoded['input_ids']))
                encodings[split].append(_encoding_evidence(encoded))
            except ValueError as exc:
                raise ValueError(f'{split.capitalize()} example {index + 1}: {exc}') from exc
    converter = Path(config.get('llama_cpp_dir', '')) / 'convert_lora_to_gguf.py'
    if not converter.is_file():
        raise ValueError('Choose the local llama.cpp source folder containing convert_lora_to_gguf.py so the trained version can be used in Chat.')
    checked = subprocess.run([sys.executable, str(converter), '--help'], capture_output=True,
                             text=True, timeout=60, check=False)
    if checked.returncode:
        raise ValueError('The adapter converter needs dependencies in this training Python. ' + (checked.stderr or checked.stdout)[-2000:])
    result = {'ready': True, 'max_example_tokens': max(lengths), 'training_examples': len(train),
              'evaluation_examples': len(evaluation), **_tokenization_metadata(encodings),
              'summary': f'{len(train)} teaching and {len(evaluation)} comparison examples fit; longest is {max(lengths)} tokens. Runtime and converter are available. Memory capacity is confirmed only during training.'}
    (run_dir / 'readiness.json').write_text(json.dumps(result), encoding='utf-8')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', required=True)
    parser.add_argument('--check-only', action='store_true', help='Check runtime, examples and conversion without loading or optimizing weights')
    args = parser.parse_args()
    try:
        if args.check_only:
            check_training(args.run_dir)
        else:
            run_training(args.run_dir)
    except Exception as exc:
        message = str(exc)
        if 'out of memory' in message.lower():
            message += ' Reduce sequence length, batch size or adapter rank, close other GPU workloads, or select a smaller base model. Increase gradient accumulation to keep a larger effective batch without storing more examples at once.'
        progress(f'Training failed: {type(exc).__name__}: {message}')
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
