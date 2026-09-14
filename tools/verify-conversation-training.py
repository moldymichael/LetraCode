#!/usr/bin/env python3
"""Prove structured training with random tiny Llama weights and a real local tokenizer.

Run with desktop Python and --training-python for the existing optional training
runtime. Uses a new output directory, no downloads and no recorded tool execution.
This proves plumbing and token supervision, not useful model quality.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys


def example(question):
    return {'schema_version': 2, 'tools': [{'type': 'function', 'function': {
        'name': 'recorded_lookup', 'description': 'Look up a value in an example table.',
        'parameters': {'type': 'object', 'properties': {'key': {'type': 'string'}}, 'required': ['key']}}}],
        'messages': [
            {'role': 'user', 'content': question + ' USER_CONTEXT_ONLY'},
            {'role': 'assistant', 'content': 'ASSISTANT_CONTEXT_ONLY', 'train': False},
            {'role': 'user', 'content': 'Use the recorded lookup.'},
            {'role': 'assistant', 'content': '', 'tool_calls': [{'id': 'lookup_1', 'type': 'function',
                'function': {'name': 'recorded_lookup', 'arguments': {'key': 'TEACH_ARGUMENT'}}}]},
            {'role': 'tool', 'tool_call_id': 'lookup_1', 'content': 'TOOL_RESULT_CONTEXT_ONLY'},
            {'role': 'assistant', 'content': 'TRAINED_ASSISTANT found the value.'},
            {'role': 'user', 'content': 'Explain the answer briefly.'},
            {'role': 'assistant', 'content': 'TRAINED_FOLLOWUP uses the recorded result.'},
        ]}


def create_fixture(output, source_tokenizer):
    import torch
    from transformers import AutoTokenizer, LlamaConfig, LlamaForCausalLM
    tokenizer = AutoTokenizer.from_pretrained(str(source_tokenizer), local_files_only=True, trust_remote_code=False)
    tokenizer.save_pretrained(output / 'model')
    torch.set_num_threads(1)
    torch.manual_seed(42)
    model = LlamaForCausalLM(LlamaConfig(vocab_size=len(tokenizer), hidden_size=32,
        intermediate_size=64, num_hidden_layers=1, num_attention_heads=4,
        num_key_value_heads=4, max_position_embeddings=4096,
        bos_token_id=tokenizer.bos_token_id, eos_token_id=tokenizer.eos_token_id))
    model.save_pretrained(output / 'model', safe_serialization=True)


def verify_supervision(output, run_dir):
    from transformers import AutoTokenizer
    from safetensors.torch import load_file
    from letracode.training_backend import encode_example, load_datasets
    tokenizer = AutoTokenizer.from_pretrained(str(output / 'model'), local_files_only=True, trust_remote_code=False)
    train, evaluation = load_datasets(run_dir)
    checks = []
    for row in train + evaluation:
        encoded = encode_example(tokenizer, row, 4096)
        learned = tokenizer.decode([label for label in encoded['labels'] if label != -100], skip_special_tokens=False)
        for excluded in ('USER_CONTEXT_ONLY', 'ASSISTANT_CONTEXT_ONLY', 'TOOL_RESULT_CONTEXT_ONLY'):
            if excluded in learned:
                raise RuntimeError('Context leaked into training targets: ' + excluded)
        for included in ('TRAINED_ASSISTANT', 'TRAINED_FOLLOWUP', 'TEACH_ARGUMENT', 'recorded_lookup'):
            if included not in learned:
                raise RuntimeError('Missing assistant/tool-call target: ' + included)
        try:
            encode_example(tokenizer, row, len(encoded['input_ids']) - 1)
        except ValueError as error:
            if 'truncat' not in str(error).lower():
                raise
        else:
            raise RuntimeError('Overlong conversation was silently accepted')
        checks.append({'tokens': len(encoded['input_ids']), 'supervised_tokens': sum(x != -100 for x in encoded['labels'])})
    weights = load_file(str(run_dir / 'adapter' / 'adapter_model.safetensors'))
    updates = sum(int(value.count_nonzero()) for name, value in weights.items() if '.lora_B.' in name)
    if not updates:
        raise RuntimeError('Optimization did not change any LoRA B weights')
    (output / 'supervision.json').write_text(json.dumps({'examples': checks, 'nonzero_lora_b_values': updates}, indent=2), encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--training-python', type=Path)
    parser.add_argument('--tokenizer', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--llama-cpp', type=Path)
    parser.add_argument('--fixture', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--verify-run', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    root = args.output.expanduser().absolute()
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    if args.fixture:
        create_fixture(root, args.tokenizer)
        return
    if args.verify_run:
        verify_supervision(root, args.verify_run)
        return
    if args.training_python is None:
        parser.error('--training-python is required')
    root.mkdir(parents=True, exist_ok=False)
    script = Path(__file__).resolve()
    environment = dict(os.environ, HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
                       HF_HUB_DISABLE_TELEMETRY='1', OMP_NUM_THREADS='1', TOKENIZERS_PARALLELISM='false')
    # The worker inherits this same bounded CPU setting.
    os.environ['OMP_NUM_THREADS'] = '1'
    def child(mode, log_name):
        command = [str(args.training_python), str(script), '--output', str(root),
                   '--tokenizer', str(args.tokenizer), *mode]
        with (root / log_name).open('x', encoding='utf-8') as stream:
            subprocess.run(command, env=environment, stdout=stream, stderr=subprocess.STDOUT, check=True)
    child(['--fixture'], 'fixture.log')
    from PySide6.QtCore import QCoreApplication
    from letracode.store import Store
    from letracode.training import TrainingConfig, TrainingRepository
    from letracode.training_examples import normalize_example
    from letracode.training_worker import TrainingWorker, file_hash
    from letracode.training_experience import check_readiness
    import threading
    app = QCoreApplication.instance() or QCoreApplication([])
    repo = TrainingRepository(Store(root / 'data'))
    rows = [dict(example('Look up first'), split='train'), dict(example('Look up second'), split='train'),
            dict(example('Look up held out'), split='eval')]
    source = root / 'source.jsonl'
    source.write_text(''.join(json.dumps(row) + '\n' for row in rows), encoding='utf-8')
    imported = repo.import_jsonl(source)
    assert all(not row['approved'] for row in imported)
    for row in imported:
        repo.save_example(messages=row['messages'], tools=row['tools'], split=row['split'],
                          source=row['source'], example_id=row['id'], approved=True)
    repo.export_jsonl(root / 'roundtrip.jsonl')
    exported = [json.loads(line) for line in (root / 'roundtrip.jsonl').read_text().splitlines()]
    assert [normalize_example(row) for row in rows] == [normalize_example(row) for row in exported]
    model = root / 'model'
    before = file_hash(model / 'model.safetensors')
    base_gguf = ''
    if args.llama_cpp:
        base_gguf = root / 'base.gguf'
        with (root / 'base-conversion.log').open('x', encoding='utf-8') as stream:
            subprocess.run([str(args.training_python), str(args.llama_cpp / 'convert_hf_to_gguf.py'),
                str(model), '--outfile', str(base_gguf), '--outtype', 'f32'], env=environment,
                stdout=stream, stderr=subprocess.STDOUT, check=True)
    config = TrainingConfig(args.training_python, model, base_gguf=base_gguf, llama_cpp_dir=args.llama_cpp or '',
        epochs=2, rank=4, max_length=4096, batch_size=1, gradient_accumulation_steps=2,
        learning_rate=.005, device='cpu', training_method='lora')
    readiness = None
    if args.llama_cpp:
        readiness = check_readiness(repo, config, threading.Event(), lambda text: print(text, flush=True))
        (root / 'readiness.json').write_text(json.dumps(readiness, indent=2, default=str), encoding='utf-8')
        if not readiness['ready']:
            raise RuntimeError('Preparation failed: ' + json.dumps(readiness['checks']))
    run = repo.create_run(config)
    worker = TrainingWorker(repo, run['id'])
    worker.status.connect(lambda text: print(text, flush=True))
    worker.run()
    saved = repo.run(run['id'])
    if saved['status'] != 'succeeded':
        raise RuntimeError(saved['error'])
    directory = repo.run_directory(run['id'])
    child(['--verify-run', str(directory)], 'supervision.log')
    if before != file_hash(model / 'model.safetensors'):
        raise RuntimeError('Training altered base weights')
    report = saved['report']
    proof = {'run_id': run['id'], 'run_directory': str(directory), 'tokenizer_source': str(args.tokenizer),
        'fixture': 'Random tiny Llama; actual local native tokenizer; CPU LoRA through TrainingWorker',
        'base_weights_unchanged': True, 'tools_executed': False, 'import_export_roundtrip': True,
        'optimization_steps': report['optimization_steps'], 'base_loss': report['base_loss'],
        'candidate_loss': report['candidate_loss'], 'eval_response_tokens': report['eval_response_tokens'],
        'conversion_error': report['conversion_error'], 'package_versions': report['package_versions'],
        'preparation_ready': readiness['ready'] if readiness else None,
        'supervision': json.loads((root / 'supervision.json').read_text()), 'provenance': report['provenance']}
    (root / 'proof.json').write_text(json.dumps(proof, indent=2), encoding='utf-8')
    print(json.dumps(proof, indent=2))


if __name__ == '__main__':
    main()
