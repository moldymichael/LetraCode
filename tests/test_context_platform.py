import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from letracode.context import ProjectFiles, readable_without_approval


def test_windows_hidden_attributes_require_approval(tmp_path, monkeypatch):
    source = tmp_path / 'private' / 'notes.txt'
    source.parent.mkdir()
    source.write_text('private content', encoding='utf-8')
    real_stat = Path.stat

    def windows_stat(path, **kwargs):
        value = real_stat(path, **kwargs)
        if path == source.parent:
            return SimpleNamespace(st_mode=value.st_mode, st_file_attributes=2)
        return value

    monkeypatch.setattr(Path, 'stat', windows_stat)
    assert not readable_without_approval(source, [str(tmp_path)])


def test_hidden_volume_root_does_not_make_all_files_sensitive(tmp_path, monkeypatch):
    source = tmp_path / 'notes.txt'
    source.write_text('ordinary content', encoding='utf-8')
    real_stat = Path.stat

    def windows_stat(path, **kwargs):
        value = real_stat(path, **kwargs)
        if path == Path(path.anchor):
            return SimpleNamespace(st_mode=value.st_mode, st_file_attributes=2 | 4 | 16)
        return value

    monkeypatch.setattr(Path, 'stat', windows_stat)
    assert readable_without_approval(source, [str(tmp_path)])
    assert ProjectFiles([str(tmp_path)]).inventory() == [source]


def test_windows_source_extensions_are_retrieved(tmp_path):
    for name in ('setup.ps1', 'launch.cmd', 'build.bat', 'App.cs', 'App.csproj'):
        (tmp_path / name).write_text('project evidence', encoding='utf-8')
    assert {p.name for p in ProjectFiles([str(tmp_path)]).inventory()} == {
        'setup.ps1', 'launch.cmd', 'build.bat', 'App.cs', 'App.csproj'}


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows junction behavior')
def test_junction_does_not_grant_access_to_another_directory(tmp_path):
    root = tmp_path / 'project'
    root.mkdir()
    external = tmp_path / 'external'
    external.mkdir()
    (external / 'secret.txt').write_text('private content', encoding='utf-8')
    junction = root / 'junction'
    subprocess.run(['cmd.exe', '/d', '/c', 'mklink', '/J', str(junction), str(external)], check=True, capture_output=True)
    try:
        assert ProjectFiles([str(root)]).inventory() == []
        assert not readable_without_approval(junction / 'secret.txt', [str(root)])
        assert not readable_without_approval(junction / 'secret.txt', [str(junction)])
    finally:
        junction.rmdir()
