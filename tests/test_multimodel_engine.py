"""Exercise the managed router against an authenticated llama.cpp protocol peer."""
from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import stat
import subprocess
import sys
import threading
import time

import pytest

from letracode.engine import Cancelled, ContextOverflowError, EngineConfig, EngineError, LocalEngine


pytestmark = pytest.mark.usefixtures('python_engine_peer')

ROUTER_SOURCE = r'''#!/usr/bin/env python3
import argparse
import configparser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import time

p = argparse.ArgumentParser()
p.add_argument('--host', required=True)
p.add_argument('--port', required=True, type=int)
p.add_argument('--api-key', required=True)
p.add_argument('--models-preset', required=True)
p.add_argument('--models-max', required=True, type=int)
p.add_argument('--no-models-autoload', action='store_true', required=True)
p.add_argument('--offline', action='store_true', required=True)
p.add_argument('--ctx-size', required=True)
p.add_argument('--n-gpu-layers', required=True)
p.add_argument('--threads', required=True)
p.add_argument('--jinja', action='store_true', required=True)
p.add_argument('--parallel', required=True)
a = p.parse_args()
own = Path(sys.argv[0])
scenario = own.with_suffix('.scenario').read_text()
preset = Path(a.models_preset).read_text(encoding='utf-8')
ini = configparser.ConfigParser(interpolation=None, inline_comment_prefixes=('#', ';'))
ini.read_string(preset.removeprefix('version = 1\n'))
assert ini.sections() == ['local', 'local-b']
assert all(Path(ini[s]['model']).read_bytes().startswith(b'GGUF') for s in ini.sections())
assert a.models_max == 2 and a.host == '127.0.0.1' and a.parallel == '1'
own.with_suffix('.args.json').write_text(json.dumps(sys.argv[1:]))
own.with_suffix('.preset').write_text(preset)
own.with_suffix('.token').write_text(a.api_key)
print('router secret=' + a.api_key, flush=True)
child_source = 'import socket,time; from pathlib import Path; s=socket.socket(); s.bind(("127.0.0.1",0)); s.listen(); Path(' + repr(str(own.with_suffix('.childport'))) + ').write_text(str(s.getsockname()[1])); time.sleep(60)'
child = subprocess.Popen([sys.executable, '-c', child_source])
loaded_at = {}
records = []

def states():
    output = []
    for model in ('local', 'local-b'):
        value = 'unloaded'
        if model in loaded_at:
            value = 'loaded' if time.monotonic() - loaded_at[model] > 0.12 else 'loading'
        status = {'value': value}
        if model == 'local-b' and model in loaded_at:
            if scenario == 'load-failure':
                status = {'value': 'unloaded', 'failed': True, 'exit_code': 1}
            if scenario == 'hang':
                status = {'value': 'loading'}
        output.append({'id': model, 'status': status})
    return output

class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    def authorized(self):
        if self.headers.get('Authorization') == 'Bearer ' + a.api_key:
            return True
        self.send_error(401)
        return False
    def reply(self, payload, code=200):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def do_GET(self):
        if not self.authorized(): return
        if self.path == '/health':
            self.reply({'status': 'ok'})
        elif self.path == '/models':
            if scenario == 'missing-api':
                self.reply({'error': {'message': 'not found'}}, 404)
            elif scenario == 'malformed':
                self.reply({'data': 'invalid'})
            elif scenario == 'oversized':
                self.reply({'data': 'x' * 100000})
            elif scenario == 'redirect':
                self.send_response(307)
                self.send_header('Location', 'https://example.invalid')
                self.send_header('Content-Length', '0')
                self.end_headers()
            elif scenario == 'drip':
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', '10000')
                self.end_headers()
                for _ in range(10000):
                    self.wfile.write(b' ')
                    self.wfile.flush()
                    time.sleep(0.02)
            else:
                self.reply({'data': states(), 'object': 'list'})
        else:
            self.send_error(404)
    def do_POST(self):
        if not self.authorized(): return
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        records.append({'path': self.path, 'body': body})
        own.with_suffix('.requests.json').write_text(json.dumps(records))
        if self.path == '/models/load':
            model = body['model']
            assert model in ('local', 'local-b')
            if scenario == 'http-error' and model == 'local-b':
                self.reply({'error': {'message': 'load refused secret=' + a.api_key}}, 500)
                return
            assert model not in loaded_at
            loaded_at[model] = time.monotonic()
            self.reply({'success': True})
        elif self.path == '/apply-template':
            assert body['model'] in ('local', 'local-b')
            if scenario == 'secondary-budget-unsupported' and body['model'] == 'local-b':
                self.reply({'error': {'message': 'unsupported'}}, 501)
                return
            self.reply({'prompt': json.dumps(body, sort_keys=True)})
        elif self.path == '/tokenize':
            assert body['model'] in ('local', 'local-b')
            assert json.loads(body['content'])['model'] == body['model']
            assert body['add_special'] and body['parse_special']
            count = 37 if body['model'] == 'local' else 61
            if scenario == 'overflow' and body['model'] == 'local-b':
                count = 10000
            self.reply({'tokens': list(range(count))})
        elif self.path == '/v1/chat/completions':
            assert all(m['status']['value'] == 'loaded' for m in states())
            assert body['model'] in ('local', 'local-b')
            if scenario == 'completion-failure':
                self.reply({'error': {'message': 'model worker failed'}}, 500)
                return
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Connection', 'close')
            self.end_headers()
            self.close_connection = True
            for event in [
                {'choices': [{'delta': {'content': body['model'] + ' response'}, 'finish_reason': None}]},
                {'choices': [{'delta': {}, 'finish_reason': 'stop'}]},
            ]:
                self.wfile.write(b'data: ' + json.dumps(event).encode() + b'\n\n')
            self.wfile.write(b'data: [DONE]\n\n')
            self.wfile.flush()
        else:
            self.send_error(404)
    def log_message(self, *args): pass

ThreadingHTTPServer((a.host, a.port), Handler).serve_forever()
'''


@pytest.fixture
def router(tmp_path):
    peer = tmp_path / 'llama-router.exe'
    peer.write_text(ROUTER_SOURCE, encoding='utf-8')
    peer.chmod(peer.stat().st_mode | stat.S_IXUSR)
    peer.with_suffix('.scenario').write_text('ok')
    first = tmp_path / 'model A café.gguf'
    second = tmp_path / 'model B.gguf'
    first.write_bytes(b'GGUF' + bytes(128))
    second.write_bytes(b'GGUF' + bytes(128))
    config = EngineConfig(executable=str(peer), model_path=str(first))
    # Assignment lets pre-feature code reach its wrong single-model startup path.
    config.secondary_model_path = str(second)
    engine = LocalEngine(config, tmp_path / 'data')
    yield engine, peer, first, second
    engine.stop()


def assert_stopped(engine, peer):
    assert not engine.running
    assert engine.loaded_models == ()
    for path in (peer.with_suffix('.args.json'), peer.with_suffix('.childport')):
        if not path.exists():
            continue
        if path.suffix == '.json':
            argv = json.loads(path.read_text())
            port = int(argv[argv.index('--port') + 1])
        else:
            port = int(path.read_text())
        with pytest.raises(OSError):
            socket.create_connection(('127.0.0.1', port), timeout=0.2)


def test_router_preloads_both_before_routing_and_cleans_owned_tree(router):
    engine, peer, first, second = router
    statuses = []
    engine.start(threading.Event(), statuses.append)
    assert engine.loaded_models == ('local', 'local-b')
    argv = json.loads(peer.with_suffix('.args.json').read_text())
    assert '--model' not in argv
    preset_path = Path(argv[argv.index('--models-preset') + 1])
    assert preset_path.read_text(encoding='utf-8') == (
        f'version = 1\n[local]\nmodel = {first.resolve()}\n'
        f'[local-b]\nmodel = {second.resolve()}\n'
    )
    if os.name == 'posix':
        assert stat.S_IMODE(preset_path.stat().st_mode) == 0o600
    for model in ('local-b', 'local'):
        result = engine.complete([{'role': 'user', 'content': 'Hello'}], None,
                                 threading.Event(), lambda _: None, model=model)
        assert result['content'] == model + ' response'
    requests = json.loads(peer.with_suffix('.requests.json').read_text())
    assert [r['path'] for r in requests] == [
        '/models/load', '/models/load',
        '/apply-template', '/tokenize', '/v1/chat/completions',
        '/apply-template', '/tokenize', '/v1/chat/completions']
    assert [r['body']['model'] for r in requests] == [
        'local', 'local-b', 'local-b', 'local-b', 'local-b', 'local', 'local', 'local']
    engine.stop()
    assert_stopped(engine, peer)
    assert not preset_path.exists()
    token = peer.with_suffix('.token').read_text()
    assert token not in (engine.data_dir / 'engine.log').read_text()
    assert any('local-b' in status or 'Model B' in status for status in statuses)


def test_router_runtime_budget_counts_each_model_and_reuses_preflight(router):
    engine, peer, _, _ = router
    messages = [{'role': 'user', 'content': 'Use the project tools'}]
    tools = [{'type': 'function', 'function': {'name': 'read_file', 'parameters': {'type': 'object'}}}]
    first = engine.request_usage(messages, tools, threading.Event(), thinking=True)
    second = engine.request_usage(messages, tools, threading.Event(), thinking=True, model='local-b')
    assert first.prompt_tokens == 37
    assert second.prompt_tokens == 61
    assert first.method == second.method == 'runtime tokenizer'
    engine.complete(messages, tools, threading.Event(), lambda _: None, thinking=True, model='local-b')
    requests = json.loads(peer.with_suffix('.requests.json').read_text())
    budgets = [r for r in requests if r['path'] in ('/apply-template', '/tokenize')]
    assert [r['body']['model'] for r in budgets] == ['local', 'local', 'local', 'local-b', 'local-b', 'local-b']
    completion = requests[-1]['body']
    assert requests[-1]['path'] == '/v1/chat/completions'
    assert completion['parallel_tool_calls'] is False
    assert completion['chat_template_kwargs'] == {'enable_thinking': True}


def test_unsupported_secondary_tokenizer_does_not_disable_primary_budget(router):
    engine, peer, _, _ = router
    peer.with_suffix('.scenario').write_text('secondary-budget-unsupported')
    messages = [{'role': 'user', 'content': 'Hello'}]
    second = engine.request_usage(messages, None, threading.Event(), model='local-b')
    first = engine.request_usage(messages, None, threading.Event())
    assert 'estimate' in second.method
    assert first.method == 'runtime tokenizer'
    assert first.prompt_tokens == 37


def test_secondary_context_overflow_refuses_generation_and_releases_models(router):
    engine, peer, _, _ = router
    peer.with_suffix('.scenario').write_text('overflow')
    with pytest.raises(ContextOverflowError):
        engine.complete([{'role': 'user', 'content': 'Hello'}], None,
                        threading.Event(), lambda _: None, model='local-b')
    requests = json.loads(peer.with_suffix('.requests.json').read_text())
    assert not any(r['path'] == '/v1/chat/completions' for r in requests)
    assert_stopped(engine, peer)


@pytest.mark.parametrize('scenario,match', [
    ('load-failure', 'local-b|Model B'),
    ('missing-api', 'current llama.cpp|router'),
    ('malformed', 'malformed|invalid'),
    ('oversized', 'size|large'),
    ('redirect', 'redirect'),
    ('http-error', 'load refused'),
])
def test_router_startup_errors_stop_both_models(router, scenario, match):
    engine, peer, _, _ = router
    peer.with_suffix('.scenario').write_text(scenario)
    with pytest.raises(EngineError, match=match) as caught:
        engine.start(threading.Event())
    assert_stopped(engine, peer)
    if peer.with_suffix('.token').exists():
        assert peer.with_suffix('.token').read_text() not in str(caught.value)


def test_cancel_during_second_model_load_stops_router_and_children(router):
    engine, peer, _, _ = router
    peer.with_suffix('.scenario').write_text('hang')
    cancel = threading.Event()
    timer = threading.Timer(0.6, cancel.set)
    timer.start()
    try:
        with pytest.raises(Cancelled):
            engine.start(cancel)
    finally:
        timer.cancel()
    assert_stopped(engine, peer)


@pytest.mark.parametrize('scenario', ['hang', 'drip'])
def test_router_load_deadline_is_bounded(router, monkeypatch, scenario):
    engine, peer, _, _ = router
    peer.with_suffix('.scenario').write_text(scenario)
    monkeypatch.setattr('letracode.engine._MODEL_LOAD_TIMEOUT', 0.4)
    started = time.monotonic()
    with pytest.raises(EngineError, match='[Tt]im.*out|deadline'):
        engine.start(threading.Event())
    assert time.monotonic() - started < 2
    assert_stopped(engine, peer)


def test_completion_failure_releases_both_models(router):
    engine, peer, _, _ = router
    peer.with_suffix('.scenario').write_text('completion-failure')
    engine.start(threading.Event())
    with pytest.raises(EngineError, match='model worker failed'):
        engine.complete([{'role': 'user', 'content': 'Hello'}], None,
                        threading.Event(), lambda _: None, model='local-b')
    assert_stopped(engine, peer)


def test_old_server_flags_error_mentions_required_router_support(router):
    engine, peer, _, _ = router
    peer.write_text("#!/usr/bin/env python3\nprint('unknown argument: --models-preset', flush=True)\n")
    with pytest.raises(EngineError, match='current llama.cpp.*router'):
        engine.start(threading.Event())
    assert not engine.running
    assert not list(engine.data_dir.glob('router-*.ini'))


def test_router_rejects_identical_weights_including_hard_links(router, tmp_path):
    engine, _, first, _ = router
    alias = tmp_path / 'same weights.gguf'
    os.link(first, alias)
    engine.config.secondary_model_path = str(alias)
    with pytest.raises(EngineError, match='different|same'):
        engine.start(threading.Event())
    assert not engine.running


@pytest.mark.parametrize('filename', ['bad;name.gguf', 'bad#name.gguf', 'bad\nname.gguf', 'bad\rname.gguf', 'trailing.gguf '])
def test_router_rejects_paths_the_llama_preset_cannot_represent(router, tmp_path, filename):
    if os.name == 'nt' and ('\n' in filename or '\r' in filename or filename.endswith(' ')):
        pytest.skip('This path is not representable on Windows')
    engine, _, _, _ = router
    bad = tmp_path / filename
    bad.write_bytes(b'GGUF')
    engine.config.secondary_model_path = str(bad)
    with pytest.raises(EngineError, match='path|preset'):
        engine.start(threading.Event())
    assert not engine.running


def test_router_rejects_invalid_second_gguf_before_launch(router):
    engine, _, _, second = router
    second.write_bytes(b'BAD!')
    with pytest.raises(EngineError, match='GGUF'):
        engine.start(threading.Event())
    assert not engine.running


@pytest.mark.parametrize('model', ['not-configured', '', '../model', None])
def test_completion_cannot_route_to_an_unconfigured_model(router, model):
    engine, _, _, _ = router
    with pytest.raises(EngineError, match='model|Model'):
        engine.complete([], None, threading.Event(), lambda _: None, model=model)
    assert not engine.running


def test_secondary_route_is_unavailable_in_single_model_mode(router):
    engine, _, _, _ = router
    engine.config.secondary_model_path = ''
    with pytest.raises(EngineError, match='model|Model'):
        engine.complete([], None, threading.Event(), lambda _: None, model='local-b')
    assert not engine.running


@pytest.mark.parametrize('operation', ['start', 'complete'])
def test_operation_waits_for_cancel_watcher_teardown_before_returning(router, monkeypatch, operation):
    engine, _, _, _ = router
    if operation == 'complete':
        engine.start(threading.Event())
    watcher_stopping = threading.Event()
    release_watcher = threading.Event()
    watcher_closed = threading.Event()
    operation_finished = threading.Event()
    errors = []

    def delayed_watcher(cancel, done, deadline, expired):
        done.wait(3)
        watcher_stopping.set()
        release_watcher.wait(3)
        watcher_closed.set()

    monkeypatch.setattr(engine, '_watch_external_cancel', delayed_watcher)

    def run():
        try:
            if operation == 'start':
                engine.start(threading.Event())
            else:
                engine.complete([{'role': 'user', 'content': 'Hello'}], None,
                                threading.Event(), lambda _: None)
        except BaseException as error:
            errors.append(error)
        finally:
            operation_finished.set()

    worker = threading.Thread(target=run)
    worker.start()
    try:
        assert watcher_stopping.wait(3)
        assert not operation_finished.wait(0.08), 'Returning early lets an old watcher cancel the next job'
    finally:
        release_watcher.set()
        worker.join(3)
    assert operation_finished.is_set()
    assert watcher_closed.is_set()
    assert not errors


def test_loaded_models_survive_the_thread_that_started_them(router):
    engine, _, _, _ = router
    errors = []

    def start():
        try:
            engine.start(threading.Event())
        except BaseException as error:
            errors.append(error)

    worker = threading.Thread(target=start)
    worker.start()
    worker.join(3)
    assert not worker.is_alive()
    assert not errors
    process = engine._process
    assert process is not None
    with pytest.raises(subprocess.TimeoutExpired):
        process.wait(timeout=0.2)
    assert engine.loaded_models == ('local', 'local-b')


@pytest.mark.skipif(not sys.platform.startswith('linux'), reason='Linux parent-death signal')
def test_linux_server_still_stops_if_the_application_process_exits(router, tmp_path):
    engine, peer, first, second = router
    # A plain server fixture lets this check observe the kernel parent-death
    # signal independently of orderly atexit cleanup or router descendants.
    peer.write_text(ROUTER_SOURCE.replace('child = subprocess.Popen([sys.executable, \'-c\', child_source])', 'child = None'))
    exited = tmp_path / 'owner-exited'
    owner_source = '''
import os, sys, threading
from pathlib import Path
from letracode.engine import EngineConfig, LocalEngine
engine = LocalEngine(EngineConfig(executable=sys.argv[1], model_path=sys.argv[2], secondary_model_path=sys.argv[3]), Path(sys.argv[4]))
worker = threading.Thread(target=engine.start, args=(threading.Event(),))
worker.start()
worker.join()
assert engine.running
Path(sys.argv[5]).touch()
os._exit(0)
'''
    owner = subprocess.Popen([
        sys.executable, '-c', owner_source, str(peer), str(first), str(second),
        str(tmp_path / 'owner-data'), str(exited),
    ], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        _, error = owner.communicate(timeout=10)
        assert owner.returncode == 0, error.decode()
        assert exited.exists()
        argv = json.loads(peer.with_suffix('.args.json').read_text())
        port = int(argv[argv.index('--port') + 1])
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            try:
                with socket.create_connection(('127.0.0.1', port), timeout=0.1):
                    pass
            except OSError:
                break
            time.sleep(0.01)
        else:
            pytest.fail('The server survived its application process')
    finally:
        if owner.poll() is None:
            owner.kill()
            owner.wait()
