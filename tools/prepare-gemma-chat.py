#!/usr/bin/env python3
"""Prepare a matching Gemma Chat GGUF from original local HF safetensors."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from letracode.training_models import gemma_manifest_path, prepare_gemma_chat


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--training-python', required=True, help='Local training Python with llama.cpp conversion dependencies installed')
    parser.add_argument('--base-model', required=True, help='Original local google/gemma-4-E2B-it or google/gemma-4-E4B-it Hugging Face directory; QAT weights are rejected')
    parser.add_argument('--llama-cpp', required=True, help='Local llama.cpp checkout containing the converter and built llama-quantize')
    parser.add_argument('--output', required=True, help='New Q4_K_M .gguf outside the original model directory; existing output is never overwritten')
    args = parser.parse_args()
    try:
        prepare_gemma_chat(args.training_python, args.base_model, args.llama_cpp, args.output)
    except (OSError, ValueError, RuntimeError) as exc:
        parser.exit(1, f'Gemma preparation failed: {exc}\n')
    print(f'Prepared Chat GGUF: {Path(args.output).expanduser().absolute()}')
    print(f'Pairing manifest: {gemma_manifest_path(Path(args.output).expanduser().absolute())}')


if __name__ == '__main__':
    main()
