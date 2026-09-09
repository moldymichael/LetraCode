#!/usr/bin/env python3
"""Exercise real local training, conversion and activation using random tiny weights.

Run with the desktop Python and separately select an installed training Python.
This creates a new scratch directory and makes no downloads or user-data changes.
The generated model is a plumbing fixture, not a useful assistant.
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


def create_fixture(root, fixture_size='tiny'):
    import sentencepiece as spm
    from sentencepiece import sentencepiece_model_pb2
    import torch
    from tokenizers import SentencePieceUnigramTokenizer
    from transformers import LlamaConfig, LlamaForCausalLM, PreTrainedTokenizerFast

    model_dir = root / 'model'
    model_dir.mkdir()
    corpus = root / 'corpus.txt'
    corpus.write_text(('hello world\nanswer with yes\nquestion response assistant user\n'
                       'test local tiny model green blue red\nthe sky is blue and grass is green\n'
                       '[INST] [/INST]\n') * 20, encoding='utf-8')
    spm.SentencePieceTrainer.train(input=str(corpus), model_prefix=str(model_dir / 'tokenizer'),
                                  vocab_size=64, hard_vocab_limit=False, bos_id=1, eos_id=2,
                                  unk_id=0, pad_id=-1, model_type='unigram')
    sys.modules['sentencepiece_model_pb2'] = sentencepiece_model_pb2
    raw = SentencePieceUnigramTokenizer.from_spm(str(model_dir / 'tokenizer.model'))
    tokenizer = PreTrainedTokenizerFast(tokenizer_object=raw, bos_token='<s>',
                                       eos_token='</s>', unk_token='<unk>')
    tokenizer.chat_template = "{{ bos_token }}{% for message in messages %}{% if message['role'] == 'user' %}{{ '[INST] ' + message['content'] + ' [/INST]' }}{% else %}{{ ' ' + message['content'] + eos_token }}{% endif %}{% endfor %}"
    tokenizer.save_pretrained(model_dir)
    torch.set_num_threads(1)
    torch.manual_seed(1234)
    dimensions = {'tiny': (32, 64, 1), 'memory': (256, 768, 4)}
    hidden_size, intermediate_size, layers = dimensions[fixture_size]
    model = LlamaForCausalLM(LlamaConfig(vocab_size=len(tokenizer), hidden_size=hidden_size,
        intermediate_size=intermediate_size, num_hidden_layers=layers, num_attention_heads=4,
        num_key_value_heads=4, max_position_embeddings=256, bos_token_id=1, eos_token_id=2))
    model.save_pretrained(model_dir, safe_serialization=True)


def check_adapter(path, require_all_linear=False):
    import torch
    from safetensors.torch import load_file
    weights = load_file(str(path))
    if any(not torch.isfinite(value).all().item() for value in weights.values()):
        raise RuntimeError('LoRA output contains nonfinite adapter weights')
    per_module = {name: torch.count_nonzero(value).item()
                  for name, value in weights.items() if '.lora_B.' in name}
    updated = sum(per_module.values())
    if not updated:
        raise RuntimeError('LoRA output contains no updated B weights')
    if require_all_linear:
        expected = {'q_proj', 'k_proj', 'v_proj', 'o_proj', 'gate_proj', 'up_proj', 'down_proj'}
        observed = {name.split('.lora_B.')[0].rsplit('.', 1)[-1] for name in per_module}
        if not expected.issubset(observed) or any(count == 0 for count in per_module.values()):
            raise RuntimeError('QLoRA output must have updated B weights in every all-linear adapter module')
    result = {'nonzero_lora_b_values': updated, 'nonzero_lora_b_by_module': per_module,
              'all_linear_updates_checked': require_all_linear}
    print(json.dumps(result))
    return result


def validate_training_details(report, args):
    details = report.get('training_details', {})
    memory = report.get('memory', {})
    expected = {'training_method': args.training_method, 'device': args.device,
                'gradient_accumulation_steps': args.gradient_accumulation_steps,
                'gradient_checkpointing': args.gradient_checkpointing}
    if any(details.get(key) != value for key, value in expected.items()):
        raise RuntimeError('Training report does not match the requested training configuration')
    if args.training_method == 'qlora':
        if (details.get('device') != 'cuda' or details.get('quantization') != 'nf4-double'
                or details.get('target_modules') != 'all-linear'
                or details.get('compute_dtype') not in ('float16', 'bfloat16')
                or not isinstance(details.get('quantized_layer_count'), int)
                or details['quantized_layer_count'] <= 0):
            raise RuntimeError('QLoRA proof requires actual CUDA NF4 layers and all-linear training')
        if any(not isinstance(memory.get(key), int) or memory[key] <= 0
               for key in ('base_model_bytes', 'peak_allocated_bytes', 'peak_reserved_bytes')):
            raise RuntimeError('QLoRA proof requires measured CUDA memory usage')


def main():
    if len(sys.argv) > 1 and sys.argv[1] == '--create-fixture':
        parser = argparse.ArgumentParser(description='Create a local random Llama fixture')
        parser.add_argument('output', type=Path)
        parser.add_argument('--fixture-size', choices=('tiny', 'memory'), default='tiny')
        args = parser.parse_args(sys.argv[2:])
        create_fixture(args.output, args.fixture_size)
        return
    if len(sys.argv) > 1 and sys.argv[1] == '--check-adapter':
        parser = argparse.ArgumentParser(description='Check that local adapter weights changed')
        parser.add_argument('adapter', type=Path)
        parser.add_argument('--require-all-linear', action='store_true')
        args = parser.parse_args(sys.argv[2:])
        check_adapter(args.adapter, args.require_all_linear)
        return
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--training-python', required=True, type=Path)
    parser.add_argument('--llama-cpp', required=True, type=Path)
    parser.add_argument('--llama-server', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path, help='new scratch directory')
    parser.add_argument('--training-method', choices=('lora', 'qlora'), default='lora')
    parser.add_argument('--device', choices=('cpu', 'cuda'), default='cpu')
    parser.add_argument('--gradient-accumulation-steps', type=int, default=1)
    parser.add_argument('--gradient-checkpointing', action='store_true')
    parser.add_argument('--fixture-size', choices=('tiny', 'memory'), default='tiny',
                        help='memory creates a larger random fixture for comparing weight memory')
    args = parser.parse_args()
    if args.training_method == 'qlora' and args.device != 'cuda':
        parser.error('QLoRA proof requires --device cuda')
    if not 1 <= args.gradient_accumulation_steps <= 128:
        parser.error('--gradient-accumulation-steps must be from 1 to 128')
    root = args.output.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=False)
    training_python = args.training_python.expanduser().absolute()
    llama_cpp = args.llama_cpp.expanduser().resolve()
    script = Path(__file__).resolve()
    environment = dict(os.environ, HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
                       HF_HUB_DISABLE_TELEMETRY='1', OMP_NUM_THREADS='1')

    def child(arguments, log_name):
        with (root / log_name).open('xb') as log:
            subprocess.run([str(training_python), *map(str, arguments)],
                           env=environment, stdout=log, stderr=subprocess.STDOUT, check=True)

    child([script, '--create-fixture', root, '--fixture-size', args.fixture_size], 'fixture.log')
    child([llama_cpp / 'convert_hf_to_gguf.py', root / 'model', '--outfile',
           root / 'base.gguf', '--outtype', 'f32'], 'base-conversion.log')
    sys.path.insert(0, str(script.parents[1]))
    from PySide6.QtCore import QCoreApplication
    from letracode.engine import EngineConfig, EngineError
    from letracode.store import Store
    from letracode.training import TrainingConfig, TrainingRepository
    from letracode.training_worker import ActivationWorker, TrainingWorker, file_hash

    app = QCoreApplication.instance() or QCoreApplication([])
    repository = TrainingRepository(Store(root / 'data'))
    train_prompts = ['answer with yes', 'hello world']
    if args.training_method == 'qlora' or args.gradient_accumulation_steps > 1:
        train_prompts.append('the sky is blue')
    for prompt in train_prompts:
        repository.save_example(prompt, 'yes yes' if prompt == 'the sky is blue' else 'yes', approved=True)
    repository.save_example('test local model', 'yes', split='eval', approved=True)
    repository.save_example('question response', 'yes yes', split='eval', approved=True)
    original_hash = file_hash(root / 'model' / 'model.safetensors')
    batch_size = 1 if args.training_method == 'qlora' or args.gradient_accumulation_steps > 1 else 2
    epochs = 8
    run = repository.create_run(TrainingConfig(python_executable=training_python,
        base_model=root / 'model', base_gguf=root / 'base.gguf', llama_cpp_dir=llama_cpp,
        epochs=epochs, learning_rate=.002 if args.training_method == 'qlora' else .01,
        rank=4, max_length=128, batch_size=batch_size, seed=42, device=args.device,
        training_method=args.training_method,
        gradient_accumulation_steps=args.gradient_accumulation_steps,
        gradient_checkpointing=args.gradient_checkpointing))
    worker = TrainingWorker(repository, run['id'])
    worker.status.connect(lambda message: print(message, flush=True))
    worker.run()
    saved = repository.run(run['id'])
    if saved['status'] != 'succeeded':
        raise RuntimeError(saved['error'])
    report = saved['report']
    validate_training_details(report, args)
    microbatches_per_epoch = math.ceil(len(train_prompts) / batch_size)
    expected_steps = epochs * math.ceil(microbatches_per_epoch / args.gradient_accumulation_steps)
    if report['optimization_steps'] != expected_steps:
        raise RuntimeError('Optimizer steps do not include every accumulation window, including the final partial window')
    if report['conversion_error'] or not report['adapter_gguf_sha256']:
        raise RuntimeError('Training succeeded but adapter conversion failed: ' + report['conversion_error'])
    if file_hash(root / 'model' / 'model.safetensors') != original_hash:
        raise RuntimeError('Training changed the original model weights')
    adapter_arguments = [script, '--check-adapter', repository.run_directory(run['id']) / 'adapter' /
                         'adapter_model.safetensors']
    if args.training_method == 'qlora':
        adapter_arguments.append('--require-all-linear')
    child(adapter_arguments, 'adapter-check.json')
    adapter_check = json.loads((root / 'adapter-check.json').read_text(encoding='utf-8'))
    if args.training_method == 'qlora' and (len(adapter_check['nonzero_lora_b_by_module'])
                                          != report['training_details']['quantized_layer_count']):
        raise RuntimeError('QLoRA adapter updates do not cover every quantized linear layer')

    original = EngineConfig(executable=str(args.llama_server.expanduser().resolve()),
        model_path=str(root / 'base.gguf'), context_size=256, threads=1, max_tokens=16)

    def activate(**options):
        activation = ActivationWorker(repository, original, **options)
        ready, errors = [], []
        activation.ready.connect(ready.append)
        activation.failed.connect(errors.append)
        activation.status.connect(lambda message: print(message, flush=True))
        activation.run()
        if errors or len(ready) != 1:
            raise RuntimeError('Activation failed: ' + '; '.join(errors))
        return ready[0]

    engine = activate(run_id=run['id'])
    try:
        deltas = []
        try:
            response = engine.complete([{'role': 'user', 'content': 'answer with yes'}],
                                       None, threading.Event(), deltas.append)
        except EngineError as error:
            # Random weights need not emit EOS. A bounded streamed response is
            # enough to establish that llama.cpp actually applied the adapter.
            if not deltas or 'truncated at the output token limit' not in str(error):
                raise
            response = {'status': 'output_token_limit', 'content': ''.join(deltas)}
        adopted_config = asdict(engine.config)
    finally:
        engine.stop()
    rollback = activate(rollback=asdict(original))
    try:
        if rollback.config.lora_path:
            raise RuntimeError('Rollback retained the candidate adapter')
    finally:
        rollback.stop()
    proof = {'run_id': run['id'], 'run_directory': str(repository.run_directory(run['id'])),
             'optimization_steps': report['optimization_steps'], 'base_loss': report['base_loss'],
             'candidate_loss': report['candidate_loss'], 'eval_response_tokens': report['eval_response_tokens'],
             'adapter_gguf_sha256': report['adapter_gguf_sha256'], 'base_weights_unchanged': True,
             'activated_config': adopted_config, 'inference_response': response,
             'rollback_loaded_without_adapter': True, 'package_versions': report['package_versions'],
             'training_details': report['training_details'], 'memory': report.get('memory', {}),
             'fixture_size': args.fixture_size, 'train_examples': len(train_prompts),
             'microbatches_per_epoch': microbatches_per_epoch,
             'final_accumulation_window_microbatches': (microbatches_per_epoch - 1) % args.gradient_accumulation_steps + 1,
             'adapter_check': adapter_check}
    (root / 'proof.json').write_text(json.dumps(proof, indent=2, allow_nan=False), encoding='utf-8')
    print(json.dumps(proof, indent=2))


if __name__ == '__main__':
    main()
