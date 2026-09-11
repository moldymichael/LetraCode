"""Windows quantizer selection must use a native executable."""
from pathlib import Path


def test_windows_quantizer_prefers_native_exe(tmp_path, monkeypatch):
    from letracode import training_models

    folder = tmp_path / 'build' / 'bin'
    folder.mkdir(parents=True)
    unix = folder / 'llama-quantize'
    native = folder / 'llama-quantize.exe'
    unix.write_bytes(b'not a Windows executable')
    native.write_bytes(b'MZ test fixture')
    unix.chmod(0o755)
    native.chmod(0o755)

    monkeypatch.setattr(training_models.sys, 'platform', 'win32')

    assert training_models._quantizer(tmp_path) == native.resolve()
