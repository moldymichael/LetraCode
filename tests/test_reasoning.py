"""Model thinking stays separate from answers across transport and saved runs."""
import io
import json
import threading
import time
from types import SimpleNamespace

import pytest

from letracode.engine import Cancelled, EngineConfig, EngineError, LocalEngine
from letracode.store import Store
from letracode.worker import ConversationWorker, conversation_messages
from test_worker import ScriptedEngine
from test_engine import fake_server, gguf_model, make_engine


def stream_reply(tmp_path, deltas, *, finish='stop', on_reasoning=None, on_delta=None):
    events = [{'choices': [{'delta': delta, 'finish_reason': None}]} for delta in deltas]
    events.append({'choices': [{'delta': {}, 'finish_reason': finish}]})
    body = ''.join('data: ' + json.dumps(event) + '\n\n' for event in events) + 'data: [DONE]\n\n'
    engine = LocalEngine(EngineConfig(), tmp_path / 'engine')
    return engine._read_completion_stream(io.BytesIO(body.encode()), SimpleNamespace(), threading.Event(),
        on_delta or (lambda _: None), time.monotonic() + 5, on_reasoning=on_reasoning)


@pytest.mark.parametrize('field', ['reasoning_content', 'reasoning'])
def test_streamed_thinking_is_returned_separately_from_answer(tmp_path, field):
    answer, thinking = [], []
    reply = stream_reply(tmp_path, [{field: 'Check '}, {field: 'the sum', 'content': 'Four.'}],
                         on_delta=answer.append, on_reasoning=thinking.append)
    assert answer == ['Four.']
    assert thinking == ['Check ', 'the sum']
    assert reply == {'role': 'assistant', 'content': 'Four.', 'reasoning_content': 'Check the sum'}


@pytest.mark.parametrize(('pieces', 'reasoning', 'answer'), [
    (['<thi', 'nk>Consider ', 'it.</th', 'ink>', 'Four.'], 'Consider it.', 'Four.'),
    (['\n<think>Unfinished thought'], 'Unfinished thought', ''),
    (['Explain <think> tags.'], '', 'Explain <think> tags.'),
    (['<th', 'imble>'], '', '<thimble>'),
    (['<think>'], '', ''),
])
def test_leading_think_block_is_split_across_token_boundaries(tmp_path, pieces, reasoning, answer):
    reply = stream_reply(tmp_path, [{'content': piece} for piece in pieces])
    assert reply['content'] == answer
    assert reply.get('reasoning_content', '') == reasoning


@pytest.mark.parametrize('field', ['reasoning_content', 'reasoning'])
def test_malformed_reasoning_is_a_protocol_error(tmp_path, field):
    with pytest.raises(EngineError, match='reasoning'):
        stream_reply(tmp_path, [{field: ['not text']}])


def test_reasoning_and_answer_share_the_response_byte_limit(tmp_path, monkeypatch):
    monkeypatch.setattr('letracode.engine._MAX_OUTPUT_BYTES', 9)
    with pytest.raises(EngineError, match='output size limit'):
        stream_reply(tmp_path, [{'reasoning_content': 'ééé'}, {'content': 'Four'}])


class ThinkingEngine(ScriptedEngine):
    def __init__(self, outcome='complete'):
        self.outcome = outcome

    def complete(self, messages, tools, cancel, on_delta, thinking=False, *, on_reasoning=None):
        if on_reasoning:
            on_reasoning('First thought. ')
            on_reasoning('Last thought.')
        if self.outcome == 'interrupted':
            cancel.set()
            raise Cancelled()
        if self.outcome == 'error':
            raise EngineError('Peer failed after thinking')
        on_delta('Answer only.')
        return {'role': 'assistant', 'content': 'Answer only.', 'reasoning_content': 'First thought. Last thought.'}


@pytest.mark.parametrize('outcome', ['complete', 'interrupted', 'error'])
def test_worker_saves_thinking_separately_including_unsaved_tail(tmp_path, outcome):
    store = Store(tmp_path / 'data')
    chat = store.create_chat('Thinking')
    store.add_message(chat, 'user', 'Calculate')
    worker = ConversationWorker(store, chat, ThinkingEngine(outcome), thinking=True, use_tools=False,
                                web_enabled=False, computer_enabled=False)
    snapshots = []
    worker.changed.connect(lambda: snapshots.extend(store.messages(chat)))
    worker.run()
    saved = next(row for row in Store(tmp_path / 'data').messages(chat) if row['role'] == 'assistant')
    payload = json.loads(saved['payload'])
    assert payload['reasoning'] == 'First thought. Last thought.'
    assert saved['status'] == outcome
    assert 'First thought' not in saved['content']
    assert payload['continuation']['version'] == 1
    assert payload['run_configuration']['thinking'] is True
    assert any(json.loads(row['payload']).get('reasoning') == 'First thought. '
               for row in snapshots if row['status'] == 'streaming')
    if outcome == 'complete':
        assert payload['message'] == {'role': 'assistant', 'content': 'Answer only.'}
        messages, _ = conversation_messages(store.messages(chat), 'System', 20000)
        assert 'First thought' not in json.dumps(messages)


def test_public_engine_streams_thinking_alongside_native_tools(tmp_path, fake_server, gguf_model, python_engine_peer):
    engine = make_engine(fake_server, gguf_model, tmp_path / 'data')
    answers, thoughts = [], []
    try:
        reply = engine.complete([{'role': 'user', 'content': 'stream-tools'}], None,
                                threading.Event(), answers.append, True, on_reasoning=thoughts.append)
        assert ''.join(answers) == 'Hello'
        assert ''.join(thoughts) == 'private chain remains private'
        assert reply['reasoning_content'] == 'private chain remains private'
        assert reply['tool_calls'][0]['function']['name'] == 'lookup'
    finally:
        engine.stop()
