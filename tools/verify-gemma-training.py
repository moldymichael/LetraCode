#!/usr/bin/env python3
"""Verify real local Gemma training, Chat adoption, restart and rollback.

Run with the desktop Python (PySide6), passing a separate training Python.
Requires original local E2B/E4B instruct weights and their prepared Chat GGUF.
All examples, settings, conversations and evidence stay in a NEW scratch folder.
This small synthetic run verifies operation, not general model improvement.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import traceback


TRAIN = [
    ('Give one brief factual answer. What color is a clear daytime sky?',
     'Fact: A clear daytime sky usually appears blue.'),
    ('Give one brief factual answer. How many sides does a triangle have?',
     'Fact: A triangle has three sides.'),
    ('Give one brief factual answer. What is 2 plus 2?',
     'Fact: 2 plus 2 equals 4.'),
]
HELD_OUT = [
    ('Give one brief factual answer. How many days are in a week?',
     'Fact: A week has seven days.'),
    ('Give one brief factual answer. What is the capital of France?',
     'Fact: The capital of France is Paris.'),
]


def adapter_check(path):
    import torch
    from safetensors.torch import load_file

    tensors = load_file(str(path), device='cpu')
    if not tensors or any(not torch.isfinite(value).all().item() for value in tensors.values()):
        raise RuntimeError('Adapter is empty or contains nonfinite weights')
    counts = {name: torch.count_nonzero(value).item()
              for name, value in tensors.items() if '.lora_B.' in name}
    expected = {'q_proj', 'k_proj', 'v_proj', 'o_proj', 'gate_proj', 'up_proj', 'down_proj'}
    observed = {name.split('.lora_B.')[0].rsplit('.', 1)[-1] for name in counts}
    if observed != expected or not counts or any(count <= 0 for count in counts.values()):
        raise RuntimeError('Every expected decoder LoRA B matrix must have a nonzero update')
    result = {'all_weights_finite': True, 'nonzero_lora_b_values': sum(counts.values()),
              'nonzero_lora_b_by_module': counts, 'target_modules': sorted(observed)}
    print(json.dumps(result, allow_nan=False))


def main():
    if len(sys.argv) > 1 and sys.argv[1] == '--check-adapter':
        adapter_check(Path(sys.argv[2]))
        return
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('base-model', 'base-gguf', 'training-python', 'llama-cpp', 'llama-server', 'output'):
        parser.add_argument('--' + name, required=True, type=Path)
    parser.add_argument('--max-length', type=int, default=256)
    parser.add_argument('--epochs', type=int, default=1)
    parser.add_argument('--rank', type=int, default=4)
    parser.add_argument('--gradient-accumulation-steps', type=int, default=2)
    parser.add_argument('--resume-run', help='reuse a succeeded run in this existing scratch output; skip training')
    args = parser.parse_args()
    base = args.base_model.expanduser().resolve()
    root = args.output.expanduser().resolve()
    if root == base or base in root.parents:
        parser.error('--output must be outside the original model directory')
    if args.resume_run:
        if not (root / 'proof.json').is_file():
            parser.error('--resume-run requires this verifier\'s existing output directory')
        previous_proof = (root / 'proof.json').read_text(encoding='utf-8')
        if json.loads(previous_proof).get('run_id') != args.resume_run:
            parser.error('--resume-run does not match the run saved in this output directory')
        (root / f'proof-before-resume-{time.time_ns()}.json').write_text(previous_proof, encoding='utf-8')
    else:
        root.mkdir(parents=True, exist_ok=False)
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from PySide6.QtWidgets import QApplication
    from letracode.engine import EngineConfig
    from letracode.store import Store
    from letracode.training import TrainingConfig, TrainingRepository
    from letracode.training_models import verify_gemma_pair
    from letracode.training_worker import TrainingWorker
    from letracode.ui import MainWindow

    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    proof = {'status': 'running', 'scope': 'real supplied Gemma weights; synthetic operational verification',
             'output': str(root), 'started_unix': time.time(), 'phases': {}, 'transcripts': {},
             'quality_claim': 'No general quality improvement is claimed from this small synthetic dataset.'}
    stop_monitor = threading.Event()
    samples = []
    phase = ['preflight']
    window = None
    training = None

    def save():
        (root / 'proof.json').write_text(json.dumps(proof, indent=2, allow_nan=False) + '\n', encoding='utf-8')

    def status(message):
        print(message, flush=True)
        with (root / 'progress.log').open('a', encoding='utf-8') as stream:
            stream.write(f'{time.time():.3f} {phase[0]} {message}\n')

    def monitor():
        with (root / 'gpu-memory.jsonl').open('a' if args.resume_run else 'x', encoding='utf-8') as stream:
            while not stop_monitor.is_set():
                try:
                    result = subprocess.run(
                        ['nvidia-smi', '--query-gpu=index,memory.used,memory.total', '--format=csv,noheader,nounits'],
                        capture_output=True, text=True, timeout=5, check=True)
                    for line in result.stdout.splitlines():
                        index, used, total = [int(value.strip()) for value in line.split(',')]
                        row = {'unix': time.time(), 'phase': phase[0], 'gpu_index': index,
                               'used_mib': used, 'total_mib': total}
                        samples.append(row)
                        stream.write(json.dumps(row) + '\n')
                    stream.flush()
                except (OSError, ValueError, subprocess.SubprocessError) as error:
                    stream.write(json.dumps({'error': str(error)}) + '\n')
                    stream.flush()
                    return
                stop_monitor.wait(2)

    monitor_thread = threading.Thread(target=monitor, daemon=True, name='gemma-proof-memory')
    monitor_thread.start()

    def wait_until(predicate, timeout, label):
        deadline = time.monotonic() + timeout
        while not predicate():
            app.processEvents()
            if time.monotonic() > deadline:
                raise TimeoutError(f'{label} exceeded {timeout} seconds')
            time.sleep(.05)
        app.processEvents()

    def open_window():
        current = MainWindow(Store(root / 'data'))
        current.mode.setCurrentText('Instant')
        current.conversation_mode.setCurrentIndex(0)
        for widget in (current.computer, current.internet, current.actions):
            widget.setChecked(False)
        return current

    def capture_process(current, label):
        process = current.engine._process
        if process is None or process.poll() is not None:
            raise RuntimeError(f'{label}: llama-server is not running')
        argv = list(process.args) if isinstance(process.args, (list, tuple)) else [process.args]
        adapter = current.engine.config.lora_path
        if adapter and ('--lora' not in argv or argv[argv.index('--lora') + 1] != adapter):
            raise RuntimeError(f'{label}: actual llama-server argv does not contain the selected adapter')
        if not adapter and '--lora' in argv:
            raise RuntimeError(f'{label}: original model unexpectedly started with an adapter')
        proof['phases'].setdefault(label, {}).update(
            engine_config=asdict(current.engine.config), server_pid=process.pid,
            adapter_argument_verified=True,
            # The server's random authentication token is intentionally omitted.
            selected_adapter=adapter)

    def chat(current, label, prompts, expected_version=None):
        phase[0] = label
        transcripts = []
        proof['transcripts'][label] = transcripts
        for number, prompt in enumerate(prompts, 1):
            chat_id = current.store.create_chat(f'Synthetic Gemma verification: {label} {number}')
            current.store.add_message(chat_id, 'user', prompt)
            current.select_chat(chat_id)
            current.start_worker()
            if current.worker is None:
                raise RuntimeError(f'{label}: Chat did not start ConversationWorker')
            current.worker.status.connect(status)
            wait_until(lambda: current.worker is None, 600, f'{label} Chat')
            rows = [dict(row) for row in current.store.messages(chat_id)]
            assistants = [row for row in rows if row['role'] == 'assistant']
            if not assistants or not any(row['content'].strip() for row in assistants):
                transcripts.append({'chat_id': chat_id, 'prompt': prompt, 'rows': rows})
                save()
                raise RuntimeError(f'{label}: Chat saved no generated response')
            payload = json.loads(assistants[-1]['payload'])
            recorded = payload.get('run_configuration', {})
            if recorded.get('training_version') != expected_version:
                raise RuntimeError(f'{label}: saved Chat training_version does not match the loaded model')
            if (recorded.get('mode') != 'Instant' or recorded.get('thinking') is not False
                    or recorded.get('use_tools') is not False):
                raise RuntimeError(f'{label}: Chat did not use Instant mode with tools disabled')
            if expected_version and recorded.get('adapter_name') != 'adapter.gguf':
                raise RuntimeError(f'{label}: Chat did not record its adapter')
            limit = any('truncated at the output token limit' in row['content'] for row in rows)
            complete = all(row['status'] == 'complete' for row in assistants)
            result = {'chat_id': chat_id, 'prompt': prompt, 'rows': rows,
                      'run_configuration': recorded,
                      'response_status': 'output_token_limit' if limit else 'complete' if complete else 'incomplete'}
            transcripts.append(result)
            save()
            if not complete and not limit:
                raise RuntimeError(f'{label}: Chat was incomplete for a reason other than its output limit')
            capture_process(current, label)
        save()

    def activation(current, label, *, run_id=None, rollback=None):
        phase[0] = label
        current.training_panel.start_activation(run_id=run_id, rollback=rollback)
        job = current.training_panel.job
        if job is None:
            raise RuntimeError(f'{label}: actual Fine-Tuning panel refused activation')
        job.status.connect(status)
        failures = []
        job.failed.connect(failures.append)
        wait_until(lambda: current.training_panel.job is None, 600, label)
        if failures:
            raise RuntimeError(f'{label}: ' + '; '.join(failures))
        if not current.engine.running:
            raise RuntimeError(f'{label}: activation did not transfer a running engine to Chat')
        capture_process(current, label)

    try:
        save()
        pair = verify_gemma_pair(base, args.base_gguf)
        if not pair:
            raise RuntimeError('This verifier requires an actual Gemma 4 checkpoint and matching manifest')
        proof['original_manifest'] = pair
        store = Store(root / 'data')
        repository = TrainingRepository(store)
        source = 'synthetic:verify-gemma-training; public factual examples; approved for this isolated verification'
        if not args.resume_run:
            for split, examples in [('train', TRAIN), ('eval', HELD_OUT)]:
                for prompt, response in examples:
                    repository.save_example(prompt, response, split=split, approved=True, source=source)
        config = TrainingConfig(
            python_executable=args.training_python.expanduser().absolute(), base_model=base,
            base_gguf=args.base_gguf.expanduser().resolve(), llama_cpp_dir=args.llama_cpp.expanduser().resolve(),
            epochs=args.epochs, learning_rate=.0002, rank=args.rank, max_length=args.max_length,
            batch_size=1, seed=42, device='cuda', training_method='qlora',
            gradient_accumulation_steps=args.gradient_accumulation_steps, gradient_checkpointing=True)
        if args.resume_run:
            run = repository.run(args.resume_run)
            if not run or run['status'] != 'succeeded' or run['config'] != config.to_dict():
                raise RuntimeError('Resume requires a succeeded run with the same requested training configuration')
        else:
            run = repository.create_run(config)
        proof.update(run_id=run['id'], run_directory=str(repository.run_directory(run['id'])),
                     requested_training_config=config.to_dict(), train_examples=len(TRAIN), eval_examples=len(HELD_OUT))
        save()
        phase[0] = 'training'
        started = time.monotonic()
        if not args.resume_run:
            training = TrainingWorker(repository, run['id'])
            training.status.connect(status)
            training.start()
            wait_until(lambda: not training.isRunning(), 14400, 'training')
        saved = repository.run(run['id'])
        proof['saved_training_run'] = saved
        proof['phases']['training'] = {'seconds': time.monotonic() - started, 'status': saved['status'],
                                       'reused_succeeded_run': bool(args.resume_run)}
        save()
        if saved['status'] != 'succeeded':
            raise RuntimeError(saved['error'])
        report = saved['report']
        expected_steps = args.epochs * math.ceil(len(TRAIN) / args.gradient_accumulation_steps)
        if report['optimization_steps'] != expected_steps or report['conversion_error'] or not report['adapter_gguf_sha256']:
            raise RuntimeError('Training optimizer-step count or adapter conversion failed verification')
        adapter = repository.run_directory(run['id']) / 'adapter' / 'adapter_model.safetensors'
        child_environment = {key: value for key, value in os.environ.items()
                             if key not in ('HF_TOKEN', 'HUGGING_FACE_HUB_TOKEN', 'PYTHONPATH', 'PYTHONHOME')}
        child_environment.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1')
        checked = subprocess.run([str(args.training_python.expanduser().absolute()), str(Path(__file__).resolve()),
                                  '--check-adapter', str(adapter)], env=child_environment,
                                 capture_output=True, text=True, check=False)
        (root / 'adapter-check.log').write_text(checked.stdout + checked.stderr, encoding='utf-8')
        if checked.returncode:
            raise RuntimeError('Adapter tensor verification failed; see adapter-check.log')
        proof['adapter_check'] = json.loads(checked.stdout)
        original = EngineConfig(executable=str(args.llama_server.expanduser().resolve()),
            model_path=str(args.base_gguf.expanduser().resolve()), gpu_layers=999,
            context_size=2048, max_tokens=128, threads=4, temperature=0.0)
        store.set_setting('engine', asdict(original))
        store.set_setting('training_active_version', None)
        store.set_setting('training_previous_engine', None)
        store.set_setting('training_previous_version', None)
        store.set_setting('mode', 'Instant')
        for name in ('computer', 'internet', 'actions'):
            store.set_setting(name, False)
        window = open_window()
        prompts = [prompt for prompt, _ in HELD_OUT]
        chat(window, 'original_chat', prompts)
        activation(window, 'candidate_activation', run_id=run['id'])
        if window.store.setting('training_active_version') != run['id']:
            raise RuntimeError('Fine-Tuning panel did not persist adoption')
        chat(window, 'candidate_chat', prompts, run['id'])
        candidate_config = asdict(window.engine_config)
        window.close()
        app.processEvents()
        window = open_window()
        if (asdict(window.engine_config) != candidate_config
                or window.store.setting('training_active_version') != run['id']):
            raise RuntimeError('Adopted model selection did not survive reopening the application')
        chat(window, 'candidate_after_restart', [prompts[0]], run['id'])
        proof['candidate_restart_persisted'] = True
        previous = window.store.setting('training_previous_engine')
        if previous != asdict(original):
            raise RuntimeError('Adoption did not retain the complete original engine configuration')
        activation(window, 'rollback_activation', rollback=previous)
        if window.engine_config.lora_path or window.store.setting('training_active_version') is not None:
            raise RuntimeError('Rollback retained an adapter or the candidate training version')
        chat(window, 'rollback_chat', [prompts[0]])
        window.close()
        app.processEvents()
        window = open_window()
        if asdict(window.engine_config) != asdict(original) or window.store.setting('training_active_version') is not None:
            raise RuntimeError('Original model selection did not survive reopening after rollback')
        proof['rollback_restart_persisted'] = True
        if verify_gemma_pair(base, args.base_gguf) != pair:
            raise RuntimeError('Original model files or GGUF changed during verification')
        proof['original_weights_and_gguf_unchanged'] = True
        proof['status'] = 'succeeded'
    except BaseException as error:
        proof.update(status='failed', error=str(error), traceback=traceback.format_exc())
        raise
    finally:
        if training is not None and training.isRunning():
            training.cancel()
            training.wait(10000)
        if window is not None:
            if window.worker is not None:
                window.worker.request_stop()
                window.worker.wait(10000)
                app.processEvents()
            if window.training_panel.job is not None:
                window.training_panel.stop()
                window.training_panel.job.wait(10000)
                app.processEvents()
            window.engine.stop()
            if window.worker is None and window.training_panel.job is None:
                window.close()
        stop_monitor.set()
        monitor_thread.join(7)
        proof['gpu_monitor'] = {'scope': 'whole-device memory including unrelated processes',
            'sample_count': len(samples), 'peak_used_mib': max((row['used_mib'] for row in samples), default=None),
            'peak_by_phase_mib': {name: max(row['used_mib'] for row in samples if row['phase'] == name)
                                  for name in sorted({row['phase'] for row in samples})}}
        proof['finished_unix'] = time.time()
        save()
        print(json.dumps({'status': proof['status'], 'proof': str(root / 'proof.json'),
                          'run_id': proof.get('run_id'), 'error': proof.get('error')}, indent=2), flush=True)


if __name__ == '__main__':
    main()
