"""Dependency-free validation and views of reviewed conversation examples.

Recorded function calls and results are training data only. This module never
loads a model, resolves a tool implementation, or executes a recorded call.
"""
from __future__ import annotations

import hashlib
import json
import re


MAX_TEXT = 100_000
MAX_MESSAGES = 1000
MAX_TOOLS = 128
MAX_TOOL_CALLS = 128
_METADATA = {'id', 'prompt', 'response', 'split', 'approved', 'source', 'created', 'updated'}
_NAME = re.compile(r'[A-Za-z0-9_-]{1,64}')


def _fields(value, allowed, location):
    if not isinstance(value, dict):
        raise ValueError(f'{location} must be a JSON object')
    unknown = set(value) - allowed
    if unknown:
        raise ValueError(f'{location} contains unsupported fields: {sorted(unknown, key=str)}')


def _text(value, location, *, empty=False):
    if not isinstance(value, str):
        raise ValueError(f'{location} must be text')
    if not empty and not value.strip():
        raise ValueError(f'{location} is required')
    if len(value) > MAX_TEXT:
        raise ValueError(f'{location} is limited to {MAX_TEXT} characters')
    return value


def _json_copy(value, location):
    # JSON encoders otherwise silently coerce non-text object keys and accept
    # NaN; neither has a portable meaning in a function schema or arguments.
    def check(item):
        if isinstance(item, dict):
            if any(not isinstance(key, str) for key in item):
                raise ValueError('object keys must be text')
            for child in item.values():
                check(child)
        elif isinstance(item, list):
            for child in item:
                check(child)
        elif item is not None and type(item) not in (str, int, float, bool):
            raise ValueError('unsupported JSON value')
    try:
        check(value)
        return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError, RecursionError) as error:
        raise ValueError(f'{location} must contain valid JSON: {error}') from error


def _tools(value):
    if not isinstance(value, list):
        raise ValueError('tools must be a list')
    if len(value) > MAX_TOOLS:
        raise ValueError(f'Examples are limited to {MAX_TOOLS} tools')
    names = set()
    tools = []
    for index, tool in enumerate(value, 1):
        location = f'tool {index}'
        _fields(tool, {'type', 'function'}, location)
        if tool.get('type') != 'function':
            raise ValueError(f'{location} type must be function')
        function = tool.get('function')
        _fields(function, {'name', 'description', 'parameters', 'strict'}, f'{location} function')
        name = function.get('name')
        if not isinstance(name, str) or not _NAME.fullmatch(name):
            raise ValueError(f'{location} function name must be 1–64 letters, digits, underscores or hyphens')
        if name in names:
            raise ValueError(f'{location} has duplicate function name {name!r}')
        names.add(name)
        if 'description' in function:
            _text(function['description'], f'{location} description', empty=True)
        if 'strict' in function and type(function['strict']) is not bool:
            raise ValueError(f'{location} strict must be true or false')
        parameters = function.get('parameters')
        if not isinstance(parameters, dict) or parameters.get('type', 'object') != 'object':
            raise ValueError(f'{location} parameters must be a JSON object schema')
        tools.append(_json_copy(tool, location))
    return tools, names


def normalize_example(row):
    """Return a validated version-2 record, upgrading a legacy pair if needed.

    Known repository metadata and display summaries are accepted but never become
    training input. Structured text is preserved exactly; only the legacy pair
    API retains its historical leading/trailing-whitespace cleanup.
    """
    _fields(row, _METADATA | {'schema_version', 'messages', 'tools'}, 'example')
    if 'schema_version' in row and (type(row['schema_version']) is not int or row['schema_version'] != 2):
        raise ValueError('schema_version must be 2')
    if 'messages' not in row:
        if 'tools' in row or 'schema_version' in row:
            raise ValueError('Structured examples require messages')
        prompt = _text(row.get('prompt'), 'Prompt').strip()
        response = _text(row.get('response'), 'Response').strip()
        return {'schema_version': 2, 'messages': [
            {'role': 'user', 'content': prompt},
            {'role': 'assistant', 'content': response, 'train': True}], 'tools': []}

    tools, names = _tools(row.get('tools', []))
    if not isinstance(row['messages'], list) or not row['messages']:
        raise ValueError('messages must be a nonempty list')
    if len(row['messages']) > MAX_MESSAGES:
        raise ValueError(f'Examples are limited to {MAX_MESSAGES} messages')
    messages, seen_ids, pending = [], set(), {}
    targets = 0
    for index, message in enumerate(row['messages'], 1):
        location = f'message {index}'
        if not isinstance(message, dict):
            raise ValueError(f'{location} must be a JSON object')
        role = message.get('role')
        if role not in ('system', 'user', 'assistant', 'tool'):
            raise ValueError(f'{location} role must be system, user, assistant or tool')
        allowed = {'role', 'content'}
        if role == 'assistant':
            allowed |= {'train', 'tool_calls'}
        elif role == 'tool':
            allowed |= {'tool_call_id', 'name'}
        _fields(message, allowed, location)
        if role == 'system' and index != 1:
            raise ValueError(f'{location}: system message must be first')
        if pending and role != 'tool':
            raise ValueError(f'{location}: tool calls require exactly one result each before conversation resumes: {sorted(pending)}')
        calls = message.get('tool_calls', [])
        if role == 'assistant' and ('tool_calls' in message and (not isinstance(calls, list) or not calls)):
            raise ValueError(f'{location} tool_calls must be a nonempty list')
        if len(calls) > MAX_TOOL_CALLS:
            raise ValueError(f'{location} is limited to {MAX_TOOL_CALLS} tool calls')
        content = _text(message.get('content', '' if calls else None), f'{location} content',
                        empty=role == 'tool' or bool(calls))
        normalized = {'role': role, 'content': content}
        if role == 'assistant':
            train = message.get('train', True)
            if type(train) is not bool:
                raise ValueError(f'{location} train must be true or false')
            normalized['train'] = train
            targets += int(train)
            if calls:
                normalized['tool_calls'] = []
            for call_index, call in enumerate(calls, 1):
                call_location = f'{location} call {call_index}'
                _fields(call, {'id', 'type', 'function'}, call_location)
                ident = _text(call.get('id'), f'{call_location} id')
                if ident in seen_ids:
                    raise ValueError(f'{call_location}: duplicate call id {ident!r}')
                if call.get('type') != 'function':
                    raise ValueError(f'{call_location} type must be function')
                function = call.get('function')
                _fields(function, {'name', 'arguments'}, f'{call_location} function')
                name = function.get('name')
                if not isinstance(name, str) or name not in names:
                    raise ValueError(f'{call_location} references unknown tool {name!r}')
                arguments = function.get('arguments')
                if isinstance(arguments, str):
                    try:
                        arguments = json.loads(arguments)
                    except (ValueError, RecursionError) as error:
                        raise ValueError(f'{call_location} arguments must be a JSON object: {error}') from error
                if not isinstance(arguments, dict):
                    raise ValueError(f'{call_location} arguments must be a JSON object')
                arguments = _json_copy(arguments, f'{call_location} arguments')
                normalized['tool_calls'].append({'id': ident, 'type': 'function',
                                                'function': {'name': name, 'arguments': arguments}})
                seen_ids.add(ident)
                pending[ident] = name
        elif role == 'tool':
            ident = message.get('tool_call_id')
            if not isinstance(ident, str) or ident not in pending:
                raise ValueError(f'{location} tool_call_id must reference an outstanding call (no unknown or duplicate results)')
            if 'name' in message:
                if message['name'] != pending[ident]:
                    raise ValueError(f'{location} name must match the recorded call')
                normalized['name'] = message['name']
            normalized['tool_call_id'] = ident
            del pending[ident]
        messages.append(normalized)
    if pending:
        raise ValueError(f'Tool calls are missing results: {sorted(pending)}')
    if not targets:
        raise ValueError('Example requires at least one assistant training target')
    return {'schema_version': 2, 'messages': messages, 'tools': tools}


def _assistant_summary(message):
    text = message['content']
    if message.get('tool_calls'):
        calls = json.dumps(message['tool_calls'], ensure_ascii=False, allow_nan=False)
        text = f'{text}\n{calls}' if text else calls
    return text


def example_summary(row):
    """Display the first question and last assistant text/calls, never flatten for training."""
    example = normalize_example(row)
    prompt = next((message['content'] for message in example['messages'] if message['role'] == 'user'), '')
    response = next(_assistant_summary(message) for message in reversed(example['messages'])
                    if message['role'] == 'assistant')
    return prompt, response


def comparison_example(row):
    """Select the first trained assistant and its entire native-template prefix."""
    example = normalize_example(row)
    index = next(index for index, message in enumerate(example['messages'])
                 if message['role'] == 'assistant' and message['train'])
    prefix = [{key: value for key, value in message.items() if key != 'train'}
              for message in example['messages'][:index]]
    prompt = next((message['content'] for message in prefix if message['role'] == 'user'), '')
    target = example['messages'][index]
    return {'prompt': prompt, 'response': _assistant_summary(target), 'messages': prefix,
            'tools': example['tools'], 'target_message': target, 'target_index': index}


def example_identity(row):
    """Key conditioning before the first trained assistant, including tool schemas."""
    example = comparison_example(row)
    for message in example['messages']:
        message['content'] = ' '.join(message['content'].split()).casefold()
    encoded = json.dumps({'messages': example['messages'], 'tools': example['tools']},
                         ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(encoded.encode('utf-8')).hexdigest()
