"""Offline LoRA and 4-bit QLoRA trainer for a separate Python environment.

The local tokenizer chat template formats user/assistant examples. Exact generation
prefix matching ensures response-only loss; no truncation or fallback formatting.
"""
from __future__ import annotations

import argparse
from contextlib import nullcontext
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


def cuda_compute_dtype(torch):
    # BF16 emulation is reported as supported on some Turing installations.
    native_bf16 = torch.cuda.get_device_capability(0)[0] >= 8 and torch.cuda.is_bf16_supported()
    return torch.bfloat16 if native_bf16 else torch.float16


def verify_adapter_parameters(model):
    trainable = [name for name, parameter in model.named_parameters() if parameter.requires_grad]
    if not trainable or any('.lora_A.' not in name and '.lora_B.' not in name for name in trainable):
        raise RuntimeError('Training must update only LoRA adapters; unexpectedly trainable base weights were detected.')
    trainable_count, total_count = model.get_nb_trainable_parameters()
    if not 0 < trainable_count < total_count:
        raise RuntimeError('Training did not preserve a frozen base and trainable adapters.')
    return int(trainable_count), int(total_count)


def train_epoch(torch, model, rows, batch, optimizer, scaler, autocast,
                batch_size, accumulation, on_step=None):
    """Normalize each optimizer update by its actual assistant-token count."""
    steps = 0
    group_size = batch_size * accumulation
    for group_start in range(0, len(rows), group_size):
        group = rows[group_start:group_start + group_size]
        token_count = sum(sum(label != -100 for label in row['labels'][1:]) for row in group)
        if not token_count:
            raise ValueError('Training group has no assistant response tokens.')
        for attempt in range(13):
            optimizer.zero_grad(set_to_none=True)
            response_loss = 0.0
            for start in range(0, len(group), batch_size):
                microbatch = group[start:start + batch_size]
                tokens = sum(sum(label != -100 for label in row['labels'][1:]) for row in microbatch)
                with autocast():
                    loss = model(**batch(microbatch)).loss
                    if not torch.isfinite(loss):
                        raise RuntimeError('Nonfinite training loss; reduce learning rate or review examples.')
                    weighted_loss = loss * (tokens / token_count)
                scaler.scale(weighted_loss).backward()
                response_loss += float(loss.detach().item()) * tokens / token_count
            scaler.unscale_(optimizer)
            parameters = [p for p in model.parameters() if p.requires_grad and p.grad is not None]
            if not parameters:
                raise RuntimeError('Training produced no adapter gradients.')
            finite = bool(torch.stack([torch.isfinite(p.grad).all() for p in parameters]).all().item())
            if not finite:
                if not scaler.is_enabled() or attempt == 12:
                    raise RuntimeError('Nonfinite adapter gradients persisted after loss-scale adjustment; reduce the learning rate or use a smaller training sequence.')
                # unscale_ recorded these nonfinite gradients, so step skips the
                # update. Recompute the same group at a lower scale: no examples
                # or optimizer updates are silently lost to normal AMP overflow.
                previous_scale = scaler.get_scale()
                scaler.step(optimizer)
                scaler.update()
                if not scaler.get_scale() < previous_scale:
                    raise RuntimeError('The training loss scaler could not recover nonfinite gradients.')
                progress('Adjusting FP16 loss scale and retrying the same training examples')
                continue
            try:
                torch.nn.utils.clip_grad_norm_(parameters, 1.0, error_if_nonfinite=True)
            except RuntimeError as exc:
                raise RuntimeError('Nonfinite adapter gradient norm; reduce the learning rate or use a smaller training sequence.') from exc
            scaler.step(optimizer)
            scaler.update()
            break
        steps += 1
        if on_step is not None:
            on_step(steps, response_loss)
    return steps


def run_training(run_dir):
    run_dir = Path(run_dir).expanduser().resolve()
    config = json.loads((run_dir / 'config.json').read_text(encoding='utf-8'))
    base = Path(config['base_model']).expanduser().resolve()
    model_config = validate_model_directory(base)
    train_rows, eval_rows = load_datasets(run_dir)
    for key, low, high in [('epochs', 1, 100), ('rank', 1, 256), ('max_length', 8, 8192), ('batch_size', 1, 64), ('seed', 0, 2**31 - 1), ('gradient_accumulation_steps', 1, 128)]:
        value = config.get(key, {'epochs': 1, 'rank': 8, 'max_length': 512, 'batch_size': 1, 'seed': 42, 'gradient_accumulation_steps': 1}[key])
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise ValueError(f'{key} must be an integer from {low} to {high}.')
        config[key] = value
    lr = config.get('learning_rate', 0.0002)
    if isinstance(lr, bool) or not isinstance(lr, (int, float)) or not math.isfinite(lr) or not 0 < lr <= .1:
        raise ValueError('learning_rate must be finite, greater than 0 and at most 0.1.')
    device = config.get('device', 'cpu')
    if device not in ('cpu', 'cuda'):
        raise ValueError('device must be cpu or cuda.')
    method = config.get('training_method', 'lora')
    if method not in ('lora', 'qlora'):
        raise ValueError('training_method must be lora or qlora.')
    checkpointing = config.get('gradient_checkpointing', False)
    if type(checkpointing) is not bool:
        raise ValueError('gradient_checkpointing must be true or false.')
    if method == 'qlora' and device != 'cuda':
        raise ValueError('4-bit QLoRA requires an NVIDIA CUDA GPU; select CUDA or choose regular LoRA for CPU training.')
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
        raise ValueError('CUDA is unavailable in the selected training Python environment. Install a CUDA-enabled PyTorch build and a compatible NVIDIA driver, or choose regular LoRA on CPU.')
    compute_dtype = cuda_compute_dtype(torch) if method == 'qlora' else torch.float32
    quantization_config = None
    if method == 'qlora':
        if torch.cuda.get_device_capability(0)[0] < 6:
            raise ValueError('4-bit QLoRA requires an NVIDIA Pascal or newer GPU (compute capability 6.0+).')
        try:
            import bitsandbytes as bnb
            import accelerate  # noqa: F401 - verify the selected training environment
            from transformers import BitsAndBytesConfig
            from peft import prepare_model_for_kbit_training
            quantization_config = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type='nf4',
                                                     bnb_4bit_use_double_quant=True,
                                                     bnb_4bit_compute_dtype=compute_dtype)
        except (ImportError, OSError, RuntimeError) as exc:
            raise RuntimeError('4-bit QLoRA needs working bitsandbytes and accelerate CUDA packages. Install packaging/training-requirements.txt into the selected training Python environment and check its CUDA compatibility.') from exc
    seed = config.get('seed', 42)
    random.seed(seed)
    torch.manual_seed(seed)
    if device == 'cuda':
        torch.cuda.manual_seed_all(seed)
        torch.cuda.reset_peak_memory_stats(0)
    progress('Hashing local model weights and approved dataset snapshots')
    provenance = {'config_sha256': sha256(run_dir / 'config.json'), 'train_sha256': sha256(run_dir / 'train.jsonl'), 'eval_sha256': sha256(run_dir / 'eval.jsonl'), 'base_model': str(base), 'model_config_sha256': sha256(base / 'config.json'), 'model_weight_manifest': {p.name: {'sha256': sha256(p), 'bytes': p.stat().st_size} for p in sorted(base.glob('*.safetensors'))}, 'tokenizer_manifest': {p.name: sha256(p) for p in sorted(base.iterdir()) if p.is_file() and (p.name.startswith('tokenizer') or p.name in ('special_tokens_map.json', 'added_tokens.json', 'chat_template.jinja'))}, 'tokenization': 'local tokenizer chat template; exact generation prefix masked; assistant completion and template terminators contribute loss'}
    progress('Loading local tokenizer and Llama safetensors weights')
    tokenizer = AutoTokenizer.from_pretrained(str(base), local_files_only=True, trust_remote_code=False)
    limit = min(config['max_length'], model_config.get('max_position_embeddings', config['max_length']))
    train = [encode_example(tokenizer, row, limit) for row in train_rows]
    evaluation = [encode_example(tokenizer, row, limit) for row in eval_rows]
    load_options = dict(local_files_only=True, trust_remote_code=False, use_safetensors=True,
                        torch_dtype=compute_dtype)
    quantized_layers = 0
    if method == 'qlora':
        progress(f'Loading 4-bit NF4 weights with double quantization and {str(compute_dtype).split(".")[-1]} computation')
        model = LlamaForCausalLM.from_pretrained(str(base), **load_options,
                                               quantization_config=quantization_config, device_map={'': 0})
        for module in model.modules():
            if isinstance(module, bnb.nn.Linear4bit):
                state = getattr(module.weight, 'quant_state', None)
                if (module.weight.device.type != 'cuda' or state is None
                        or state.quant_type != 'nf4' or not state.nested):
                    raise RuntimeError('The training runtime did not load CUDA 4-bit NF4 weights with double quantization; QLoRA was stopped.')
                quantized_layers += 1
        if not quantized_layers:
            raise RuntimeError('No 4-bit layers were loaded; QLoRA was stopped instead of falling back to full weights.')
        model = prepare_model_for_kbit_training(model, use_gradient_checkpointing=checkpointing,
                                                gradient_checkpointing_kwargs={'use_reentrant': False})
    else:
        model = LlamaForCausalLM.from_pretrained(str(base), **load_options).to(device)
        if checkpointing:
            model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={'use_reentrant': False})
    model.config.use_cache = False
    base_model_bytes = int(model.get_memory_footprint())
    autocast = (lambda: torch.autocast(device_type='cuda', dtype=compute_dtype)) if method == 'qlora' else nullcontext
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
        with torch.no_grad(), autocast():
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
        with torch.no_grad(), autocast():
            for row in evaluation[:3]:
                ids = torch.tensor([row['prompt_ids']], dtype=torch.long, device=device)
                output = current.generate(input_ids=ids, attention_mask=torch.ones_like(ids), do_sample=False, max_new_tokens=min(64, limit - ids.shape[1]), pad_token_id=pad, eos_token_id=tokenizer.eos_token_id)
                result.append(tokenizer.decode(output[0, ids.shape[1]:], skip_special_tokens=True))
        return result

    progress('Evaluating unchanged base on held-out responses')
    base_loss, eval_tokens = evaluate(model)
    base_examples = generate(model)
    target_modules = 'all-linear' if method == 'qlora' else ['q_proj', 'v_proj']
    model = get_peft_model(model, LoraConfig(task_type=TaskType.CAUSAL_LM, r=config['rank'], lora_alpha=2 * config['rank'], lora_dropout=0.0, target_modules=target_modules, bias='none'))
    trainable_count, total_count = verify_adapter_parameters(model)
    optimizer = torch.optim.AdamW((p for p in model.parameters() if p.requires_grad), lr=lr)
    scaler = torch.amp.GradScaler('cuda', enabled=method == 'qlora' and compute_dtype == torch.float16,
                                 init_scale=1024.0)
    steps = 0
    for epoch in range(config['epochs']):
        model.train()
        order = list(train)
        random.shuffle(order)
        def on_step(epoch_step, loss):
            progress(f'Epoch {epoch + 1}/{config["epochs"]}, step {steps + epoch_step}, response loss {loss:.6f}')
        steps += train_epoch(torch, model, order, batch, optimizer, scaler, autocast,
                             config['batch_size'], config['gradient_accumulation_steps'], on_step)
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
    packages = ('torch', 'transformers', 'peft', 'safetensors') + (('bitsandbytes', 'accelerate') if method == 'qlora' else ())
    details = {'training_method': method, 'quantization': 'nf4-double' if method == 'qlora' else 'none',
               'compute_dtype': str(compute_dtype).split('.')[-1], 'target_modules': target_modules,
               'gradient_checkpointing': bool(model.is_gradient_checkpointing),
               'gradient_accumulation_steps': config['gradient_accumulation_steps'],
               'effective_batch_size': config['batch_size'] * config['gradient_accumulation_steps'],
               'device': device, 'device_name': torch.cuda.get_device_name(0) if device == 'cuda' else 'CPU',
               'quantized_layer_count': quantized_layers, 'base_frozen': True,
               'trainable_parameter_count': trainable_count, 'total_parameter_count': total_count}
    memory = {'base_model_bytes': base_model_bytes,
              'peak_allocated_bytes': int(torch.cuda.max_memory_allocated(0)) if device == 'cuda' else 0,
              'peak_reserved_bytes': int(torch.cuda.max_memory_reserved(0)) if device == 'cuda' else 0}
    report = {'base_loss': base_loss, 'candidate_loss': candidate_loss, 'eval_response_tokens': eval_tokens, 'eval_examples': [dict(row, base_output=before, candidate_output=after) for row, before, after in zip(eval_rows[:3], base_examples, candidate_examples)], 'adapter_path': str(adapter), 'adapter_gguf': gguf, 'conversion_error': conversion_error, 'package_versions': {name: importlib.metadata.version(name) for name in packages}, 'provenance': provenance, 'optimization_steps': steps, 'training_details': details, 'memory': memory}
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
        message = str(exc)
        if 'out of memory' in message.lower():
            message += ' Reduce sequence length, batch size or adapter rank, close other GPU workloads, or select a smaller base model. Increase gradient accumulation to keep a larger effective batch without storing more examples at once.'
        progress(f'Training failed: {type(exc).__name__}: {message}')
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
