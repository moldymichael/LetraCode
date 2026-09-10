import json
from pathlib import Path
import pytest
from letracode import training_backend as backend


def original(tmp_path, variant='E2B'):
    text = dict(model_type='gemma4_text',hidden_size=1536 if variant=='E2B' else 2560,
                num_hidden_layers=35 if variant=='E2B' else 42,hidden_size_per_layer_input=256,
                enable_moe_block=False)
    config = dict(model_type='gemma4',architectures=['Gemma4ForConditionalGeneration'],text_config=text,
                  vision_config={'model_type':'gemma4_vision'},audio_config={'model_type':'gemma4_audio'})
    (tmp_path/'config.json').write_text(json.dumps(config))
    (tmp_path/'model.safetensors').write_bytes(b'fixture')
    return config


@pytest.mark.parametrize('variant',['E2B','E4B'])
def test_original_gemma4_multimodal_checkpoint_is_accepted_for_text_training(tmp_path, variant):
    config=original(tmp_path,variant)
    assert backend.validate_model_directory(tmp_path)==config


@pytest.mark.parametrize('alteration',[{'quantization_config':{'quant_method':'compressed-tensors'}},
                                     {'text_config':{'model_type':'gemma4_text','enable_moe_block':True}},
                                     {'architectures':['Gemma4AssistantForCausalLM']}])
def test_gemma_rejects_quantized_other_sizes_and_assistant_draft_weights(tmp_path,alteration):
    config=original(tmp_path);config.update(alteration)
    (tmp_path/'config.json').write_text(json.dumps(config))
    with pytest.raises(ValueError):backend.validate_model_directory(tmp_path)


def test_gemma_template_explicitly_disables_thinking_and_preserves_terminator():
    class Tokenizer:
        chat_template='local template'
        def apply_chat_template(self,messages,**kwargs):
            assert kwargs['enable_thinking'] is False
            return [2,10,11] if kwargs['add_generation_prompt'] else [2,10,11,50,106]
    row=backend.encode_example(Tokenizer(),{'prompt':'one','response':'two'},32,gemma=True)
    assert row['labels']==[-100,-100,-100,50,106]


def test_frozen_cpu_ple_lookup_transfers_only_rows():
    torch=pytest.importorskip('torch')
    class Model(torch.nn.Module):
        def __init__(self):
            super().__init__(); self.model=torch.nn.Module()
            self.model.embed_tokens=torch.nn.Embedding(32,4)
            self.model.embed_tokens_per_layer=torch.nn.Embedding(32,8)
    model=Model();ple=model.model.embed_tokens_per_layer
    expected=ple(torch.tensor([[1,3]])).detach().clone()
    backend.offload_gemma_ple(model,torch)
    assert ple.weight.device.type=='cpu'
    assert not ple.weight.requires_grad
    assert torch.equal(ple(torch.tensor([[1,3]])),expected)


@pytest.mark.parametrize('quantization', [
    {'quantization_config': {'quant_method': 'compressed-tensors'}},
    {'quantization_status': 'compressed'},
    {'quant_method': 'bitsandbytes'},
])
def test_standalone_gemma_rejects_nested_quantized_originals(tmp_path, quantization):
    config = original(tmp_path)
    config['text_config'].update(quantization)
    (tmp_path / 'config.json').write_text(json.dumps(config))
    with pytest.raises(ValueError, match='original|unquantized|QAT'):
        backend.validate_model_directory(tmp_path)


@pytest.mark.parametrize('name', [
    'google/gemma-4-E2B-it-qat',
    'google/gemma-4-E4B-it-QAT-q4_0',
    'google/gemma-4-E2B-it_qat_checkpoint',
])
def test_standalone_gemma_rejects_quantization_aware_checkpoint_metadata(tmp_path, name):
    config = original(tmp_path)
    config['_name_or_path'] = name
    (tmp_path / 'config.json').write_text(json.dumps(config))
    with pytest.raises(ValueError, match='original|unquantized|QAT'):
        backend.validate_model_directory(tmp_path)


@pytest.mark.parametrize('name,rejected', [
    ('gemma-4-E2B-it-qat', True),
    ('gemma-4-E4B-it-QAT-q4_0', True),
    ('qat', True),
    ('qatar-original-model', False),
    ('gemma-4-E2B-it-HF', False),
])
def test_standalone_gemma_interprets_quantization_aware_directory_tokens(tmp_path, name, rejected):
    base = tmp_path / name
    base.mkdir()
    config = original(base)
    if rejected:
        with pytest.raises(ValueError, match='original|unquantized|QAT'):
            backend.validate_model_directory(base)
    else:
        assert backend.validate_model_directory(base) == config


def test_gemma_original_guard_preserves_legacy_llama_directory_rules(tmp_path):
    base = tmp_path / 'legacy-qat-name'
    base.mkdir()
    config = {'model_type': 'llama', '_name_or_path': 'some-qat-name'}
    (base / 'config.json').write_text(json.dumps(config))
    (base / 'model.safetensors').write_bytes(b'fixture')
    assert backend.validate_model_directory(base) == config
