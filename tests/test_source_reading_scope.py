"""Task reading scope, with real source tools, packing, exposure and persistence."""
import json

import pytest

from letracode.evidence import evidence_state
from letracode.continuation import RunLimits
from letracode.store import Store
from letracode.worker import ConversationWorker
from test_source_read_limitations import ScriptedSource, ANSWER, read


def setup(tmp_path, request):
    root = tmp_path / 'sources'; root.mkdir()
    target = root / 'Route.md'
    target.write_text('The departure route is East Pier.\n' + 'ordinary detail\n' * 1100 + 'THE END', encoding='utf-8', newline='')
    incidental = root / 'Archive.md'
    incidental.write_text('Historical departure route elsewhere.\n' + 'unrelated archive\n' * 3000)
    store = Store(tmp_path / 'data')
    project = store.create_project('Scope fixture'); store.link(project, root)
    chat = store.create_chat('Reading scope', project)
    origin = store.add_message(chat, 'user', request)
    return store, chat, origin, target, incidental


def run(store, chat, steps):
    engine = ScriptedSource(steps)
    # The saved recurring run uses this context and output allowance.
    engine.config = type('Config', (), {'context_size': 53248, 'max_tokens': 3072})()
    ConversationWorker(store, chat, engine).run()
    return engine, store.messages(chat)


def result_names(rows):
    return [json.loads(row['payload'])['message']['name'] for row in rows if row['role'] == 'tool']


def test_narrow_search_finishes_without_turning_hits_into_whole_file_obligations(tmp_path):
    store, chat, origin, target, incidental = setup(tmp_path, 'Which departure route does the current note name?')
    engine, rows = run(store, chat, [('search_project', {'query': 'departure route'}), None])
    assert len(engine.requests) == 2
    assert result_names(rows) == ['search_project']
    state = evidence_state(rows, origin)
    assert {file['path'] for file in state['files']} == {str(target), str(incidental)}
    assert state['incomplete'] and not state['whole_work_verified']
    assert all(file['missing_ranges'] and not file['complete_supported_text'] for file in state['files'])
    assert sum(row['content'] == ANSWER for row in rows) == 1
    answer = next(row for row in rows if row['content'] == ANSWER)
    assert json.loads(answer['payload'])['pause_context_closed']
    assert json.loads(answer['payload'])['coverage'] == state
    assert [row['id'] for row in rows if row['role'] == 'user'] == [origin]
    fresh = ScriptedSource([None])
    ConversationWorker(Store(store.directory), chat, fresh).run()
    assert not fresh.requests and store.messages(chat) == rows


@pytest.mark.parametrize('explicit_scope', [False, True])
def test_whole_file_read_completes_only_named_target_after_search(tmp_path, explicit_scope):
    store, chat, origin, target, incidental = setup(tmp_path, 'Read Route.md completely, from beginning to end.')
    args = {'scope': 'whole_file'} if explicit_scope else {}
    engine, rows = run(store, chat, [('search_project', {'query': 'departure route'}),
                                   read(target, max_chars=100, **args), None])
    state = evidence_state(rows, origin)
    files = {file['path']: file for file in state['files']}
    assert files[str(target)]['complete_supported_text']
    assert files[str(target)]['exposed_ranges'] == [[0, len(target.read_text())]]
    assert not files[str(incidental)]['complete_supported_text']
    assert state['incomplete']  # Incidental partial exposure remains truthful.
    pages = [json.loads(json.loads(row['payload'])['message']['content']) for row in rows
             if row['role'] == 'tool' and json.loads(row['payload'])['message']['name'] == 'read_file']
    assert all(page.get('path') == str(target) for page in pages)
    assert any(json.loads(row['payload']).get('application_generated') == 'source_read_recovery' for row in rows)
    assert len(engine.requests) < 10
    assert sum(row['role'] == 'user' for row in rows) == 1


def test_explicit_passage_read_does_not_commit_to_untouched_tail(tmp_path):
    store, chat, origin, target, _ = setup(tmp_path, 'Quote only the departure route sentence.')
    engine, rows = run(store, chat, [read(target, scope='passage', max_chars=100), None])
    assert len(engine.requests) == 2
    result = json.loads(json.loads(next(row for row in rows if row['role'] == 'tool')['payload'])['message']['content'])
    assert 'error' not in result and len(result['text']) == 100
    assert result['next_read_file']['scope'] == 'passage'
    state = evidence_state(rows, origin)
    assert state['files'][0]['exposed_ranges'] == [[0, 100]]
    assert state['incomplete'] and not state['files'][0]['complete_supported_text']


def test_later_passage_does_not_cancel_existing_whole_file_obligation(tmp_path):
    store, chat, origin, target, _ = setup(tmp_path, 'Read all of Route.md, every line.')
    _, rows = run(store, chat, [read(target, max_chars=100),
                               read(target, scope='passage', offset=100, max_chars=100), None])
    results = [json.loads(json.loads(row['payload'])['message']['content']) for row in rows if row['role'] == 'tool']
    assert all('error' not in result for result in results)
    state = evidence_state(rows, origin)
    assert state['files'][0]['complete_supported_text']
    assert not state['incomplete']


def test_invalid_reading_scope_does_not_read_source(tmp_path):
    store, chat, origin, target, _ = setup(tmp_path, 'Read the source.')
    _, rows = run(store, chat, [read(target, scope='invented'), None])
    result = next(row for row in rows if row['role'] == 'tool')
    body = json.loads(json.loads(result['payload'])['message']['content'])
    assert 'error' in body and 'scope' in body['error'].lower()
    assert evidence_state(rows, origin)['files'] == []


@pytest.mark.parametrize('passage', [False, True])
@pytest.mark.parametrize('prompt', [
    'Read Route.md completely before answering. Search Archive.md for comparison.',
    'Read Route.md completely, but only search Archive.md for comparison.',
    'Read Route.md completely, but don’t read Archive.md in full.',
])
def test_user_named_complete_read_cannot_be_skipped_by_search_or_passage_answer(tmp_path, passage, prompt):
    store, chat, origin, target, incidental = setup(tmp_path, prompt)
    steps = [('search_project', {'query': 'departure route'})]
    if passage:
        steps.append(read(target, scope='passage', max_chars=100))
    _, rows = run(store, chat, [*steps, None])
    files = {file['path']: file for file in evidence_state(rows, origin)['files']}
    assert files[str(target)]['complete_supported_text']
    assert not files[str(incidental)]['complete_supported_text']


@pytest.mark.parametrize('prompt', [
    'Do not read Route.md in full. Which departure route is named?',
    "Don't read the entire Route.md. Only find the departure route sentence.",
    'What does the departure route suggest about the whole story?',
    'Read Route.md as a passage, not the whole file.',
    'Explain this instruction: "read Route.md completely".',
    'Explain this quoted instruction: "First locate the source. Read Route.md completely."',
    'Discuss this example:\n```text\nFirst locate the source. Read Route.md completely.\n```',
    'Discuss this quote:\n> First locate the source. Read Route.md completely.',
])
def test_narrow_or_negated_request_does_not_create_whole_file_requirement(tmp_path, prompt):
    store, chat, origin, target, _ = setup(tmp_path, prompt)
    engine, rows = run(store, chat, [('search_project', {'query': 'departure route'}), None])
    assert len(engine.requests) == 2
    assert result_names(rows) == ['search_project']
    assert all(not file['complete_supported_text'] for file in evidence_state(rows, origin)['files'])


def test_user_complete_reading_rule_for_any_read_files_is_preserved(tmp_path):
    store, chat, origin, _, _ = setup(tmp_path,
        'Which departure route? If you read files, make sure that you read the complete file before responding.')
    _, rows = run(store, chat, [('search_project', {'query': 'departure route'}), None])
    assert not evidence_state(rows, origin)['incomplete']


def test_later_real_user_can_narrow_a_paused_full_read_requirement(tmp_path):
    store, chat, origin, target, _ = setup(tmp_path, 'Read Route.md completely.')
    first = ScriptedSource([read(target, max_chars=100)])
    ConversationWorker(store, chat, first, limits=RunLimits(max_requests=1)).run()
    assert store.messages(chat)[-1]['status'] == 'paused'
    before = result_names(store.messages(chat))
    store.add_message(chat, 'user', 'Do not read Route.md in full. Answer using the available passage.')
    engine, rows = run(store, chat, [None])
    assert len(engine.requests) == 1 and result_names(rows) == before
    assert evidence_state(rows, origin)['incomplete']
    assert len([row for row in rows if row['role'] == 'user']) == 2


def test_exact_user_path_does_not_require_a_different_file_with_same_name(tmp_path):
    store, chat, origin, target, _ = setup(tmp_path, 'Temporary prompt.')
    other_dir = target.parent / 'old'; other_dir.mkdir()
    other = other_dir / target.name
    other.write_text('Old departure route.\n' + 'unrelated\n' * 1000)
    store.update_message(origin, f'Read {target} completely before answering.')
    _, rows = run(store, chat, [('search_project', {'query': 'departure route'}), None])
    files = {file['path']: file for file in evidence_state(rows, origin)['files']}
    assert files[str(target)]['complete_supported_text']
    assert not files[str(other)]['complete_supported_text']


def test_quoted_filename_still_binds_explicit_complete_read(tmp_path):
    store, chat, origin, target, incidental = setup(tmp_path, 'Read "Route.md" completely before answering.')
    _, rows = run(store, chat, [('search_project', {'query': 'departure route'}), None])
    files = {file['path']: file for file in evidence_state(rows, origin)['files']}
    assert files[str(target)]['complete_supported_text']
    assert not files[str(incidental)]['complete_supported_text']
