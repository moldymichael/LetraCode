"""Portable test peers; production still launches a native llama.cpp executable."""
import sys

import pytest


@pytest.fixture
def python_engine_peer(monkeypatch):
    if sys.platform == 'win32':
        from letracode.engine import LocalEngine
        monkeypatch.setattr(LocalEngine, '_launcher_argv', staticmethod(
            lambda executable, arguments: [sys.executable, str(executable), *arguments]))


@pytest.fixture
def make_symlink():
    def create(link, target, target_is_directory=False):
        try:
            link.symlink_to(target, target_is_directory=target_is_directory)
        except OSError as error:
            if getattr(error, 'winerror', None) == 1314:
                pytest.skip('Windows symlink privilege or Developer Mode is unavailable')
            raise
    return create
