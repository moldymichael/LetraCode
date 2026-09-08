"""Managed local llama.cpp inference process.

The engine owns one ``llama-server`` child and talks to it only over a
randomly authenticated loopback connection. The public methods are
synchronous so GUI callers must invoke ``start`` and ``complete`` from a
worker thread.
"""

from __future__ import annotations

import atexit
from dataclasses import dataclass
import http.client
import json
import math
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
from typing import BinaryIO, Callable
import weakref

from .processes import start_process, stop_process

from .budgeting import RequestUsage, fallback_usage


_HOST = "127.0.0.1"
_MODEL_LOAD_TIMEOUT = 180.0
_REQUEST_TIMEOUT = 300.0
_BUDGET_REQUEST_TIMEOUT = 5.0
_STOP_TIMEOUT = 2.0
_LOG_LIMIT = 1_048_576
_LOG_TAIL_LIMIT = 32_768
_MAX_REQUEST_BYTES = 8_388_608
_MAX_ERROR_BYTES = 65_536
_MAX_SSE_LINE_BYTES = 4_194_304
_MAX_OUTPUT_BYTES = 8_388_608
_MAX_TOOL_BYTES = 2_097_152
_MAX_TOOL_CALLS = 128

_LINUX_LAUNCHER = """\
import ctypes
import os
import signal
import sys

parent = os.getppid()
libc = ctypes.CDLL(None, use_errno=True)
libc.prctl(1, signal.SIGTERM)
if os.getppid() != parent:
    os.kill(os.getpid(), signal.SIGTERM)
os.execv(sys.argv[1], sys.argv[1:])
"""


@dataclass
class EngineConfig:
    executable: str = ""
    model_path: str = ""
    context_size: int = 8192
    gpu_layers: int = 0
    threads: int = 4
    max_tokens: int = 2048
    temperature: float = 0.7


class EngineError(RuntimeError):
    """A configuration, process, or local protocol failure."""


class ContextOverflowError(EngineError):
    """The intact request cannot fit with its reserved response capacity."""


class _BudgetUnavailable(Exception):
    pass


class Cancelled(Exception):
    """The active local inference operation was cancelled."""


_INSTANCES: "weakref.WeakSet[LocalEngine]" = weakref.WeakSet()


def _stop_instances() -> None:
    for engine in list(_INSTANCES):
        engine.stop()


atexit.register(_stop_instances)


class LocalEngine:
    def __init__(self, config: EngineConfig, data_dir: Path):
        self.config = config
        self.data_dir = Path(data_dir)
        self._state_lock = threading.RLock()
        self._start_lock = threading.Lock()
        self._operation_lock = threading.Lock()
        self._log_lock = threading.Lock()
        self._cancel_requested = threading.Event()
        self._process: subprocess.Popen[bytes] | None = None
        self._log_thread: threading.Thread | None = None
        self._port: int | None = None
        self._api_token: str | None = None
        self._active_connection: http.client.HTTPConnection | None = None
        self._active_response: http.client.HTTPResponse | None = None
        self._diagnostic_tail = ""
        self._budget_unavailable = False
        self._usage_cache = None
        _INSTANCES.add(self)

    @property
    def running(self) -> bool:
        with self._state_lock:
            process = self._process
            return process is not None and process.poll() is None

    def start(
        self,
        cancel: threading.Event,
        on_status: Callable[[str], None] = lambda s: None,
    ) -> None:
        """Validate configuration, launch llama-server, and await readiness."""
        if cancel.is_set():
            raise Cancelled("Local model startup was cancelled")

        with self._start_lock:
            if self.running:
                return
            self.stop()
            self._cancel_requested.clear()
            executable, model = self._validated_paths()
            self._prepare_data_dir()
            port = self._reserve_port()
            token = 'lc_' + secrets.token_urlsafe(32)
            arguments = [
                "--host",
                _HOST,
                "--port",
                str(port),
                "--api-key",
                token,
                "--model",
                str(model),
                "--ctx-size",
                str(self.config.context_size),
                "--n-gpu-layers",
                str(self.config.gpu_layers),
                "--threads",
                str(self.config.threads),
                "--jinja",
                "--parallel",
                "1",
            ]
            argv = self._launcher_argv(executable, arguments)
            environment = {
                key: value
                for key, value in os.environ.items()
                if not key.startswith("LLAMA_") and key != "HF_TOKEN"
            }
            on_status("Starting local model server")
            self._append_log("Starting local model server\n")
            try:
                process = start_process(
                    argv,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    env=environment,
                )
            except OSError as exc:
                raise EngineError(f"Could not start the local model executable: {exc}") from exc

            with self._state_lock:
                self._process = process
                self._port = port
                self._api_token = token
                self._diagnostic_tail = ""
                self._budget_unavailable = False
                self._usage_cache = None
            self._log_thread = threading.Thread(
                target=self._pump_log,
                args=(process.stdout, token),
                name="letracode-engine-log",
                daemon=True,
            )
            self._log_thread.start()

            deadline = time.monotonic() + _MODEL_LOAD_TIMEOUT
            try:
                on_status("Loading local model")
                while time.monotonic() < deadline:
                    if cancel.is_set() or self._cancel_requested.is_set():
                        self.stop()
                        raise Cancelled("Local model startup was cancelled")
                    if process.poll() is not None:
                        self._join_log_thread(0.25)
                        error = self._startup_exit_error(process.returncode)
                        self.stop()
                        raise error
                    if self._health_ready(port, token):
                        on_status("Local model ready")
                        return
                    time.sleep(0.05)
            except (Cancelled, EngineError):
                raise
            except (OSError, http.client.HTTPException):
                if process.poll() is not None:
                    self._join_log_thread(0.25)
                    error = self._startup_exit_error(process.returncode)
                    self.stop()
                    raise error
            except BaseException:
                self.stop()
                raise
            self.stop()
            raise EngineError("Timed out after 180 seconds while loading the local model")

    def complete(
        self,
        messages: list[dict],
        tools: list[dict] | None,
        cancel: threading.Event,
        on_delta: Callable[[str], None],
        thinking: bool = False,
    ) -> dict:
        """Stream one authenticated OpenAI-compatible chat completion."""
        if not self._operation_lock.acquire(blocking=False):
            raise EngineError("A local model completion is already in progress")
        watcher_done = threading.Event()
        watcher = None
        deadline_expired = threading.Event()
        connection: http.client.HTTPConnection | None = None
        response: http.client.HTTPResponse | None = None
        try:
            if cancel.is_set():
                raise Cancelled("Local model completion was cancelled")
            self._cancel_requested.clear()
            payload = self._completion_payload(messages, tools, thinking)
            self.start(cancel)
            if cancel.is_set() or self._cancel_requested.is_set():
                self.stop()
                raise Cancelled("Local model completion was cancelled")
            usage = self._request_usage(payload, messages, tools, cancel, thinking)
            if usage.total_tokens > self.config.context_size:
                raise ContextOverflowError(
                    f'The local model context window cannot fit this request: '
                    f'{usage.prompt_tokens} prompt + {usage.reply_tokens} reserved reply + '
                    f'{usage.safety_tokens} safety tokens ({usage.method}), '
                    f'context {self.config.context_size}.')
            with self._state_lock:
                port = self._port
                token = self._api_token
                process = self._process
            if port is None or token is None or process is None:
                self._raise_if_cancelled(cancel)
                raise EngineError("The local model server is not running")

            deadline = time.monotonic() + _REQUEST_TIMEOUT
            watcher = threading.Thread(
                target=self._watch_external_cancel,
                args=(cancel, watcher_done, deadline, deadline_expired),
                name="letracode-engine-cancel",
                daemon=True,
            )
            watcher.start()
            connection = http.client.HTTPConnection(_HOST, port, timeout=_REQUEST_TIMEOUT)
            with self._state_lock:
                self._active_connection = connection
            connection.request(
                "POST",
                "/v1/chat/completions",
                body=payload,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "Accept": "text/event-stream",
                },
            )
            response = connection.getresponse()
            with self._state_lock:
                self._active_response = response

            if 300 <= response.status < 400:
                response.read(_MAX_ERROR_BYTES + 1)
                raise EngineError("The local model server attempted an HTTP redirect, which was refused")
            if response.status < 200 or response.status >= 300:
                raw = response.read(_MAX_ERROR_BYTES + 1)
                raise self._server_response_error(response.status, raw, bool(tools))
            content_type = response.getheader("Content-Type", "").lower()
            if "text/event-stream" not in content_type:
                raw = response.read(_MAX_ERROR_BYTES + 1)
                raise EngineError(
                    "The local model server returned a non-streaming response: "
                    + self._error_detail(raw)
                )
            return self._read_completion_stream(
                response, process, cancel, on_delta, deadline
            )
        except Cancelled:
            if deadline_expired.is_set():
                raise EngineError('Local model completion timed out at its request deadline') from None
            raise
        except EngineError:
            if deadline_expired.is_set():
                raise EngineError('Local model completion timed out at its request deadline') from None
            raise
        except (
            OSError,
            http.client.HTTPException,
            TimeoutError,
            ValueError,
            AttributeError,
        ) as exc:
            if deadline_expired.is_set():
                raise EngineError('Local model completion timed out at its request deadline') from exc
            if cancel.is_set() or self._cancel_requested.is_set():
                raise Cancelled("Local model completion was cancelled") from exc
            with self._state_lock:
                process = self._process
            if process is not None and process.poll() is None:
                # A socket reset can precede Windows process-exit notification.
                try:
                    process.wait(timeout=0.1)
                except subprocess.TimeoutExpired:
                    pass
            if process is not None and process.poll() is not None:
                raise EngineError(
                    f"The local model server exited during completion (code {process.returncode})"
                ) from exc
            raise EngineError(f"Local model connection failed: {exc}") from exc
        finally:
            watcher_done.set()
            if watcher is not None:
                watcher.join()
            with self._state_lock:
                if self._active_response is response:
                    self._active_response = None
                if self._active_connection is connection:
                    self._active_connection = None
            if response is not None:
                try:
                    response.close()
                except (OSError, ValueError, AttributeError):
                    pass
            if connection is not None:
                try:
                    connection.close()
                except (OSError, ValueError, AttributeError):
                    pass
            self._operation_lock.release()

    def _completion_payload(
        self, messages: list[dict], tools: list[dict] | None, thinking: bool
    ) -> bytes:
        if not isinstance(messages, list) or not all(isinstance(item, dict) for item in messages):
            raise EngineError("Messages must be a list of chat message objects")
        if tools is not None and (
            not isinstance(tools, list) or not all(isinstance(item, dict) for item in tools)
        ):
            raise EngineError("Tools must be a list of tool definition objects")
        body: dict[str, object] = {
            "model": "local",
            "messages": messages,
            "stream": True,
            "max_tokens": self.config.max_tokens,
            "temperature": self.config.temperature,
            "chat_template_kwargs": {"enable_thinking": bool(thinking)},
        }
        if tools:
            body["tools"] = tools
            body["parallel_tool_calls"] = False
        try:
            payload = json.dumps(
                body, ensure_ascii=False, separators=(",", ":"), allow_nan=False
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise EngineError(f"The chat request is not valid JSON: {exc}") from exc
        if len(payload) > _MAX_REQUEST_BYTES:
            raise EngineError("The chat request is too large for the local engine")
        return payload

    def request_usage(self, messages, tools, cancel, thinking=False) -> RequestUsage:
        """Count the runtime-formatted prompt without generating model output."""
        if not self._operation_lock.acquire(blocking=False):
            raise EngineError("A local model completion is already in progress")
        try:
            if cancel.is_set():
                raise Cancelled('Request counting was cancelled')
            self._cancel_requested.clear()
            payload = self._completion_payload(messages, tools, thinking)
            self.start(cancel)
            return self._request_usage(payload, messages, tools, cancel, thinking)
        finally:
            self._operation_lock.release()

    def _request_usage(self, payload, messages, tools, cancel, thinking):
        self._raise_if_cancelled(cancel)
        if self._usage_cache and self._usage_cache[0] == payload:
            return self._usage_cache[1]
        if not self._budget_unavailable:
            try:
                body = json.loads(payload)
                formatted = self._budget_json('/apply-template', body, cancel)
                prompt = formatted.get('prompt')
                if not isinstance(prompt, str):
                    raise _BudgetUnavailable()
                if tools:
                    # Older servers may accept this endpoint but ignore tools.
                    # Do not report an exact count when definitions are omitted.
                    without_tools = dict(body)
                    without_tools.pop('tools', None)
                    without_tools.pop('parallel_tool_calls', None)
                    bare = self._budget_json('/apply-template', without_tools, cancel)
                    if bare.get('prompt') == prompt:
                        raise _BudgetUnavailable()
                tokenized = self._budget_json('/tokenize', {
                    'content': prompt, 'add_special': True, 'parse_special': True,
                    'with_pieces': False}, cancel)
                tokens = tokenized.get('tokens')
                if not isinstance(tokens, list) or not all(type(t) is int for t in tokens):
                    raise _BudgetUnavailable()
                usage = RequestUsage(len(tokens), self.config.max_tokens, 128, 'runtime tokenizer')
                self._usage_cache = (payload, usage)
                return usage
            except _BudgetUnavailable:
                self._budget_unavailable = True
        usage = fallback_usage(messages, tools, self.config.max_tokens, thinking)
        self._usage_cache = (payload, usage)
        return usage

    def _budget_json(self, path, body, cancel):
        """Bounded authenticated loopback call; never follow a redirect."""
        self._raise_if_cancelled(cancel)
        with self._state_lock:
            port, token = self._port, self._api_token
        if port is None or token is None:
            self._raise_if_cancelled(cancel)
            raise EngineError('The local model server is not running')
        payload = json.dumps(body, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')
        if len(payload) > _MAX_REQUEST_BYTES:
            raise _BudgetUnavailable()
        connection = http.client.HTTPConnection(_HOST, port, timeout=_BUDGET_REQUEST_TIMEOUT)
        response = None
        watcher_done, deadline_expired = threading.Event(), threading.Event()
        deadline = time.monotonic() + _BUDGET_REQUEST_TIMEOUT
        watcher = threading.Thread(target=self._watch_external_cancel,
            args=(cancel, watcher_done, deadline, deadline_expired),
            name='letracode-budget-cancel', daemon=True)
        watcher.start()
        with self._state_lock:
            self._active_connection = connection
        try:
            connection.request('POST', path, body=payload, headers={
                'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'})
            response = connection.getresponse()
            with self._state_lock:
                self._active_response = response
            raw = response.read(_MAX_REQUEST_BYTES + 1)
            self._raise_if_cancelled(cancel)
            if response.status in (400, 404, 405, 422, 501):
                raise _BudgetUnavailable()
            if response.status != 200:
                raise EngineError(f'Local model budget endpoint failed (HTTP {response.status})')
            if len(raw) > _MAX_REQUEST_BYTES:
                raise _BudgetUnavailable()
            try:
                result = json.loads(raw)
            except (ValueError, UnicodeDecodeError):
                raise _BudgetUnavailable() from None
            if not isinstance(result, dict):
                raise _BudgetUnavailable()
            return result
        except Cancelled:
            if deadline_expired.is_set():
                raise EngineError('Local model budget request timed out at its deadline') from None
            raise
        except (OSError, http.client.HTTPException, ValueError, AttributeError) as exc:
            if deadline_expired.is_set():
                raise EngineError('Local model budget request timed out at its deadline') from exc
            self._raise_if_cancelled(cancel)
            raise EngineError(f'Local model budget connection failed: {exc}') from exc
        finally:
            watcher_done.set()
            watcher.join()
            with self._state_lock:
                if self._active_connection is connection:
                    self._active_connection = None
                if self._active_response is response:
                    self._active_response = None
            if response is not None:
                response.close()
            connection.close()

    def _watch_external_cancel(
        self, cancel: threading.Event, watcher_done: threading.Event,
        deadline: float, deadline_expired: threading.Event,
    ) -> None:
        while not watcher_done.wait(0.05):
            if cancel.is_set():
                self.cancel()
                return
            if time.monotonic() >= deadline:
                deadline_expired.set()
                self.cancel()
                return

    def _read_completion_stream(
        self,
        response: http.client.HTTPResponse,
        process: subprocess.Popen[bytes],
        cancel: threading.Event,
        on_delta: Callable[[str], None],
        deadline: float,
    ) -> dict:
        content_parts: list[str] = []
        content_bytes = 0
        tool_parts: dict[int, dict[str, str]] = {}
        tool_bytes = 0
        finish_reason: str | None = None
        saw_done = False
        event_lines: list[str] = []
        event_size = 0

        while True:
            self._raise_if_cancelled(cancel)
            if time.monotonic() >= deadline:
                self.stop()
                raise EngineError("Local model completion timed out after 300 seconds")
            raw_line = response.readline(_MAX_SSE_LINE_BYTES + 1)
            if not raw_line:
                break
            if len(raw_line) > _MAX_SSE_LINE_BYTES:
                raise EngineError("The local model server sent an oversized stream event")
            try:
                line = raw_line.decode("utf-8", errors="strict").rstrip("\r\n")
            except UnicodeDecodeError as exc:
                raise EngineError("The local model server sent malformed UTF-8") from exc
            if not line:
                if event_lines:
                    data = "\n".join(event_lines)
                    event_lines = []
                    event_size = 0
                    if data == "[DONE]":
                        saw_done = True
                        break
                    event = self._decode_stream_event(data)
                    content_bytes, tool_bytes, finish_reason = self._consume_stream_event(
                        event,
                        content_parts,
                        content_bytes,
                        tool_parts,
                        tool_bytes,
                        finish_reason,
                        on_delta,
                    )
                continue
            if line.startswith(":"):
                continue
            if line.startswith("data:"):
                value = line[5:]
                if value.startswith(" "):
                    value = value[1:]
                event_size += len(value.encode("utf-8"))
                if event_size > _MAX_SSE_LINE_BYTES:
                    raise EngineError("The local model server sent an oversized stream event")
                event_lines.append(value)

        self._raise_if_cancelled(cancel)
        if not saw_done:
            # Pipe EOF can arrive just before the OS reports process exit.
            # Briefly settle that race before classifying an incomplete stream.
            if process.poll() is None:
                try:
                    process.wait(timeout=0.1)
                except subprocess.TimeoutExpired:
                    pass
            if process.poll() is not None:
                raise EngineError(
                    f"The local model server exited during completion (code {process.returncode})"
                )
            raise EngineError("The local model server returned a truncated completion stream")
        if finish_reason == "length":
            raise EngineError("The local model response was truncated at the output token limit")
        if finish_reason not in ("stop", "tool_calls"):
            raise EngineError("The local model server returned a truncated completion stream")

        result: dict[str, object] = {
            "role": "assistant",
            "content": "".join(content_parts),
        }
        if tool_parts:
            calls: list[dict[str, object]] = []
            for index in sorted(tool_parts):
                parts = tool_parts[index]
                identifier = parts["id"]
                call_type = parts["type"] or "function"
                name = parts["name"]
                arguments = parts["arguments"]
                if not identifier or call_type != "function" or not name:
                    raise EngineError("The local model server returned a malformed tool call")
                try:
                    decoded_arguments = json.loads(arguments)
                except json.JSONDecodeError as exc:
                    raise EngineError(
                        "The local model server returned malformed JSON tool arguments"
                    ) from exc
                if not isinstance(decoded_arguments, dict):
                    raise EngineError(
                        "The local model server returned non-object JSON tool arguments"
                    )
                calls.append(
                    {
                        "id": identifier,
                        "type": "function",
                        "function": {"name": name, "arguments": arguments},
                    }
                )
            result["tool_calls"] = calls
        return result

    @staticmethod
    def _decode_stream_event(data: str) -> dict:
        try:
            event = json.loads(data)
        except json.JSONDecodeError as exc:
            raise EngineError("The local model server sent malformed JSON in its stream") from exc
        if not isinstance(event, dict):
            raise EngineError("The local model server sent malformed JSON in its stream")
        error = event.get("error")
        if error:
            if isinstance(error, dict):
                detail = str(error.get("message") or error)
            else:
                detail = str(error)
            raise EngineError(f"The local model server reported an error: {detail}")
        return event

    def _consume_stream_event(
        self,
        event: dict,
        content_parts: list[str],
        content_bytes: int,
        tool_parts: dict[int, dict[str, str]],
        tool_bytes: int,
        finish_reason: str | None,
        on_delta: Callable[[str], None],
    ) -> tuple[int, int, str | None]:
        choices = event.get("choices", [])
        if not isinstance(choices, list):
            raise EngineError("The local model server sent a malformed completion event")
        if not choices:
            return content_bytes, tool_bytes, finish_reason
        choice = choices[0]
        if not isinstance(choice, dict):
            raise EngineError("The local model server sent a malformed completion choice")
        new_finish = choice.get("finish_reason")
        if new_finish is not None:
            if not isinstance(new_finish, str):
                raise EngineError("The local model server sent a malformed finish reason")
            finish_reason = new_finish
        delta = choice.get("delta", {})
        if delta is None:
            delta = {}
        if not isinstance(delta, dict):
            raise EngineError("The local model server sent a malformed completion delta")
        content = delta.get("content")
        if content is not None:
            if not isinstance(content, str):
                raise EngineError("The local model server sent malformed response content")
            content_bytes += len(content.encode("utf-8"))
            if content_bytes > _MAX_OUTPUT_BYTES:
                raise EngineError("The local model response exceeded the output size limit")
            if content:
                content_parts.append(content)
                on_delta(content)

        calls = delta.get("tool_calls", [])
        if calls is None:
            calls = []
        if not isinstance(calls, list):
            raise EngineError("The local model server sent malformed tool calls")
        for call in calls:
            if not isinstance(call, dict):
                raise EngineError("The local model server sent a malformed tool call")
            index = call.get("index")
            if isinstance(index, bool) or not isinstance(index, int) or index < 0:
                raise EngineError("The local model server sent a tool call without a valid index")
            if index not in tool_parts:
                if len(tool_parts) >= _MAX_TOOL_CALLS:
                    raise EngineError("The local model response exceeded the tool-call limit")
                tool_parts[index] = {"id": "", "type": "", "name": "", "arguments": ""}
            parts = tool_parts[index]
            identifier = call.get("id")
            call_type = call.get("type")
            function = call.get("function", {})
            if function is None:
                function = {}
            if not isinstance(function, dict):
                raise EngineError("The local model server sent a malformed tool function")
            name = function.get("name")
            arguments = function.get("arguments")
            for field_name, fragment in (
                ("id", identifier),
                ("type", call_type),
                ("name", name),
                ("arguments", arguments),
            ):
                if fragment is None:
                    continue
                if not isinstance(fragment, str):
                    raise EngineError("The local model server sent malformed tool-call fields")
                tool_bytes += len(fragment.encode("utf-8"))
                if tool_bytes > _MAX_TOOL_BYTES:
                    raise EngineError("The local model response exceeded the tool-call size limit")
                if field_name in ("id", "type") and parts[field_name] == fragment:
                    continue
                parts[field_name] += fragment
        return content_bytes, tool_bytes, finish_reason

    def _raise_if_cancelled(self, cancel: threading.Event) -> None:
        if cancel.is_set() or self._cancel_requested.is_set():
            self.stop()
            raise Cancelled("Local model completion was cancelled")

    def _server_response_error(
        self, status: int, raw: bytes, used_tools: bool
    ) -> EngineError:
        detail = self._error_detail(raw)
        lowered = detail.lower()
        if "context" in lowered and any(
            marker in lowered for marker in ("exceed", "size", "full", "too large", "kv")
        ):
            return ContextOverflowError(f"The local model context window is full: {detail}")
        if used_tools and "tool" in lowered and any(
            marker in lowered for marker in ("template", "support", "jinja", "function")
        ):
            return EngineError(f"The selected model has an unsupported tool template: {detail}")
        return EngineError(f"Local model server error (HTTP {status}): {detail}")

    @staticmethod
    def _error_detail(raw: bytes) -> str:
        if len(raw) > _MAX_ERROR_BYTES:
            return "error response exceeded the size limit"
        try:
            value = json.loads(raw)
            if isinstance(value, dict) and "error" in value:
                error = value["error"]
                if isinstance(error, dict):
                    return str(error.get("message") or error)
                return str(error)
            return str(value)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return raw.decode("utf-8", errors="replace")[:2000] or "empty error response"

    def cancel(self) -> None:
        """Cancel active I/O and stop the owned server to unblock generation."""
        self._cancel_requested.set()
        self.stop()

    def stop(self) -> None:
        """Stop only the child process group created by this instance."""
        self._cancel_requested.set()
        with self._state_lock:
            process = self._process
            self._process = None
            self._port = None
            self._api_token = None
            response = self._active_response
            connection = self._active_connection
            self._active_response = None
            self._active_connection = None
        if process is not None:
            stop_process(process, timeout=_STOP_TIMEOUT)
        for stream in (connection, response):
            if stream is not None:
                try:
                    stream.close()
                except (OSError, ValueError, AttributeError):
                    pass
        if process is None:
            return
        self._join_log_thread(0.5)

    def _validated_paths(self) -> tuple[Path, Path]:
        executable_setting = self.config.executable.strip()
        if not executable_setting:
            executable_setting = shutil.which('llama-server.exe' if sys.platform == 'win32' else 'llama-server') or ''
        elif not any(separator in executable_setting for separator in (os.sep, os.altsep) if separator):
            name = executable_setting
            if sys.platform == 'win32' and not Path(name).suffix:
                name += '.exe'
            executable_setting = shutil.which(name) or executable_setting
        if not executable_setting:
            raise EngineError("No llama-server executable is configured")
        executable = Path(executable_setting).expanduser().resolve()
        if sys.platform == 'win32' and executable.suffix.lower() != '.exe':
            raise EngineError('Select the native llama-server.exe executable, not a script or batch file')
        access = os.R_OK if sys.platform == 'win32' else os.R_OK | os.X_OK
        if not executable.is_file() or not os.access(executable, access):
            raise EngineError("The configured local model executable is missing or not executable")

        if not self.config.model_path.strip():
            raise EngineError("No local GGUF model is configured")
        model = Path(self.config.model_path).expanduser().resolve()
        if not model.is_file() or not os.access(model, os.R_OK):
            raise EngineError("The configured local model file is missing or not readable")
        try:
            with model.open("rb") as handle:
                magic = handle.read(4)
        except OSError as exc:
            raise EngineError(f"Could not read the configured model: {exc}") from exc
        if magic != b"GGUF":
            raise EngineError("The configured model is not a valid GGUF file")

        self._validate_number("context size", self.config.context_size, 1, 1_048_576)
        self._validate_number("GPU layers", self.config.gpu_layers, 0, 1_000_000)
        self._validate_number("threads", self.config.threads, 1, 4096)
        self._validate_number("maximum output tokens", self.config.max_tokens, 1, 1_048_576)
        if (
            isinstance(self.config.temperature, bool)
            or not isinstance(self.config.temperature, (int, float))
            or not math.isfinite(self.config.temperature)
            or not 0 <= self.config.temperature <= 5
        ):
            raise EngineError("Temperature must be a finite number from 0 through 5")
        return executable, model

    @staticmethod
    def _validate_number(name: str, value: object, minimum: int, maximum: int) -> None:
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not minimum <= value <= maximum
        ):
            raise EngineError(f"The {name} must be an integer from {minimum} through {maximum}")

    def _prepare_data_dir(self) -> None:
        try:
            self.data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            if os.name == "posix":
                self.data_dir.chmod(0o700)
        except OSError as exc:
            raise EngineError(f"Could not create the local engine data directory: {exc}") from exc

    @staticmethod
    def _reserve_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind((_HOST, 0))
            return int(sock.getsockname()[1])

    @staticmethod
    def _launcher_argv(executable: Path, arguments: list[str]) -> list[str]:
        if sys.platform.startswith("linux"):
            return [sys.executable, "-c", _LINUX_LAUNCHER, str(executable), *arguments]
        return [str(executable), *arguments]

    @staticmethod
    def _health_ready(port: int, token: str) -> bool:
        connection = http.client.HTTPConnection(_HOST, port, timeout=0.5)
        try:
            connection.request(
                "GET",
                "/health",
                headers={"Authorization": f"Bearer {token}"},
            )
            response = connection.getresponse()
            response.read(65_536)
            return response.status == 200
        except (OSError, http.client.HTTPException):
            return False
        finally:
            connection.close()

    def _startup_exit_error(self, returncode: int | None) -> EngineError:
        diagnostic = self._diagnostic_tail.strip()
        lowered = diagnostic.lower()
        if any(
            marker in lowered
            for marker in ("unknown argument", "unrecognized argument", "invalid argument")
        ):
            return EngineError(
                "The local model server rejected required flags; install a current llama.cpp build"
            )
        detail = f": {diagnostic[-2000:]}" if diagnostic else ""
        return EngineError(
            f"The local model server exited during startup (code {returncode}){detail}"
        )

    def _pump_log(self, stream: BinaryIO | None, token: str) -> None:
        if stream is None:
            return
        carry = ""
        keep = max(0, len(token) - 1)
        try:
            while True:
                block = os.read(stream.fileno(), 4096)
                if not block:
                    break
                combined = carry + block.decode("utf-8", errors="replace")
                newline = combined.rfind("\n")
                if newline >= 0:
                    self._append_log(combined[: newline + 1].replace(token, "[REDACTED]"))
                    carry = combined[newline + 1 :]
                elif len(combined) > 8192 + keep:
                    split = len(combined) - keep
                    self._append_log(combined[:split].replace(token, "[REDACTED]"))
                    carry = combined[split:]
                else:
                    carry = combined
            if carry:
                self._append_log(carry.replace(token, "[REDACTED]"))
        except (OSError, ValueError):
            pass
        finally:
            try:
                stream.close()
            except OSError:
                pass

    def _append_log(self, text: str) -> None:
        if not text:
            return
        encoded = text.encode("utf-8", errors="replace")
        with self._log_lock:
            self._diagnostic_tail = (self._diagnostic_tail + text)[-_LOG_TAIL_LIMIT:]
            log_path = self.data_dir / "engine.log"
            backup_path = self.data_dir / "engine.log.1"
            try:
                size = log_path.stat().st_size if log_path.exists() else 0
                if size + len(encoded) > _LOG_LIMIT:
                    backup_path.unlink(missing_ok=True)
                    if log_path.exists():
                        log_path.replace(backup_path)
                with log_path.open("ab") as handle:
                    handle.write(encoded)
                if os.name == "posix":
                    log_path.chmod(0o600)
            except OSError:
                pass

    def _join_log_thread(self, timeout: float) -> None:
        thread = self._log_thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=timeout)
