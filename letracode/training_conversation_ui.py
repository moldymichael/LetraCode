"""Editable conversation cards with lossless, intentionally unvalidated drafts."""
from __future__ import annotations

import json
import uuid

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton, QScrollArea,
    QSizePolicy, QVBoxLayout, QWidget)


def _json_text(value):
    return json.dumps(value, ensure_ascii=False, indent=2)


def _json_object(text, label):
    try:
        value = json.loads(text)
    except (ValueError, RecursionError) as error:
        raise ValueError(f'{label}: enter a valid JSON object ({error})') from error
    if not isinstance(value, dict):
        raise ValueError(f'{label}: enter a JSON object, not a list or scalar')
    return value


def _text_editor(placeholder, accessible_name, height=90):
    field = QPlainTextEdit()
    field.setPlaceholderText(placeholder)
    field.setAccessibleName(accessible_name)
    field.setMinimumHeight(height)
    field.setMaximumHeight(height + 60)
    return field


def _button(text, callback, layout):
    button = QPushButton(text)
    button.clicked.connect(callback)
    layout.addWidget(button)
    return button


class ToolCallCard(QGroupBox):
    changed = Signal()

    def __init__(self, state=None, parent=None):
        super().__init__('Function call', parent)
        state = state or {}
        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        form = QFormLayout()
        self.name = QLineEdit(state.get('name', ''))
        self.name.setPlaceholderText('Function name')
        self.name.setAccessibleName('Called function name')
        self.call_id = QLineEdit(state.get('id', 'call_' + uuid.uuid4().hex[:8]))
        self.call_id.setAccessibleName('Function call ID')
        form.addRow('Function', self.name)
        form.addRow('Call ID', self.call_id)
        row.addLayout(form, 1)
        self.remove_button = QPushButton('Remove call')
        row.addWidget(self.remove_button, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(row)
        self.arguments = _text_editor('{"key": "value"}', 'Function arguments JSON', 70)
        self.arguments.setPlainText(state.get('arguments_text', '{}'))
        layout.addWidget(QLabel('Arguments (JSON object)'))
        layout.addWidget(self.arguments)
        self.result_button = QPushButton('Add or edit result')
        layout.addWidget(self.result_button)
        self.name.textChanged.connect(self.changed)
        self.call_id.textChanged.connect(self.changed)
        self.arguments.textChanged.connect(self.changed)

    def draft_state(self):
        return {'name': self.name.text(), 'id': self.call_id.text(),
                'arguments_text': self.arguments.toPlainText()}

    def tool_call(self):
        return {'id': self.call_id.text(), 'type': 'function', 'function': {
            'name': self.name.text(), 'arguments': _json_object(self.arguments.toPlainText(),
                f'Function {self.name.text() or "(unnamed)"} arguments')}}


class ToolDefinitionCard(QGroupBox):
    changed = Signal()

    def __init__(self, state=None, parent=None):
        super().__init__('Available function', parent)
        state = state or {}
        layout = QVBoxLayout(self)
        fields = QFormLayout()
        self.name = QLineEdit(state.get('name', ''))
        self.name.setPlaceholderText('For example: read_file')
        self.name.setAccessibleName('Available function name')
        self._description_present = state.get('description_present', 'description' in state)
        self.description = QLineEdit(state.get('description', ''))
        self.description.setAccessibleName('Available function description')
        fields.addRow('Function', self.name)
        fields.addRow('Description', self.description)
        self.strict = QComboBox()
        self.strict.addItem('Unspecified', None)
        self.strict.addItem('Require strict arguments', True)
        self.strict.addItem('Allow flexible arguments', False)
        self.strict.setCurrentIndex(self.strict.findData(state.get('strict')))
        fields.addRow('Argument matching', self.strict)
        layout.addLayout(fields)
        self.parameters = _text_editor('{"type": "object", "properties": {}}',
                                       'Function parameters JSON schema', 90)
        self.parameters.setPlainText(state.get('parameters_text', _json_text({'type': 'object', 'properties': {}})))
        layout.addWidget(QLabel('Parameters (JSON schema)'))
        layout.addWidget(self.parameters)
        self.remove_button = QPushButton('Remove function')
        layout.addWidget(self.remove_button)
        self.name.textChanged.connect(self.changed)
        self.description.textChanged.connect(self.changed)
        self.strict.currentIndexChanged.connect(self.changed)
        self.parameters.textChanged.connect(self.changed)

    def draft_state(self):
        return {'name': self.name.text(), 'description': self.description.text(),
                'description_present': self._description_present, 'strict': self.strict.currentData(),
                'parameters_text': self.parameters.toPlainText()}

    def definition(self):
        function = {'name': self.name.text(), 'parameters': _json_object(
            self.parameters.toPlainText(), f'Function {self.name.text() or "(unnamed)"} parameters')}
        if self._description_present or self.description.text():
            function['description'] = self.description.text()
        if self.strict.currentData() is not None:
            function['strict'] = self.strict.currentData()
        return {'type': 'function', 'function': function}


class MessageCard(QGroupBox):
    changed = Signal()
    result_requested = Signal(object)
    focus_requested = Signal(object)

    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.role = state['role']
        self.call_cards = []
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        layout = QVBoxLayout(self)
        self.controls = QWidget()
        controls = QHBoxLayout(self.controls)
        controls.setContentsMargins(0, 0, 0, 0)
        self.badge = QLabel()
        controls.addWidget(self.badge, 1)
        self.up_button = QPushButton('↑')
        self.up_button.setAccessibleName('Move message up')
        self.up_button.setToolTip('Move this message up')
        self.down_button = QPushButton('↓')
        self.down_button.setAccessibleName('Move message down')
        self.down_button.setToolTip('Move this message down')
        self.remove_button = QPushButton('Remove')
        self.remove_button.setAccessibleName('Remove message')
        for button in (self.up_button, self.down_button, self.remove_button):
            controls.addWidget(button)
        layout.addWidget(self.controls)
        if self.role == 'assistant':
            self.context_only = QCheckBox('Context only — do not learn this turn')
            self.context_only.setChecked(not state.get('train', True))
            self.context_only.toggled.connect(self.update_badge)
            self.context_only.toggled.connect(self.changed)
            layout.addWidget(self.context_only)
        if self.role == 'tool':
            self.tool_call_id = QComboBox()
            self.tool_call_id.setEditable(True)
            self.tool_call_id.setAccessibleName('Tool result call ID')
            self.tool_call_id.setCurrentText(state.get('tool_call_id', ''))
            linked = QFormLayout()
            linked.addRow('Result for call ID', self.tool_call_id)
            self.tool_name = QLineEdit(state.get('name', ''))
            self.tool_name.setPlaceholderText('Optional; must match the called function')
            self.tool_name.setAccessibleName('Tool result function name')
            linked.addRow('Function name', self.tool_name)
            self.tool_name.textChanged.connect(self.changed)
            layout.addLayout(linked)
            self.collapse = QCheckBox('Collapse tool result')
            self.collapse.setChecked(state.get('collapsed', True))
            layout.addWidget(self.collapse)
        placeholders = {'system': 'Instructions or background for this conversation.',
            'user': 'For example: Explain a metaphor to someone new to poetry.',
            'assistant': 'Write the reply you want Strand to learn. For example: A metaphor describes one thing as another. “Time is a river” suggests it keeps moving forward.',
            'tool': 'Recorded result returned by the function. This text is never executed.'}
        self.content = _text_editor(placeholders[self.role], self.role.title() + ' message text')
        self.content.setPlainText(state.get('content', ''))
        layout.addWidget(self.content)
        if self.role == 'tool':
            self.result_preview = QLabel()
            self.result_preview.setTextFormat(Qt.TextFormat.PlainText)
            self.result_preview.setWordWrap(True)
            layout.addWidget(self.result_preview)
            self.collapse.toggled.connect(self.update_collapse)
            self.collapse.toggled.connect(self.changed)
            self.content.textChanged.connect(self.update_collapse)
            self.tool_call_id.currentTextChanged.connect(self.changed)
            self.update_collapse()
        self.content.textChanged.connect(self.changed)
        if self.role == 'assistant':
            self.calls_layout = QVBoxLayout()
            layout.addLayout(self.calls_layout)
            self.add_call_button = _button('Add function call', self.add_call, layout)
            for call in state.get('tool_calls', []):
                self.add_call(call)
        self.update_badge()

    def set_advanced(self, advanced):
        has_details = self.role not in ('user', 'assistant') or bool(self.call_cards)
        if self.role == 'assistant':
            has_details = has_details or self.context_only.isChecked()
            self.context_only.setVisible(advanced or has_details)
            self.add_call_button.setVisible(advanced or has_details)
        self.controls.setVisible(advanced or has_details)
        self.content.setMaximumHeight(150 if advanced or has_details else 90)

    def update_badge(self, *_):
        learned = self.role == 'assistant' and not self.context_only.isChecked()
        self.badge.setText('Learn this turn' if learned else 'Context · not learned')

    def update_collapse(self, *_):
        collapsed = self.collapse.isChecked()
        self.content.setVisible(not collapsed)
        text = self.content.toPlainText()
        self.result_preview.setText((text[:180].replace('\n', ' ') + ('…' if len(text) > 180 else ''))
                                    or 'Empty tool result · expand to edit')
        self.result_preview.setVisible(collapsed)

    def add_call(self, state=None):
        # QPushButton.clicked passes a bool when this is called from the UI.
        call = ToolCallCard(state if isinstance(state, dict) else None, self)
        self.call_cards.append(call)
        self.calls_layout.addWidget(call)
        call.changed.connect(self.changed)
        call.remove_button.clicked.connect(lambda: self.remove_call(call))
        call.result_button.clicked.connect(lambda: self.result_requested.emit(call))
        self.changed.emit()
        if not isinstance(state, dict):
            self.focus_requested.emit(call.name)
        return call

    def remove_call(self, call):
        self.call_cards.remove(call)
        self.calls_layout.removeWidget(call)
        call.deleteLater()
        self.changed.emit()

    def draft_state(self):
        value = {'role': self.role, 'content': self.content.toPlainText()}
        if self.role == 'assistant':
            value['train'] = not self.context_only.isChecked()
            value['tool_calls'] = [call.draft_state() for call in self.call_cards]
        elif self.role == 'tool':
            value['tool_call_id'] = self.tool_call_id.currentText()
            if self.tool_name.text():
                value['name'] = self.tool_name.text()
            value['collapsed'] = self.collapse.isChecked()
        return value

    def message(self):
        value = {'role': self.role, 'content': self.content.toPlainText()}
        if self.role == 'assistant':
            value['train'] = not self.context_only.isChecked()
            if self.call_cards:
                value['tool_calls'] = [call.tool_call() for call in self.call_cards]
        elif self.role == 'tool':
            value['tool_call_id'] = self.tool_call_id.currentText()
            if self.tool_name.text():
                value['name'] = self.tool_name.text()
        return value


class ConversationEditor(QWidget):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.cards = []
        self.tool_cards = []
        self._loading = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.example_button = QPushButton('Show an example')
        self.example_button.setCheckable(True)
        self.example_help = QDialog(self)
        self.example_help.setWindowTitle('An example of clear, helpful teaching')
        self.example_help.resize(500, 260)
        example_layout = QVBoxLayout(self.example_help)
        example = QLabel('You say: Explain a metaphor to someone new to poetry.\n\n'
            'Strand should respond: A metaphor describes one thing as another. '
            '“Time is a river” suggests it keeps moving forward.\n\n'
            'You write both the request and the reply you want Strand to learn. '
            'Use your own wording in your example. Strand does not answer in the teaching editor.')
        example.setWordWrap(True)
        example.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        example.setAlignment(Qt.AlignmentFlag.AlignTop)
        help_scroll = QScrollArea()
        help_scroll.setWidgetResizable(True)
        help_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        help_scroll.setWidget(example)
        example_layout.addWidget(help_scroll, 1)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.example_help.reject)
        example_layout.addWidget(close)
        self.example_help.finished.connect(lambda _: self.example_button.setChecked(False))
        self.example_help.hide()
        self.example_button.toggled.connect(self.example_help.setVisible)
        options = QHBoxLayout()
        self.more_options = QCheckBox('More conversation options')
        self.more_options.setToolTip('Add individual messages, instructions, context-only turns and recorded function calls.')
        self.more_options.toggled.connect(self._apply_options)
        options.addWidget(self.more_options)
        options.addStretch()
        options.addWidget(self.example_button)
        self.followup_button = _button('Add a follow-up', self.add_followup, options)
        layout.addLayout(options)
        self.advanced_toolbar = QWidget()
        toolbar = QHBoxLayout(self.advanced_toolbar)
        toolbar.setContentsMargins(0, 0, 0, 0)
        self.new_role = QComboBox()
        for label, role in (('You say', 'user'), ('Strand should respond', 'assistant'),
                            ('Tool result', 'tool'), ('System instructions', 'system')):
            self.new_role.addItem(label, role)
        self.new_role.setAccessibleName('Role for new conversation message')
        toolbar.addWidget(self.new_role)
        _button('Add message', lambda: self.add_message(self.new_role.currentData()), toolbar)
        toolbar.addStretch()
        layout.addWidget(self.advanced_toolbar)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        page = QWidget()
        self.messages_layout = QVBoxLayout(page)
        self.tools_group = QGroupBox('Available functions')
        self.tools_layout = QVBoxLayout(self.tools_group)
        hint = QLabel('Describe the functions available in this example. Recorded calls and results are training data; they are never run.')
        hint.setWordWrap(True)
        self.tools_layout.addWidget(hint)
        self.add_tool_button = _button('Add function definition', lambda: self.add_tool(), self.tools_layout)
        self.messages_layout.addWidget(self.tools_group)
        self.messages_layout.addStretch()
        self.scroll.setWidget(page)
        layout.addWidget(self.scroll, 1)
        self.set_conversation([{'role': 'user', 'content': ''}, {'role': 'assistant', 'content': ''}], [])

    @staticmethod
    def _message_state(message):
        state = dict(message)
        if message['role'] == 'assistant':
            state['tool_calls'] = [{'id': call['id'], 'name': call['function']['name'],
                'arguments_text': _json_text(call['function']['arguments'])}
                for call in message.get('tool_calls', [])]
        return state

    @staticmethod
    def _tool_state(definition):
        function = definition['function']
        return {'name': function['name'], 'description': function.get('description', ''),
                'description_present': 'description' in function, 'strict': function.get('strict'),
                'parameters_text': _json_text(function['parameters'])}

    def set_conversation(self, messages, tools=None):
        self.load_draft_state({'messages': [self._message_state(message) for message in messages],
                              'tools': [self._tool_state(tool) for tool in (tools or [])]})

    def load_draft_state(self, state):
        self._loading = True
        try:
            for card in self.cards:
                self.messages_layout.removeWidget(card)
                card.deleteLater()
            self.cards = []
            for card in self.tool_cards:
                self.tools_layout.removeWidget(card)
                card.deleteLater()
            self.tool_cards = []
            for message in state.get('messages', []):
                self._add_message_state(message)
            for tool in state.get('tools', []):
                self._add_tool_state(tool)
            advanced = (self._has_technical_data() or len(self.cards) != 2
                        or [card.role for card in self.cards] != ['user', 'assistant'])
            self.more_options.setChecked(advanced)
            self._update_cards()
        finally:
            self._loading = False
        self.changed.emit()

    def add_message(self, role, message=None):
        if role not in ('system', 'user', 'assistant', 'tool'):
            raise ValueError('Choose system, user, assistant or tool for a message')
        state = self._message_state(message) if message else {'role': role, 'content': ''}
        if role == 'tool' and not message:
            state['collapsed'] = False
            used = {card.tool_call_id.currentText() for card in self.cards if card.role == 'tool'}
            state['tool_call_id'] = next((call.call_id.text() for card in self.cards
                for call in card.call_cards if call.call_id.text() not in used), '')
        card = self._add_message_state(state, index=0 if role == 'system' else None)
        self._edited()
        self._focus_field(card.content)
        return card

    def add_followup(self):
        # Persist only the complete pair so autosave never records half an action.
        user = self._add_message_state({'role': 'user', 'content': ''})
        self._add_message_state({'role': 'assistant', 'content': ''})
        self._edited()
        self._focus_field(user.content)

    def _focus_field(self, field):
        def reveal():
            field.setFocus(Qt.FocusReason.OtherFocusReason)
            self.scroll.ensureWidgetVisible(field, 0, 12)
        QTimer.singleShot(0, field, reveal)

    def _add_message_state(self, state, index=None):
        card = MessageCard(state, self)
        index = len(self.cards) if index is None else index
        self.cards.insert(index, card)
        self.messages_layout.insertWidget(index, card)
        card.changed.connect(self._edited)
        card.result_requested.connect(lambda call: self.add_result(card, call))
        card.focus_requested.connect(self._focus_field)
        card.remove_button.clicked.connect(lambda: self.remove_message(card))
        card.up_button.clicked.connect(lambda: self.move_message(card, -1))
        card.down_button.clicked.connect(lambda: self.move_message(card, 1))
        return card

    def add_result(self, assistant, call):
        ident = call.call_id.text()
        for card in self.cards:
            if card.role == 'tool' and card.tool_call_id.currentText() == ident:
                card.collapse.setChecked(False)
                self._focus_field(card.content)
                return card
        # Put new results in call order, ahead of any existing final reply.
        order = [item.call_id.text() for item in assistant.call_cards]
        index = self.cards.index(assistant) + 1
        while index < len(self.cards) and self.cards[index].role == 'tool':
            other = self.cards[index].tool_call_id.currentText()
            if other not in order or order.index(other) > order.index(ident):
                break
            index += 1
        result = self._add_message_state({'role': 'tool', 'tool_call_id': ident,
                                         'content': '', 'collapsed': False}, index=index)
        self._edited()
        self._focus_field(result.content)
        return result

    def remove_message(self, card):
        self.cards.remove(card)
        self.messages_layout.removeWidget(card)
        card.deleteLater()
        self._edited()

    def move_message(self, card, direction):
        index = self.cards.index(card)
        destination = max(0, min(len(self.cards) - 1, index + direction))
        if destination == index:
            return
        self.cards.insert(destination, self.cards.pop(index))
        self.messages_layout.removeWidget(card)
        self.messages_layout.insertWidget(destination, card)
        self._edited()

    def add_tool(self, definition=None):
        card = self._add_tool_state(self._tool_state(definition) if definition else {})
        self._edited()
        self._focus_field(card.name)
        return card

    def _add_tool_state(self, state):
        card = ToolDefinitionCard(state, self)
        self.tool_cards.append(card)
        self.tools_layout.insertWidget(self.tools_layout.count() - 1, card)
        card.changed.connect(self._edited)
        card.remove_button.clicked.connect(lambda: self.remove_tool(card))
        return card

    def remove_tool(self, card):
        self.tool_cards.remove(card)
        self.tools_layout.removeWidget(card)
        card.deleteLater()
        self._edited()

    def _update_cards(self):
        # Keep even invalid/renamed IDs editable instead of silently relinking results.
        call_ids = [call.call_id.text() for card in self.cards for call in card.call_cards]
        for index, card in enumerate(self.cards):
            title = {'user': 'You say', 'assistant': 'Strand should respond',
                     'system': 'System instructions', 'tool': 'Tool result'}[card.role]
            card.setTitle((f'{index + 1}. ' if len(self.cards) > 2 else '') + title)
            card.up_button.setEnabled(index > 0)
            card.down_button.setEnabled(index < len(self.cards) - 1)
            if card.role == 'tool':
                selector = card.tool_call_id
                selected = selector.currentText()
                selector.blockSignals(True)
                selector.clear()
                selector.addItems(call_ids)
                selector.setCurrentText(selected)
                selector.blockSignals(False)
        self._apply_options()

    def _has_technical_data(self):
        return bool(self.tool_cards) or any(card.role not in ('user', 'assistant')
            or card.call_cards or (card.role == 'assistant' and card.context_only.isChecked())
            for card in self.cards)

    def _apply_options(self, *_):
        advanced = self.more_options.isChecked()
        self.advanced_toolbar.setVisible(advanced)
        self.tools_group.setVisible(advanced or bool(self.tool_cards))
        for card in self.cards:
            card.set_advanced(advanced)

    def _edited(self, *_):
        if self._loading:
            return
        if self._has_technical_data():
            self.more_options.setChecked(True)
        self._update_cards()
        self.changed.emit()

    def draft_state(self):
        return {'messages': [card.draft_state() for card in self.cards],
                'tools': [card.draft_state() for card in self.tool_cards]}

    def has_draft_content(self):
        return self.draft_state() != {'messages': [{'role': 'user', 'content': ''},
            {'role': 'assistant', 'content': '', 'train': True, 'tool_calls': []}], 'tools': []}

    def conversation(self):
        return {'schema_version': 2, 'messages': [card.message() for card in self.cards],
                'tools': [card.definition() for card in self.tool_cards]}

    def text_editor(self, role, *, last=False):
        cards = [card for card in self.cards if card.role == role]
        if not cards:
            return None
        return cards[-1 if last else 0].content
