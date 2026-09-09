"""Offline LoRA trainer. Executable directly in a separate Python environment.

The local tokenizer chat template formats user/assistant examples. Exact generation
prefix matching ensures response-only loss; no truncation or fallback formatting.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import random
import subprocess
import sys

# Direct execution must not let sibling platform.py shadow Python's stdlib.
if __name__ == '__main__':
    script_directory = str(Path(__file__).resolve().parent)
    sys.path[:] = [entry for entry in sys.path if str(Path(entry).resolve()) != script_directory]


def progress(message):
    print(json.dumps({'message': message}), flush=True)


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def validate_model_directory(directory):
    directory = Path(directory).expanduser().resolve()
    if not directory.is_dir() or not (directory / 'config.json').is_file():
        raise ValueError('Select a local Hugging Face model directory with config.json; downloads are disabled.')
    if (directory / 'adapter_config.json').exists():
        raise ValueError('Select original base weights, not an existing adapter directory.')
    config = json.loads((directory / 'config.json').read_text(encoding='utf-8'))
    if config.get('model_type') != 'llama' or config.get('vision_config') or config.get('quantization_config'):
        raise ValueError('Only unquantized text-only Llama training weights are supported.')
    if not list(directory.glob('*.safetensors')):
        raise ValueError('Local safetensors model weights are required; GGUF and pickle weights cannot be trained.')
    return config


def load_datasets(run_dir):
    datasets = []
    seen = set()
    for split in ('train', 'eval'):
        rows = []
        for line in (Path(run_dir) / f'{split}.jsonl').read_text(encoding='utf-8').splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict) or any(not isinstance(row.get(key), str) or not row[key].strip() for key in ('prompt', 'response')):
                raise ValueError(f'{split}: every example requires nonempty prompt and response strings.')
            prompt = ' '.join(row['prompt'].split()).casefold()
            if prompt in seen:
                raise ValueError('Duplicate prompt across training/evaluation examples; review the split.')
            seen.add(prompt)
            rows.append({'prompt': row['prompt'], 'response': row['response']})
        if not rows:
            raise ValueError(f'{split} dataset must contain approved examples.')
        datasets.append(rows)
    return datasets


def encode_example(tokenizer, row, max_length):
    if not getattr(tokenizer, 'chat_template', None):
        raise ValueError('A local tokenizer chat template is required for assistant fine-tuning; select matching Llama instruct weights/tokenizer.')
    messages = [{'role': 'user', 'content': row['prompt']}]
    prompt = tokenizer.apply_chat_template(messages, tokenize=True, add_generation_prompt=True, return_dict=False)
    tokens = tokenizer.apply_chat_template(messages + [{'role': 'assistant', 'content': row['response']}], tokenize=True, add_generation_prompt=False, return_dict=False)
    if not prompt or tokens[:len(prompt)] != prompt:
        raise ValueError('Tokenizer chat template does not preserve the exact generation prefix; this template is unsupported for response-only training.')
    response = tokens[len(prompt):]
    if not response:
        raise ValueError('Response must produce tokens after the generation prefix.')
    if len(tokens) > max_length:
        raise ValueError(f'Example token length {len(tokens)} exceeds max_length {max_length}; shorten it or increase the limit. No tokens were truncated.')
    return {'input_ids': tokens, 'labels': [-100] * len(prompt) + response, 'prompt_ids': prompt}


def convert_adapter(run_dir, base_model, llama_cpp_dir):
    output = Path(run_dir) / 'adapter.gguf'
    converter = Path(llama_cpp_dir) / 'convert_lora_to_gguf.py'
    if not converter.is_file():
        return '', 'Select a local llama.cpp checkout containing convert_lora_to_gguf.py; PEFT adapter and evaluation are preserved.'
    env = dict(os.environ, HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1')
    try:
        result = subprocess.run([sys.executable, str(converter), '--base', str(base_model), '--outfile', str(output), str(Path(run_dir) / 'adapter')], env=env, capture_output=True, text=True, check=False)
        (Path(run_dir) / 'conversion.log').write_text(result.stdout + '\n' + result.stderr, encoding='utf-8')
        if result.returncode or not output.is_file() or output.stat().st_size == 0:
            return '', f'GGUF conversion failed ({result.returncode}). Install the selected llama.cpp conversion requirements into the training environment. See conversion.log. ' + (result.stderr or result.stdout)[-3000:]
        return str(output), ''
    except OSError as exc:
        return '', f'GGUF converter could not start: {exc}. PEFT adapter is preserved.'


def run_training(run_dir):
    run_dir = Path(run_dir).expanduser().resolve()
    config = json.loads((run_dir / 'config.json').read_text(encoding='utf-8'))
    base = Path(config['base_model']).expanduser().resolve()
    model_config = validate_model_directory(base)
    train_rows, eval_rows = load_datasets(run_dir)
    for key, low, high in [('epochs', 1, 100), ('rank', 1, 256), ('max_length', 8, 8192), ('batch_size', 1, 64), ('seed', 0, 2**31 - 1)]:
        value = config.get(key, {'epochs': 1, 'rank': 8, 'max_length': 512, 'batch_size': 1, 'seed': 42}[key])
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise ValueError(f'{key} must be an integer from {low} to {high}.')
        config[key] = value
    lr = config.get('learning_rate', 0.0002)
    if isinstance(lr, bool) or not isinstance(lr, (int, float)) or not math.isfinite(lr) or not 0 < lr <= .1:
        raise ValueError('learning_rate must be finite, greater than 0 and at most 0.1.')
    device = config.get('device', 'cpu')
    if device not in ('cpu', 'cuda'):
        raise ValueError('device must be cpu or cuda.')
    if any((run_dir / name).exists() for name in ('adapter', 'adapter.gguf', 'report.json')):
        raise ValueError('Run already contains output; create a fresh run to preserve earlier artifacts.')
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1')
    try:
        import torch
        from transformers import AutoTokenizer, LlamaForCausalLM
        from peft import LoraConfig, TaskType, get_peft_model
    except ImportError as exc:
        raise RuntimeError('Install packaging/training-requirements.txt into the selected separate training Python environment.') from exc
    if device == 'cuda' and not torch.cuda.is_available():
        raise ValueError('CUDA is unavailable in the selected training Python environment.')
    seed = config.get('seed', 42)
    random.seed(seed)
    torch.manual_seed(seed)
    if device == 'cuda':
        torch.cuda.manual_seed_all(seed)
    progress('Hashing local model weights and approved dataset snapshots')
    provenance = {'config_sha256': sha256(run_dir / 'config.json'), 'train_sha256': sha256(run_dir / 'train.jsonl'), 'eval_sha256': sha256(run_dir / 'eval.jsonl'), 'base_model': str(base), 'model_config_sha256': sha256(base / 'config.json'), 'model_weight_manifest': {p.name: {'sha256': sha256(p), 'bytes': p.stat().st_size} for p in sorted(base.glob('*.safetensors'))}, 'tokenizer_manifest': {p.name: sha256(p) for p in sorted(base.iterdir()) if p.is_file() and (p.name.startswith('tokenizer') or p.name in ('special_tokens_map.json', 'added_tokens.json', 'chat_template.jinja'))}, 'tokenization': 'local tokenizer chat template; exact generation prefix masked; assistant completion and template terminators contribute loss'}
    progress('Loading local tokenizer and Llama safetensors weights')
    tokenizer = AutoTokenizer.from_pretrained(str(base), local_files_only=True, trust_remote_code=False)
    limit = min(config['max_length'], model_config.get('max_position_embeddings', config['max_length']))
    train = [encode_example(tokenizer, row, limit) for row in train_rows]
    evaluation = [encode_example(tokenizer, row, limit) for row in eval_rows]
    model = LlamaForCausalLM.from_pretrained(str(base), local_files_only=True, trust_remote_code=False, use_safetensors=True, torch_dtype=torch.float32).to(device)
    model.config.use_cache = False
    pad = tokenizer.pad_token_id if tokenizer.pad_token_id is not None else tokenizer.eos_token_id
    if pad is None:
        pad = 0

    def batch(rows):
        width = max(len(row['input_ids']) for row in rows)
        return {key: torch.tensor(values, dtype=torch.long, device=device) for key, values in {
            'input_ids': [r['input_ids'] + [pad] * (width - len(r['input_ids'])) for r in rows],
            'labels': [r['labels'] + [-100] * (width - len(r['labels'])) for r in rows],
            'attention_mask': [[1] * len(r['input_ids']) + [0] * (width - len(r['input_ids'])) for r in rows],
        }.items()}

    def evaluate(current):
        current.eval()
        total, count = 0.0, 0
        with torch.no_grad():
            for start in range(0, len(evaluation), config['batch_size']):
                inputs = batch(evaluation[start:start + config['batch_size']])
                tokens = int((inputs['labels'][:, 1:] != -100).sum().item())
                loss = float(current(**inputs).loss.item())
                if not math.isfinite(loss):
                    raise RuntimeError('Nonfinite evaluation loss; no completed report produced.')
                total += loss * tokens
                count += tokens
        return total / count, count

    def generate(current):
        current.eval()
        result = []
        with torch.no_grad():
            for row in evaluation[:3]:
                ids = torch.tensor([row['prompt_ids']], dtype=torch.long, device=device)
                output = current.generate(input_ids=ids, attention_mask=torch.ones_like(ids), do_sample=False, max_new_tokens=min(64, limit - ids.shape[1]), pad_token_id=pad, eos_token_id=tokenizer.eos_token_id)
                result.append(tokenizer.decode(output[0, ids.shape[1]:], skip_special_tokens=True))
        return result

    progress('Evaluating unchanged base on held-out responses')
    base_loss, eval_tokens = evaluate(model)
    base_examples = generate(model)
    model = get_peft_model(model, LoraConfig(task_type=TaskType.CAUSAL_LM, r=config['rank'], lora_alpha=2 * config['rank'], lora_dropout=0.0, target_modules=['q_proj', 'v_proj'], bias='none'))
    optimizer = torch.optim.AdamW((p for p in model.parameters() if p.requires_grad), lr=lr)
    steps = 0
    for epoch in range(config['epochs']):
        model.train()
        order = list(train)
        random.shuffle(order)
        for start in range(0, len(order), config['batch_size']):
            optimizer.zero_grad(set_to_none=True)
            loss = model(**batch(order[start:start + config['batch_size']])).loss
            if not torch.isfinite(loss):
                raise RuntimeError('Nonfinite training loss; reduce learning rate or review examples.')
            loss.backward()
            torch.nn.utils.clip_grad_norm_((p for p in model.parameters() if p.requires_grad), 1.0, error_if_nonfinite=True)
            optimizer.step()
            steps += 1
            progress(f'Epoch {epoch + 1}/{config["epochs"]}, step {steps}, response loss {loss.item():.6f}')
    progress('Evaluating candidate on identical held-out responses')
    candidate_loss, _ = evaluate(model)
    candidate_examples = generate(model)
    adapter = run_dir / 'adapter'
    model.save_pretrained(str(adapter), safe_serialization=True)
    provenance['adapter_manifest'] = {p.name: sha256(p) for p in sorted(adapter.iterdir()) if p.is_file()}
    progress('Converting preserved PEFT adapter to GGUF')
    gguf, conversion_error = convert_adapter(run_dir, base, config.get('llama_cpp_dir', ''))
    if gguf:
        provenance['adapter_gguf_sha256'] = sha256(gguf)
    report = {'base_loss': base_loss, 'candidate_loss': candidate_loss, 'eval_response_tokens': eval_tokens, 'eval_examples': [dict(row, base_output=before, candidate_output=after) for row, before, after in zip(eval_rows[:3], base_examples, candidate_examples)], 'adapter_path': str(adapter), 'adapter_gguf': gguf, 'conversion_error': conversion_error, 'package_versions': {name: importlib.metadata.version(name) for name in ('torch', 'transformers', 'peft', 'safetensors')}, 'provenance': provenance, 'optimization_steps': steps}
    temporary = run_dir / 'report.json.tmp'
    temporary.write_text(json.dumps(report, indent=2, allow_nan=False), encoding='utf-8')
    temporary.replace(run_dir / 'report.json')
    progress('Training and held-out evaluation complete' + ('; GGUF conversion requires attention' if conversion_error else ''))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', required=True)
    args = parser.parse_args()
    try:
        run_training(args.run_dir)
    except Exception as exc:
        progress(f'Training failed: {type(exc).__name__}: {exc}')
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
