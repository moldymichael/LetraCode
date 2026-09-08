"""Focused independent review reproductions with synthetic inference only."""
import json
import threading

from letracode.budgeting import RequestUsage
from letracode.engine import Cancelled
from letracode.store import Store
from letracode.tools import ToolExecutor
from letracode.worker import ConversationWorker


class ReviewEngine:
    config = type('Config', (), {'context_size': 32768, 'max_tokens': 1024})()

    def __init__(self):
        self.requests = 0

    def start(self, *args):
        pass

    def cancel(self):
        pass

    def request_usage(self, messages, tools, cancel, thinking=False):
        return RequestUsage(len(json.dumps(messages)) // 3 + 1000, 1024, 128, 'review fixture')


def tool(name, args, ident):
    return {'id': ident, 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args)}}


def fixture(tmp_path):
    sources = tmp_path / 'sources'
    sources.mkdir()
    store = Store(tmp_path / 'data')
    project = store.create_project('Review fixture')
    store.link(project, sources)
    chat = store.create_chat('Review', project)
    store.add_message(chat, 'user', 'Read chapter.txt and follow my latest direction.')
    return sources, store, chat


def test_new_input_between_batch_actions_prevents_the_next_write(tmp_path, monkeypatch):
    sources, store, chat = fixture(tmp_path)
    chapter = sources / 'chapter.txt'
    chapter.write_text('Readable evidence.')
    destination = sources / 'must-not-write.txt'

    class Batch(ReviewEngine):
        def complete(self, *args):
            self.requests += 1
            return {'role': 'assistant', 'content': 'Read and then write.', 'tool_calls': [
                tool('read_file', {'path': str(chapter)}, 'read'),
                tool('write_file', {'path': str(destination), 'content': 'old direction', 'expected_sha256': None}, 'write'),
            ]}

    execute = ToolExecutor.execute

    def save_new_input(executor, name, args):
        result = execute(executor, name, args)
        if name == 'read_file':
            store.add_message(chat, 'user', 'Stop the earlier plan. Do not write any file.')
        return result

    monkeypatch.setattr(ToolExecutor, 'execute', save_new_input)
    worker = ConversationWorker(store, chat, Batch())
    approvals = []
    worker.approval_needed.connect(lambda pending: (approvals.append(pending.request), pending.decide(True)))
    worker.run()
    assert not destination.exists(), 'An old batch wrote after newer user input was saved'
    assert not approvals
    rows = store.messages(chat)
    outcomes = [json.loads(json.loads(row['payload'])['message']['content']) for row in rows if row['role'] == 'tool']
    assert len(outcomes) == 2 and outcomes[-1]['executed'] is False
    assert json.loads(rows[-1]['payload'])['checkpoint']['reason'] == 'new_input'


def test_successful_read_recovery_does_not_leave_permanent_incomplete_coverage(tmp_path):
    sources, store, chat = fixture(tmp_path)
    chapter = sources / 'chapter.txt'

    class Recovery(ReviewEngine):
        def complete(self, *args):
            self.requests += 1
            if self.requests == 2:
                chapter.write_text('The complete chapter says East Pier.')
            if self.requests <= 2:
                return {'role': 'assistant', 'content': 'Read the available chapter.', 'tool_calls': [
                    tool('read_file', {'path': str(chapter)}, f'read-{self.requests}') ]}
            return {'role': 'assistant', 'content': 'The complete available chapter says East Pier.'}

    engine = Recovery()
    ConversationWorker(store, chat, engine).run()
    rows = store.messages(chat)
    assert engine.requests == 3, 'A recovered source error permanently blocked a final response'
    assert rows[-1]['role'] == 'assistant' and rows[-1]['status'] == 'complete'


def test_old_async_cancel_cannot_cancel_a_new_worker_using_the_same_engine(tmp_path):
    sources, store, chat = fixture(tmp_path)
    cancel_started, release_cancel, cancel_done = threading.Event(), threading.Event(), threading.Event()

    class Reused(ReviewEngine):
        cancelled = False

        def cancel(self):
            cancel_started.set()
            release_cancel.wait(5)
            self.cancelled = True
            cancel_done.set()

        def start(self, *args):
            self.cancelled = False
            release_cancel.set()

        def complete(self, *args):
            self.requests += 1
            assert cancel_done.wait(5)
            if self.cancelled:
                raise Cancelled('The previous worker cancelled this new request')
            return {'role': 'assistant', 'content': 'A fresh response to the new user request.'}

    engine = Reused()
    old = ConversationWorker(store, chat, engine, use_tools=False)
    try:
        old.request_stop()
        assert cancel_started.wait(5)
        old.run()
        store.add_message(chat, 'user', 'Start a fresh ordinary response.')
        ConversationWorker(store, chat, engine, use_tools=False).run()
        rows = store.messages(chat)
        assert rows[-1]['role'] == 'assistant' and rows[-1]['status'] == 'complete', (
            'Old cancellation outlived its worker and interrupted the next worker')
    finally:
        release_cancel.set()
        cancel_done.wait(5)


def test_repeated_stop_is_nonblocking_and_has_one_owned_cancel(tmp_path):
    _, store, chat = fixture(tmp_path)
    started, release, returned = threading.Event(), threading.Event(), threading.Event()
    calls = []

    class SlowCancel(ReviewEngine):
        def cancel(self):
            calls.append('cancel')
            started.set()
            release.wait(5)

    worker = ConversationWorker(store, chat, SlowCancel())

    def stop_repeatedly():
        worker.request_stop()
        worker.request_stop()
        worker._cancel('time_budget')
        returned.set()

    stopper = threading.Thread(target=stop_repeatedly)
    stopper.start()
    runner = threading.Thread(target=worker.run)
    try:
        assert started.wait(2)
        assert returned.wait(1), 'Stopping blocked the Qt caller on engine teardown'
        runner.start()
        runner.join(0.1)
        assert runner.is_alive(), 'Worker returned while its engine cancel was unfinished'
        assert calls == ['cancel'], 'Repeated stop launched competing cancellations'
    finally:
        release.set()
        stopper.join(2)
        if runner.ident is not None:
            runner.join(2)
    assert not runner.is_alive()
    worker.request_stop()
    assert calls == ['cancel']
