"""Local Gemma checkpoint/GGUF provenance, without model runtime dependencies."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading

import pytest


ROOT = Path(__file__).parents[1]
MODULE = ROOT / 'letracode' / 'training_models.py'
CLI = ROOT / 'tools' / 'prepare-gemma-chat.py'


def pairing():
    assert MODULE.is_file(), 'Gemma checkpoint/GGUF provenance helper is missing'
    spec = importlib.util.spec_from_file_location('training_models_under_test', MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def model(tmp_path):
    base = tmp_path / 'gemma-4-E2B-it'
    base.mkdir()
    (base / 'config.json').write_text(json.dumps({
        'model_type': 'gemma4', 'architectures': ['Gemma4ForConditionalGeneration'],
        'text_config': {'model_type': 'gemma4_text', 'hidden_size': 1536,
                        'num_hidden_layers': 35, 'hidden_size_per_layer_input': 256,
                        'enable_moe_block': False},
    }))
    (base / 'model.safetensors').write_bytes(b'original full precision weights')
    (base / 'tokenizer.json').write_text('{"model":{"vocab":{"hello":1}}}')
    (base / 'tokenizer_config.json').write_text('{"tokenizer_class":"GemmaTokenizer"}')
    (base / 'chat_template.jinja').write_text('{{ messages }}')
    return base


def _write_quantizer_peer(checkout, body):
    if sys.platform == 'win32':
        # The Windows fixture launches a real python.exe copy as llama-quantize.exe;
        # its first argument is this dual-purpose GGUF/Python intermediate.
        (checkout / 'quantizer-peer.py').write_text('GGUF = None\n' + body)
        return
    binary = checkout / 'build' / 'bin' / 'llama-quantize'
    binary.write_text(f'#!{sys.executable}\n' + body)
    binary.chmod(0o755)


@pytest.fixture
def converter(tmp_path, monkeypatch):
    checkout = tmp_path / 'llama.cpp'
    checkout.mkdir()
    folder = checkout / 'build' / 'bin'
    folder.mkdir(parents=True)
    _write_quantizer_peer(checkout, '''import sys
from pathlib import Path
assert len(sys.argv) == 3 and sys.argv[2] == 'Q4_K_M'
Path(sys.argv[1]).write_bytes(b'GGUFmatching chat weights')
''')
    (checkout / 'convert_hf_to_gguf.py').write_text('''import argparse, os
from pathlib import Path
p = argparse.ArgumentParser()
p.add_argument('model')
p.add_argument('--outfile', required=True)
p.add_argument('--outtype', required=True)
a = p.parse_args()
assert a.outtype == 'f16'
assert os.environ['HF_HUB_OFFLINE'] == '1'
assert os.environ['TRANSFORMERS_OFFLINE'] == '1'
assert (Path(a.model) / 'model.safetensors').read_bytes() == b'original full precision weights'
peer = Path(__file__).with_name('quantizer-peer.py')
if peer.is_file():
    Path(a.outfile).write_bytes(peer.read_bytes())
else:
    Path(a.outfile).write_bytes(b'GGUFintermediate')
''')
    if sys.platform == 'win32':
        binary = folder / 'llama-quantize.exe'
        shutil.copy2(sys.executable, binary)
        # A copied python.exe needs the original runtime location for stdlib/DLLs.
        monkeypatch.setenv('PYTHONHOME', sys.base_prefix)
        # Native Python 3.13+ runners observed a file-identity transition on the
        # copied interpreter's first execution. Prime it before it is recorded.
        subprocess.run([str(binary), '-c', 'pass'], check=True, capture_output=True)
    else:
        binary = folder / 'llama-quantize'
        binary.write_text(f'#!{sys.executable}\n' + '''import sys
from pathlib import Path
assert len(sys.argv) == 4 and sys.argv[3] == 'Q4_K_M'
assert Path(sys.argv[1]).read_bytes().startswith(b'GGUF')
Path(sys.argv[2]).write_bytes(b'GGUFmatching chat weights')
''')
        binary.chmod(0o755)
    return checkout


def prepare(base, converter, output):
    assert CLI.is_file(), 'Gemma preparation command is missing'
    return subprocess.run([
        sys.executable, str(CLI), '--training-python', sys.executable,
        '--base-model', str(base), '--llama-cpp', str(converter),
        '--output', str(output),
    ], capture_output=True, text=True)


def test_non_gemma_does_not_require_pairing_manifest(tmp_path):
    (tmp_path / 'config.json').write_text('{"model_type":"llama"}')
    assert pairing().verify_gemma_pair(tmp_path, 'not-present.gguf') == {}


def test_unproven_existing_gemma_gguf_is_rejected(model, tmp_path):
    output = tmp_path / 'qat.gguf'
    output.write_bytes(b'GGUFexisting arbitrary QAT weights')
    with pytest.raises(ValueError, match='prepar|manifest'):
        pairing().verify_gemma_pair(model, output)


def test_preparation_links_exact_original_files_and_cleans_intermediate(model, converter, tmp_path):
    output = tmp_path / 'chat.gguf'
    before = {p.name: p.read_bytes() for p in model.iterdir()}
    result = prepare(model, converter, output)
    assert result.returncode == 0, result.stdout + result.stderr
    manifest = pairing().verify_gemma_pair(model, output)
    assert manifest['schema'] == 'letracode.gemma-pair.v1'
    assert manifest['source']['base_model'] == str(model.resolve())
    assert manifest['source']['weights']['model.safetensors']['sha256'] == hashlib.sha256(before['model.safetensors']).hexdigest()
    assert manifest['source']['files']['config.json']['sha256'] == hashlib.sha256(before['config.json']).hexdigest()
    assert manifest['source']['files']['chat_template.jinja']['sha256'] == hashlib.sha256(before['chat_template.jinja']).hexdigest()
    assert manifest['gguf']['sha256'] == hashlib.sha256(output.read_bytes()).hexdigest()
    assert manifest['conversion']['quantization'] == 'Q4_K_M'
    assert {p.name: p.read_bytes() for p in model.iterdir()} == before
    assert not list(tmp_path.rglob('*f16.gguf'))


@pytest.mark.parametrize('name', ['config.json', 'model.safetensors', 'tokenizer.json', 'chat_template.jinja', 'chat.gguf'])
def test_same_size_file_mutation_is_rejected_even_with_restored_mtime(model, converter, tmp_path, name):
    output = tmp_path / 'chat.gguf'
    assert prepare(model, converter, output).returncode == 0
    helper = pairing()
    helper.verify_gemma_pair(model, output)
    path = output if name == output.name else model / name
    previous = path.stat()
    contents = path.read_bytes()
    # Config remains valid JSON and still has a supported architecture.
    changed = contents.replace(b'gemma4_text', b'gemma4_texx') if name == 'config.json' else contents[:-1] + b'!'
    assert len(changed) == len(contents) and changed != contents
    path.write_bytes(changed)
    os.utime(path, ns=(previous.st_atime_ns, previous.st_mtime_ns))
    with pytest.raises(ValueError, match='changed|match|unsupported|Unsupported'):
        helper.verify_gemma_pair(model, output)


def test_windows_rehashes_when_file_identity_cannot_detect_a_rewrite(tmp_path, monkeypatch):
    helper = pairing()
    path = tmp_path / 'model.safetensors'
    path.write_bytes(b'first contents')
    identity = path.stat()
    monkeypatch.setattr(helper.sys, 'platform', 'win32')
    # Windows creation time does not provide POSIX ctime's rewrite signal.
    monkeypatch.setattr(helper, '_identity', lambda info: (
        info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, identity.st_ctime_ns,
    ))
    original = helper._file_record(path)

    path.write_bytes(b'other contents')
    os.utime(path, ns=(identity.st_atime_ns, identity.st_mtime_ns))

    changed = helper._file_record(path)

    assert changed['sha256'] != original['sha256']


def test_prepared_pair_rejects_another_source_directory(model, converter, tmp_path):
    output = tmp_path / 'chat.gguf'
    assert prepare(model, converter, output).returncode == 0
    other = tmp_path / 'other'
    shutil.copytree(model, other)
    with pytest.raises(ValueError, match='source|base model'):
        pairing().verify_gemma_pair(other, output)


@pytest.mark.parametrize('change', ['missing', 'added'])
def test_weight_inventory_changes_are_rejected(model, converter, tmp_path, change):
    output = tmp_path / 'chat.gguf'
    assert prepare(model, converter, output).returncode == 0
    if change == 'missing':
        (model / 'model.safetensors').unlink()
    else:
        (model / 'extra.safetensors').write_bytes(b'new shard')
    with pytest.raises(ValueError, match='changed|safetensors|match'):
        pairing().verify_gemma_pair(model, output)


def test_cancelled_verification_never_accepts_cached_files(model, converter, tmp_path):
    output = tmp_path / 'chat.gguf'
    assert prepare(model, converter, output).returncode == 0
    helper = pairing()
    helper.verify_gemma_pair(model, output)
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(RuntimeError, match='cancel|stop|Stop'):
        helper.verify_gemma_pair(model, output, cancel)


@pytest.mark.parametrize('patch', [
    {'model_type': 'gemma3'},
    {'architectures': ['Gemma4UnsupportedModel']},
    {'quantization_config': {'quant_method': 'qat'}},
    {'text_config': {'model_type': 'gemma4_text', 'quantization_config': {'bits': 4}}},
    {'_name_or_path': 'google/gemma-4-E2B-it-qat'},
])
def test_preparation_rejects_unsupported_or_quantized_originals(model, converter, tmp_path, patch):
    config_path = model / 'config.json'
    config = json.loads(config_path.read_text())
    if 'text_config' in patch:
        patch = {**patch, 'text_config': {**config['text_config'], **patch['text_config']}}
    config_path.write_text(json.dumps({**config, **patch}))
    output = tmp_path / 'chat.gguf'
    result = prepare(model, converter, output)
    assert result.returncode != 0
    assert 'Gemma' in result.stderr or 'original' in result.stderr
    assert not output.exists()
    assert not Path(str(output) + '.letracode.json').exists()


@pytest.mark.parametrize('existing', ['gguf', 'manifest'])
def test_preparation_never_overwrites_existing_output(model, converter, tmp_path, existing):
    output = tmp_path / 'chat.gguf'
    preserved = output if existing == 'gguf' else Path(str(output) + '.letracode.json')
    preserved.write_bytes(b'keep user data')
    result = prepare(model, converter, output)
    assert result.returncode != 0
    assert 'exist' in result.stderr
    assert preserved.read_bytes() == b'keep user data'
    assert not list(tmp_path.rglob('*f16.gguf'))


def test_converter_failure_cannot_publish_pairing_manifest(model, converter, tmp_path):
    (converter / 'convert_hf_to_gguf.py').write_text('import sys\nprint("broken converter", file=sys.stderr)\nsys.exit(7)\n')
    output = tmp_path / 'chat.gguf'
    result = prepare(model, converter, output)
    assert result.returncode != 0
    assert 'conversion' in result.stderr.lower()
    assert not output.exists()
    assert not Path(str(output) + '.letracode.json').exists()
    assert (model / 'model.safetensors').read_bytes() == b'original full precision weights'


def test_preparation_never_writes_into_original_model_directory(model, converter):
    output = model / 'chat.gguf'
    result = prepare(model, converter, output)
    assert result.returncode != 0
    assert not output.exists()


def test_relative_training_python_still_runs_inside_fresh_scratch(model, converter, tmp_path, monkeypatch):
    name = 'python.exe' if sys.platform == 'win32' else 'python'
    python = tmp_path / 'training' / 'bin' / name
    python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)
    monkeypatch.chdir(tmp_path)
    output = tmp_path / 'chat.gguf'
    helper = pairing()
    helper.prepare_gemma_chat(str(Path('training/bin') / name), model, converter, output)
    assert helper.verify_gemma_pair(model, output)['gguf']['bytes'] > 4


@pytest.mark.parametrize('failure', ['failed', 'missing_output'])
def test_quantization_failure_preserves_diagnostics_without_publishing(model, converter, tmp_path, failure):
    body = f'import sys\nprint("quantizer diagnostics")\nsys.exit({7 if failure == "failed" else 0})\n'
    _write_quantizer_peer(converter, body)
    output = tmp_path / 'chat.gguf'
    result = prepare(model, converter, output)
    assert result.returncode != 0
    assert not output.exists()
    assert not Path(str(output) + '.letracode.json').exists()
    logs = list(tmp_path.glob('.letracode-gemma-*/quantization.log'))
    assert len(logs) == 1 and 'quantizer diagnostics' in logs[0].read_text()
    assert (model / 'model.safetensors').read_bytes() == b'original full precision weights'


def test_original_source_change_during_conversion_prevents_publication(model, converter, tmp_path):
    script = converter / 'convert_hf_to_gguf.py'
    script.write_text(script.read_text() + "\n(Path(a.model) / 'chat_template.jinja').write_text('changed template')\n")
    output = tmp_path / 'chat.gguf'
    result = prepare(model, converter, output)
    assert result.returncode != 0
    assert 'changed' in result.stderr
    assert not output.exists()
    assert not Path(str(output) + '.letracode.json').exists()


@pytest.mark.parametrize('mapping', [
    {'weight': 'missing.safetensors'},
    {'weight': '../outside.safetensors'},
    {'weight': ['model.safetensors']},
])
def test_invalid_weight_index_is_rejected_as_a_validation_error(model, converter, mapping):
    (model / 'model.safetensors.index.json').write_text(json.dumps({'weight_map': mapping}))
    with pytest.raises(ValueError, match='index|shard'):
        pairing().prepare_gemma_chat(sys.executable, model, converter, model.parent / 'chat.gguf')


@pytest.mark.parametrize('patch', [
    {'hidden_size': 5376, 'num_hidden_layers': 62},
    {'hidden_size': 1536, 'num_hidden_layers': 34},
    {'hidden_size_per_layer_input': 128},
    {'enable_moe_block': True},
])
def test_preparation_rejects_non_e2b_e4b_gemma_configurations(model, converter, tmp_path, patch):
    config_path = model / 'config.json'
    config = json.loads(config_path.read_text())
    config['text_config'].update(patch)
    config_path.write_text(json.dumps(config))
    with pytest.raises(ValueError, match='Gemma|E2B|E4B'):
        pairing().prepare_gemma_chat(sys.executable, model, converter, tmp_path / 'chat.gguf')


def test_preparation_rejects_text_only_gemma_checkpoint_in_place_of_original(model, converter, tmp_path):
    config_path = model / 'config.json'
    config = json.loads(config_path.read_text())['text_config']
    config['architectures'] = ['Gemma4ForCausalLM']
    config_path.write_text(json.dumps(config))
    with pytest.raises(ValueError, match='original|Unsupported'):
        pairing().prepare_gemma_chat(sys.executable, model, converter, tmp_path / 'chat.gguf')


def test_original_e4b_dimensions_are_supported(model, converter, tmp_path):
    config_path = model / 'config.json'
    config = json.loads(config_path.read_text())
    config['text_config'].update(hidden_size=2560, num_hidden_layers=42)
    config_path.write_text(json.dumps(config))
    output = tmp_path / 'chat.gguf'
    assert prepare(model, converter, output).returncode == 0
    assert pairing().verify_gemma_pair(model, output)['source']['model_type'] == 'gemma4'
