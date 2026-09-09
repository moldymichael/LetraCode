"""Owned local training and model-activation jobs; no GUI work off the Qt thread."""
from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import os
from pathlib import Path
import queue
import subprocess
import threading

from PySide6.QtCore import QThread, Signal

from .engine import Cancelled, EngineConfig, LocalEngine
from .processes import start_process, stop_process
from .strand import safe_read


BACKEND_SCRIPT = Path(__file__).with_name('training_backend.py')


def _output_lines(process):
    """Drain bounded output while observing a parent that exits before helpers."""
    events = queue.Queue(maxsize=64)
    finished = threading.Event()

    def publish(item):
        while not finished.is_set():
            try:
                events.put(item, timeout=.05)
                return
            except queue.Full:
                pass

    def read():
        try:
            for chunk in iter(lambda: process.stdout.readline(8192), b''):
                if finished.is_set():
                    break
                publish(chunk)
        except Exception as error:
            publish(error)
        finally:
            publish(None)

    reader = threading.Thread(target=read, daemon=True, name='letracode-training-output')
    reader.start()
    reaped = False
    try:
        while True:
            if not reaped and process.poll() is not None:
                stop_process(process)
                reaped = True
            try:
                item = events.get(timeout=.05)
            except queue.Empty:
                continue
            if item is None:
                break
            if isinstance(item, Exception):
                raise item
            yield item
    finally:
        finished.set()
        if reader.is_alive():
            stop_process(process)
        reader.join(2)


def file_hash(path, cancel=None):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError(f'Expected an ordinary local file: {path}')
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        before = os.fstat(stream.fileno())
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            if cancel is not None and cancel.is_set():
                raise Cancelled('Stopped')
            digest.update(chunk)
        after = os.fstat(stream.fileno())
    current = path.stat()
    identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns)
    if identity(before) != identity(after) or identity(after) != identity(current):
        raise ValueError(f'File changed while verifying it: {path}')
    return digest.hexdigest()


def read_report(directory, cancel, run):
    raw = safe_read(directory / 'report.json', 2 * 1024 * 1024)
    if not raw:
        raise ValueError('Training exited without an evaluation report.')
    report = json.loads(raw)
    if not isinstance(report, dict):
        raise ValueError('Training produced an invalid report.')
    for field in ('base_loss', 'candidate_loss'):
        number = report.get(field)
        if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number) or number < 0:
            raise ValueError('Training did not produce finite held-out comparison losses.')
    for field in ('optimization_steps', 'eval_response_tokens'):
        if type(report.get(field)) is not int or report[field] <= 0:
            raise ValueError(f'Training report needs a positive {field} count.')
    expected_steps = run['config']['epochs'] * math.ceil(len(run['examples']['train']) / run['config']['batch_size'])
    if report['optimization_steps'] != expected_steps:
        raise ValueError('Training report does not contain the configured optimization steps.')
    comparisons = report.get('eval_examples')
    held_out = run['examples']['eval'][:3]
    if not isinstance(comparisons, list) or len(comparisons) != len(held_out):
        raise ValueError('Training did not preserve held-out comparisons.')
    for comparison, expected in zip(comparisons, held_out):
        if (not isinstance(comparison, dict)
                or any(not isinstance(comparison.get(key), str) for key in ('prompt', 'response', 'base_output', 'candidate_output'))
                or any(comparison[key] != expected[key] for key in ('prompt', 'response'))):
            raise ValueError('Training comparison does not match the approved held-out examples.')
    versions = report.get('package_versions')
    if (not isinstance(versions, dict)
            or any(not isinstance(versions.get(name), str) or not versions[name]
                   for name in ('torch', 'transformers', 'peft', 'safetensors'))):
        raise ValueError('Training report is missing package versions.')
    provenance = report.get('provenance')
    if not isinstance(provenance, dict):
        raise ValueError('Training report is missing dataset provenance.')
    for field, filename in (('config_sha256', 'config.json'), ('train_sha256', 'train.jsonl'), ('eval_sha256', 'eval.jsonl')):
        if provenance.get(field) != file_hash(directory / filename, cancel):
            raise ValueError(f'Training report does not match the {filename} snapshot.')
    if (not isinstance(report.get('conversion_error'), str)
            or (not report.get('adapter_gguf') and not report['conversion_error'])):
        raise ValueError('Training report must preserve a converted adapter or a conversion error.')
    adapter = directory / 'adapter'
    if adapter.is_symlink() or not adapter.is_dir():
        raise ValueError('Training did not preserve a local adapter.')
    config = safe_read(adapter / 'adapter_config.json')
    if not config or not isinstance(json.loads(config), dict):
        raise ValueError('Training did not preserve the adapter configuration.')
    report['adapter_path'] = str(adapter)
    report['adapter_sha256'] = file_hash(adapter / 'adapter_model.safetensors', cancel)
    if report.get('adapter_gguf'):
        converted = directory / 'adapter.gguf'
        if Path(report['adapter_gguf']).resolve() != converted.resolve():
            raise ValueError('Converted adapter is outside this training run.')
        with converted.open('rb') as stream:
            if stream.read(4) != b'GGUF':
                raise ValueError('Converted adapter is not a GGUF file.')
        report['adapter_gguf'] = str(converted)
        report['adapter_gguf_sha256'] = file_hash(converted, cancel)
    return report


class TrainingWorker(QThread):
    status = Signal(str)

    def __init__(self, repository, run_id, parent=None):
        super().__init__(parent)
        self.repository = repository
        self.run_id = run_id
        self.cancelled = threading.Event()
        self.process = None
        self._lock = threading.Lock()

    def cancel(self):
        self.cancelled.set()
        with self._lock:
            process = self.process
        if process is not None:
            stop_process(process)

    def run(self):
        repo = self.repository
        process = None
        tail = ''
        try:
            run = repo.verify_run_snapshot(self.run_id)
            if run['status'] != 'queued':
                raise ValueError('Only a new queued training run can be started.')
            repo.update_run(self.run_id, 'running')
            config = run['config']
            directory = repo.run_directory(self.run_id)
            if self.cancelled.is_set():
                raise Cancelled('Training stopped before launch.')
            base_hash = ''
            if config.get('base_gguf'):
                self.status.emit('Verifying the base GGUF for this version…')
                base_hash = file_hash(config['base_gguf'], self.cancelled)
            environment = {k: v for k, v in os.environ.items()
                           if k not in ('HF_TOKEN', 'HUGGING_FACE_HUB_TOKEN', 'PYTHONPATH', 'PYTHONHOME')}
            environment.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
                               HF_DATASETS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1',
                               TOKENIZERS_PARALLELISM='false', PYTHONUNBUFFERED='1')
            process = start_process([config['python_executable'], str(BACKEND_SCRIPT),
                                     '--run-dir', str(directory)], cwd=str(directory),
                                    env=environment, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            with self._lock:
                self.process = process
            if self.cancelled.is_set():
                stop_process(process)
            written = 0
            with (directory / 'training.log').open('xb') as log:
                for chunk in _output_lines(process):
                    if written < 1024 * 1024:
                        saved = chunk[:1024 * 1024 - written]
                        log.write(saved); written += len(saved)
                    line = chunk.decode('utf-8', errors='replace').strip()
                    tail = (tail + line + '\n')[-8000:]
                    try:
                        event = json.loads(line)
                        if isinstance(event, dict) and isinstance(event.get('message'), str):
                            self.status.emit(event['message'][:2000])
                    except ValueError:
                        pass
            code = process.wait()
            if self.cancelled.is_set():
                raise Cancelled('Training stopped. Partial outputs are retained.')
            if code != 0:
                raise ValueError(f'Training exited with code {code}.\n{tail}')
            repo.verify_run_snapshot(self.run_id)
            report = read_report(directory, self.cancelled, run)
            report['base_gguf_sha256'] = base_hash
            if self.cancelled.is_set():
                raise Cancelled('Training stopped before its result was accepted.')
            repo.update_run(self.run_id, 'succeeded', report=report)
            self.status.emit('Training and held-out evaluation completed. Review the comparison before adopting.')
        except Cancelled as error:
            repo.update_run(self.run_id, 'cancelled', error=str(error))
            self.status.emit(str(error))
        except Exception as error:
            try:
                saved = repo.run(self.run_id)
                if saved and saved['status'] in ('queued', 'running'):
                    repo.update_run(self.run_id, 'cancelled' if self.cancelled.is_set() else 'failed', error=str(error))
            finally:
                self.status.emit(str(error))
        finally:
            if process is not None:
                stop_process(process)
                if process.stdout is not None:
                    process.stdout.close()
            with self._lock:
                self.process = None


class ActivationWorker(QThread):
    status = Signal(str)
    ready = Signal(object)
    failed = Signal(str)

    def __init__(self, repository, engine_config, run_id=None, rollback=None, parent=None, *, secondary_enabled=True):
        super().__init__(parent)
        self.repository = repository
        self.original_config = engine_config
        self.secondary_enabled = secondary_enabled
        self.selected_config = None
        self.run_id = run_id
        self.rollback = rollback
        self.cancelled = threading.Event()
        self.engine = None

    def cancel(self):
        self.cancelled.set()
        if self.engine is not None:
            self.engine.cancel()

    def run(self):
        transferred = False
        try:
            if self.rollback is not None:
                fields = {f.name for f in dataclasses.fields(EngineConfig)}
                config = EngineConfig(**{k: v for k, v in self.rollback.items() if k in fields})
            else:
                run = self.repository.run(self.run_id)
                if run['status'] != 'succeeded':
                    raise ValueError('This version has no completed training and evaluation.')
                report = run['report']
                adapter = self.repository.run_directory(self.run_id) / 'adapter.gguf'
                base = run['config'].get('base_gguf', '')
                if not base or not report.get('base_gguf_sha256') or not report.get('adapter_gguf_sha256'):
                    raise ValueError('This version needs a matching base GGUF and a converted adapter before adoption.')
                self.status.emit('Verifying this version’s base model and adapter…')
                if file_hash(base, self.cancelled) != report['base_gguf_sha256']:
                    raise ValueError('The base GGUF changed since training. Adoption was cancelled.')
                if file_hash(adapter, self.cancelled) != report['adapter_gguf_sha256']:
                    raise ValueError('The adapter changed since evaluation. Adoption was cancelled.')
                config = dataclasses.replace(self.original_config, model_path=base, lora_path=str(adapter))
            if self.cancelled.is_set():
                raise Cancelled('Model change stopped.')
            self.selected_config = config
            active_config = config if self.secondary_enabled else dataclasses.replace(config, secondary_model_path='')
            self.engine = LocalEngine(active_config, self.repository.store.directory)
            self.engine.start(self.cancelled, self.status.emit)
            if self.rollback is None:
                self.status.emit('Checking that model files stayed unchanged during loading…')
                if (file_hash(base, self.cancelled) != report['base_gguf_sha256'] or
                        file_hash(adapter, self.cancelled) != report['adapter_gguf_sha256']):
                    raise ValueError('The model or adapter changed while loading. Adoption was cancelled.')
            if self.cancelled.is_set():
                raise Cancelled('Model change stopped.')
            self.ready.emit(self.engine)
            transferred = True
        except Exception as error:
            self.failed.emit(str(error))
        finally:
            if self.engine is not None and not transferred:
                self.engine.stop()
