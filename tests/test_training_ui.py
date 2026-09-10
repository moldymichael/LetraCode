import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtWidgets import QApplication

from letracode.store import Store
from letracode.ui import MainWindow


def test_separate_fine_tuning_workspace_reviews_examples_and_persists(tmp_path):
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path / 'data')
    window = MainWindow(store)
    assert [window.workspaces.tabText(i) for i in range(window.workspaces.count())] == ['Chat', 'Knowledge', 'Improve', 'Settings']
    panel = window.training_panel
    panel.prompt.setPlainText('How should this answer be written?')
    panel.response.setPlainText('A reviewed, desired answer.')
    panel.save_example()
    assert len(panel.repository.examples()) == 1
    assert not panel.repository.examples()[0]['approved']
    panel.approve_example()
    assert panel.repository.examples()[0]['approved']
    panel.response.setPlainText('An edited answer needs review again.')
    panel.save_example()
    assert not panel.repository.examples()[0]['approved']
    window.close()
    reopened = MainWindow(Store(tmp_path / 'data'))
    assert reopened.training_panel.repository.examples()[0]['response'] == 'An edited answer needs review again.'
    reopened.close()


def test_learn_from_reply_creates_reviewable_draft_without_training(tmp_path):
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path / 'data')
    chat = store.create_chat('Example')
    store.add_message(chat, 'user', 'What should I do?')
    store.add_message(chat, 'assistant', 'Check the evidence first.')
    window = MainWindow(store)
    window.select_chat(chat)
    window.learn_from_reply()
    panel = window.training_panel
    assert window.workspaces.currentWidget() is panel
    assert panel.prompt.toPlainText() == 'What should I do?'
    assert panel.response.toPlainText() == 'Check the evidence first.'
    panel.save_example()
    saved = panel.repository.examples()[0]
    assert saved['source'].startswith('chat:')
    assert not saved['approved']
    assert panel.repository.runs() == []
    window.close()


def test_busy_chat_disables_training_start_and_preserves_example_draft(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(Store(tmp_path / 'data'))
    panel = window.training_panel
    panel.prompt.setPlainText('Draft prompt')
    window.set_busy(True)
    assert not panel.start_button.isEnabled()
    assert not window.learn_button.isEnabled()
    window.set_busy(False)
    assert panel.prompt.toPlainText() == 'Draft prompt'
    assert panel.start_button.isEnabled()
    window.close()


def select_example_id(panel, ident):
    from PySide6.QtCore import Qt
    for index in range(panel.examples_list.count()):
        item = panel.examples_list.item(index)
        if item.data(Qt.ItemDataRole.UserRole) == ident:
            panel.examples_list.setCurrentItem(item)
            return
    raise AssertionError(f'Missing example {ident}')


def test_unsaved_example_edits_survive_navigation_reopen_and_clear_approval(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(Store(tmp_path / 'data'))
    panel = window.training_panel
    a = panel.repository.save_example('A', 'Original A', approved=True)
    b = panel.repository.save_example('B', 'Original B')
    panel.refresh_examples()
    select_example_id(panel, a['id'])
    panel.response.setPlainText('Edited A')
    saved = next(row for row in panel.repository.examples() if row['id'] == a['id'])
    assert saved['response'] == 'Original A'
    assert not saved['approved']
    select_example_id(panel, b['id'])
    select_example_id(panel, a['id'])
    assert panel.response.toPlainText() == 'Edited A'
    select_example_id(panel, b['id'])
    window.close()
    reopened = MainWindow(Store(tmp_path / 'data'))
    select_example_id(reopened.training_panel, a['id'])
    assert reopened.training_panel.response.toPlainText() == 'Edited A'
    reopened.close()


def test_new_drafts_remain_selectable_after_learn_from_reply_and_reopen(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(Store(tmp_path / 'data'))
    chat = window.store.create_chat('Example')
    window.store.add_message(chat, 'user', 'Chat prompt')
    window.store.add_message(chat, 'assistant', 'Chat answer')
    window.select_chat(chat)
    panel = window.training_panel
    panel.prompt.setPlainText('Unfinished prompt')
    panel.response.setPlainText('Unfinished answer')
    window.learn_from_reply()
    assert panel.prompt.toPlainText() == 'Chat prompt'
    window.close()
    reopened = MainWindow(Store(tmp_path / 'data'))
    panel = reopened.training_panel
    assert panel.prompt.toPlainText() == 'Chat prompt'
    matches = [panel.examples_list.item(i) for i in range(panel.examples_list.count())
               if 'Unfinished prompt' in panel.examples_list.item(i).text()]
    assert len(matches) == 1
    panel.examples_list.setCurrentItem(matches[0])
    assert panel.response.toPlainText() == 'Unfinished answer'
    assert panel.repository.examples() == []
    reopened.close()


def test_deleting_example_does_not_resurrect_its_draft(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(Store(tmp_path / 'data'))
    panel = window.training_panel
    panel.prompt.setPlainText('Delete me'); panel.response.setPlainText('Answer')
    panel.save_example()
    panel.response.setPlainText('Edited answer')
    panel.delete_example()
    window.close()
    reopened = MainWindow(Store(tmp_path / 'data'))
    assert reopened.training_panel.repository.examples() == []
    assert reopened.training_panel.examples_list.count() == 0
    assert reopened.training_panel.prompt.toPlainText() == ''
    reopened.close()


def test_new_training_setup_recommends_qlora_and_discovers_local_environment(tmp_path, monkeypatch):
    from pathlib import Path
    training_python = tmp_path / '.local/share/letracode-training-qlora/bin/python'
    training_python.parent.mkdir(parents=True)
    training_python.touch()
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    app = QApplication.instance() or QApplication([])
    window = MainWindow(Store(tmp_path / 'data'))
    panel = window.training_panel
    config = panel.configuration()
    assert config.training_method == 'qlora'
    assert config.device == 'cuda'
    assert config.gradient_accumulation_steps == 4
    assert config.gradient_checkpointing is True
    assert config.python_executable == str(training_python)
    assert not panel.fields['device'].isEnabled()
    assert '4 examples' in panel.effective_batch.text()
    window.close()


def test_saved_legacy_training_setup_is_not_silently_changed(tmp_path):
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path / 'data')
    store.set_setting('training_config', {'python_executable': '/custom/python',
        'base_model': '/custom/model', 'device': 'cpu', 'batch_size': 2})
    window = MainWindow(store)
    panel = window.training_panel
    config = panel.configuration()
    assert config.training_method == 'lora'
    assert config.device == 'cpu'
    assert config.gradient_accumulation_steps == 1
    assert config.gradient_checkpointing is False
    assert config.python_executable == '/custom/python'
    assert panel.fields['device'].isEnabled()
    window.close()


def test_switching_to_qlora_selects_cuda_and_persists_all_settings_before_training(tmp_path):
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path / 'data')
    store.set_setting('training_config', {'device': 'cpu'})
    window = MainWindow(store)
    panel = window.training_panel
    mode = panel.fields['training_method']
    mode.setCurrentIndex(mode.findData('qlora'))
    assert panel.configuration().device == 'cuda'
    assert panel.configuration().gradient_checkpointing is True
    panel.fields['batch_size'].setValue(3)
    panel.fields['gradient_accumulation_steps'].setValue(5)
    panel.fields['python_executable'].setText('/selected/training/python')
    assert '15 examples' in panel.effective_batch.text()
    assert panel.repository.runs() == []
    window.close()
    reopened = MainWindow(Store(tmp_path / 'data'))
    restored = reopened.training_panel.configuration()
    assert restored.training_method == 'qlora'
    assert restored.device == 'cuda'
    assert restored.gradient_checkpointing is True
    assert restored.gradient_accumulation_steps == 5
    assert restored.batch_size == 3
    assert restored.python_executable == '/selected/training/python'
    reopened.close()


def test_version_results_show_training_precision_memory_and_legacy_runs(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(Store(tmp_path / 'data'))
    panel = window.training_panel
    run = {'id': 'test-version', 'status': 'succeeded', 'created': '2026-09-09',
        'config': {'training_method': 'qlora', 'device': 'cuda', 'batch_size': 1,
                   'gradient_accumulation_steps': 4},
        'report': {'base_loss': 2.5, 'candidate_loss': 2.3,
            'training_details': {'training_method': 'qlora', 'quantization': 'nf4-double',
                'compute_dtype': 'float16', 'effective_batch_size': 4,
                'gradient_checkpointing': True, 'target_modules': 'all-linear', 'device': 'cuda'},
            'memory': {'base_model_bytes': 1024**3, 'peak_allocated_bytes': 2 * 1024**3,
                       'peak_reserved_bytes': 3 * 1024**3}}}
    panel.show_run(run)
    text = panel.results.toPlainText()
    assert '4-bit QLoRA' in text
    assert 'NF4 with double quantization' in text
    assert 'float16' in text
    assert 'Effective batch: up to 4 examples' in text
    assert 'Base model footprint: 1.00 GiB' in text
    assert 'Peak GPU allocation: 2.00 GiB' in text
    assert 'same 4-bit base' in text
    run['config'] = {'device': 'cpu'}
    run['report'] = {'base_loss': 2.5, 'candidate_loss': 2.3}
    panel.show_run(run)
    legacy = panel.results.toPlainText()
    assert 'LoRA (full precision)' in legacy
    assert 'Memory measurements were not recorded' in legacy
    assert 'Base: 2.500000' in legacy
    window.close()
