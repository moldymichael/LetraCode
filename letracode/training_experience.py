"""Preparation, recoverable conversion, and comparisons in the Chat runtime.

Optimizer snapshots stay immutable. Later conversion receipts, comparisons,
and the user's judgments live separately in ordinary persisted settings.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import threading
import time
import uuid

from PySide6.QtCore import QThread, Signal

from .context import SYSTEM
from .engine import Cancelled, LocalEngine
from .processes import start_process, stop_process
from .store import now
from .training import TrainingRepository
from .training_models import verify_gemma_pair, gemma_manifest_path
from .training_worker import BACKEND_SCRIPT, file_hash, training_model_family


def version_review(repo, ident):
    value = repo.store.setting('training_review_' + ident, {})
    return value if isinstance(value, dict) else {}


def save_version_review(repo, ident, **fields):
    if repo.run(ident) is None:
        raise ValueError('This training version does not exist.')
    if set(fields) - {'name', 'notes', 'judgment', 'comparison', 'conversion'}:
        raise ValueError('Unknown version review field.')
    for key, limit in (('name', 120), ('notes', 12000)):
        if key in fields and (not isinstance(fields[key], str) or len(fields[key]) > limit):
            raise ValueError(f'{key} must be text of at most {limit} characters.')
    if 'judgment' in fields and fields['judgment'] not in ('unreviewed', 'better', 'same', 'worse', 'mixed'):
        raise ValueError('Choose a supported comparison judgment.')
    value = dict(version_review(repo, ident), **fields, updated=now())
    repo.store.set_setting('training_review_' + ident, value)
    return value


def effective_report(repo, ident):
    report = dict(repo.run(ident)['report'])
    conversion = version_review(repo, ident).get('conversion') or {}
    if conversion.get('status') == 'complete':
        report.update(adapter_gguf=conversion['adapter_gguf'],
                      adapter_gguf_sha256=conversion['adapter_gguf_sha256'], conversion_error='')
    return report


def converted_artifact_state(repo, ident):
    """Cheap UI availability check; comparisons/activation recheck the full hash."""
    if not effective_report(repo, ident).get('adapter_gguf_sha256'):
        return 'unconverted'
    path = repo.run_directory(ident) / 'adapter.gguf'
    try:
        if not os.path.lexists(path):
            return 'missing'
        if path.is_symlink() or not stat.S_ISREG(path.stat().st_mode):
            return 'unavailable'
        with path.open('rb') as stream:
            if stream.read(4) != b'GGUF' or path.stat().st_size <= 4:
                return 'unavailable'
    except OSError:
        return 'unavailable'
    return 'present'


def can_retry_conversion(repo, ident):
    run = repo.run(ident)
    if run['status'] != 'succeeded' or repo.store.setting('training_active_version') == ident:
        return False
    path = repo.run_directory(ident) / 'adapter.gguf'
    if not os.path.lexists(path):
        return True
    # Existing files with a successful validation record must never be replaced.
    return (not effective_report(repo, ident).get('adapter_gguf_sha256')
            and not path.is_symlink() and path.is_file())


def version_stage(repo, ident):
    run = repo.run(ident)
    if repo.store.setting('training_active_version') == ident:
        if run['status'] == 'succeeded' and converted_artifact_state(repo, ident) != 'present':
            return 'In use by Strand · adapter unavailable; restore previous version'
        return 'In use by Strand'
    if run['status'] != 'succeeded':
        return {'queued': 'Waiting to train', 'running': 'Training', 'failed': 'Training needs attention',
                'cancelled': 'Training stopped', 'interrupted': 'Training interrupted'}.get(run['status'], run['status'])
    artifact = converted_artifact_state(repo, ident)
    if artifact == 'missing':
        return 'Trained · converted adapter missing; retry conversion'
    if artifact == 'unavailable':
        return 'Trained · converted adapter needs attention'
    if artifact == 'unconverted':
        return 'Trained · conversion needs attention'
    comparison = version_review(repo, ident).get('comparison') or {}
    if comparison.get('status') == 'complete':
        return 'Compared in Chat · review your results'
    if comparison:
        return 'Comparison incomplete · review errors or try again'
    return 'Converted · Compare in Chat next'


def offline_environment():
    env = {k: v for k, v in os.environ.items()
           if k not in ('HF_TOKEN', 'HUGGING_FACE_HUB_TOKEN', 'PYTHONPATH', 'PYTHONHOME')}
    env.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_DATASETS_OFFLINE='1',
               HF_HUB_DISABLE_TELEMETRY='1', TOKENIZERS_PARALLELISM='false', PYTHONUNBUFFERED='1')
    return env


def run_local(command, directory, logfile, cancel, *, timeout=1800):
    """Run a fixed app command in its own cancellable process group."""
    process = None
    deadline = time.monotonic() + timeout
    try:
        with Path(logfile).open('xb') as log:
            if cancel.is_set():
                raise Cancelled('Stopped before local preparation started.')
            process = start_process(command, cwd=str(directory), env=offline_environment(),
                                    stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
            while process.poll() is None:
                if cancel.is_set():
                    raise Cancelled('Local preparation stopped; completed artifacts are retained.')
                if time.monotonic() >= deadline:
                    raise ValueError('Local preparation exceeded its time limit. See the retained log.')
                if log.tell() > 4 * 1024 * 1024:
                    raise ValueError('Local preparation exceeded its log limit. See the retained log.')
                time.sleep(.05)
            if cancel.is_set():
                raise Cancelled('Local preparation stopped.')
        if process.returncode:
            with Path(logfile).open('rb') as stream:
                stream.seek(max(0, Path(logfile).stat().st_size - 6000))
                detail = stream.read().decode('utf-8', errors='replace')
            raise ValueError(detail.strip() or f'Local process exited with code {process.returncode}.')
    finally:
        if process is not None:
            stop_process(process)


def discover_configuration(store, engine_config):
    """Reuse explicit settings and known local pairing receipts; never scan chats."""
    saved = store.setting('training_config', {})
    values = dict(saved) if isinstance(saved, dict) else {}
    if not values.get('base_gguf') and engine_config.model_path:
        values['base_gguf'] = engine_config.model_path
    if values.get('base_gguf'):
        sidecar = gemma_manifest_path(values['base_gguf'])
        try:
            if sidecar.stat().st_size <= 4 * 1024 * 1024:
                manifest = json.loads(sidecar.read_text(encoding='utf-8'))
                source = manifest.get('source', {}).get('base_model')
                converter = manifest.get('conversion', {}).get('converter', {}).get('path')
                if not values.get('base_model') and source and Path(source).is_dir():
                    values['base_model'] = source
                if not values.get('llama_cpp_dir') and converter and Path(converter).is_file():
                    values['llama_cpp_dir'] = str(Path(converter).parent)
        except (OSError, ValueError, TypeError, AttributeError):
            pass
    if not values.get('python_executable'):
        for name in ('letracode-training-gemma4', 'letracode-training-qlora', 'letracode-training'):
            for relative in ('bin/python', 'Scripts/python.exe'):
                candidate = Path.home() / '.local/share' / name / relative
                if candidate.is_file():
                    values['python_executable'] = str(candidate)
                    break
            if values.get('python_executable'):
                break
    return values


def approved_examples(repo):
    rows = repo.examples()
    selected = {split: [r for r in rows if r['approved'] and r['split'] == split] for split in ('train', 'eval')}
    if not selected['train']:
        raise ValueError('Approve at least one example to teach Strand.')
    if not selected['eval']:
        raise ValueError('Keep at least one different approved example for comparison; it will not be trained on.')
    seen = set()
    for row in selected['train'] + selected['eval']:
        prompt = TrainingRepository._normalized_prompt(row['prompt'])
        if prompt in seen:
            raise ValueError('Repeated prompts must be resolved before training. Test questions must differ from teaching questions.')
        seen.add(prompt)
    return selected


def examples_fingerprint(repo):
    return hashlib.sha256(json.dumps(approved_examples(repo), sort_keys=True).encode()).hexdigest()


def check_readiness(repo, config, cancel, on_status=lambda _: None):
    checks = []
    def check(name, action):
        try:
            result = action()
            checks.append({'name': name, 'ready': True, 'detail': 'Ready'})
            return result
        except Cancelled:
            raise
        except Exception as error:
            checks.append({'name': name, 'ready': False, 'detail': str(error)})
            return None
    on_status('Checking preparation without unloading Strand…')
    values = check('Local training files', config.to_dict)
    examples = check('Reviewed examples', lambda: approved_examples(repo))
    if values is not None:
        def pair():
            if not values.get('base_gguf'):
                raise ValueError('Choose a Chat GGUF or prepare a matching model below.')
            with Path(values['base_gguf']).open('rb') as stream:
                if stream.read(4) != b'GGUF':
                    raise ValueError('The Chat model does not have a GGUF header.')
            manifest = verify_gemma_pair(values['base_model'], values['base_gguf'], cancel)
            if not manifest:
                return 'Llama file selected. Exact source-weight derivation is not independently verified; check publisher provenance.'
            return 'Matching model conversion verified.'
        note = check('Chat model compatibility', pair)
        if note:
            checks[-1]['detail'] = note
        if examples is not None:
            def runtime():
                with tempfile.TemporaryDirectory(prefix='.training-check-', dir=repo.store.directory) as scratch:
                    directory = Path(scratch)
                    for name, data in repo._snapshot_files(values, examples).items():
                        (directory / name).write_bytes(data)
                    run_local([values['python_executable'], str(BACKEND_SCRIPT), '--check-only', '--run-dir', str(directory)],
                              directory, directory / 'readiness.log', cancel, timeout=180)
                    result = json.loads((directory / 'readiness.json').read_text(encoding='utf-8'))
                    if result.get('ready') is not True:
                        raise ValueError('Training runtime did not confirm readiness.')
                    return result
            result = check('Runtime, tokenizer and conversion tools', runtime)
            if result:
                checks[-1]['detail'] = result.get('summary', 'Ready')
    return {'ready': bool(checks) and all(row['ready'] for row in checks), 'checks': checks,
            'examples_fingerprint': hashlib.sha256(json.dumps(examples, sort_keys=True).encode()).hexdigest(),
            'checked': now(), 'configuration': dataclasses.asdict(config)}


def prepare_matching_model(repo, config, cancel, on_status=lambda _: None):
    values = dataclasses.replace(config, base_gguf='').to_dict()
    if training_model_family(values) != 'gemma4':
        raise ValueError('Automatic matching-model preparation currently supports original Gemma 4 E2B/E4B weights. For Llama, choose the matching Chat GGUF in preparation details.')
    directory = repo.store.directory / 'training' / 'models'
    directory.mkdir(parents=True, exist_ok=True)
    attempt = directory / uuid.uuid4().hex
    attempt.mkdir(mode=0o700)
    output = attempt / 'strand-base-Q4_K_M.gguf'
    on_status('Preparing a matching local Chat model. Existing models stay in place; conversion can take a while…')
    script = Path(__file__).with_name('training_models.py')
    run_local([values['python_executable'], str(script), '--training-python', values['python_executable'],
               '--base-model', values['base_model'], '--llama-cpp', values['llama_cpp_dir'], '--output', str(output)],
              attempt, attempt / 'preparation.log', cancel, timeout=3600)
    verify_gemma_pair(values['base_model'], output, cancel)
    return {'base_gguf': str(output), 'log': str(attempt / 'preparation.log')}


def candidate_configuration(repo, ident, original, cancel):
    run = repo.verify_run_snapshot(ident)
    if run['status'] != 'succeeded':
        raise ValueError('This version has no completed optimization and held-out evaluation.')
    report = effective_report(repo, ident)
    base = run['config'].get('base_gguf', '')
    adapter = repo.run_directory(ident) / 'adapter.gguf'
    if not base or not report.get('base_gguf_sha256') or not report.get('adapter_gguf_sha256'):
        raise ValueError('Prepare a matching base model and convert this adapter first.')
    if training_model_family(run['config']) == 'gemma4' or 'gemma_pair' in report:
        if not report.get('gemma_pair') or verify_gemma_pair(run['config']['base_model'], base, cancel) != report['gemma_pair']:
            raise ValueError('The Gemma model pair changed since training.')
    if file_hash(base, cancel) != report['base_gguf_sha256'] or file_hash(adapter, cancel) != report['adapter_gguf_sha256']:
        raise ValueError('The base model or adapter changed since its verified run.')
    return dataclasses.replace(original, model_path=base, lora_path=str(adapter), secondary_model_path='')


def model_fingerprints(config, cancel):
    result = {}
    for key in ('executable', 'model_path', 'lora_path'):
        value = getattr(config, key)
        if value:
            if key == 'executable':
                value = Path(shutil.which(value) or value).expanduser().resolve()
            result[key] = file_hash(value, cancel)
    return result


def comparison_is_current(repo, ident, original, thinking=False):
    comparison = version_review(repo, ident).get('comparison') or {}
    current = dataclasses.replace(original, secondary_model_path='')
    if (comparison.get('status') != 'complete' or comparison.get('current_config') != dataclasses.asdict(current)
            or comparison.get('thinking') != thinking
            or comparison.get('system_sha256') != hashlib.sha256(SYSTEM.encode()).hexdigest()):
        return False
    try:
        candidate = candidate_configuration(repo, ident, current, threading.Event())
        return (comparison.get('candidate_config') == dataclasses.asdict(candidate)
                and comparison.get('current_fingerprints') == model_fingerprints(current, None)
                and comparison.get('candidate_fingerprints') == model_fingerprints(candidate, None))
    except (OSError, ValueError, Cancelled):
        return False


def compare_version(repo, ident, original, cancel, on_status=lambda _: None, thinking=False):
    current = dataclasses.replace(original, secondary_model_path='')
    candidate = candidate_configuration(repo, ident, current, cancel)
    rows = repo.run(ident)['examples']['eval']
    report = {'status': 'running', 'created': now(), 'thinking': thinking,
              'system_sha256': hashlib.sha256(SYSTEM.encode()).hexdigest(),
              'runtime': 'Local llama.cpp Chat engine; independent requests without action tools or project files',
              'current_config': dataclasses.asdict(current), 'candidate_config': dataclasses.asdict(candidate),
              'current_fingerprints': model_fingerprints(current, cancel),
              'candidate_fingerprints': model_fingerprints(candidate, cancel),
              'base_changed': current.model_path != candidate.model_path,
              'examples': [dict(prompt=row['prompt'], response=row['response']) for row in rows]}
    save_version_review(repo, ident, comparison=report, judgment='unreviewed')
    try:
        for label, config in (('current', current), ('candidate', candidate)):
            engine = LocalEngine(config, repo.store.directory / 'training' / 'comparisons' / ident / label)
            try:
                engine.start(cancel, on_status)
                for index, row in enumerate(report['examples']):
                    if cancel.is_set():
                        raise Cancelled('Comparison stopped. Saved answers remain available.')
                    on_status(f'Comparing {label} Strand · answer {index + 1} of {len(rows)}')
                    parts = []
                    try:
                        reply = engine.complete([{'role': 'system', 'content': SYSTEM},
                                                 {'role': 'user', 'content': row['prompt']}],
                                                None, cancel, parts.append, thinking=thinking)
                        row[label + '_output'] = reply.get('content') or ''.join(parts)
                        if not row[label + '_output'].strip():
                            raise ValueError('The model returned no visible answer.')
                        if reply.get('tool_calls'):
                            raise ValueError('The model requested actions during a tools-free comparison.')
                    except Cancelled:
                        row[label + '_output'] = ''.join(parts)
                        raise
                    except Exception as error:
                        row[label + '_output'] = ''.join(parts)
                        row[label + '_error'] = str(error)
                    save_version_review(repo, ident, comparison=report)
            finally:
                engine.stop()
        if (report['current_fingerprints'] != model_fingerprints(current, cancel)
                or report['candidate_fingerprints'] != model_fingerprints(candidate, cancel)):
            raise ValueError('A model file changed during comparison. Run it again with unchanged files.')
        report['status'] = 'incomplete' if any(key.endswith('_error') for row in report['examples'] for key in row) else 'complete'
    except Cancelled as error:
        report.update(status='cancelled', error=str(error))
    except Exception as error:
        report.update(status='incomplete', error=str(error))
    save_version_review(repo, ident, comparison=report)
    return report


def verify_conversion_source(run, directory, cancel):
    """A retry must consume the same source and trained adapter as optimization."""
    provenance = run['report'].get('provenance') or {}
    base = Path(run['config']['base_model'])
    weights = provenance.get('model_weight_manifest')
    adapter_files = provenance.get('adapter_manifest')
    tokenizers = provenance.get('tokenizer_manifest')
    if (not provenance.get('model_config_sha256') or not isinstance(weights, dict) or not weights
            or not isinstance(adapter_files, dict) or not {'adapter_config.json', 'adapter_model.safetensors'} <= set(adapter_files)
            or not isinstance(tokenizers, dict)):
        raise ValueError('This historical version lacks the source and adapter configuration provenance needed for a safe conversion retry. Its trained files are preserved.')
    if file_hash(base / 'config.json', cancel) != provenance['model_config_sha256']:
        raise ValueError('The original model config changed since training.')
    if set(weights) != {path.name for path in base.glob('*.safetensors')}:
        raise ValueError('The original model weight files changed since training.')
    current_tokenizers = {p.name for p in base.iterdir() if p.is_file() and (
        p.name.startswith('tokenizer') or p.name in ('special_tokens_map.json', 'added_tokens.json', 'chat_template.jinja'))}
    if current_tokenizers != set(tokenizers):
        raise ValueError('The original tokenizer files changed since training.')
    for root, records in ((base, {name: record.get('sha256') for name, record in weights.items()}),
                          (base, tokenizers), (directory / 'adapter', adapter_files)):
        for name, expected in records.items():
            if Path(name).name != name or name in ('.', '..') or not expected:
                raise ValueError('Invalid file provenance in the preserved training report.')
            if file_hash(root / name, cancel) != expected:
                raise ValueError(f'Training source or adapter file changed: {name}.')


def retry_conversion(repo, ident, checkout, python, cancel, on_status=lambda _: None):
    run = repo.verify_run_snapshot(ident)
    if run['status'] != 'succeeded':
        raise ValueError('Only a trained version can retry conversion.')
    directory = repo.run_directory(ident)
    if repo.store.setting('training_active_version') == ident:
        raise ValueError('This version is in use by Strand. Restore another version before preparing replacement artifacts.')
    destination = directory / 'adapter.gguf'
    if effective_report(repo, ident).get('adapter_gguf_sha256') and destination.exists():
        raise ValueError('This version already has a verified converted adapter. Existing artifacts are preserved.')
    original_output = file_hash(destination, cancel) if destination.exists() else None
    verify_conversion_source(run, directory, cancel)
    adapter = directory / 'adapter' / 'adapter_model.safetensors'
    if file_hash(adapter, cancel) != run['report'].get('adapter_sha256'):
        raise ValueError('The preserved trained adapter changed; conversion was stopped.')
    converter = Path(checkout).expanduser() / 'convert_lora_to_gguf.py'
    if not converter.is_file() or not Path(python).expanduser().is_file():
        raise ValueError('Choose the training Python and a llama.cpp folder containing convert_lora_to_gguf.py in preparation details.')
    if training_model_family(run['config']) == 'gemma4':
        if verify_gemma_pair(run['config']['base_model'], run['config']['base_gguf'], cancel) != run['report'].get('gemma_pair'):
            raise ValueError('The Gemma source model changed since training.')
    scratch = Path(tempfile.mkdtemp(prefix='conversion-', dir=directory))
    output = scratch / 'adapter.gguf'
    on_status('Converting the saved adapter; optimization will not repeat…')
    try:
        run_local([str(Path(python).expanduser().absolute()), str(converter.resolve()), '--base', run['config']['base_model'],
                   '--outfile', str(output), str(directory / 'adapter')], scratch, scratch / 'conversion.log', cancel)
        with output.open('rb') as stream:
            if stream.read(4) != b'GGUF' or output.stat().st_size <= 4:
                raise ValueError('Conversion did not produce a complete GGUF adapter.')
        verify_conversion_source(run, directory, cancel)
        if cancel.is_set():
            raise Cancelled('Conversion stopped before publication.')
        if destination.exists():
            # A failed converter may leave a partial file. Retain it in this attempt.
            if original_output is None or file_hash(destination, cancel) != original_output:
                raise ValueError('The adapter output changed during conversion; publication stopped.')
            destination.rename(scratch / 'previous-incomplete.gguf')
        os.link(output, destination)
        result = {'status': 'complete', 'created': now(), 'adapter_gguf': str(destination),
                  'adapter_gguf_sha256': file_hash(destination, cancel), 'log': str(scratch / 'conversion.log')}
        save_version_review(repo, ident, conversion=result, comparison={}, judgment='unreviewed')
        return result
    except Exception as error:
        save_version_review(repo, ident, conversion={'status': 'failed', 'error': str(error), 'log': str(scratch / 'conversion.log')})
        raise


class ExperienceWorker(QThread):
    status = Signal(str)
    ready = Signal(object)
    failed = Signal(str)

    def __init__(self, operation, parent=None):
        super().__init__(parent)
        self.operation = operation
        self.cancelled = threading.Event()

    def cancel(self):
        self.cancelled.set()

    def run(self):
        try:
            result = self.operation(self.cancelled, self.status.emit)
            if not self.cancelled.is_set():
                self.ready.emit(result)
        except Exception as error:
            self.failed.emit(str(error))
