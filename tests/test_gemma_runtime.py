"""CPU regressions for Gemma 4's real Transformers implementation.

These use tiny random models, never pretrained weights or a GPU. They run when
the optional training dependencies are installed and guard KV sharing, gradient
checkpointing, and extraction of text weights from conditional checkpoints.
"""
import copy

import pytest


torch = pytest.importorskip('torch')
transformers = pytest.importorskip('transformers')
if not hasattr(transformers, 'Gemma4ForCausalLM'):
    pytest.skip('The training runtime requires Gemma 4 support', allow_module_level=True)


@pytest.fixture(autouse=True)
def bounded_cpu_runtime():
    previous_threads = torch.get_num_threads()
    torch.set_num_threads(1)
    with torch.random.fork_rng(devices=[]):
        torch.manual_seed(73)
        yield
    torch.set_num_threads(previous_threads)


def text_config():
    # Both attention types have a KV producer followed by a shared consumer.
    return transformers.Gemma4TextConfig(
        vocab_size=128,
        vocab_size_per_layer_input=128,
        hidden_size=32,
        hidden_size_per_layer_input=8,
        intermediate_size=64,
        num_hidden_layers=4,
        num_attention_heads=2,
        num_key_value_heads=1,
        head_dim=16,
        global_head_dim=16,
        num_kv_shared_layers=2,
        layer_types=['sliding_attention', 'full_attention'] * 2,
        sliding_window=4,
        final_logit_softcapping=30.0,
        attention_dropout=0.0,
        tie_word_embeddings=True,
    )


def text_model():
    with torch.device('cpu'):
        return transformers.Gemma4ForCausalLM(text_config()).float()


def token_ids():
    return torch.tensor([[2, 7, 12, 16, 33, 9, 21]], device='cpu')


def test_cache_and_no_cache_have_equal_prefill_and_decode_logits():
    model = text_model().eval()
    ids = token_ids()
    with torch.no_grad():
        cached = model(input_ids=ids, use_cache=True)
        uncached = model(input_ids=ids, use_cache=False)
        torch.testing.assert_close(cached.logits, uncached.logits, rtol=1e-5, atol=1e-6)

        # Decode past the sliding-window boundary as well as testing prefill.
        prefix = model(input_ids=ids[:, :-1], use_cache=True)
        decoded = model(
            input_ids=ids[:, -1:],
            past_key_values=prefix.past_key_values,
            use_cache=True,
        )
        torch.testing.assert_close(decoded.logits[:, -1], uncached.logits[:, -1], rtol=1e-5, atol=1e-6)


def gradients(model, *, use_cache, checkpoint):
    model.train()
    if checkpoint:
        model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={'use_reentrant': False})
    ids = token_ids()
    output = model(input_ids=ids, labels=ids, use_cache=use_cache)
    assert torch.isfinite(output.loss)
    output.loss.backward()
    result = {}
    for name, parameter in model.named_parameters():
        if parameter.requires_grad:
            assert parameter.grad is not None, f'Missing gradient: {name}'
            assert torch.isfinite(parameter.grad).all(), f'Nonfinite gradient: {name}'
            result[name] = parameter.grad.detach().clone()
    return result


@pytest.mark.parametrize('with_lora', [False, True], ids=['all-weights', 'lora'])
def test_nonreentrant_checkpoint_preserves_every_shared_kv_gradient(with_lora):
    model = text_model()
    if with_lora:
        peft = pytest.importorskip('peft')
        model = peft.get_peft_model(model, peft.LoraConfig(
            r=2,
            lora_alpha=2,
            target_modules=['q_proj', 'k_proj', 'v_proj', 'o_proj', 'gate_proj', 'up_proj', 'down_proj'],
            lora_dropout=0.0,
            bias='none',
            task_type='CAUSAL_LM',
        ))
        # Default zero B matrices make every A gradient zero, hiding failures.
        with torch.no_grad():
            for name, parameter in model.named_parameters():
                if '.lora_B.' in name:
                    parameter.normal_(mean=0.0, std=0.02)

    reference = gradients(copy.deepcopy(model), use_cache=True, checkpoint=False)
    plain = gradients(copy.deepcopy(model), use_cache=False, checkpoint=False)
    checkpointed = gradients(copy.deepcopy(model), use_cache=False, checkpoint=True)
    assert reference.keys() == plain.keys() == checkpointed.keys()
    for name, expected in reference.items():
        torch.testing.assert_close(plain[name], expected, rtol=2e-5, atol=1e-7, msg=name)
        torch.testing.assert_close(checkpointed[name], expected, rtol=2e-5, atol=1e-7, msg=name)

    # Explicitly verify the cross-layer producers and consumers are exercised.
    for layer, projections in [(0, ['k_proj', 'v_proj']), (1, ['k_proj', 'v_proj']),
                               (2, ['q_proj']), (3, ['q_proj'])]:
        for projection in projections:
            matches = [value for name, value in reference.items()
                       if f'layers.{layer}.self_attn.{projection}.' in name]
            assert matches, f'No gradient path for layer {layer} {projection}'
            assert all(torch.count_nonzero(value) > 0 for value in matches)


def test_conditional_checkpoint_loads_all_text_weights_with_explicit_mapping(tmp_path):
    # Keep the official conditional architecture and weight prefixes, while
    # omitting the unrelated vision/audio towers to keep the fixture small.
    config = transformers.Gemma4Config(
        text_config=text_config(), vision_config=None, audio_config=None,
        boi_token_id=100, eoi_token_id=101, image_token_id=102,
        video_token_id=103, boa_token_id=104, eoa_token_index=105, audio_token_id=106,
    )
    with torch.device('cpu'):
        conditional = transformers.Gemma4ForConditionalGeneration(config).float().eval()
    conditional.save_pretrained(tmp_path)

    causal, loading_info = transformers.Gemma4ForCausalLM.from_pretrained(
        tmp_path,
        config=config.text_config,
        key_mapping={r'^model\.language_model\.': 'model.'},
        output_loading_info=True,
        local_files_only=True,
        device_map='cpu',
        dtype=torch.float32,
    )
    assert not loading_info['missing_keys'], loading_info
    assert not loading_info['unexpected_keys'], loading_info
    assert not loading_info['mismatched_keys'], loading_info
    assert causal.lm_head.weight is causal.model.embed_tokens.weight
    conditional_weights = conditional.state_dict()
    for name, actual in causal.state_dict().items():
        source_name = name.replace('model.', 'model.language_model.', 1) if name.startswith('model.') else name
        torch.testing.assert_close(actual, conditional_weights[source_name], rtol=0, atol=0, msg=name)
    with torch.no_grad():
        expected = conditional(input_ids=token_ids(), use_cache=False).logits
        actual = causal(input_ids=token_ids(), use_cache=False).logits
    torch.testing.assert_close(actual, expected, rtol=1e-5, atol=1e-6)


def response_batch():
    # Different prompt/response lengths, right padding, and a shared selected
    # column test the causal shift and masking independently of the helper.
    return {
        'input_ids': torch.tensor([[2, 7, 12, 16, 33, 9, 21, 0, 0],
                                   [2, 10, 3, 18, 19, 22, 43, 31, 32]]),
        'labels': torch.tensor([[-100, -100, -100, -100, 33, 9, 21, -100, -100],
                                [-100, -100, -100, -100, -100, -100, 43, 31, 32]]),
        'attention_mask': torch.tensor([[1, 1, 1, 1, 1, 1, 1, 0, 0],
                                        [1, 1, 1, 1, 1, 1, 1, 1, 1]]),
        'use_cache': False,
    }


@pytest.mark.parametrize('with_lora,checkpoint', [(False, False), (True, False), (True, True)],
                         ids=['base', 'lora', 'lora-shared-kv-checkpoint'])
def test_response_position_logits_preserve_loss_and_every_gradient(with_lora, checkpoint):
    from letracode import training_backend as backend

    model = text_model()
    if with_lora:
        peft = pytest.importorskip('peft')
        model = peft.get_peft_model(model, peft.LoraConfig(
            r=2, lora_alpha=2, target_modules=backend.GEMMA_TARGETS,
            lora_dropout=0.0, bias='none', task_type='CAUSAL_LM',
        ))
        # Exercise A gradients as well as B gradients.
        with torch.no_grad():
            for name, parameter in model.named_parameters():
                if '.lora_B.' in name:
                    parameter.normal_(mean=0.0, std=0.02)
    if checkpoint:
        model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={'use_reentrant': False})

    reference, selected = copy.deepcopy(model).train(), copy.deepcopy(model).train()
    inputs = response_batch()
    saved_inputs = copy.deepcopy(inputs)
    expected = reference(**inputs).loss
    expected.backward()
    projected_positions = []
    head = selected.get_base_model().lm_head if with_lora else selected.lm_head
    handle = head.register_forward_pre_hook(lambda module, args: projected_positions.append(args[0].shape[1]))
    actual = backend.compute_response_loss(torch, selected, inputs, gemma=True)
    actual.backward()
    handle.remove()

    # Only columns 3..7 predict an unmasked response. All 9 input positions
    # remain available to the decoder, including both complete user prompts.
    assert projected_positions == [5]
    torch.testing.assert_close(actual, expected, rtol=1e-6, atol=1e-7)
    expected_parameters = dict(reference.named_parameters())
    for name, parameter in selected.named_parameters():
        if parameter.requires_grad:
            assert parameter.grad is not None, name
            assert torch.isfinite(parameter.grad).all(), name
            torch.testing.assert_close(parameter.grad, expected_parameters[name].grad,
                                       rtol=2e-5, atol=1e-7, msg=name)
    for key in ('input_ids', 'labels', 'attention_mask'):
        torch.testing.assert_close(inputs[key], saved_inputs[key], rtol=0, atol=0)

    # The original and candidate evaluation use the same selected-position
    # loss with gradients disabled; generation has a separate unchanged path.
    with torch.no_grad():
        torch.testing.assert_close(
            backend.compute_response_loss(torch, selected.eval(), inputs, gemma=True),
            reference.eval()(**inputs).loss, rtol=1e-6, atol=1e-7,
        )


def test_response_position_loss_rejects_missing_assistant_targets():
    from letracode import training_backend as backend

    inputs = response_batch()
    inputs['labels'].fill_(-100)
    with pytest.raises(ValueError, match='response tokens'):
        backend.compute_response_loss(torch, text_model(), inputs, gemma=True)


def test_default_response_loss_preserves_llama_loss_and_full_head_projection():
    from letracode import training_backend as backend

    model = transformers.LlamaForCausalLM(transformers.LlamaConfig(
        vocab_size=128, hidden_size=32, intermediate_size=64,
        num_hidden_layers=2, num_attention_heads=2, num_key_value_heads=1,
    )).eval()
    inputs = response_batch()
    projected_positions = []
    with torch.no_grad():
        expected = model(**inputs).loss
        handle = model.lm_head.register_forward_pre_hook(
            lambda module, args: projected_positions.append(args[0].shape[1]))
        actual = backend.compute_response_loss(torch, model, inputs)
        handle.remove()
    assert projected_positions == [9]
    torch.testing.assert_close(actual, expected, rtol=0, atol=0)


def test_selected_position_training_preserves_accumulated_updates_and_partial_group():
    from contextlib import nullcontext
    from letracode import training_backend as backend

    peft = pytest.importorskip('peft')
    model = peft.get_peft_model(text_model(), peft.LoraConfig(
        r=2, lora_alpha=2, target_modules=backend.GEMMA_TARGETS,
        lora_dropout=0.0, bias='none', task_type='CAUSAL_LM',
    ))
    with torch.no_grad():
        for name, parameter in model.named_parameters():
            if '.lora_B.' in name:
                parameter.normal_(mean=0.0, std=0.02)
    model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={'use_reentrant': False})
    model.config.use_cache = False
    inputs = response_batch()
    rows = [{key: value[i].tolist() for key, value in inputs.items() if torch.is_tensor(value)}
            for i in range(2)]
    # A different response-token count catches per-example normalization and a
    # final partial accumulation group catches a silently skipped last update.
    rows.append({'input_ids': [2, 7, 12, 16], 'labels': [-100, -100, -100, 16],
                 'attention_mask': [1, 1, 1, 1]})

    def batch(microbatch):
        return {key: torch.tensor([row[key] for row in microbatch]) for key in
                ('input_ids', 'labels', 'attention_mask')}

    trained = []
    for gemma in (False, True):
        current = copy.deepcopy(model).train()
        projected_positions = []
        handle = current.get_base_model().lm_head.register_forward_pre_hook(
            lambda module, args: projected_positions.append(args[0].shape[1]))
        optimizer = torch.optim.SGD([p for p in current.parameters() if p.requires_grad], lr=0.1)
        steps = backend.train_epoch(
            torch, current, rows, batch, optimizer, torch.amp.GradScaler('cuda', enabled=False),
            nullcontext, 1, 2, gemma=gemma,
        )
        handle.remove()
        assert steps == 2
        assert projected_positions == ([3, 3, 1] if gemma else [9, 9, 4])
        trained.append(dict(current.named_parameters()))
    for name, expected in trained[0].items():
        if expected.requires_grad:
            torch.testing.assert_close(trained[1][name], expected, rtol=2e-5, atol=1e-7, msg=name)
