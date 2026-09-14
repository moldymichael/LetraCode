"""The teaching path stays simple without losing structured example data."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtWidgets import QApplication
from letracode.training_conversation_ui import ConversationEditor


@pytest.fixture
def editor():
    app = QApplication.instance() or QApplication([])
    view = ConversationEditor()
    view.resize(660, 460)
    view.show()
    app.processEvents()
    yield view
    view.close()


def test_fresh_example_exposes_both_teaching_fields_without_technical_controls(editor):
    assert not editor.more_options.isChecked()
    assert not editor.new_role.isVisible()
    assert not editor.cards[1].context_only.isVisible()
    assert not editor.cards[1].add_call_button.isVisible()
    assert not editor.tools_group.isVisible()
    assert all(card.content.isVisible() for card in editor.cards)
    editor.more_options.setChecked(True)
    assert editor.new_role.isVisible()
    assert editor.cards[1].context_only.isVisible()
    assert editor.cards[1].add_call_button.isVisible()


def test_followup_adds_complete_pair_once_and_focuses_new_question(editor):
    editor.cards[0].content.setPlainText('Original question')
    editor.cards[1].content.setPlainText('Original answer')
    observed = []
    editor.changed.connect(lambda: observed.append(editor.draft_state()))
    editor.followup_button.click()
    QApplication.processEvents()
    assert [card.role for card in editor.cards] == ['user', 'assistant', 'user', 'assistant']
    assert editor.cards[0].content.toPlainText() == 'Original question'
    assert editor.cards[1].content.toPlainText() == 'Original answer'
    assert len(observed) == 1
    assert len(observed[0]['messages']) == 4
    assert editor.cards[2].content.hasFocus()
    top = editor.cards[2].content.mapTo(editor.scroll.viewport(), editor.cards[2].content.rect().topLeft())
    assert 0 <= top.y() < editor.scroll.viewport().height()


def test_demonstration_is_read_only_and_never_replaces_a_draft(editor):
    editor.cards[0].content.setPlainText('My unfinished question')
    before = editor.draft_state()
    changes = []
    editor.changed.connect(lambda: changes.append(True))
    editor.example_button.click()
    assert editor.example_help.isVisible()
    assert editor.draft_state() == before
    editor.example_button.click()
    assert not editor.example_help.isVisible()
    assert editor.draft_state() == before
    assert changes == []


def test_loaded_structured_data_reveals_options_and_cannot_be_hidden(editor):
    state = {'messages': [
        {'role': 'system', 'content': 'Use this background.'},
        {'role': 'user', 'content': 'Question'},
        {'role': 'assistant', 'content': 'Context', 'train': False, 'tool_calls': [
            {'name': 'read', 'id': 'one', 'arguments_text': '{unfinished'}]},
        {'role': 'tool', 'content': 'Result', 'tool_call_id': 'one', 'collapsed': False},
        {'role': 'assistant', 'content': 'Desired answer', 'train': True, 'tool_calls': []}],
        'tools': [{'name': 'read', 'description': '', 'description_present': False,
                   'strict': None, 'parameters_text': '{schema draft'}]}
    editor.load_draft_state(state)
    assert editor.more_options.isChecked()
    editor.more_options.setChecked(False)
    QApplication.processEvents()
    assert editor.cards[2].context_only.isVisible()
    assert editor.cards[2].call_cards[0].isVisible()
    assert editor.tools_group.isVisible()
    assert editor.draft_state() == state


def test_new_tool_result_opens_for_editing_and_system_inserts_first(editor):
    result = editor.add_message('tool')
    QApplication.processEvents()
    assert not result.collapse.isChecked()
    assert result.content.isVisible()
    assert result.content.hasFocus()
    system = editor.add_message('system')
    QApplication.processEvents()
    assert editor.cards[0] is system
    assert system.content.hasFocus()
    assert len(editor.cards) == 4


def test_loaded_tool_result_stays_collapsed(editor):
    editor.set_conversation([{'role': 'tool', 'tool_call_id': 'one', 'content': 'Recorded output'}])
    QApplication.processEvents()
    assert editor.cards[0].collapse.isChecked()
    assert editor.cards[0].result_preview.isVisible()


def test_add_result_links_parallel_calls_in_order_before_the_final_answer(editor):
    calls = [{'id': ident, 'type': 'function', 'function': {'name': 'read', 'arguments': {}}}
             for ident in ('one', 'two')]
    editor.set_conversation([{'role': 'user', 'content': 'Read both files.'},
        {'role': 'assistant', 'content': '', 'tool_calls': calls},
        {'role': 'assistant', 'content': 'The final answer is kept.'}])
    # Starting with the second result must not force a new answer or lose data.
    first, second = editor.cards[1].call_cards
    second.result_button.click()
    first.result_button.click()
    QApplication.processEvents()
    assert [card.role for card in editor.cards] == ['user', 'assistant', 'tool', 'tool', 'assistant']
    assert [card.tool_call_id.currentText() for card in editor.cards[2:4]] == ['one', 'two']
    assert editor.cards[4].content.toPlainText() == 'The final answer is kept.'
    assert editor.cards[2].content.hasFocus()
    editor.cards[2].content.setPlainText('First result')
    first.result_button.click()
    QApplication.processEvents()
    assert len(editor.cards) == 5
    assert editor.cards[2].content.toPlainText() == 'First result'
    assert not editor.cards[2].collapse.isChecked()


def test_example_help_does_not_shrink_the_teaching_view_and_reopens_after_close(editor):
    editor.resize(540, 290)
    QApplication.processEvents()
    before = editor.scroll.viewport().size()
    editor.example_button.click()
    QApplication.processEvents()
    assert editor.scroll.viewport().size() == before
    editor.example_help.close()
    assert not editor.example_button.isChecked()
    editor.example_button.click()
    assert editor.example_help.isVisible()
