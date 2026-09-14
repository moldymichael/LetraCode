"""Exercise conversation editing through the real panel and persistent store."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtWidgets import QApplication

from letracode.store import Store
from letracode.ui import MainWindow


TOOLS = [{'type': 'function', 'function': {'name': 'lookup', 'description': 'Read a fact',
          'parameters': {'type': 'object', 'properties': {'key': {'type': 'string'}}}}}]
MESSAGES = [
    {'role': 'system', 'content': 'Use the supplied facts.'},
    {'role': 'user', 'content': 'Find two facts.'},
    {'role': 'assistant', 'content': 'I will check.', 'train': False,
     'tool_calls': [
         {'id': 'first', 'type': 'function', 'function': {'name': 'lookup', 'arguments': {'key': 'one'}}},
         {'id': 'second', 'type': 'function', 'function': {'name': 'lookup', 'arguments': {'key': 'two'}}}]},
    {'role': 'tool', 'tool_call_id': 'first', 'content': 'First fact'},
    {'role': 'tool', 'tool_call_id': 'second', 'content': 'Second fact'},
    {'role': 'assistant', 'content': 'Both facts are now available.', 'train': True},
]


@pytest.fixture
def panel_window(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(Store(tmp_path / 'data'))
    yield window.training_panel, window
    window.close()


def test_conversation_cards_save_reload_calls_results_and_context_targets(panel_window, tmp_path):
    panel, window = panel_window
    editor = panel.conversation
    editor.set_conversation(MESSAGES, TOOLS)
    assert editor.cards[2].context_only.isChecked()
    assert len(editor.cards[2].call_cards) == 2
    assert editor.cards[3].collapse.isChecked()
    editor.cards[3].collapse.setChecked(False)
    editor.cards[3].content.setPlainText('Corrected first fact')
    panel.save_example()
    row = panel.repository.examples()[0]
    assert row['messages'][3]['content'] == 'Corrected first fact'
    assert row['messages'][2]['train'] is False
    assert row['messages'][2]['tool_calls'][1]['function']['arguments'] == {'key': 'two'}
    assert row['tools'] == TOOLS
    panel.approve_example()
    assert panel.repository.examples()[0]['approved']
    window.close()
    reopened = MainWindow(Store(tmp_path / 'data'))
    try:
        restored = reopened.training_panel.conversation
        assert restored.cards[3].content.toPlainText() == 'Corrected first fact'
        assert not restored.cards[3].collapse.isChecked()
        assert len(restored.cards[2].call_cards) == 2
        assert restored.tool_cards[0].name.text() == 'lookup'
    finally:
        reopened.close()


def test_invalid_arguments_and_schema_drafts_survive_switch_and_restart(panel_window, tmp_path):
    panel, window = panel_window
    panel.conversation.set_conversation(MESSAGES, TOOLS)
    panel.save_example()
    ident = panel.example_id
    panel.conversation.cards[2].call_cards[0].arguments.setPlainText('{"key":')
    panel.conversation.tool_cards[0].parameters.setPlainText('{broken schema')
    panel.new_example()
    item = next(panel.examples_list.item(i) for i in range(panel.examples_list.count())
                if panel.examples_list.item(i).data(256) == ident)
    panel.examples_list.setCurrentItem(item)
    assert panel.conversation.cards[2].call_cards[0].arguments.toPlainText() == '{"key":'
    window.close()
    reopened = MainWindow(Store(tmp_path / 'data'))
    try:
        editor = reopened.training_panel.conversation
        assert editor.cards[2].call_cards[0].arguments.toPlainText() == '{"key":'
        assert editor.tool_cards[0].parameters.toPlainText() == '{broken schema'
        with pytest.raises(ValueError, match='arguments'):
            editor.conversation()
    finally:
        reopened.close()


@pytest.mark.parametrize('change', ['context', 'call', 'result', 'schema', 'add', 'remove', 'reorder', 'split'])
def test_every_substantive_edit_immediately_revokes_saved_approval(panel_window, change):
    panel, _ = panel_window
    panel.conversation.set_conversation(MESSAGES, TOOLS)
    panel.approve_example()
    assert panel.repository.examples()[0]['approved']
    editor = panel.conversation
    if change == 'context':
        editor.cards[2].context_only.setChecked(False)
    elif change == 'call':
        editor.cards[2].call_cards[0].arguments.setPlainText('{invalid')
    elif change == 'result':
        editor.cards[3].tool_call_id.setCurrentText('second')
    elif change == 'schema':
        editor.tool_cards[0].description.setText('A revised tool definition')
    elif change == 'add':
        editor.add_message('user')
    elif change == 'remove':
        editor.remove_message(editor.cards[0])
    elif change == 'reorder':
        editor.move_message(editor.cards[0], 1)
    else:
        panel.split.setCurrentIndex(1)
    row = panel.repository.examples()[0]
    assert not row['approved']
    assert row['messages'] == MESSAGES
    assert row['tools'] == TOOLS


def test_structural_only_drafts_are_retained_and_message_controls_work(panel_window, tmp_path):
    panel, window = panel_window
    editor = panel.conversation
    assistant = editor.cards[1]
    assistant.add_call()
    assistant.call_cards[0].call_id.setText('draft-call')
    result = editor.add_message('tool')
    assert result.tool_call_id.findText('draft-call') >= 0
    editor.move_message(result, -1)
    assert [card.role for card in editor.cards] == ['user', 'tool', 'assistant']
    editor.remove_message(editor.cards[0])
    editor.add_tool()
    panel.new_example()
    assert panel.examples_list.count() == 1
    window.close()
    reopened = MainWindow(Store(tmp_path / 'data'))
    try:
        panel = reopened.training_panel
        panel.examples_list.setCurrentItem(panel.examples_list.item(0))
        assert [card.role for card in panel.conversation.cards] == ['tool', 'assistant']
        assert panel.conversation.cards[1].call_cards[0].call_id.text() == 'draft-call'
        assert len(panel.conversation.tool_cards) == 1
    finally:
        reopened.close()


def test_optional_tool_metadata_is_preserved_without_inventing_fields(panel_window):
    panel, _ = panel_window
    messages = [dict(message) for message in MESSAGES]
    messages[3]['name'] = 'lookup'
    tools = [{'type': 'function', 'function': {'name': 'lookup', 'strict': False,
              'parameters': {'type': 'object'}}}]
    panel.conversation.set_conversation(messages, tools)
    record = panel.conversation.conversation()
    assert record['messages'][3]['name'] == 'lookup'
    assert record['tools'] == tools
    panel.new_example()
    panel.examples_list.setCurrentItem(panel.examples_list.item(0))
    assert panel.conversation.conversation()['tools'] == tools


def test_loading_and_collapsing_optional_metadata_keeps_approval(panel_window):
    panel, _ = panel_window
    messages = [dict(message) for message in MESSAGES]
    messages[3]['name'] = 'lookup'
    tools = [{'type': 'function', 'function': {'name': 'lookup', 'strict': True,
              'parameters': {'type': 'object'}}}]
    saved = panel.repository.save_example(messages=messages, tools=tools, approved=True)
    panel.refresh_examples()
    panel.examples_list.setCurrentItem(panel.examples_list.item(0))
    assert panel.example_id == saved['id']
    panel.conversation.cards[3].collapse.setChecked(False)
    assert panel.repository.examples()[0]['approved']
    unchanged = panel.save_example()
    assert unchanged['messages'] == messages
    assert unchanged['tools'] == tools


def test_invalid_save_keeps_saved_content_and_the_recoverable_draft(panel_window, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    panel, _ = panel_window
    panel.conversation.set_conversation(MESSAGES, TOOLS)
    panel.approve_example()
    panel.conversation.tool_cards[0].parameters.setPlainText('[1, 2]')
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: None)
    assert panel.save_example() is None
    assert 'parameters' in panel.progress.text()
    saved = panel.repository.examples()[0]
    assert saved['tools'] == TOOLS
    assert not saved['approved']
    assert panel.store.setting('training_editor')['conversation_editor']['tools'][0]['parameters_text'] == '[1, 2]'


def test_selecting_nonactive_recovered_legacy_draft_revokes_stale_approval(tmp_path):
    from letracode.training import TrainingRepository
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path / 'data')
    row = TrainingRepository(store).save_example('Question', 'Approved answer', approved=True)
    store.set_setting('training_editor_drafts', {row['id']: {
        'id': row['id'], 'prompt': 'Question', 'response': 'Recovered changed answer', 'split': 'train'}})
    window = MainWindow(store)
    try:
        panel = window.training_panel
        panel.examples_list.setCurrentItem(panel.examples_list.item(0))
        assert panel.response.toPlainText() == 'Recovered changed answer'
        assert not panel.repository.examples()[0]['approved']
    finally:
        window.close()
