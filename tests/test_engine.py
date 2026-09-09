from __future__ import annotations

import json
from pathlib import Path
import socket
import stat
import subprocess
import sys
import textwrap
import time

import pytest


pytestmark = pytest.mark.usefixtures('python_engine_peer')


PEER_SOURCE = r'''#!/usr/bin/env python3
import argparse
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
import time

parser = argparse.ArgumentParser()
parser.add_argument("--host", required=True)
parser.add_argument("--port", required=True, type=int)
parser.add_argument("--api-key", required=True)
parser.add_argument("--model", required=True)
parser.add_argument("--ctx-size", required=True)
parser.add_argument("--n-gpu-layers", required=True)
parser.add_argument("--threads", required=True)
parser.add_argument("--jinja", action="store_true")
parser.add_argument("--parallel", required=True)
parser.add_argument("--lora")
args = parser.parse_args()

own_path = Path(sys.argv[0])
own_path.with_suffix(".args.json").write_text(json.dumps(sys.argv[1:]))
own_path.with_suffix(".token").write_text(args.api_key)
print("peer diagnostic; secret=" + args.api_key, flush=True)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        if self.path not in ("/health", "/v1/health"):
            self.send_error(404)
            return
        if self.headers.get("Authorization") != "Bearer " + args.api_key:
            self.send_error(401)
            return
        body = b'{"status":"ok"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        request = json.loads(raw)
        record_path = own_path.with_suffix(".requests.json")
        records = json.loads(record_path.read_text()) if record_path.exists() else []
        records.append({
            "path": self.path,
            "authorization": self.headers.get("Authorization"),
            "body": request,
        })
        record_path.write_text(json.dumps(records))
        if self.path == "/apply-template":
            scenario = request["messages"][-1]["content"]
            if (
                len(request["messages"]) >= 2
                and all(message["role"] == "assistant" for message in request["messages"][-2:])
            ):
                self._json({"error": {
                    "message": "Cannot have 2 or more assistant messages at the end of the list.",
                    "type": "invalid_request_error",
                }}, status=422 if scenario == "budget-invalid-422" else 400)
                return
            if scenario == "budget-drip":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True
                self.wfile.write(b'{"prompt":"')
                for _ in range(30):
                    self.wfile.write(b'x')
                    self.wfile.flush()
                    time.sleep(0.05)
                self.wfile.write(b'"}')
                self.wfile.flush()
                return
            unsupported_status = {
                "budget-unsupported": 404,
                "budget-unsupported-405": 405,
                "budget-unsupported-501": 501,
            }.get(scenario)
            if unsupported_status is not None:
                self.send_error(unsupported_status)
                return
            formatted = {"messages": request["messages"],
                         "thinking": request["chat_template_kwargs"]}
            if scenario != "budget-blind":
                formatted["tools"] = request.get("tools", [])
            self._json({"prompt": json.dumps(formatted)})
            return
        if self.path == "/tokenize":
            for status in (400, 422):
                if "budget-tokenize-invalid-" + str(status) in request["content"]:
                    self._json({"error": {
                        "message": "Invalid tokenizer content",
                        "type": "invalid_request_error",
                    }}, status=status)
                    return
            self._json({"tokens": [17] * (9000 if "budget-overflow" in request["content"] else 37)})
            return
        scenario = request["messages"][-1]["content"]

        if scenario == "redirect":
            self.send_response(307)
            self.send_header("Location", "http://example.invalid/stolen")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if scenario in ("server-error", "context-error", "tool-error"):
            messages = {
                "server-error": "model worker failed",
                "context-error": "request exceeds the available context size",
                "tool-error": "tools require a supported chat template",
            }
            payload = json.dumps({"error": {"message": messages[scenario]}}).encode()
            self.send_response(400 if scenario != "server-error" else 500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

        if scenario == "malformed":
            self.wfile.write(b"data: {not-json}\n\n")
            self.wfile.flush()
            return
        if scenario == "server-exit":
            os._exit(17)
        if scenario == "truncated":
            self._event({"choices": [{"delta": {"content": "partial"}, "finish_reason": None}]})
            return
        if scenario == "length":
            self._event({"choices": [{"delta": {"content": "partial"}, "finish_reason": "length"}]})
            self._done()
            return
        if scenario == "bad-tool-arguments":
            self._event({"choices": [{"delta": {"tool_calls": [{
                "index": 0,
                "id": "call_bad",
                "type": "function",
                "function": {"name": "lookup", "arguments": "{"},
            }]}, "finish_reason": "tool_calls"}]})
            self._done()
            return
        if scenario == "slow":
            time.sleep(3)
            return
        if scenario == "drip":
            for _ in range(60):
                self.wfile.write(b" ")
                self.wfile.flush()
                time.sleep(0.05)
            self.wfile.write(b"\n")
            self.wfile.flush()
            return
        if scenario == "stream-tools":
            self._event({"choices": [{"delta": {
                "role": "assistant",
                "reasoning_content": "private chain",
                "content": "Hel",
                "tool_calls": [{
                    "index": 0,
                    "id": "call_123",
                    "type": "function",
                    "function": {"name": "lo", "arguments": "{\"q\":"},
                }],
            }, "finish_reason": None}]})
            self._event({"choices": [{"delta": {
                "reasoning_content": " remains private",
                "content": "lo",
                "tool_calls": [{
                    "index": 0,
                    "function": {"name": "okup", "arguments": "\"docs\"}"},
                }],
            }, "finish_reason": None}]})
            self._event({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]})
            self._done()
            return

        self._event({"choices": [{"delta": {"content": "ok"}, "finish_reason": None}]})
        self._event({"choices": [{"delta": {}, "finish_reason": "stop"}]})
        self._done()

    def _json(self, value, status=200):
        body = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _event(self, value):
        self.wfile.write(b"data: " + json.dumps(value).encode() + b"\n\n")
        self.wfile.flush()

    def _done(self):
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def log_message(self, format, *values):
        pass


ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()
'''


@pytest.fixture
def fake_server(tmp_path: Path) -> Path:
    folder = tmp_path / 'Model server café with spaces'
    folder.mkdir()
    path = folder / "llama-server-fake.exe"
    path.write_text(textwrap.dedent(PEER_SOURCE))
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


@pytest.fixture
def gguf_model(tmp_path: Path) -> Path:
    path = tmp_path / "model.gguf"
    path.write_bytes(b"GGUF" + bytes(128))
    return path


def wait_for_text(path: Path, needle: str, timeout: float = 2.0) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            text = path.read_text(errors="replace")
            if needle in text:
                return text
        time.sleep(0.01)
    return path.read_text(errors="replace") if path.exists() else ""


def make_engine(fake_server: Path, gguf_model: Path, data_dir: Path):
    from letracode.engine import EngineConfig, LocalEngine

    return LocalEngine(
        EngineConfig(
            executable=str(fake_server),
            model_path=str(gguf_model),
            max_tokens=321,
            temperature=0.25,
        ),
        data_dir,
    )


def test_engine_config_public_defaults() -> None:
    try:
        from letracode.engine import EngineConfig
    except ModuleNotFoundError as exc:
        raise AssertionError("the managed engine module is missing") from exc

    assert EngineConfig() == EngineConfig(
        executable="",
        model_path="",
        context_size=8192,
        gpu_layers=0,
        threads=4,
        max_tokens=2048,
        temperature=0.7,
    )


def test_wall_clock_deadline_stops_slow_drip(fake_server, gguf_model, tmp_path, monkeypatch):
    import threading
    import letracode.engine as module
    engine = make_engine(fake_server,gguf_model,tmp_path/'data')
    engine.start(threading.Event())
    monkeypatch.setattr(module,'_REQUEST_TIMEOUT',0.3)
    started=time.monotonic()
    try:
        with pytest.raises(module.EngineError,match='[Tt]im.*out|deadline'):
            engine.complete([{'role':'user','content':'drip'}],None,threading.Event(),lambda _:None)
        assert time.monotonic()-started < 1.2
        assert not engine.running
    finally:
        engine.stop()


def test_random_token_cannot_be_mistaken_for_a_cli_option(fake_server,gguf_model,tmp_path,monkeypatch):
    import threading
    import letracode.engine as module
    monkeypatch.setattr(module.secrets,'token_urlsafe',lambda _: '-starts-with-dash')
    engine=make_engine(fake_server,gguf_model,tmp_path/'data')
    try:
        engine.start(threading.Event())
        assert engine.running
    finally:
        engine.stop()


def test_start_rejects_missing_binary_and_invalid_model(
    tmp_path: Path, fake_server: Path
) -> None:
    from letracode.engine import EngineConfig, EngineError, LocalEngine

    cancel = __import__("threading").Event()
    missing_binary = LocalEngine(
        EngineConfig(executable=str(tmp_path / "missing"), model_path="unused.gguf"),
        tmp_path / "data-a",
    )
    with pytest.raises(EngineError, match="executable"):
        missing_binary.start(cancel)

    missing_model = LocalEngine(
        EngineConfig(executable=str(fake_server), model_path=str(tmp_path / "missing.gguf")),
        tmp_path / "data-b",
    )
    with pytest.raises(EngineError, match="model"):
        missing_model.start(cancel)

    invalid = tmp_path / "invalid.gguf"
    invalid.write_bytes(b"not a model")
    invalid_model = LocalEngine(
        EngineConfig(executable=str(fake_server), model_path=str(invalid)),
        tmp_path / "data-c",
    )
    with pytest.raises(EngineError, match="GGUF"):
        invalid_model.start(cancel)


def test_start_manages_loopback_process_with_required_arguments_and_redacted_log(
    tmp_path: Path, fake_server: Path, gguf_model: Path
) -> None:
    from letracode.engine import EngineConfig, LocalEngine
    from threading import Event

    data_dir = tmp_path / "private data"
    engine = LocalEngine(
        EngineConfig(
            executable=str(fake_server),
            model_path=str(gguf_model),
            context_size=4096,
            gpu_layers=7,
            threads=3,
        ),
        data_dir,
    )
    statuses: list[str] = []
    engine.start(Event(), statuses.append)
    try:
        assert engine.running
        argv = json.loads(fake_server.with_suffix(".args.json").read_text())
        token = fake_server.with_suffix(".token").read_text()
        assert argv == [
            "--host",
            "127.0.0.1",
            "--port",
            argv[3],
            "--api-key",
            token,
            "--model",
            str(gguf_model.resolve()),
            "--ctx-size",
            "4096",
            "--n-gpu-layers",
            "7",
            "--threads",
            "3",
            "--jinja",
            "--parallel",
            "1",
        ]
        assert int(argv[3]) > 0
        assert len(token) >= 32
        log_text = wait_for_text(data_dir / "engine.log", "peer diagnostic")
        assert "peer diagnostic" in log_text
        assert token not in log_text
        assert "[REDACTED]" in log_text
        assert statuses
    finally:
        port = int(json.loads(fake_server.with_suffix(".args.json").read_text())[3])
        engine.stop()

    assert not engine.running
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", port), timeout=0.2)


def test_start_reports_rejected_required_flags_as_outdated_server(
    tmp_path: Path, gguf_model: Path
) -> None:
    from letracode.engine import EngineConfig, EngineError, LocalEngine
    from threading import Event

    executable = tmp_path / "old-llama-server.exe"
    executable.write_text(
        "#!/usr/bin/env python3\nprint('error: unknown argument: --jinja', flush=True)\n"
    )
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    engine = LocalEngine(
        EngineConfig(executable=str(executable), model_path=str(gguf_model)),
        tmp_path / "data",
    )

    with pytest.raises(EngineError, match="current llama.cpp build"):
        engine.start(Event())
    assert not engine.running


def test_complete_streams_content_and_collects_fragmented_native_tool_calls(
    tmp_path: Path, fake_server: Path, gguf_model: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from letracode.engine import LocalEngine
    from threading import Event

    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    engine: LocalEngine = make_engine(fake_server, gguf_model, tmp_path / "data")
    deltas: list[str] = []
    tools = [{
        "type": "function",
        "function": {
            "name": "lookup",
            "description": "Find a source",
            "parameters": {"type": "object", "properties": {"q": {"type": "string"}}},
        },
    }]
    try:
        result = engine.complete(
            [{"role": "user", "content": "stream-tools"}],
            tools,
            Event(),
            deltas.append,
        )
        assert result == {
            "role": "assistant",
            "content": "Hello",
            "reasoning_content": "private chain remains private",
            "tool_calls": [{
                "id": "call_123",
                "type": "function",
                "function": {"name": "lookup", "arguments": '{"q":"docs"}'},
            }],
        }
        assert deltas == ["Hel", "lo"]

        record = json.loads(fake_server.with_suffix(".requests.json").read_text())[-1]
        token = fake_server.with_suffix(".token").read_text()
        assert record == {
            "path": "/v1/chat/completions",
            "authorization": "Bearer " + token,
            "body": {
                "model": "local",
                "messages": [{"role": "user", "content": "stream-tools"}],
                "stream": True,
                "max_tokens": 321,
                "temperature": 0.25,
                "chat_template_kwargs": {"enable_thinking": False},
                "tools": tools,
                "parallel_tool_calls": False,
            },
        }
    finally:
        engine.stop()


def test_complete_sends_model_dependent_thinking_switch(
    tmp_path: Path, fake_server: Path, gguf_model: Path
) -> None:
    from threading import Event

    engine = make_engine(fake_server, gguf_model, tmp_path / "data")
    try:
        assert engine.complete(
            [{"role": "user", "content": "plain"}], None, Event(), lambda text: None,
            thinking=True,
        ) == {"role": "assistant", "content": "ok"}
        body = json.loads(fake_server.with_suffix(".requests.json").read_text())[-1]["body"]
        assert body["chat_template_kwargs"] == {"enable_thinking": True}
        assert "tools" not in body
    finally:
        engine.stop()


@pytest.mark.parametrize(
    ("scenario", "tools", "message"),
    [
        ("malformed", None, "malformed JSON"),
        ("truncated", None, "truncated"),
        ("length", None, "token limit"),
        ("server-error", None, "model worker failed"),
        ("context-error", None, "context window"),
        ("tool-error", [{"type": "function", "function": {"name": "x"}}], "tool template"),
        ("bad-tool-arguments", [{"type": "function", "function": {"name": "x"}}], "tool arguments"),
        ("redirect", None, "redirect"),
        ("server-exit", None, "server exited"),
    ],
)
def test_complete_classifies_protocol_and_server_failures(
    tmp_path: Path,
    fake_server: Path,
    gguf_model: Path,
    scenario: str,
    tools: list[dict] | None,
    message: str,
) -> None:
    from letracode.engine import EngineError
    from threading import Event

    engine = make_engine(fake_server, gguf_model, tmp_path / "data")
    try:
        with pytest.raises(EngineError, match=message):
            engine.complete(
                [{"role": "user", "content": scenario}], tools, Event(), lambda text: None
            )
    finally:
        engine.stop()


def test_complete_rejects_concurrency_and_cancel_unblocks_active_stream(
    tmp_path: Path, fake_server: Path, gguf_model: Path
) -> None:
    from letracode.engine import Cancelled, EngineError
    from threading import Event, Thread

    engine = make_engine(fake_server, gguf_model, tmp_path / "data")
    outcome: list[BaseException | dict] = []

    def run() -> None:
        try:
            outcome.append(engine.complete(
                [{"role": "user", "content": "slow"}], None, Event(), lambda text: None
            ))
        except BaseException as exc:
            outcome.append(exc)

    thread = Thread(target=run)
    thread.start()
    wait_for_text(fake_server.with_suffix(".requests.json"), "slow")
    with pytest.raises(EngineError, match="already in progress"):
        engine.complete(
            [{"role": "user", "content": "plain"}], None, Event(), lambda text: None
        )
    started = time.monotonic()
    engine.cancel()
    thread.join(timeout=2)

    assert not thread.is_alive()
    assert time.monotonic() - started < 2
    assert len(outcome) == 1
    assert isinstance(outcome[0], Cancelled)
    assert not engine.running


def test_complete_honors_external_cancel_event(
    tmp_path: Path, fake_server: Path, gguf_model: Path
) -> None:
    from letracode.engine import Cancelled
    from threading import Event, Thread

    engine = make_engine(fake_server, gguf_model, tmp_path / "data")
    cancel = Event()
    outcome: list[BaseException | dict] = []

    def run() -> None:
        try:
            outcome.append(engine.complete(
                [{"role": "user", "content": "slow"}], None, cancel, lambda text: None
            ))
        except BaseException as exc:
            outcome.append(exc)

    thread = Thread(target=run)
    thread.start()
    wait_for_text(fake_server.with_suffix(".requests.json"), "slow")
    cancel.set()
    thread.join(timeout=2)
    engine.stop()

    assert not thread.is_alive()
    assert len(outcome) == 1
    assert isinstance(outcome[0], Cancelled)


def test_request_usage_formats_tools_thinking_and_reserves_reply(fake_server, gguf_model, tmp_path):
    from threading import Event
    engine = make_engine(fake_server, gguf_model, tmp_path / "data")
    tools = [{"type": "function", "function": {"name": "read_file"}}]
    messages = [{"role": "user", "content": "Unicode 你好 \\ code"}]
    try:
        usage = engine.request_usage(messages, tools, Event(), thinking=True)
        assert usage.prompt_tokens == 37
        assert usage.reply_tokens == 321
        assert usage.safety_tokens > 0
        assert usage.total_tokens == 37 + 321 + usage.safety_tokens
        assert usage.method == "runtime tokenizer"
        records = json.loads(fake_server.with_suffix(".requests.json").read_text())
        formatted = next(r["body"] for r in records if r["path"] == "/apply-template")
        assert formatted["messages"] == messages
        assert formatted["tools"] == tools
        assert formatted["parallel_tool_calls"] is False
        assert formatted["chat_template_kwargs"] == {"enable_thinking": True}
        tokenizer = next(r["body"] for r in records if r["path"] == "/tokenize")
        assert "read_file" in tokenizer["content"]
        assert tokenizer["add_special"] is True
        assert tokenizer["parse_special"] is True
    finally:
        engine.stop()


@pytest.mark.parametrize("endpoint", ["/apply-template", "/tokenize"])
@pytest.mark.parametrize("status", [400, 422])
def test_budget_request_error_does_not_disable_runtime_tokenizer(
    fake_server, gguf_model, tmp_path, endpoint, status,
):
    from threading import Event
    from letracode.engine import EngineError

    engine = make_engine(fake_server, gguf_model, tmp_path / "data")
    if endpoint == "/apply-template":
        messages = [
            {"role": "user", "content": "Continue the conversation"},
            {"role": "assistant", "content": "First partial answer"},
            {"role": "assistant", "content": f"budget-invalid-{status}"},
        ]
        diagnostic = "Cannot have 2 or more assistant messages at the end of the list."
    else:
        messages = [{"role": "user", "content": f"budget-tokenize-invalid-{status}"}]
        diagnostic = "Invalid tokenizer content"
    try:
        with pytest.raises(EngineError, match=diagnostic) as error:
            engine.request_usage(messages, None, Event())
        assert f"HTTP {status}" in str(error.value)
        assert engine.running

        valid_messages = [{"role": "user", "content": "A valid next request"}]
        usage = engine.request_usage(valid_messages, None, Event())
        assert usage.method == "runtime tokenizer"
        assert usage.prompt_tokens == 37
        records = json.loads(fake_server.with_suffix(".requests.json").read_text())
        assert records[-2]["path"] == "/apply-template"
        assert records[-2]["body"]["messages"] == valid_messages
        assert records[-1]["path"] == "/tokenize"
        assert not any(r["path"] == "/v1/chat/completions" for r in records)
    finally:
        engine.stop()


@pytest.mark.parametrize("scenario", [
    "budget-unsupported", "budget-unsupported-405", "budget-unsupported-501", "budget-blind",
])
def test_request_usage_fallback_counts_utf8_tools_and_template(fake_server, gguf_model, tmp_path, scenario):
    from threading import Event
    engine = make_engine(fake_server, gguf_model, tmp_path / "data")
    tools = [{"type": "function", "function": {"name": "lookup", "description": "X" * 1000}}]
    messages = [{"role": "user", "content": scenario}]
    try:
        usage = engine.request_usage(messages, tools, Event())
        assert "estimate" in usage.method
        assert usage.prompt_tokens >= 1000
        # The fallback accounts for every UTF-8 byte, including escaped code.
        unicode_messages = [{"role": "user", "content": "漢" * 1000 + '\\' * 500}]
        unicode_usage = engine.request_usage(unicode_messages, tools, Event())
        assert unicode_usage.prompt_tokens > usage.prompt_tokens + 3000
        plain = engine.request_usage(unicode_messages, None, Event())
        assert unicode_usage.prompt_tokens >= plain.prompt_tokens + 1000
    finally:
        engine.stop()


def test_completion_refuses_overflow_before_inference(fake_server, gguf_model, tmp_path):
    from threading import Event
    from letracode.engine import ContextOverflowError
    engine = make_engine(fake_server, gguf_model, tmp_path / "data")
    try:
        with pytest.raises(ContextOverflowError):
            engine.complete([{"role": "user", "content": "budget-overflow"}], None, Event(), lambda _: None)
        records = json.loads(fake_server.with_suffix(".requests.json").read_text())
        assert not any(r["path"] == "/v1/chat/completions" for r in records)
    finally:
        engine.stop()


def test_budget_endpoint_has_wall_clock_deadline(fake_server, gguf_model, tmp_path, monkeypatch):
    from threading import Event
    import letracode.engine as module
    monkeypatch.setattr(module, '_BUDGET_REQUEST_TIMEOUT', 0.15, raising=False)
    engine = make_engine(fake_server, gguf_model, tmp_path / 'data')
    try:
        started=time.monotonic()
        with pytest.raises(module.EngineError, match='budget.*deadline'):
            engine.request_usage([{'role':'user','content':'budget-drip'}],None,Event())
        assert time.monotonic()-started<1.2
        assert not engine.running
    finally:
        engine.stop()


@pytest.mark.parametrize('operation', ['completion', 'budget'])
def test_operation_owns_cancel_watcher_until_teardown_finishes(fake_server, gguf_model, tmp_path, monkeypatch, operation):
    from threading import Event, Thread
    import letracode.engine as module
    engine = make_engine(fake_server, gguf_model, tmp_path / 'data')
    cancel_started, release, returned = Event(), Event(), Event()
    cancel = Event()
    original_cancel = engine.cancel

    def slow_cancel():
        original_cancel()
        cancel_started.set()
        release.wait(5)

    monkeypatch.setattr(engine, 'cancel', slow_cancel)
    monkeypatch.setattr(module, '_BUDGET_REQUEST_TIMEOUT', 0.15)
    monkeypatch.setattr(module, '_REQUEST_TIMEOUT', 0.15)
    outcomes = []

    def run():
        try:
            if operation == 'completion':
                engine.complete([{'role': 'user', 'content': 'drip'}], None, cancel, lambda _: None)
            else:
                engine.request_usage([{'role': 'user', 'content': 'budget-drip'}], None, cancel)
        except (module.EngineError, module.Cancelled) as error:
            outcomes.append(error)
        finally:
            returned.set()

    runner = Thread(target=run)
    runner.start()
    try:
        assert cancel_started.wait(3)
        assert not returned.wait(0.15), 'Operation returned with a live cancellation watcher'
    finally:
        release.set()
        runner.join(3)
        engine.stop()
    assert not runner.is_alive()
    assert len(outcomes) == 1


def test_cancel_after_budgeting_is_reported_as_cancelled_before_generation(fake_server, gguf_model, tmp_path, monkeypatch):
    from threading import Event
    from letracode.engine import Cancelled
    engine = make_engine(fake_server, gguf_model, tmp_path / 'data')
    count = engine._request_usage

    def cancel_after_count(*args):
        usage = count(*args)
        engine.cancel()
        return usage

    monkeypatch.setattr(engine, '_request_usage', cancel_after_count)
    try:
        with pytest.raises(Cancelled):
            engine.complete([{'role': 'user', 'content': 'plain'}], None, Event(), lambda _: None)
    finally:
        engine.stop()


def test_windows_rejects_batch_engine_launchers(tmp_path, gguf_model, monkeypatch):
    import letracode.engine as engine_module
    from types import SimpleNamespace
    from letracode.engine import EngineConfig, EngineError, LocalEngine
    monkeypatch.setattr(engine_module, 'sys', SimpleNamespace(platform='win32'))
    executable = tmp_path / 'llama-server.cmd'
    executable.write_text('@echo off\n', encoding='utf-8')
    executable.chmod(0o700)
    engine = LocalEngine(EngineConfig(executable=str(executable), model_path=str(gguf_model)), tmp_path / 'data')
    with pytest.raises(EngineError, match=r'\.exe'):
        engine._validated_paths()


@pytest.mark.parametrize('setting', ['', 'llama-server'])
def test_windows_discovers_native_executable_before_batch_files(tmp_path, gguf_model, monkeypatch, setting):
    import letracode.engine as engine_module
    from types import SimpleNamespace
    from letracode.engine import EngineConfig, LocalEngine
    native = tmp_path / 'llama-server.exe'
    native.touch()
    batch = tmp_path / 'llama-server.cmd'
    batch.touch()
    monkeypatch.setattr(engine_module, 'sys', SimpleNamespace(platform='win32'))
    monkeypatch.setattr(engine_module.shutil, 'which', lambda name: str(native if name == 'llama-server.exe' else batch))
    engine = LocalEngine(EngineConfig(executable=setting, model_path=str(gguf_model)), tmp_path / 'data')
    executable, _ = engine._validated_paths()
    assert executable == native


def test_restart_cleans_descendants_of_an_exited_server(tmp_path, fake_server, gguf_model):
    from letracode.processes import start_process, stop_process
    from threading import Event
    ready = tmp_path / 'old-server-child-ready'
    survived = tmp_path / 'old-server-child-survived'
    child = (
        f'from pathlib import Path; import time; Path({str(ready)!r}).touch(); '
        f'time.sleep(1.5); Path({str(survived)!r}).touch()'
    )
    parent = (
        'import subprocess, sys, time; from pathlib import Path; '
        f'subprocess.Popen([sys.executable, "-c", {child!r}], '
        'stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); '
        f'\nwhile not Path({str(ready)!r}).exists(): time.sleep(0.01)'
    )
    previous = start_process([sys.executable, '-c', parent], stdout=subprocess.DEVNULL)
    engine = make_engine(fake_server, gguf_model, tmp_path / 'data')
    try:
        previous.wait(timeout=10)
        assert ready.exists()
        engine._process = previous
        engine.start(Event())
        time.sleep(1.7)
        assert not survived.exists()
    finally:
        engine.stop()
        stop_process(previous, timeout=0.5)
