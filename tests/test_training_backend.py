"""Standalone backend contracts; no training dependencies needed for validation tests."""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

MODULE = Path(__file__).parents[1] / 'letracode' / 'training_backend.py'


def backend():
    assert MODULE.exists(), 'Standalone training backend is missing'
    spec = importlib.util.spec_from_file_location('standalone_training_backend', MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Tokenizer:
    bos_token_id = 1
    eos_token_id = 2
    def encode(self, text, add_special_tokens=False):
        return [ord(c) for c in text]
    chat_template = 'test template'
    def apply_chat_template(self, messages, tokenize=True, add_generation_prompt=False, return_dict=False):
        prefix = [1] + [ord(c) for c in messages[0]['content']] + [3]
        return prefix if add_generation_prompt else prefix + [ord(c) for c in messages[1]['content']] + [2]


def test_response_labels_exclude_prompt_but_include_eos():
    result = backend().encode_example(Tokenizer(), {'prompt': 'ab', 'response': 'cd'}, 8)
    assert result['input_ids'] == [1, 97, 98, 3, 99, 100, 2]
    assert result['labels'] == [-100, -100, -100, -100, 99, 100, 2]
    assert result['prompt_ids'] == [1, 97, 98, 3]


def test_overlength_answer_is_rejected_without_truncation():
    with pytest.raises(ValueError, match='length'):
        backend().encode_example(Tokenizer(), {'prompt': 'ab', 'response': 'cdef'}, 5)


def test_remote_model_identifier_is_rejected():
    with pytest.raises(ValueError, match='local'):
        backend().validate_model_directory('vendor/nonexistent-model')


def test_only_supported_safe_local_weights_are_accepted(tmp_path):
    (tmp_path / 'config.json').write_text(json.dumps({'model_type': 'qwen2'}))
    (tmp_path / 'model.safetensors').write_bytes(b'fixture')
    with pytest.raises(ValueError, match='Llama'):
        backend().validate_model_directory(tmp_path)
    (tmp_path / 'config.json').write_text(json.dumps({'model_type': 'llama'}))
    assert backend().validate_model_directory(tmp_path)['model_type'] == 'llama'
    (tmp_path / 'model.safetensors').unlink()
    (tmp_path / 'pytorch_model.bin').write_bytes(b'unsafe')
    with pytest.raises(ValueError, match='safetensors'):
        backend().validate_model_directory(tmp_path)


def test_dataset_leakage_and_empty_answers_are_rejected(tmp_path):
    train = tmp_path / 'train.jsonl'
    test = tmp_path / 'eval.jsonl'
    train.write_text('{"prompt":"same","response":"yes"}\n')
    test.write_text('{"prompt":"same","response":"other"}\n')
    with pytest.raises(ValueError, match='prompt'):
        backend().load_datasets(tmp_path)
    test.write_text('{"prompt":"new","response":""}\n')
    with pytest.raises(ValueError, match='response'):
        backend().load_datasets(tmp_path)


def test_conversion_failure_preserves_adapter_and_diagnostics(tmp_path):
    converter = tmp_path / 'converter'
    converter.mkdir()
    (converter / 'convert_lora_to_gguf.py').write_text('import sys\nprint("missing conversion dependency", file=sys.stderr)\nsys.exit(2)\n')
    adapter = tmp_path / 'adapter'
    adapter.mkdir()
    (adapter / 'adapter_model.safetensors').write_bytes(b'keep')
    gguf, error = backend().convert_adapter(tmp_path, tmp_path, converter)
    assert gguf == ''
    assert 'missing conversion dependency' in error
    assert (adapter / 'adapter_model.safetensors').read_bytes() == b'keep'


def test_tiny_local_training_updates_adapter_and_evaluates(tmp_path):
    torch = pytest.importorskip('torch')
    transformers = pytest.importorskip('transformers')
    pytest.importorskip('peft')
    from tokenizers import Tokenizer as FastTokenizer
    from tokenizers.models import WordLevel
    from tokenizers.pre_tokenizers import Whitespace
    from safetensors.torch import load_file
    torch.set_num_threads(1)
    model_dir = tmp_path / 'model'
    raw = FastTokenizer(WordLevel({'[UNK]': 0, '<s>': 1, '</s>': 2, 'hello': 3, 'world': 4, 'test': 5}, unk_token='[UNK]'))
    raw.pre_tokenizer = Whitespace()
    tokenizer = transformers.PreTrainedTokenizerFast(tokenizer_object=raw, bos_token='<s>', eos_token='</s>', unk_token='[UNK]')
    tokenizer.chat_template = "{{ bos_token }}{% for message in messages %}{{ message['content'] }} {{ eos_token if message['role'] == 'assistant' else '' }}{% endfor %}"
    tokenizer.save_pretrained(model_dir)
    model = transformers.LlamaForCausalLM(transformers.LlamaConfig(vocab_size=6, hidden_size=16, intermediate_size=32, num_hidden_layers=1, num_attention_heads=2, num_key_value_heads=2, max_position_embeddings=64, bos_token_id=1, eos_token_id=2))
    model.save_pretrained(model_dir, safe_serialization=True)
    original = (model_dir / 'model.safetensors').read_bytes()
    run = tmp_path / 'run'
    run.mkdir()
    (run / 'config.json').write_text(json.dumps({'base_model': str(model_dir), 'llama_cpp_dir': str(tmp_path / 'missing'), 'epochs': 3, 'learning_rate': 0.02, 'rank': 2, 'max_length': 32, 'batch_size': 1, 'seed': 42, 'device': 'cpu'}))
    (run / 'train.jsonl').write_text('{"prompt":"hello","response":"world"}\n')
    (run / 'eval.jsonl').write_text('{"prompt":"test","response":"world"}\n')
    completed = subprocess.run([sys.executable, str(MODULE), '--run-dir', str(run)], capture_output=True, text=True, env=dict(os.environ, OMP_NUM_THREADS='1'))
    assert completed.returncode == 0, completed.stdout + completed.stderr
    report = json.loads((run / 'report.json').read_text())
    assert report['optimization_steps'] == 3
    assert report['base_loss'] > 0 and report['candidate_loss'] > 0
    assert report['base_loss'] != report['candidate_loss']
    assert report['eval_response_tokens'] == 2
    template = model_dir / 'chat_template.jinja'
    assert report['provenance']['tokenizer_manifest'][template.name] == backend().sha256(template)
    assert len(report['eval_examples']) == 1
    assert report['conversion_error'] and not report['adapter_gguf']
    weights = load_file(str(run / 'adapter' / 'adapter_model.safetensors'))
    assert any(torch.count_nonzero(v).item() for k, v in weights.items() if 'lora_B' in k)
    assert (model_dir / 'model.safetensors').read_bytes() == original
    assert json.loads((run / 'report.json').read_text()) == report


def test_template_must_preserve_generation_prefix():
    class BadTemplate(Tokenizer):
        def apply_chat_template(self, messages, tokenize=True, add_generation_prompt=False, return_dict=False):
            return [1, 2] if add_generation_prompt else [1, 3, 4]
    with pytest.raises(ValueError, match='prefix'):
        backend().encode_example(BadTemplate(), {'prompt': 'a', 'response': 'b'}, 8)


def test_missing_chat_template_is_rejected():
    tokenizer = Tokenizer()
    tokenizer.chat_template = None
    with pytest.raises(ValueError, match='chat template'):
        backend().encode_example(tokenizer, {'prompt': 'a', 'response': 'b'}, 8)


def test_adapter_directory_cannot_be_used_as_original_base(tmp_path):
    (tmp_path / 'config.json').write_text('{"model_type":"llama"}')
    (tmp_path / 'model.safetensors').write_bytes(b'fixture')
    (tmp_path / 'adapter_config.json').write_text('{}')
    with pytest.raises(ValueError, match='original'):
        backend().validate_model_directory(tmp_path)


def test_existing_gguf_output_cannot_be_overwritten(tmp_path):
    model = tmp_path / 'model'
    model.mkdir()
    (model / 'config.json').write_text('{"model_type":"llama"}')
    (model / 'model.safetensors').write_bytes(b'fixture')
    (tmp_path / 'config.json').write_text(json.dumps({'base_model': str(model)}))
    (tmp_path / 'train.jsonl').write_text('{"prompt":"train","response":"yes"}\n')
    (tmp_path / 'eval.jsonl').write_text('{"prompt":"eval","response":"yes"}\n')
    (tmp_path / 'adapter.gguf').write_bytes(b'prior version')
    with pytest.raises(ValueError, match='fresh run'):
        backend().run_training(tmp_path)
    assert (tmp_path / 'adapter.gguf').read_bytes() == b'prior version'


@pytest.mark.parametrize('change,field', [
    ({'max_length': 8193}, 'max_length'),
    ({'learning_rate': .11}, 'learning_rate'),
    ({'seed': -1}, 'seed'),
    ({'seed': True}, 'seed'),
])
def test_standalone_backend_uses_desktop_configuration_bounds(tmp_path, change, field):
    model = tmp_path / 'model'
    model.mkdir()
    (model / 'config.json').write_text('{"model_type":"llama"}')
    (model / 'model.safetensors').write_bytes(b'fixture')
    (tmp_path / 'config.json').write_text(json.dumps({'base_model': str(model), **change}))
    (tmp_path / 'train.jsonl').write_text('{"prompt":"train","response":"yes"}\n')
    (tmp_path / 'eval.jsonl').write_text('{"prompt":"eval","response":"yes"}\n')
    with pytest.raises(ValueError, match=field):
        backend().run_training(tmp_path)


def test_backend_rejects_normalized_duplicate_prompts(tmp_path):
    (tmp_path / 'train.jsonl').write_text('{"prompt":"Same   prompt","response":"yes"}\n')
    (tmp_path / 'eval.jsonl').write_text('{"prompt":" same PROMPT ","response":"yes"}\n')
    with pytest.raises(ValueError, match='Duplicate prompt'):
        backend().load_datasets(tmp_path)


@pytest.mark.parametrize('change,field', [
    ({'training_method': 'int8'}, 'training_method'),
    ({'training_method': 'qlora', 'device': 'cpu'}, 'CUDA'),
    ({'gradient_accumulation_steps': 0}, 'gradient_accumulation_steps'),
    ({'gradient_accumulation_steps': True}, 'gradient_accumulation_steps'),
    ({'gradient_accumulation_steps': 129}, 'gradient_accumulation_steps'),
    ({'gradient_checkpointing': 'yes'}, 'gradient_checkpointing'),
])
def test_backend_rejects_invalid_qlora_configuration_before_loading(tmp_path, change, field):
    model = tmp_path / 'model'
    model.mkdir()
    (model / 'config.json').write_text('{"model_type":"llama"}')
    (model / 'model.safetensors').write_bytes(b'fixture')
    (tmp_path / 'config.json').write_text(json.dumps({'base_model': str(model), **change}))
    (tmp_path / 'train.jsonl').write_text('{"prompt":"train","response":"yes"}\n')
    (tmp_path / 'eval.jsonl').write_text('{"prompt":"eval","response":"yes"}\n')
    with pytest.raises(ValueError, match=field):
        backend().run_training(tmp_path)


def test_qlora_uses_fp16_on_turing_even_when_bf16_emulation_is_available():
    from types import SimpleNamespace
    torch = SimpleNamespace(float16='fp16', bfloat16='bf16', cuda=SimpleNamespace(
        get_device_capability=lambda index: (7, 5), is_bf16_supported=lambda: True))
    assert backend().cuda_compute_dtype(torch) == 'fp16'
    torch.cuda.get_device_capability = lambda index: (8, 0)
    assert backend().cuda_compute_dtype(torch) == 'bf16'


def test_accumulation_matches_token_weighted_full_batch_and_final_partial_group():
    torch = pytest.importorskip('torch')
    from contextlib import nullcontext
    from types import SimpleNamespace
    trainer = backend()
    # Unequal response lengths expose averaging microbatch means incorrectly.
    rows = [{'labels': [-100] + [1] * count, 'target': .01 * (index + 1)}
            for index, count in enumerate((1, 4, 2, 3, 1))]

    class LossModel(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.weight = torch.nn.Parameter(torch.tensor(0.0))
        def forward(self, rows):
            losses = [(self.weight - row['target']) ** 2 for row in rows]
            counts = [len(row['labels']) - 1 for row in rows]
            return SimpleNamespace(loss=sum(loss * count for loss, count in zip(losses, counts)) / sum(counts))

    def run(batch_size, accumulation):
        model = LossModel()
        optimizer = torch.optim.SGD(model.parameters(), lr=.1)
        scaler = torch.amp.GradScaler('cuda', enabled=False)
        result = trainer.train_epoch(torch, model, rows, lambda values: {'rows': values}, optimizer,
                                     scaler, nullcontext, batch_size, accumulation)
        return model.weight.detach(), result

    accumulated, result = run(1, 3)
    full_batch, full_result = run(3, 1)
    assert result == full_result == 2
    assert torch.allclose(accumulated, full_batch, atol=1e-7)
    assert accumulated > 0


def test_adapter_verification_rejects_accidentally_trainable_base_weights():
    from types import SimpleNamespace
    adapter = SimpleNamespace(requires_grad=True)
    frozen = SimpleNamespace(requires_grad=False)
    model = SimpleNamespace(named_parameters=lambda: [('layer.lora_A.default.weight', adapter),
                                                      ('layer.base_layer.weight', frozen)],
                            get_nb_trainable_parameters=lambda: (8, 100))
    assert backend().verify_adapter_parameters(model) == (8, 100)
    frozen.requires_grad = True
    with pytest.raises(RuntimeError, match='base weights'):
        backend().verify_adapter_parameters(model)


def test_fp16_scale_overflow_retries_group_without_counting_skipped_updates():
    torch = pytest.importorskip('torch')
    from types import SimpleNamespace
    class Model(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.linear = torch.nn.Linear(1, 1, bias=False)
            torch.nn.init.zeros_(self.linear.weight)
        def forward(self, x):
            return SimpleNamespace(loss=torch.nn.functional.mse_loss(self.linear(x), torch.tensor([[100.0]])))
    model = Model()
    optimizer = torch.optim.SGD(model.parameters(), lr=.001)
    applied = []
    optimizer.register_step_post_hook(lambda *args: applied.append(True))
    scaler = torch.amp.GradScaler('cpu', init_scale=1024.0)
    steps = backend().train_epoch(torch, model, [{'labels': [-100, 1]}],
                                  lambda rows: {'x': torch.ones(1, 1)}, optimizer, scaler,
                                  lambda: torch.autocast('cpu', dtype=torch.float16), 1, 1)
    assert steps == len(applied) == 1
    assert scaler.get_scale() < 1024
    assert torch.isfinite(model.linear.weight).all()
    assert model.linear.weight.item() > 0


def test_persistent_nonfinite_gradients_stop_without_applying_an_update():
    torch = pytest.importorskip('torch')
    from contextlib import nullcontext
    from types import SimpleNamespace
    class Model(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.weight = torch.nn.Parameter(torch.tensor(0.0))
            self.calls = 0
        def forward(self):
            self.calls += 1
            return SimpleNamespace(loss=self.weight.sqrt())
    model = Model()
    optimizer = torch.optim.SGD(model.parameters(), lr=.001)
    applied = []
    optimizer.register_step_post_hook(lambda *args: applied.append(True))
    with pytest.raises(RuntimeError, match='persisted'):
        backend().train_epoch(torch, model, [{'labels': [-100, 1]}], lambda rows: {},
                              optimizer, torch.amp.GradScaler('cpu', init_scale=1024.0),
                              nullcontext, 1, 1)
    assert 1 < model.calls <= 13
    assert not applied
    assert model.weight.item() == 0
