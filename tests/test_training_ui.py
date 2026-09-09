import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtWidgets import QApplication

from letracode.store import Store
from letracode.ui import MainWindow


def test_separate_fine_tuning_workspace_reviews_examples_and_persists(tmp_path):
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path / 'data')
    window = MainWindow(store)
    assert [window.workspaces.tabText(i) for i in range(window.workspaces.count())] == ['Chat', 'Fine-Tuning']
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
