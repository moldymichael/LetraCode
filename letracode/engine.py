"""Managed local llama.cpp inference process.

The engine owns one ``llama-server`` child (a router for two models) and talks to it only over a
randomly authenticated loopback connection. The public methods are
synchronous so GUI callers must invoke ``start`` and ``complete`` from a
worker thread.
"""

from __future__ import annotations

import atexit
from concurrent.futures import ThreadPoolExecutor
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
import tempfile
import threading
import time
from typing import BinaryIO, Callable
import weakref

from .processes import start_process, stop_process
from .reasoning import ThinkSplitter

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

# Linux's parent-death signal follows the thread that forks the child, not
# the application's lifetime. Qt conversation workers are short lived, so
# create processes on one persistent stdlib worker. It performs only Popen;
# loading and inference still run on the caller's background thread.
_PROCESS_LAUNCHER = (
    ThreadPoolExecutor(max_workers=1, thread_name_prefix="letracode-process-launch")
    if sys.platform.startswith("linux") else None
)

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
    lora_path: str = ""
    secondary_model_path: str = ""


class EngineError(RuntimeError):
    """A configuration, process, or local protocol failure."""


def validated_lora_path(value: str) -> Path | None:
    """Validate one optional GGUF adapter; the engine checks model compatibility."""
    if not isinstance(value, str):
        raise EngineError("The LoRA adapter path must be text")
    if not value.strip():
        return None
    try:
        adapter = Path(value).expanduser().resolve()
        # llama.cpp splits this option on commas, even with a single argv value.
        if ',' in value or ',' in str(adapter):
            raise EngineError("The LoRA adapter path cannot contain commas. Rename or move the file first.")
        if not adapter.is_file() or not os.access(adapter, os.R_OK):
            raise EngineError("The configured LoRA adapter is missing or not a readable file")
        with adapter.open('rb') as handle:
            if handle.read(4) != b'GGUF':
                raise EngineError("The configured LoRA adapter is not a valid GGUF file")
    except (OSError, ValueError, RuntimeError) as exc:
        if isinstance(exc, EngineError):
            raise
        raise EngineError(f"Could not read the configured LoRA adapter: {exc}") from exc
    return adapter


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
        self._budget_unavailable: set[str] = set()
        self._usage_cache = None
        self._preset_path: Path | None = None
        self._loaded_models: tuple[str, ...] = ()
        _INSTANCES.add(self)

    @property
    def running(self) -> bool:
        with self._state_lock:
            process = self._process
            return process is not None and process.poll() is None

    @property
    def loaded_models(self) -> tuple[str, ...]:
        """Model IDs whose startup was verified, empty after the server stops."""
        with self._state_lock:
            return self._loaded_models if self.running else ()

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
            adapter = validated_lora_path(self.config.lora_path)
            secondary = self._validated_secondary_model(model)
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
            if secondary is not None:
                preset = self._write_model_preset(model, secondary, adapter)
                arguments[6:8] = [
                    "--models-preset", str(preset), "--models-max", "2",
                    "--no-models-autoload", "--offline",
                ]
            elif adapter is not None:
                arguments.extend(["--lora", str(adapter)])
            argv = self._launcher_argv(executable, arguments)
            environment = {
                key: value
                for key, value in os.environ.items()
                if not key.startswith("LLAMA_") and key != "HF_TOKEN"
            }
            on_status("Starting local model server")
            self._append_log("Starting local model server\n")
            try:
                options = {
                    "stdin": subprocess.DEVNULL, "stdout": subprocess.PIPE,
                    "stderr": subprocess.STDOUT, "env": environment,
                }
                if _PROCESS_LAUNCHER is not None:
                    process = _PROCESS_LAUNCHER.submit(start_process, argv, **options).result()
                else:
                    process = start_process(argv, **options)
            except OSError as exc:
                self.stop()
                raise EngineError(f"Could not start the local model executable: {exc}") from exc

            with self._state_lock:
                self._process = process
                self._port = port
                self._api_token = token
                self._diagnostic_tail = ""
                self._budget_unavailable.clear()
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
                        if secondary is not None:
                            self._load_router_models(port, token, cancel, deadline, on_status)
                        else:
                            with self._state_lock:
                                self._loaded_models = ("local",)
                        on_status("Local model ready")
                        return
                    time.sleep(0.05)
            except (Cancelled, EngineError):
                self.stop()
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

    def _write_model_preset(self, model: Path, secondary: Path, adapter: Path | None = None) -> Path:
        """Write only the selected models; no user-supplied preset directives."""
        if adapter is not None:
            self._validate_router_path(adapter)
        # A router-wide --lora would be inherited by both models. Restrict the
        # adapter to Model A's preset, which llama.cpp renders into its argv.
        primary_adapter = f"lora = {adapter}\n" if adapter is not None else ""
        content = (
            f"version = 1\n[local]\nmodel = {model}\n"
            f"{primary_adapter}"
            f"[local-b]\nmodel = {secondary}\n"
        )
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", newline="\n", prefix="router-",
                suffix=".ini", dir=self.data_dir, delete=False,
            ) as handle:
                self._preset_path = Path(handle.name)
                handle.write(content)
            return self._preset_path
        except OSError as exc:
            self.stop()
            raise EngineError(f"Could not write the local model preset: {exc}") from exc

    def _load_router_models(
        self, port: int, token: str, cancel: threading.Event, deadline: float,
        on_status: Callable[[str], None],
    ) -> None:
        """Explicit loads are asynchronous; verify that both workers are ready."""
        watcher_done = threading.Event()
        deadline_expired = threading.Event()
        watcher = threading.Thread(
            target=self._watch_external_cancel,
            args=(cancel, watcher_done, deadline, deadline_expired),
            name="letracode-router-cancel", daemon=True,
        )
        watcher.start()
        try:
            states = self._router_model_states(port, token)
            for model in ("local", "local-b"):
                self._raise_if_cancelled(cancel)
                on_status(f"Loading {model} ({'Model A' if model == 'local' else 'Model B'})")
                if states[model] == "unloaded":
                    result = self._router_json(port, token, "POST", "/models/load", {"model": model})
                    if result.get("success") is not True:
                        raise EngineError(f"The local model router did not accept loading {model}")
            while time.monotonic() < deadline:
                self._raise_if_cancelled(cancel)
                states = self._router_model_states(port, token)
                with self._state_lock:
                    self._loaded_models = tuple(
                        model for model in ("local", "local-b") if states[model] == "loaded"
                    )
                if states == {"local": "loaded", "local-b": "loaded"}:
                    on_status("Model A and Model B loaded")
                    return
                cancel.wait(0.05)
            raise EngineError("Timed out while loading both local models")
        except (Cancelled, EngineError):
            if deadline_expired.is_set():
                raise EngineError("Timed out while loading both local models") from None
            if cancel.is_set() or self._cancel_requested.is_set():
                raise Cancelled("Local model startup was cancelled") from None
            raise
        finally:
            watcher_done.set()
            if watcher is not threading.current_thread():
                watcher.join()

    def _router_model_states(self, port: int, token: str) -> dict[str, str]:
        result = self._router_json(port, token, "GET", "/models")
        entries = result.get("data")
        if not isinstance(entries, list):
            raise EngineError("The local model router returned malformed model status")
        states = {}
        for entry in entries:
            if not isinstance(entry, dict):
                raise EngineError("The local model router returned malformed model status")
            model = entry.get("id")
            if model not in ("local", "local-b"):
                continue
            status = entry.get("status")
            if not isinstance(status, dict) or model in states:
                raise EngineError("The local model router returned malformed model status")
            if status.get("failed"):
                raise EngineError(f"The local model router failed to load {model}; check engine.log and available memory")
            state = status.get("value")
            if state not in ("unloaded", "loading", "loaded"):
                raise EngineError(f"The local model router returned invalid status for {model}")
            states[model] = state
        if len(states) != 2:
            raise EngineError("The local model router did not list both configured models; install a current llama.cpp build")
        return states

    def _router_json(
        self, port: int, token: str, method: str, path: str, body: dict | None = None,
    ) -> dict:
        # The watchdog enforces the whole startup deadline. A short socket
        # timeout would incorrectly fail a busy host while it spawns a worker.
        connection = http.client.HTTPConnection(_HOST, port, timeout=_MODEL_LOAD_TIMEOUT)
        response = None
        try:
            with self._state_lock:
                self._active_connection = connection
            connection.request(
                method, path, body=json.dumps(body).encode("utf-8") if body is not None else None,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            )
            response = connection.getresponse()
            with self._state_lock:
                self._active_response = response
            if 300 <= response.status < 400:
                raise EngineError("The local model router attempted an HTTP redirect, which was refused")
            raw = response.read(_MAX_ERROR_BYTES + 1)
            if len(raw) > _MAX_ERROR_BYTES:
                raise EngineError("The local model router response exceeded the size limit")
            if response.status == 404:
                raise EngineError("Multi-model conversations require a current llama.cpp build with router support")
            if not 200 <= response.status < 300:
                detail = self._error_detail(raw).replace(token, "[REDACTED]")
                raise EngineError(f"Local model router error (HTTP {response.status}): {detail}")
            try:
                result = json.loads(raw)
            except (ValueError, UnicodeDecodeError) as exc:
                raise EngineError("The local model router returned malformed JSON") from exc
            if not isinstance(result, dict):
                raise EngineError("The local model router returned malformed JSON")
            return result
        except (OSError, http.client.HTTPException, ValueError, AttributeError) as exc:
            raise EngineError(f"Local model router connection failed: {exc}") from exc
        finally:
            with self._state_lock:
                if self._active_connection is connection:
                    self._active_connection = None
                if self._active_response is response:
                    self._active_response = None
            for stream in (response, connection):
                if stream is not None:
                    try:
                        stream.close()
                    except (OSError, ValueError, AttributeError):
                        pass

    def complete(
        self,
        messages: list[dict],
        tools: list[dict] | None,
        cancel: threading.Event,
        on_delta: Callable[[str], None],
        thinking: bool = False,
        model: str = "local",
        *,
        on_reasoning: Callable[[str], None] | None = None,
    ) -> dict:
        """Stream an answer and optional model thinking through separate callbacks."""
        if not self._operation_lock.acquire(blocking=False):
            raise EngineError("A local model completion is already in progress")
        watcher_done = threading.Event()
        watcher = None
        deadline_expired = threading.Event()
        connection: http.client.HTTPConnection | None = None
        response: http.client.HTTPResponse | None = None
        completion_succeeded = False
        try:
            if cancel.is_set():
                raise Cancelled("Local model completion was cancelled")
            self._cancel_requested.clear()
            payload = self._completion_payload(messages, tools, thinking, model)
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
            result = self._read_completion_stream(
                response, process, cancel, on_delta, deadline, on_reasoning
            )
            completion_succeeded = True
            return result
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
            if watcher is not None and watcher is not threading.current_thread():
                watcher.join()
            if self.config.secondary_model_path and not completion_succeeded:
                self.stop()
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
        self, messages: list[dict], tools: list[dict] | None, thinking: bool,
        model: str = "local",
    ) -> bytes:
        if model != "local" and not (model == "local-b" and self.config.secondary_model_path):
            raise EngineError("The requested model is not configured in this local engine")
        if not isinstance(messages, list) or not all(isinstance(item, dict) for item in messages):
            raise EngineError("Messages must be a list of chat message objects")
        if tools is not None and (
            not isinstance(tools, list) or not all(isinstance(item, dict) for item in tools)
        ):
            raise EngineError("Tools must be a list of tool definition objects")
        body: dict[str, object] = {
            "model": model,
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

    def request_usage(self, messages, tools, cancel, thinking=False, model="local") -> RequestUsage:
        """Count the runtime-formatted prompt without generating model output."""
        if not self._operation_lock.acquire(blocking=False):
            raise EngineError("A local model completion is already in progress")
        try:
            if cancel.is_set():
                raise Cancelled('Request counting was cancelled')
            self._cancel_requested.clear()
            payload = self._completion_payload(messages, tools, thinking, model)
            self.start(cancel)
            return self._request_usage(payload, messages, tools, cancel, thinking)
        finally:
            self._operation_lock.release()

    def _request_usage(self, payload, messages, tools, cancel, thinking):
        self._raise_if_cancelled(cancel)
        if self._usage_cache and self._usage_cache[0] == payload:
            return self._usage_cache[1]
        body = json.loads(payload)
        model = body['model']
        if model not in self._budget_unavailable:
            try:
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
                    'model': model,
                    'content': prompt, 'add_special': True, 'parse_special': True,
                    'with_pieces': False}, cancel)
                tokens = tokenized.get('tokens')
                if not isinstance(tokens, list) or not all(type(t) is int for t in tokens):
                    raise _BudgetUnavailable()
                usage = RequestUsage(len(tokens), self.config.max_tokens, 128, 'runtime tokenizer')
                self._usage_cache = (payload, usage)
                return usage
            except _BudgetUnavailable:
                self._budget_unavailable.add(model)
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
            if response.status in (404, 405, 501):
                raise _BudgetUnavailable()
            if response.status != 200:
                # Rejected requests do not mean the runtime lacks token counting.
                raise EngineError(
                    f'Local model budget endpoint {path} failed (HTTP {response.status}): '
                    f'{self._error_detail(raw)}')
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
        on_reasoning: Callable[[str], None] | None = None,
    ) -> dict:
        content_parts: list[str] = []
        reasoning_parts: list[str] = []

        def content_delta(text):
            content_parts.append(text)
            on_delta(text)

        def reasoning_delta(text):
            if text:
                reasoning_parts.append(text)
                if on_reasoning is not None:
                    on_reasoning(text)

        splitter = ThinkSplitter(content_delta, reasoning_delta)
        content_bytes = 0
        tool_parts: dict[int, dict[str, str]] = {}
        tool_bytes = 0
        finish_reason: str | None = None
        saw_done = False
        event_lines: list[str] = []
        event_size = 0

        try:
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
                            content_bytes,
                            tool_parts,
                            tool_bytes,
                            finish_reason,
                            splitter.feed,
                            reasoning_delta,
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

        finally:
            # Keep the final partial tag/text on cancellation or protocol errors.
            splitter.finish()

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
        if reasoning_parts:
            result["reasoning_content"] = "".join(reasoning_parts)
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
        content_bytes: int,
        tool_parts: dict[int, dict[str, str]],
        tool_bytes: int,
        finish_reason: str | None,
        on_delta: Callable[[str], None],
        on_reasoning: Callable[[str], None],
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
        for field in ("reasoning_content", "reasoning"):
            value = delta.get(field)
            if value is not None and not isinstance(value, str):
                raise EngineError("The local model server sent malformed reasoning content")
        # Servers may expose both aliases. Prefer the canonical field once.
        reasoning = delta.get("reasoning_content") or delta.get("reasoning")
        if reasoning:
            content_bytes += len(reasoning.encode("utf-8"))
            if content_bytes > _MAX_OUTPUT_BYTES:
                raise EngineError("The local model response exceeded the output size limit")
            on_reasoning(reasoning)
        content = delta.get("content")
        if content is not None:
            if not isinstance(content, str):
                raise EngineError("The local model server sent malformed response content")
            content_bytes += len(content.encode("utf-8"))
            if content_bytes > _MAX_OUTPUT_BYTES:
                raise EngineError("The local model response exceeded the output size limit")
            if content:
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
            self._loaded_models = ()
            preset = self._preset_path
            self._preset_path = None
            response = self._active_response
            connection = self._active_connection
            self._active_response = None
            self._active_connection = None
        if process is not None:
            stop_process(process, timeout=_STOP_TIMEOUT)
        if preset is not None:
            try:
                preset.unlink(missing_ok=True)
            except OSError:
                pass
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

    def _validated_secondary_model(self, primary: Path) -> Path | None:
        setting = self.config.secondary_model_path
        if not isinstance(setting, str):
            raise EngineError("The second local model path must be a string")
        if not setting:
            return None
        try:
            secondary = Path(setting).expanduser().resolve()
            if not secondary.is_file() or not os.access(secondary, os.R_OK):
                raise EngineError("The second local model file is missing or not readable")
            with secondary.open("rb") as handle:
                if handle.read(4) != b"GGUF":
                    raise EngineError("The second local model is not a valid GGUF file")
            if primary == secondary or primary.samefile(secondary):
                raise EngineError("Choose two different local model files for a multi-model conversation")
        except (OSError, ValueError) as exc:
            raise EngineError(f"Could not read the second local model: {exc}") from exc
        for model in (primary, secondary):
            self._validate_router_path(model)
        return secondary

    @staticmethod
    def _validate_router_path(path: Path) -> None:
        # llama.cpp's INI parser does not support quoted/escaped comment markers.
        # Reject paths it would truncate or interpret as a new directive.
        value = str(path)
        if (len(value.encode("utf-8")) > 32768 or value != value.rstrip()
                or any(char in value for char in "\r\n\x00;#")):
            raise EngineError(
                "The model or adapter path cannot be represented in a llama.cpp router preset; "
                "move or rename files to remove #, ;, line breaks, or trailing whitespace"
            )

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
                + (" with router support for multi-model conversations" if self.config.secondary_model_path else "")
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
