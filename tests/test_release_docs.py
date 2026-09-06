"""Source releases must contain the small documents/scripts they reference."""
import importlib.util
import io
from pathlib import Path
import re
import tarfile
from urllib.parse import unquote, urlsplit


def test_release_includes_current_and_historical_explanations_and_tools():
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location('release', root / 'packaging/build-release.py')
    release = importlib.util.module_from_spec(spec); spec.loader.exec_module(release)
    payload = release.archive_bytes('test')
    with tarfile.open(fileobj=io.BytesIO(payload), mode='r:gz') as archive:
        files = {Path(item.name).relative_to('LetraCode-test').as_posix() for item in archive.getmembers()}
        required = {'docs/CURRENT-STATE.md', 'docs/RELIABILITY-VERIFICATION.md',
                    'docs/FUTURE-TASKS-DESIGN.md', 'docs/STRAND-M1A-REVIEW.md',
                    'docs/SUPERVISED-CODING-PROOF.md', 'docs/VERIFICATION.md',
                    'tools/run_acceptance.py', 'tools/measure_history.py'}
        assert required <= files
        for name in sorted(files):
            if not name.endswith('.md'):
                continue
            text = archive.extractfile('LetraCode-test/' + name).read().decode()
            for target in re.findall(r'\[[^\]]+\]\(([^)]+)\)', text):
                parsed = urlsplit(target)
                if parsed.scheme or target.startswith(('/', '#')):
                    continue
                resolved = ((root / name).parent / unquote(parsed.path)).resolve()
                assert resolved.is_relative_to(root), (name, target)
                assert resolved.relative_to(root).as_posix() in files, (name, target)
        assert not any(name.endswith(('.gguf', '.sqlite3')) or 'test-scratch' in name for name in files)
