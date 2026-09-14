import copy

import pytest

from letracode.training_examples import (
    comparison_example, example_identity, example_summary, normalize_example,
)


def conversation():
    return {
        'schema_version': 2,
        'messages': [
            {'role': 'system', 'content': 'Use the recorded weather.'},
            {'role': 'user', 'content': 'How warm is Phoenix?'},
            {'role': 'assistant', 'content': '', 'train': False, 'tool_calls': [
                {'id': 'weather-1', 'type': 'function', 'function': {
                    'name': 'weather', 'arguments': {'city': 'Phoenix'}}}]},
            {'role': 'tool', 'tool_call_id': 'weather-1', 'content': '{"temperature": 32}'},
            {'role': 'assistant', 'content': 'It is 32 degrees.'},
            {'role': 'user', 'content': 'In Fahrenheit?'},
            {'role': 'assistant', 'content': '89.6 degrees.'},
        ],
        'tools': [{'type': 'function', 'function': {
            'name': 'weather', 'description': 'Read weather.',
            'parameters': {'type': 'object', 'properties': {'city': {'type': 'string'}},
                           'required': ['city']}}}],
    }


def test_legacy_pair_upgrades_and_conversation_preserves_text_and_targets():
    assert normalize_example({'prompt': ' question ', 'response': ' answer '}) == {
        'schema_version': 2, 'messages': [
            {'role': 'user', 'content': 'question'},
            {'role': 'assistant', 'content': 'answer', 'train': True}], 'tools': []}
    original = conversation()
    original['messages'][4]['content'] = '  It is 32 degrees.\n'
    row = normalize_example(original)
    assert row['messages'][2]['train'] is False
    assert row['messages'][4] == {
        'role': 'assistant', 'content': '  It is 32 degrees.\n', 'train': True}
    assert row['messages'][6]['train'] is True
    row['tools'][0]['function']['parameters']['required'].append('other')
    assert original['tools'][0]['function']['parameters']['required'] == ['city']


def test_arguments_accept_json_object_strings_and_calls_without_text():
    row = conversation()
    del row['messages'][2]['content']
    row['messages'][2]['tool_calls'][0]['function']['arguments'] = '{"city":"Phoenix"}'
    normalized = normalize_example(row)
    assert normalized['messages'][2]['content'] == ''
    assert normalized['messages'][2]['tool_calls'][0]['function']['arguments'] == {'city': 'Phoenix'}


def test_consecutive_messages_are_not_artificially_rejected():
    row = {'messages': [{'role': 'user', 'content': 'First'},
                        {'role': 'user', 'content': 'Second'},
                        {'role': 'assistant', 'content': 'Thought', 'train': False},
                        {'role': 'assistant', 'content': 'Answer'}]}
    assert len(normalize_example(row)['messages']) == 4


def test_parallel_calls_accept_results_in_either_order():
    row = conversation()
    second = copy.deepcopy(row['messages'][2]['tool_calls'][0])
    second['id'] = 'weather-2'
    row['messages'][2]['tool_calls'].append(second)
    row['messages'].insert(3, {'role': 'tool', 'tool_call_id': 'weather-2', 'content': '31'})
    assert len(normalize_example(row)['messages'][2]['tool_calls']) == 2


@pytest.mark.parametrize('mutation,error', [
    (lambda r: r.update(schema_version=3), 'schema_version'),
    (lambda r: r.update(unknown=True), 'unsupported fields'),
    (lambda r: r['messages'][1].update(role='developer'), 'message 2.*role'),
    (lambda r: r['messages'][1].update(content=[{'type': 'text', 'text': 'hello'}]), 'message 2.*text'),
    (lambda r: r['messages'][1].update(train=False), 'message 2.*unsupported fields'),
    (lambda r: r['messages'][4].update(train=1), 'message 5.*train'),
    (lambda r: r['messages'][4].update(content=''), 'message 5.*content'),
    (lambda r: r['messages'][2]['tool_calls'][0]['function'].update(arguments='[]'), 'message 3.*arguments'),
    (lambda r: r['messages'][2]['tool_calls'][0]['function'].update(arguments='not json'), 'message 3.*arguments'),
    (lambda r: r['messages'][2]['tool_calls'][0]['function'].update(name='missing'), 'message 3.*unknown tool'),
    (lambda r: r['messages'][3].update(tool_call_id='missing'), 'message 4.*call'),
    (lambda r: r['messages'][3].update(name='wrong'), 'message 4.*name'),
    (lambda r: r['messages'].pop(3), 'message 4.*result'),
    (lambda r: r['messages'].insert(4, dict(r['messages'][3])), 'message 5.*call'),
    (lambda r: r['messages'].append({'role': 'system', 'content': 'late'}), 'system.*first'),
    (lambda r: r['tools'].append(copy.deepcopy(r['tools'][0])), 'duplicate.*name'),
    (lambda r: r['tools'][0]['function'].update(parameters=[]), 'parameters'),
    (lambda r: r['tools'][0]['function'].update(parameters={'type': 'array'}), 'parameters'),
    (lambda r: r['messages'][2]['tool_calls'][0]['function'].update(arguments={'x': float('nan')}), 'JSON'),
    (lambda r: [m.update(train=False) for m in r['messages'] if m['role'] == 'assistant'], 'target'),
])
def test_invalid_conversations_fail_with_actionable_locations(mutation, error):
    row = conversation()
    mutation(row)
    with pytest.raises(ValueError, match=error):
        normalize_example(row)


def test_ids_cannot_be_reused_after_an_earlier_call_finishes():
    row = conversation()
    row['messages'][4] = copy.deepcopy(row['messages'][2])
    with pytest.raises(ValueError, match='message 5.*duplicate.*id'):
        normalize_example(row)


def test_unfinished_calls_and_tool_results_without_calls_are_rejected():
    row = conversation()
    row['messages'] = row['messages'][:3]
    with pytest.raises(ValueError, match='result'):
        normalize_example(row)
    with pytest.raises(ValueError, match='call'):
        normalize_example({'messages': [{'role': 'tool', 'tool_call_id': 'x', 'content': 'result'},
                                       {'role': 'assistant', 'content': 'answer'}]})


def test_comparison_uses_first_target_and_full_prior_context():
    row = conversation()
    preview = comparison_example(row)
    assert preview['prompt'] == 'How warm is Phoenix?'
    assert preview['response'] == 'It is 32 degrees.'
    assert [message['role'] for message in preview['messages']] == ['system', 'user', 'assistant', 'tool']
    assert 'train' not in preview['messages'][2]
    assert preview['target_index'] == 4
    assert preview['target_message'] == {'role': 'assistant', 'content': 'It is 32 degrees.', 'train': True}
    assert preview['tools'] == row['tools']
    assert example_summary(row) == ('How warm is Phoenix?', '89.6 degrees.')


def test_tool_call_targets_have_readable_response_summaries():
    row = conversation()
    row['messages'][2]['train'] = True
    preview = comparison_example(row)
    assert 'weather' in preview['response'] and 'Phoenix' in preview['response']
    assert [message['role'] for message in preview['messages']] == ['system', 'user']


def test_identity_compares_conditioning_not_target_text_or_display_prompt():
    row = conversation()
    baseline = example_identity(row)
    row['messages'][4]['content'] = 'A different answer.'
    row['messages'][6]['content'] = 'Another ending.'
    row['messages'][1]['content'] = '  HOW WARM  is Phoenix?\n'
    assert example_identity(row) == baseline
    row['messages'][3]['content'] = 'A different tool result.'
    assert example_identity(row) != baseline
    row = conversation()
    row['tools'][0]['function']['description'] = 'Changed schema'
    assert example_identity(row) != baseline
    assert example_identity({'prompt': 'Hello  THERE', 'response': 'one'}) == example_identity({
        'messages': [{'role': 'user', 'content': ' hello there '},
                     {'role': 'assistant', 'content': 'two'}]})


def test_message_and_tool_lists_are_bounded_before_expensive_processing():
    with pytest.raises(ValueError, match='1000 messages'):
        normalize_example({'messages': [{'role': 'assistant', 'content': 'answer'}] * 1001})
    row = conversation()
    row['tools'] = [{'type': 'function', 'function': {'name': f'tool_{index}', 'parameters': {}}}
                    for index in range(129)]
    with pytest.raises(ValueError, match='128 tools'):
        normalize_example(row)
    row = conversation()
    row['messages'][2]['tool_calls'] = [
        {'id': str(index), 'type': 'function', 'function': {'name': 'weather', 'arguments': {}}}
        for index in range(129)]
    with pytest.raises(ValueError, match='128 tool calls'):
        normalize_example(row)
