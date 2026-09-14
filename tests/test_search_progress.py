"""Search progress follows new evidence through real tools and worker dispatch."""
import copy
import hashlib
import json

import pytest

from letracode.budgeting import RequestUsage
from letracode.continuation import RunHalted, RunLimits, RunProgress
from letracode.store import Store
from letracode.worker import ConversationWorker


class Searches:
    # Match the saved looping run's context and output budgets. Inference and
    # token accounting remain scripted; this is not a real-model verification.
    config = type('Config', (), {'context_size': 53248, 'max_tokens': 3072})()

    def __init__(self, steps):
        self.steps, self.requests = steps, []

    def start(self, *args, **kwargs):
        pass

    def cancel(self):
        pass

    def request_usage(self, messages, *args, **kwargs):
        return RequestUsage(len(json.dumps(messages)) // 3 + 1500, 3072, 128,
                            'scripted search progress accounting')

    def complete(self, messages, *args, **kwargs):
        self.requests.append(copy.deepcopy(messages))
        index = len(self.requests) - 1
        step = self.steps[min(index, len(self.steps) - 1)] if isinstance(self.steps, list) else self.steps(index)
        if callable(step):
            step = step()
        if step is None:
            return {'role': 'assistant', 'content': 'The supported route is East Pier.'}
        name, arguments = step
        return {'role': 'assistant', 'content': 'Checking source evidence.', 'tool_calls': [{
            'id': f'search-{index}', 'type': 'function', 'function': {
                'name': name, 'arguments': json.dumps(arguments)}}]}


def fixture(tmp_path):
    sources = tmp_path / 'sources'
    sources.mkdir()
    source = sources / 'route.md'
    source.write_text('The route is East Pier.\n')
    store = Store(tmp_path / 'data')
    project = store.create_project('Synthetic search progress')
    store.link(project, sources)
    chat = store.create_chat('Route lookup', project)
    store.add_message(chat, 'user', 'What route is supported by the sources?')
    store.memory.create_file('route.md', 'The route is East Pier.\n')
    return store, chat, source


def search(name, query, **kwargs):
    return name, {'query': query, **({'scope': 'global'} if name == 'search_memory' else {}), **kwargs}


def assert_stopped(store, chat, engine, expected_requests):
    rows = store.messages(chat)
    assert len(engine.requests) == expected_requests
    checkpoint = json.loads(rows[-1]['payload'])['checkpoint']
    assert checkpoint['reason'] == 'no_progress'
    assert checkpoint['continuation']['stalls'] == 3
    assert sum(row['role'] == 'user' for row in rows) == 1
    assert all(sum(message['role'] == 'user' for message in request) == 1
               for request in engine.requests)
    assert sum(row['role'] == 'tool' for row in rows) == expected_requests
    assert not any(json.loads(row['payload']).get('segment_boundary') for row in rows)
    ConversationWorker(Store(store.directory), chat, engine).run()
    assert len(engine.requests) == expected_requests
    assert store.messages(chat) == rows


@pytest.mark.parametrize('name', ['search_project', 'search_memory'])
@pytest.mark.parametrize('reword', [False, True])
def test_repeated_search_evidence_stops_even_with_changed_query(name, reword, tmp_path):
    store, chat, source = fixture(tmp_path)
    engine = Searches(lambda index: search(name, 'route pier' + '!' * (index if reword else 0)))
    ConversationWorker(store, chat, engine).run()
    assert_stopped(store, chat, engine, 4)


@pytest.mark.parametrize('name', ['search_project', 'search_memory'])
def test_empty_searches_cannot_reset_stalls_with_changed_arguments(name, tmp_path):
    store, chat, source = fixture(tmp_path)
    engine = Searches(lambda index: search(name, 'absentword' + '!' * index))
    ConversationWorker(store, chat, engine).run()
    assert_stopped(store, chat, engine, 3)


@pytest.mark.parametrize('name', ['search_project', 'search_memory'])
def test_missing_reads_interleaved_with_unchanged_search_evidence_stop(name, tmp_path):
    store, chat, source = fixture(tmp_path)
    engine = Searches(lambda index: search(name, 'route pier' + '!' * index) if index % 3 == 2
                      else ('read_file', {'path': str(source.with_name('missing.md'))}))
    ConversationWorker(store, chat, engine).run()
    assert_stopped(store, chat, engine, 6)
    failed = [row for row in store.messages(chat) if row['role'] == 'tool'
              and json.loads(row['payload'])['message']['name'] == 'read_file']
    assert len(failed) == 4
    assert all(json.loads(row['payload'])['source_evidence'] == [] for row in failed)


def test_memory_result_subsets_reordering_and_scores_are_not_new_evidence(tmp_path):
    store, chat, source = fixture(tmp_path)
    store.memory.create_file('second.md', 'The route is East Pier. Harbor harbor.\n')
    steps = [search('search_memory', 'route', limit=20),
             search('search_memory', 'route harbor', limit=20),
             search('search_memory', 'route harbor', limit=1),
             search('search_memory', 'route! pier!', limit=20)]
    engine = Searches(steps)
    ConversationWorker(store, chat, engine).run()
    assert_stopped(store, chat, engine, 4)
    outcomes = [json.loads(json.loads(row['payload'])['message']['content'])
                for row in store.messages(chat) if row['role'] == 'tool']
    assert outcomes[0]['results'][0]['path'] != outcomes[1]['results'][0]['path']
    assert len(outcomes[2]['results']) == 1


def test_project_result_subsets_reordering_and_scores_are_not_new_evidence(tmp_path):
    store, chat, source = fixture(tmp_path)
    source.with_name('second.md').write_text('The route is East Pier. ' + 'Harbor ' * 8 + '\n')
    engine = Searches([search('search_project', query)
                       for query in ('route', 'route harbor', 'harbor', 'route! pier!')])
    ConversationWorker(store, chat, engine).run()
    assert_stopped(store, chat, engine, 4)
    outcomes = [json.loads(json.loads(row['payload'])['message']['content'])
                for row in store.messages(chat) if row['role'] == 'tool']
    assert outcomes[0]['results'][0]['path'] != outcomes[1]['results'][0]['path']
    assert len(outcomes[2]['results']) == 1


@pytest.mark.parametrize('name', ['search_project', 'search_memory'])
def test_genuinely_new_search_evidence_and_changed_sources_advance(name, tmp_path):
    store, chat, source = fixture(tmp_path)
    if name == 'search_project':
        source.with_name('new.md').write_text('The harbor is West Pier.\n')
    else:
        store.memory.create_file('new.md', 'The harbor is West Pier.\n')

    def changed():
        if name == 'search_project':
            source.write_text('The route is now East Pier after the harbor correction.\n')
        else:
            old = store.memory.file_snapshot('route.md')
            store.memory.replace_file('route.md', 'The route is now East Pier after correction.\n', old['sha256'])
        return search(name, 'route')

    engine = Searches([search(name, 'route'), search(name, 'harbor'), changed, None])
    ConversationWorker(store, chat, engine, limits=RunLimits(max_stalls=1)).run()
    assert len(engine.requests) == 4
    assert not any(row['status'] in ('error', 'paused') for row in store.messages(chat))
    assert json.loads(store.messages(chat)[-1]['payload']).get('pause_context_closed') is True


@pytest.mark.parametrize('name', ['search_project', 'search_memory'])
def test_new_search_passage_in_the_same_version_advances(name, tmp_path):
    store, chat, source = fixture(tmp_path)
    contents = 'alpha route'.ljust(5200, ' ') + 'beta East Pier.'.ljust(800, ' ')
    if name == 'search_project':
        source.write_text(contents)
    else:
        old = store.memory.file_snapshot('route.md')
        store.memory.replace_file('route.md', contents, old['sha256'])
    engine = Searches([search(name, 'alpha'), search(name, 'beta'), None])
    ConversationWorker(store, chat, engine, limits=RunLimits(max_stalls=1)).run()
    assert len(engine.requests) == 3
    rows = store.messages(chat)
    assert not any(row['status'] in ('error', 'paused') for row in rows)
    outcomes = [json.loads(json.loads(row['payload'])['message']['content'])
                for row in rows if row['role'] == 'tool']
    first, second = (outcome['results'][0] for outcome in outcomes)
    assert first['sha256'] == second['sha256']
    assert first['offset'] == 0 < second['offset']
    assert json.loads(rows[-1]['payload']).get('pause_context_closed') is True


def hit(path='a.md', offset=0, text='abcdefghij', version='one'):
    return {'path': path, 'offset': offset, 'text': text,
            'sha256': hashlib.sha256(version.encode()).hexdigest(), 'score': 1}


@pytest.mark.parametrize('name', ['search_project', 'search_memory'])
def test_controller_counts_range_union_and_versions_not_queries_or_result_lists(name):
    run = RunProgress(RunLimits(max_stalls=10))
    first, second = hit(), hit('b.md')
    assert run.observe(name, {'query': 'one', 'scope': 'global'}, {'results': [first, second]}, 1)
    assert not run.observe(name, {'query': 'two', 'scope': 'global'},
                           {'results': [dict(second, score=20), dict(first, score=3)]}, 2)
    assert not run.observe(name, {'query': 'three', 'scope': 'global'}, {'results': [first]}, 3)
    assert not run.observe(name, {'query': 'smaller', 'scope': 'global'},
                           {'results': [hit(offset=2, text='cdef')]}, 4)
    assert run.observe(name, {'query': 'expanded', 'scope': 'global'},
                       {'results': [hit(offset=5, text='fghijklmno')]}, 5)
    assert run.observe(name, {'query': 'changed', 'scope': 'global'},
                       {'results': [hit(version='two')]}, 6)


@pytest.mark.parametrize('name', ['search_project', 'search_memory'])
def test_controller_fallback_atoms_ignore_order_score_and_queries(name):
    run = RunProgress(RunLimits(max_stalls=3))
    rows = [{'path': 'a.md', 'text': 'First known evidence.'},
            {'path': 'b.md', 'text': 'Second known evidence.'}]
    assert run.observe(name, {'query': 'first'}, {'results': rows}, 1)
    assert not run.observe(name, {'query': 'second'},
                           {'results': [dict(row, score=20) for row in reversed(rows)]}, 2)
    assert not run.observe(name, {'query': 'third'}, {'results': rows[:1]}, 3)
    with pytest.raises(RunHalted, match='without new evidence'):
        run.observe(name, {'query': 'fourth'}, {'results': rows}, 4)


def test_explicit_project_range_comparison_overrides_fingerprint_novelty():
    run = RunProgress()
    assert run.observe('search_project', {'query': 'one'}, {'results': [hit()]}, 1, progress=True)
    assert not run.observe('search_project', {'query': 'two'},
                           {'results': [hit('new-looking.md')]}, 2, progress=False)


def test_search_of_already_read_source_bytes_does_not_establish_progress(tmp_path):
    store, chat, source = fixture(tmp_path)
    engine = Searches([('read_file', {'path': str(source), 'offset': 0, 'max_chars': 4000}),
                       search('search_project', 'route'),
                       search('search_project', 'route pier'),
                       search('search_project', 'route! pier!')])
    ConversationWorker(store, chat, engine).run()
    assert_stopped(store, chat, engine, 4)


def test_changed_extractor_version_is_new_evidence_for_unchanged_binary_source():
    run = RunProgress()
    page = {**hit('/source.pdf'), 'source_sha256': hit()['sha256'], 'sha256': None,
            'extraction': {'version': 'pdf-v1'}}
    assert run.observe('search_project', {'query': 'route'}, {'results': [page]}, 1)
    assert not run.observe('search_project', {'query': 'route!'}, {'results': [page]}, 2)
    assert run.observe('search_project', {'query': 'route'},
                       {'results': [{**page, 'extraction': {'version': 'pdf-v2'}}]}, 3)
