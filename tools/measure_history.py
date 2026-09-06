#!/usr/bin/env python3
"""Measure retained-history costs in an explicit NEW disposable directory.

No data is deleted. Timing includes instrumentation overhead. Counts describe
successful _read_at bytes and os.open calls, not physical disk/cache traffic.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from letracode import strand as module
from letracode.store import Store


def disk_usage(root):
    files = [path for path in root.rglob('*') if path.is_file()]
    stats = [path.stat() for path in files]
    return {'files': len(files), 'apparent_bytes': sum(info.st_size for info in stats),
            'allocated_bytes': sum(getattr(info, 'st_blocks', 0) * 512 for info in stats)}


def measure(action):
    counters = dict(current_bytes=0, historical_bytes=0, metadata_bytes=0,
                    file_opens=0, directory_opens=0)
    original_read, original_open = module._read_at, os.open

    def read(fd, name, *args, **kwargs):
        result = original_read(fd, name, *args, **kwargs)
        raw = result[0] if kwargs.get('with_stat') and result is not None else result
        if raw is not None:
            kind = ('historical_bytes' if name.endswith('.before') or
                    (name.endswith('.md') and len(name) == 35) else
                    'current_bytes' if name.endswith('.md') else 'metadata_bytes')
            counters[kind] += len(raw)
        return result

    def opened(path, flags, *args, **kwargs):
        result = original_open(path, flags, *args, **kwargs)
        counters['directory_opens' if flags & os.O_DIRECTORY else 'file_opens'] += 1
        return result

    start = time.perf_counter()
    with patch.object(module, '_read_at', read), patch.object(os, 'open', opened):
        result = action()
    counters['seconds'] = time.perf_counter() - start
    return result, counters


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='New absolute directory; existing paths are refused')
    parser.add_argument('--counts', type=int, nargs='+', default=[1, 20, 100, 1000])
    parser.add_argument('--bytes', type=int, default=256, dest='size')
    parser.add_argument('--max-seconds', type=int, default=600)
    parser.add_argument('--max-disk-mib', type=int, default=128)
    args = parser.parse_args()
    if len(set(args.counts)) != len(args.counts):
        parser.error('Revision counts must be unique; each sample requires its own fresh history')
    if (not args.output.is_absolute() or args.output.exists() or args.output.is_symlink()
            or not 16 <= args.size <= 4096 or not 1 <= args.max_seconds <= 3600
            or not 1 <= args.max_disk_mib <= 512 or not args.counts
            or any(not 1 <= count <= 1000 for count in args.counts) or len(args.counts) > 4):
        parser.error('Use a new absolute output, 1–4 counts in 1–1000, 16–4096 bytes, 1–3600 seconds and 1–512 MiB')
    args.output.mkdir(mode=0o700)
    started = time.monotonic()
    report = {'status': 'running', 'fixture_bytes': args.size, 'counts': args.counts,
              'max_seconds': args.max_seconds, 'max_disk_mib': args.max_disk_mib,
              'python': sys.version, 'rows': [],
              'measurement': 'Successful _read_at bytes and os.open calls; warm local filesystem; instrumented wall time.',
              'bounds': 'Checked between operations and every 10 fixture saves; one in-progress operation can exceed the time/disk threshold.'}

    def check_bounds():
        if time.monotonic() - started >= args.max_seconds:
            raise RuntimeError('Time bound reached; preserved partial fixture')
        usage = disk_usage(args.output)
        if max(usage['apparent_bytes'], usage['allocated_bytes']) >= args.max_disk_mib * 1024 * 1024:
            raise RuntimeError('Disk bound reached; preserved partial fixture')

    try:
        for count in args.counts:
            check_bounds()
            store = Store(args.output / f'revisions-{count}')
            current_hash = store.strand.snapshot('global')['sha256']
            seed_start = time.perf_counter()
            for revision in range(count):
                if revision % 10 == 0:
                    check_bounds()
                text = f'{revision:08d}:' + 'x' * (args.size - 9)
                saved = store.strand.replace('global', text, current_hash)
                current_hash = saved['after_sha256']
            row = {'revisions': count, 'seed_seconds': time.perf_counter() - seed_start,
                   'disk_before_probes': disk_usage(store.directory)}
            snapshot, row['snapshot'] = measure(lambda: store.strand.snapshot('global'))
            check_bounds()
            saved, row['save'] = measure(lambda: store.strand.replace('global', 'y' * args.size, snapshot['sha256']))
            check_bounds()
            _, row['undo'] = measure(lambda: store.strand.undo(saved['id']))
            row['disk_after_probes'] = disk_usage(store.directory)
            row['revisions_after_probes'] = count + 2
            report['rows'].append(row)
            print(json.dumps(row), flush=True)
        report['status'] = 'complete'
    except (Exception, KeyboardInterrupt) as error:
        report['status'] = 'stopped'
        report['error'] = str(error) or type(error).__name__
    finally:
        report['elapsed_seconds'] = time.monotonic() - started
        report['total_disk'] = disk_usage(args.output)
        (args.output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    return 0 if report['status'] == 'complete' else 1


if __name__ == '__main__':
    raise SystemExit(main())
