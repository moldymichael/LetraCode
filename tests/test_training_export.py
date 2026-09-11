"""A local version export preserves full reviews without touching training data."""
import json
from pathlib import Path

import pytest

from letracode.store import Store
from letracode.training import TrainingConfig, TrainingRepository


def exporter():
    try:
        from letracode.training_export import export_version_review
    except ImportError:
        pytest.fail('A selected version needs an explicit comparison export')
    return export_version_review


@pytest.fixture
def version(tmp_path):
    base = tmp_path / 'source'; base.mkdir()
    (base / 'model.safetensors').write_bytes(b'original model bytes')
    python = tmp_path / 'training-python'; python.touch()
    repo = TrainingRepository(Store(tmp_path / 'data'))
    repo.save_example('Teaching question', 'Teaching answer', approved=True)
    repo.save_example('Held-out question', 'Expected answer', 'eval', approved=True)
    run = repo.create_run(TrainingConfig(python, base))
    repo.update_run(run['id'], 'running')
    repo.update_run(run['id'], 'succeeded', report={
        'base_loss': 2.0, 'candidate_loss': 1.8,
        'eval_examples': [{'prompt': 'Held-out question', 'response': 'Expected answer',
                           'base_output': 'Short optimizer baseline',
                           'candidate_output': 'Short optimizer candidate'}],
        'conversion_error': 'Conversion was unavailable during training',
    })
    return repo, run['id']


def test_export_keeps_full_fresh_answers_history_and_saved_review(version, tmp_path):
    repo, ident = version
    answer = 'A complete answer with useful details.\n' * 1000 + 'Final café conclusion.'
    review = {
        'name': 'Clear explanations', 'judgment': 'mixed', 'notes': 'Regressed on detail.',
        'comparison': {'status': 'complete', 'runtime': 'Local llama.cpp Chat engine',
            'current_config': {'model_path': '/local/models/current.gguf', 'max_tokens': 4000},
            'candidate_config': {'model_path': '/local/models/base.gguf', 'max_tokens': 4000},
            'examples': [{'prompt': 'Fresh question\nSecond paragraph', 'response': '',
                          'current_output': answer, 'candidate_output': answer + '\nCandidate detail.'}]},
        'comparison_history': [{'status': 'cancelled', 'error': 'Stopped by user',
                               'examples': [{'prompt': 'Earlier question', 'current_output': 'Saved partial',
                                             'current_error': 'Output limit reached'}]}],
        'comparison_prompts': [{'prompt': 'Unsaved-for-comparison next question', 'response': 'Optional rubric'}],
        'conversion': {'status': 'complete', 'adapter_gguf': '/local/run/adapter.gguf',
                       'adapter_gguf_sha256': 'verified-conversion-hash'},
    }
    repo.store.set_setting('training_review_' + ident, review)
    destination = tmp_path / 'review.json'

    exporter()(repo, ident, destination)

    text = destination.read_text(encoding='utf-8')
    saved = json.loads(text)
    assert saved['format'] == 'letracode-version-review'
    assert saved['format_version'] == 1
    assert saved['exported_at']
    assert saved['run'] == repo.run(ident)
    assert saved['review'] == review
    assert saved['effective_report']['adapter_gguf'] == '/local/run/adapter.gguf'
    assert saved['effective_report']['adapter_gguf_sha256'] == 'verified-conversion-hash'
    assert saved['effective_report']['conversion_error'] == ''
    assert saved['run']['report']['conversion_error'] == 'Conversion was unavailable during training'
    assert 'café' in text and '\n  "run": {' in text


@pytest.mark.parametrize('review', [None, {}, {'name': 'Historical version', 'judgment': 'unreviewed'},
    {'comparison': {'status': 'incomplete', 'error': 'Candidate failed to load',
                    'examples': [{'prompt': 'Fresh question', 'current_output': 'Full current answer',
                                  'candidate_output': 'Partial candidate', 'candidate_error': 'Output limit'}]}}])
def test_export_retains_empty_historical_and_incomplete_reviews_honestly(version, tmp_path, review):
    repo, ident = version
    if review is not None:
        repo.store.set_setting('training_review_' + ident, review)
    destination = tmp_path / 'historical-review.json'

    exporter()(repo, ident, destination)

    saved = json.loads(destination.read_text(encoding='utf-8'))
    assert saved['review'] == (review or {})
    assert saved['effective_report'] == saved['run']['report']


def test_export_reads_only_selected_run_and_review_without_mutating_data(version, tmp_path):
    repo, ident = version
    chat = repo.store.create_chat('Private conversation marker')
    repo.store.add_message(chat, 'user', 'Private conversation body marker')
    repo.store.set_setting('unrelated_private_setting', 'Unrelated secret marker')
    repo.store.set_setting('training_review_' + '0' * 32, {'notes': 'Other version secret marker'})
    directory = repo.run_directory(ident)
    (directory / 'adapter.gguf').write_bytes(b'Private model binary marker')
    (directory / 'report.json').write_text('{"original":"immutable disk report"}', encoding='utf-8')
    before = {p.relative_to(repo.store.directory): p.read_bytes()
              for p in repo.store.directory.rglob('*') if p.is_file()}
    destination = tmp_path / 'selected-review.json'

    exporter()(repo, ident, destination)

    after = {p.relative_to(repo.store.directory): p.read_bytes()
             for p in repo.store.directory.rglob('*') if p.is_file()}
    assert after == before
    text = destination.read_text(encoding='utf-8')
    for private in ('Private conversation marker', 'Private conversation body marker',
                    'Unrelated secret marker', 'Other version secret marker', 'Private model binary marker'):
        assert private not in text


def test_export_refuses_to_replace_existing_files_or_links(version, tmp_path):
    repo, ident = version
    destination = tmp_path / 'existing.json'; destination.write_bytes(b'Keep existing export')
    target = tmp_path / 'target.json'; target.write_bytes(b'Keep linked file')
    link = tmp_path / 'link.json'; link.symlink_to(target)

    for selected in (destination, link):
        with pytest.raises((FileExistsError, ValueError)):
            exporter()(repo, ident, selected)

    assert destination.read_bytes() == b'Keep existing export'
    assert target.read_bytes() == b'Keep linked file'
    assert link.is_symlink()
    assert not list(tmp_path.glob('.letracode-version-review-*'))


def test_export_rejects_app_data_and_missing_run_before_creating_output(version, tmp_path):
    repo, ident = version
    destination = repo.store.directory / 'review.json'
    with pytest.raises(ValueError):
        exporter()(repo, ident, destination)
    assert not destination.exists()

    destination = tmp_path / 'missing.json'
    with pytest.raises(ValueError, match='exist'):
        exporter()(repo, '0' * 32, destination)
    assert not destination.exists()
