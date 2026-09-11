"""Offline provenance for a Gemma training checkpoint and its derived Chat GGUF.

This module intentionally uses only the standard library, including in desktop
verification. A preparation manifest records local derivation, not an assertion
that arbitrary local files have been authenticated by Hugging Face.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile


MANIFEST_SCHEMA = 'letracode.gemma-pair.v1'
_HASH_CACHE = {}


def _check_cancel(cancel):
    if cancel is not None and cancel.is_set():
        raise RuntimeError('Model verification cancelled.')


def _identity(info):
    # ctime catches same-size rewrites even if a caller restores mtime.
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _file_record(path, cancel=None):
    _check_cancel(cancel)
    path = Path(path)
    try:
        current = path.stat()
        if not stat.S_ISREG(current.st_mode):
            raise ValueError(f'Expected an ordinary local file: {path}')
        identity = _identity(current)
        key = str(path.absolute())
        cached = _HASH_CACHE.get(key)
        # Windows exposes creation time through st_ctime, so a same-size rewrite
        # can restore mtime and retain every identity field. Rehash there rather
        # than trusting a cache entry that cannot prove the bytes are unchanged.
        if sys.platform != 'win32' and cached and cached[0] == identity:
            _check_cancel(cancel)
            if _identity(path.stat()) != identity:
                raise ValueError(f'File changed while verifying it: {path}')
            return dict(cached[1])
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            before = _identity(os.fstat(stream.fileno()))
            for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b''):
                _check_cancel(cancel)
                digest.update(chunk)
            after = _identity(os.fstat(stream.fileno()))
        _check_cancel(cancel)
        if identity != before or before != after or after != _identity(path.stat()):
            raise ValueError(f'File changed while verifying it: {path}')
    except OSError as exc:
        raise ValueError(f'Cannot verify local file {path}: {exc}') from exc
    record = {'sha256': digest.hexdigest(), 'bytes': current.st_size}
    # Cache only in this process, never from an untrusted manifest or disk cache.
    # All identities are checked again on every use, including cancellation.
    if len(_HASH_CACHE) >= 256:
        _HASH_CACHE.clear()
    _HASH_CACHE[key] = (identity, record)
    return dict(record)


def _read_json(path, limit=4 * 1024 * 1024):
    try:
        with Path(path).open('rb') as stream:
            raw = stream.read(limit + 1)
        if len(raw) > limit:
            raise ValueError(f'Local JSON file is too large: {path}')
        value = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f'Cannot read local JSON file {path}: {exc}') from exc
    if not isinstance(value, dict):
        raise ValueError(f'Expected a JSON object: {path}')
    return value


def _is_gemma(config):
    architectures = config.get('architectures', [])
    return (str(config.get('model_type', '')).startswith('gemma') or
            (isinstance(architectures, list) and any(str(a).startswith('Gemma') for a in architectures)))


def _validate_original(base, config):
    if (config.get('model_type') != 'gemma4' or
            config.get('architectures') != ['Gemma4ForConditionalGeneration']):
        raise ValueError('Unsupported Gemma architecture; select original Gemma 4 E2B/E4B instruct safetensors.')
    text_config = config.get('text_config', {})
    if (not isinstance(text_config, dict) or text_config.get('model_type') != 'gemma4_text' or
            (text_config.get('hidden_size'), text_config.get('num_hidden_layers')) not in ((1536, 35), (2560, 42)) or
            text_config.get('hidden_size_per_layer_input') != 256 or text_config.get('enable_moe_block')):
        raise ValueError('Unsupported Gemma text architecture; select original dense Gemma 4 E2B/E4B instruct weights with 256-wide per-layer inputs.')
    if (base / 'adapter_config.json').exists():
        raise ValueError('Select original Gemma base weights, not an adapter directory.')

    def quantized(value):
        if isinstance(value, dict):
            return any((key in ('quantization_config', 'quantization_status', 'quant_method') and bool(item))
                       or quantized(item) for key, item in value.items())
        if isinstance(value, list):
            return any(quantized(item) for item in value)
        return isinstance(value, str) and bool(re.search(r'(^|[^a-z])qat([^a-z]|$)', value.lower()))

    if quantized(config) or re.search(r'(^|[^a-z])qat([^a-z]|$)', str(base).lower()):
        raise ValueError('Use original unquantized Gemma weights from google/gemma-4-E2B-it or google/gemma-4-E4B-it; QAT/quantized checkpoints cannot establish this pair.')


def _source_snapshot(base, cancel=None):
    _check_cancel(cancel)
    config = _read_json(base / 'config.json')
    _validate_original(base, config)
    weights = sorted(base.glob('*.safetensors'))
    if not weights:
        raise ValueError('Original local Gemma safetensors weights are required.')
    files = [base / 'config.json']
    files += sorted(p for p in base.iterdir() if p.is_file() and (
        p.name.startswith('tokenizer') or p.name.startswith('chat_template') or
        p.name in ('special_tokens_map.json', 'added_tokens.json', 'generation_config.json') or
        p.name.endswith('.safetensors.index.json')))
    template_dir = base / 'chat_templates'
    if template_dir.is_dir():
        files += sorted(p for p in template_dir.rglob('*') if p.is_file())
    if not any(p.name in ('tokenizer.json', 'tokenizer.model') for p in files):
        raise ValueError('The original local Gemma tokenizer files are required.')
    if not any(p.name.startswith('chat_template') or template_dir in p.parents for p in files):
        tokenizer_config = _read_json(base / 'tokenizer_config.json')
        if not tokenizer_config.get('chat_template'):
            raise ValueError('The original local Gemma chat template is required.')
    weight_names = {p.name for p in weights}
    for index in (p for p in files if p.name.endswith('.safetensors.index.json')):
        mapping = _read_json(index, 32 * 1024 * 1024).get('weight_map')
        if (not isinstance(mapping, dict) or not mapping or
                any(not isinstance(name, str) for name in mapping.values()) or
                set(mapping.values()) != weight_names):
            raise ValueError('Gemma safetensors shard files do not match their index.')
    return {'base_model': str(base), 'model_type': config['model_type'],
            'weights': {p.name: _file_record(p, cancel) for p in weights},
            'files': {str(p.relative_to(base)): _file_record(p, cancel) for p in files}}


def gemma_manifest_path(base_gguf):
    return Path(str(base_gguf) + '.letracode.json')


def verify_gemma_pair(base_model, base_gguf, cancel=None):
    """Return verified provenance, or {} for a non-Gemma local model config.

    Gemma manifests must have been produced by prepare-gemma-chat.py. Every
    current weight, config, tokenizer/template and GGUF byte is accounted for;
    a process-local hash cache only reuses unchanged filesystem identities.
    """
    _check_cancel(cancel)
    base = Path(base_model).expanduser().resolve()
    config = _read_json(base / 'config.json')
    if not _is_gemma(config):
        return {}
    _validate_original(base, config)
    output = Path(base_gguf).expanduser().absolute()
    sidecar = gemma_manifest_path(output)
    if not sidecar.is_file():
        raise ValueError('Gemma Chat GGUF requires a preparation manifest. Run tools/prepare-gemma-chat.py against these exact original Hugging Face weights.')
    manifest = _read_json(sidecar)
    if (manifest.get('schema') != MANIFEST_SCHEMA or
            not isinstance(manifest.get('conversion'), dict) or
            manifest['conversion'].get('quantization') != 'Q4_K_M' or
            manifest['conversion'].get('text_only') is not True):
        raise ValueError('Unsupported Gemma preparation manifest.')
    source = manifest.get('source')
    if not isinstance(source, dict) or source.get('base_model') != str(base):
        raise ValueError('Gemma preparation manifest does not match the selected source base model.')
    if source != _source_snapshot(base, cancel):
        raise ValueError('Original Gemma config, weights or tokenizer/template files changed or do not match the preparation manifest.')
    actual = {'name': output.name, **_file_record(output, cancel)}
    if manifest.get('gguf') != actual:
        raise ValueError('Gemma Chat GGUF changed or does not match its preparation manifest.')
    _check_cancel(cancel)
    return manifest


def _quantizer(checkout):
    names = ('llama-quantize.exe',) if sys.platform == 'win32' else ('llama-quantize', 'llama-quantize.exe')
    access = os.R_OK if sys.platform == 'win32' else os.X_OK
    for folder in ('build/bin', 'build-cuda/bin', 'bin', '.', 'build/bin/Release', 'build/Release'):
        for name in names:
            candidate = checkout / folder / name
            if candidate.is_file() and os.access(candidate, access):
                return candidate.resolve()
    raise ValueError('llama-quantize is missing; build the llama-quantize target in the selected llama.cpp checkout.')


def _run_conversion(command, scratch, log_name, env):
    logfile = scratch / log_name
    with logfile.open('x', encoding='utf-8') as log:
        result = subprocess.run(command, cwd=scratch, env=env, stdout=log, stderr=subprocess.STDOUT, check=False)
    if result.returncode:
        raise RuntimeError(f'Gemma conversion failed ({result.returncode}); diagnostics preserved at {logfile}')


def _require_gguf(path):
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f'Gemma conversion did not produce an ordinary GGUF: {path}')
    with path.open('rb') as stream:
        if stream.read(4) != b'GGUF' or path.stat().st_size <= 4:
            raise RuntimeError(f'Gemma conversion did not produce a valid GGUF header: {path}')


def prepare_gemma_chat(training_python, base_model, llama_cpp, output):
    """Convert locally into fresh scratch space and publish a hash-linked pair.

    Existing files and original model directories are never overwritten. Only
    the intermediate files created in this invocation are removed on success;
    failed conversion scratch and logs remain available for diagnosis.
    """
    base = Path(base_model).expanduser().resolve()
    checkout = Path(llama_cpp).expanduser().resolve()
    output = Path(output).expanduser().absolute()
    sidecar = gemma_manifest_path(output)
    if output.suffix.lower() != '.gguf':
        raise ValueError('Choose a new output filename ending in .gguf.')
    if os.path.lexists(output) or os.path.lexists(sidecar):
        raise ValueError('GGUF output or preparation manifest already exists; choose a new output path.')
    if output.resolve().is_relative_to(base):
        raise ValueError('Choose an output outside the original model directory to preserve original files.')
    # Do not resolve this symlink: a venv Python must retain its venv identity.
    python = shutil.which(str(Path(training_python).expanduser()))
    if not python:
        raise ValueError('Select an existing executable for --training-python.')
    python = os.path.abspath(python)
    converter = checkout / 'convert_hf_to_gguf.py'
    if not converter.is_file():
        raise ValueError('The selected llama.cpp checkout is missing convert_hf_to_gguf.py.')
    quantizer = _quantizer(checkout)
    source = _source_snapshot(base)
    conversion = {'quantization': 'Q4_K_M', 'intermediate_type': 'f16', 'text_only': True,
                  'converter': {'path': str(converter), **_file_record(converter)},
                  'quantizer': {'path': str(quantizer), **_file_record(quantizer)}}
    output.parent.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix='.letracode-gemma-', dir=output.parent))
    intermediate = scratch / 'base-f16.gguf'
    candidate = scratch / 'base-Q4_K_M.gguf'
    env = dict(os.environ, HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
               HF_DATASETS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1')
    print(f'Converting original Gemma text weights; scratch and logs: {scratch}', flush=True)
    _run_conversion([python, str(converter), str(base), '--outfile', str(intermediate), '--outtype', 'f16'],
                    scratch, 'conversion.log', env)
    _require_gguf(intermediate)
    print('Quantizing matching Chat GGUF to Q4_K_M', flush=True)
    _run_conversion([str(quantizer), str(intermediate), str(candidate), 'Q4_K_M'], scratch, 'quantization.log', env)
    _require_gguf(candidate)
    if source != _source_snapshot(base):
        raise ValueError('Original Gemma files changed during conversion; no pairing manifest was published.')
    if (conversion['converter'] != {'path': str(converter), **_file_record(converter)} or
            conversion['quantizer'] != {'path': str(quantizer), **_file_record(quantizer)}):
        raise ValueError('Gemma conversion tools changed during conversion; no pairing manifest was published.')
    manifest = {'schema': MANIFEST_SCHEMA, 'source': source, 'conversion': conversion,
                'gguf': {'name': output.name, **_file_record(candidate)}}
    staged_manifest = scratch / 'manifest.json'
    staged_manifest.write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    # Hard links publish complete files atomically, and fail if a destination
    # appeared while conversion was running. Scratch uses the same filesystem.
    os.link(candidate, output)
    try:
        os.link(staged_manifest, sidecar)
    except OSError:
        if output.exists() and os.path.samefile(candidate, output):
            output.unlink()
        raise
    intermediate.unlink()
    candidate.unlink()
    staged_manifest.unlink()
    return manifest


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Prepare an offline, hash-linked Gemma Chat model')
    parser.add_argument('--training-python', required=True)
    parser.add_argument('--base-model', required=True)
    parser.add_argument('--llama-cpp', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    try:
        prepare_gemma_chat(args.training_python, args.base_model, args.llama_cpp, args.output)
    except (OSError, ValueError, RuntimeError) as error:
        parser.exit(1, str(error) + '\n')
