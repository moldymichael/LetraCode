"""Opt-in regression against an installed Hermes GGUF and llama-server.

Set LETRACODE_HERMES_TEST_CONFIG to a JSON file containing EngineConfig fields.
The test uses the configured primary model, starts its own temporary local
engine, and never executes model-requested tools or writes application data.
"""
import dataclasses
import json
import os
from pathlib import Path
import threading

import pytest

from letracode.engine import EngineConfig, LocalEngine
from letracode.tools import TOOL_SCHEMAS


def test_hermes_counts_full_tool_schema_and_generates_an_ordinary_reply(tmp_path):
    config_path = os.environ.get('LETRACODE_HERMES_TEST_CONFIG')
    if not config_path:
        pytest.skip('Set LETRACODE_HERMES_TEST_CONFIG to run the real Hermes regression')
    config = EngineConfig(**json.loads(Path(config_path).read_text(encoding='utf-8')))
    # Match the application's single-model mode even if setup has a Model B.
    config = dataclasses.replace(config, secondary_model_path='')
    engine = LocalEngine(config, tmp_path / 'engine')
    cancel = threading.Event()
    messages = [{'role': 'user', 'content': 'Reply with a short greeting. Do not call any tools.'}]
    deltas = []

    try:
        # Array-valued schema types previously caused /apply-template to fail
        # here, before any generation, even for this ordinary greeting.
        with_tools = engine.request_usage(messages, TOOL_SCHEMAS, cancel)
        without_tools = engine.request_usage(messages, None, cancel)
        assert with_tools.method == 'runtime tokenizer'
        assert without_tools.method == 'runtime tokenizer'
        assert with_tools.prompt_tokens > without_tools.prompt_tokens > 0

        reply = engine.complete(messages, TOOL_SCHEMAS, cancel, deltas.append)
        assert ''.join(deltas).strip()
        assert reply['content'].strip()
        assert not reply.get('tool_calls')
    finally:
        engine.stop()
