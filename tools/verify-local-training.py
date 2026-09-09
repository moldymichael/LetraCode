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
import os
from pathlib import Path
import subprocess
import sys
import threading


def create_fixture(root):
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
    model = LlamaForCausalLM(LlamaConfig(vocab_size=len(tokenizer), hidden_size=32,
        intermediate_size=64, num_hidden_layers=1, num_attention_heads=4,
        num_key_value_heads=4, max_position_embeddings=256, bos_token_id=1, eos_token_id=2))
    model.save_pretrained(model_dir, safe_serialization=True)


def check_adapter(path):
    import torch
    from safetensors.torch import load_file
    weights = load_file(str(path))
    updated = sum(torch.count_nonzero(value).item() for name, value in weights.items() if 'lora_B' in name)
    if not updated:
        raise RuntimeError('LoRA output contains no updated B weights')
    print(json.dumps({'nonzero_lora_b_values': updated}))


def main():
    if len(sys.argv) == 3 and sys.argv[1] == '--create-fixture':
        create_fixture(Path(sys.argv[2]))
        return
    if len(sys.argv) == 3 and sys.argv[1] == '--check-adapter':
        check_adapter(Path(sys.argv[2]))
        return
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--training-python', required=True, type=Path)
    parser.add_argument('--llama-cpp', required=True, type=Path)
    parser.add_argument('--llama-server', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path, help='new scratch directory')
    args = parser.parse_args()
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

    child([script, '--create-fixture', root], 'fixture.log')
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
    for prompt in ('answer with yes', 'hello world'):
        repository.save_example(prompt, 'yes', approved=True)
    repository.save_example('test local model', 'yes', split='eval', approved=True)
    repository.save_example('question response', 'yes yes', split='eval', approved=True)
    original_hash = file_hash(root / 'model' / 'model.safetensors')
    run = repository.create_run(TrainingConfig(python_executable=training_python,
        base_model=root / 'model', base_gguf=root / 'base.gguf', llama_cpp_dir=llama_cpp,
        epochs=8, learning_rate=.01, rank=4, max_length=128, batch_size=2, seed=42, device='cpu'))
    worker = TrainingWorker(repository, run['id'])
    worker.status.connect(lambda message: print(message, flush=True))
    worker.run()
    saved = repository.run(run['id'])
    if saved['status'] != 'succeeded':
        raise RuntimeError(saved['error'])
    report = saved['report']
    if report['conversion_error'] or not report['adapter_gguf_sha256']:
        raise RuntimeError('Training succeeded but adapter conversion failed: ' + report['conversion_error'])
    if file_hash(root / 'model' / 'model.safetensors') != original_hash:
        raise RuntimeError('Training changed the original model weights')
    child([script, '--check-adapter', repository.run_directory(run['id']) / 'adapter' /
           'adapter_model.safetensors'], 'adapter-check.json')

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
             'rollback_loaded_without_adapter': True, 'package_versions': report['package_versions']}
    (root / 'proof.json').write_text(json.dumps(proof, indent=2, allow_nan=False), encoding='utf-8')
    print(json.dumps(proof, indent=2))


if __name__ == '__main__':
    main()
