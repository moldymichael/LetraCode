"""Measurement inputs must not silently reuse an earlier sample's history."""
from pathlib import Path
import subprocess
import sys


def test_duplicate_revision_counts_are_rejected_before_creating_output(tmp_path):
    script = Path(__file__).resolve().parents[1] / 'tools' / 'measure_history.py'
    output = tmp_path / 'measurement'
    result = subprocess.run([
        sys.executable, str(script), '--output', str(output),
        '--counts', '1', '1', '--bytes', '16',
    ], capture_output=True, text=True, timeout=10)
    assert result.returncode == 2, result.stdout + result.stderr
    assert 'unique' in result.stderr.lower()
    assert not output.exists()
