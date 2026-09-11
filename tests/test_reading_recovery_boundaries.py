"""Real persistence/source boundaries for application-driven reading recovery."""
import hashlib
import json
import zipfile

import pytest

from letracode.evidence import evidence_state
from letracode.store import Store
from letracode.worker import ConversationWorker, conversation_messages
from test_reading_recovery import Reader, saved_pages, setup


AUTO_READ = 'Reading next source page automatically…'


def paired(messages):
    pending = []
    for message in messages:
        if message['role'] == 'tool':
            assert message['tool_call_id'] in pending
            pending.remove(message['tool_call_id'])
        else:
            assert not pending, 'An application-generated tool call lost its paired result'
            pending = [call['id'] for call in message.get('tool_calls', [])]
    assert not pending


def test_recovery_restarts_missing_prefix_when_source_shrinks_between_pages(tmp_path):
    old = 'original source ' * 1500
    new = 'Changed αβ\r\nTHE END'
    store, chat, origin, engine = setup(tmp_path, old)
    worker = ConversationWorker(store, chat, engine)
    automatic = []

    def change_source(status):
        if status == AUTO_READ:
            automatic.append(status)
            if len(automatic) == 2:
                engine.path.write_text(new, encoding='utf-8', newline='')

    worker.status.connect(change_source)
    worker.run()
    assert len(automatic) >= 2, 'No automatic paging reached the version-change boundary'
    rows = store.messages(chat)
    file = evidence_state(rows, origin)['files'][0]
    old_sha = hashlib.sha256(old.encode()).hexdigest()
    new_sha = hashlib.sha256(new.encode()).hexdigest()
    assert {version['source_sha256'] for version in file['versions']} == {old_sha, new_sha}
    assert file['source_sha256'] == new_sha
    assert file['exposed_ranges'] == [[0, len(new)]]
    assert file['complete_supported_text']
    assert any(page.get('sha256') == new_sha and page.get('offset') == 0
               and page.get('text') == new for _, page in saved_pages(store, chat))
    assert rows[-1]['status'] == 'complete'
    for request in engine.requests:
        paired(request)


def test_truncated_document_reads_supported_text_then_stops_without_empty_eof_loop(tmp_path, monkeypatch):
    import letracode.context as context

    monkeypatch.setattr(context, 'MAX_FILE', 9000)
    store, chat, origin, engine = setup(tmp_path, '')
    engine.path.unlink()
    engine.path = engine.path.with_suffix('.docx')
    with zipfile.ZipFile(engine.path, 'w', zipfile.ZIP_DEFLATED) as document:
        document.writestr('word/document.xml',
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            '<w:body><w:p><w:r><w:t>' + 'paragraph ' * 1100 +
            '</w:t></w:r></w:p></w:body></w:document>')
    store.update_message(origin, f'Read {engine.path} from beginning to end, every line.')
    ConversationWorker(store, chat, engine).run()
    rows = store.messages(chat)
    state = evidence_state(rows, origin)
    file = state['files'][0]
    assert file['source_truncated'] and state['incomplete']
    assert file['exposed_ranges'] == [[0, 9000]]
    assert file['missing_ranges'] == []
    assert not file['complete_supported_text']
    pages = [page for _, page in saved_pages(store, chat) if 'text' in page]
    assert pages and all(page['text'] for page in pages), 'Unsupported extraction tail caused empty EOF retries'
    assert len(pages) < 10
    assert rows[-1]['status'] != 'complete'
    assert not any(row['status'] == 'complete' and row['content'] == 'I read the entire file.' for row in rows)


@pytest.mark.parametrize('boundary', ['cancel', 'new_input', 'denial'])
def test_recovery_dispatch_obeys_stop_new_input_and_read_denial(tmp_path, monkeypatch, boundary):
    import letracode.tools as tools

    store, chat, origin, engine = setup(tmp_path, 'source ' * 3000 + 'THE END')
    worker = ConversationWorker(store, chat, engine)
    triggered = []
    enabled = tools.tool_enabled

    def permit(name, **permissions):
        if boundary == 'denial' and triggered and name == 'read_file':
            return False
        return enabled(name, **permissions)

    monkeypatch.setattr(tools, 'tool_enabled', permit)

    def stop_before_read(status):
        if status != AUTO_READ or triggered:
            return
        triggered.append(status)
        if boundary == 'cancel':
            worker.request_stop()
        elif boundary == 'new_input':
            store.add_message(chat, 'user', 'Stop reading that file now.')

    worker.status.connect(stop_before_read)
    worker.run()
    assert triggered, 'The boundary must interrupt an application-generated read'
    rows = store.messages(chat)
    pages = saved_pages(store, chat)
    assert len([page for _, page in pages if 'text' in page]) == 1
    assert len(engine.requests) == 2, 'The halted recovery allowed another inference request'
    assert evidence_state(rows, origin)['incomplete']
    if boundary == 'denial':
        assert any('denied' in page for _, page in pages)
        assert json.loads(rows[-1]['payload'])['checkpoint']['reason'] == 'approval_denied'
    elif boundary == 'new_input':
        assert json.loads(rows[-1]['payload'])['checkpoint']['reason'] == 'new_input'
    else:
        assert rows[-1]['status'] in ('interrupted', 'paused')
    saved_protocol = [json.loads(row['payload'])['message'] for row in rows
                      if row['role'] in ('assistant', 'tool') and 'message' in json.loads(row['payload'])]
    paired(saved_protocol)


def test_automatic_read_calls_and_completed_exposure_survive_reopen(tmp_path):
    source = 'line αβ\r\n' * 2200 + 'THE END'
    store, chat, origin, engine = setup(tmp_path, source)
    ConversationWorker(store, chat, engine).run()
    rows = store.messages(chat)
    assert not evidence_state(rows, origin)['incomplete']
    generated = [row for row in rows if json.loads(row['payload']).get('application_generated') == 'source_read_recovery']
    assert generated
    assert all(json.loads(row['payload']).get('request_completed') is False for row in generated)
    reopened = Store(store.directory)
    assert reopened.messages(chat) == rows
    assert evidence_state(reopened.messages(chat), origin)['files'][0]['exposed_ranges'] == [[0, len(source)]]
    replay, _ = conversation_messages(reopened.messages(chat), 'Retain complete source evidence.', 57392)
    paired(replay)
    for request in engine.requests:
        paired(request)
        assert sum(message['role'] == 'user' for message in request) == 1
        assert not any(message.get('content') == 'I read the entire file.' for message in request)
    fresh = Reader(engine.path)
    ConversationWorker(reopened, chat, fresh).run()
    assert not fresh.requests, 'Reopening completed reading restarted its original user request'
    assert reopened.messages(chat) == rows


def test_escape_heavy_unicode_recovers_full_exposure_under_small_context(tmp_path):
    source = '\ufeff' + ('🐍"\\\t\r\n' * 2200) + 'THE END'
    store, chat, origin, engine = setup(tmp_path, source, {'offset': 0, 'max_chars': 16000})
    engine.config = type('Config', (), {'context_size': 12000, 'max_tokens': 1024})()
    ConversationWorker(store, chat, engine).run()
    rows = store.messages(chat)
    state = evidence_state(rows, origin)
    assert not state['incomplete']
    assert state['files'][0]['exposed_ranges'] == [[0, len(source)]]
    assert any(json.loads(message['content']).get('context_truncated')
               for request in engine.requests for message in request if message['role'] == 'tool')
    pages = [page for _, page in saved_pages(store, chat) if page.get('text')]
    assert any(0 < len(page['text']) < 4000 for page in pages[1:]), 'Escaped pages were not reduced to fit'
    assert len(pages) < 60
    for page in pages:
        assert page['text'] == source[page['offset']:page['offset'] + len(page['text'])]
    for request in engine.requests:
        paired(request)
    assert rows[-1]['status'] == 'complete'


@pytest.mark.parametrize('third_reply', ['answer', 'tool_calls'])
def test_deferred_recovery_stall_finalizes_reply_and_pairs_unexecuted_calls(tmp_path, third_reply):
    import copy

    from letracode.continuation import RunLimits

    store, chat, origin, engine = setup(tmp_path, 'The complete first source.')
    other = tmp_path / 'partly-read.txt'
    other.write_text('The second source is only partly read. ' * 500)
    store.update_message(origin, f'Read both {engine.path} and {other} from beginning to end.')

    def call(path, offset, ident):
        return {'id': ident, 'type': 'function', 'function': {
            'name': 'read_file', 'arguments': json.dumps({
                'path': str(path), 'offset': offset, 'max_chars': 100})}}

    def complete(messages, *args):
        engine.requests.append(copy.deepcopy(messages))
        request = len(engine.requests)
        if request == 1:
            calls = [call(engine.path, 0, 'first-source'), call(other, 0, 'second-source')]
        elif request == 2:
            # Repairing this invalid cursor rereads fully exposed text. The
            # deferred observation must halt only after finalizing reply three.
            calls = [call(engine.path, -1, 'invalid-cursor')]
        elif third_reply == 'tool_calls':
            calls = [call(other, 100, 'blocked-new-read')]
        else:
            return {'role': 'assistant', 'content': 'I read both entire files.'}
        return {'role': 'assistant', 'content': '', 'tool_calls': calls}

    engine.complete = complete
    ConversationWorker(store, chat, engine, limits=RunLimits(max_stalls=1)).run()
    rows = store.messages(chat)
    assert len(engine.requests) == 3
    assert rows[-1]['status'] == 'paused'
    assert json.loads(rows[-1]['payload'])['checkpoint']['reason'] == 'no_progress'
    assert evidence_state(rows, origin)['incomplete']
    if third_reply == 'answer':
        answer = next(row for row in rows if row['content'] == 'I read both entire files.')
        assert answer['status'] == 'incomplete'
        assert json.loads(answer['payload'])['task_outcome'] == 'source_incomplete'
    else:
        results = [page for payload, page in saved_pages(store, chat)
                   if payload['message']['tool_call_id'] == 'blocked-new-read']
        assert len(results) == 1
        assert results[0].get('executed') is False
        assert 'text' not in results[0]
    protocol = [json.loads(row['payload'])['message'] for row in rows
                if row['role'] in ('assistant', 'tool') and 'message' in json.loads(row['payload'])]
    paired(protocol)
