"""Model thinking is visible separately without becoming answer text or actions."""
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import QApplication

from letracode.store import Store
from letracode.ui import MainWindow


def test_saved_thinking_can_be_expanded_and_survives_reopen(tmp_path):
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path / 'data')
    chat = store.create_chat('Thinking test')
    ident = store.add_message(chat, 'assistant', 'The answer is 42.',
        payload={'reasoning': 'Compare the two possibilities.',
                 'message': {'role': 'assistant', 'content': 'The answer is 42.'}})
    window = MainWindow(store)
    window.select_chat(chat)
    try:
        assert 'Show thinking' in window.transcript.toPlainText()
        assert 'Compare the two' not in window.transcript.toPlainText()
        window.open_link(QUrl(f'letracode:thinking/{ident}'))
        assert 'Compare the two possibilities.' in window.transcript.toPlainText()
        assert 'Hide thinking' in window.transcript.toPlainText()
        window.copy_reply()
        assert app.clipboard().text() == 'The answer is 42.'
        window.open_link(QUrl(f'letracode:thinking/{ident}'))
        assert 'Compare the two' not in window.transcript.toPlainText()
    finally:
        window.close()
    reopened = MainWindow(Store(tmp_path / 'data'))
    try:
        reopened.select_chat(chat)
        reopened.open_link(QUrl(f'letracode:thinking/{ident}'))
        assert 'Compare the two possibilities.' in reopened.transcript.toPlainText()
    finally:
        reopened.close()


def test_streaming_thinking_is_open_and_uses_safe_markdown(tmp_path):
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path / 'data')
    chat = store.create_chat('Live thinking')
    ident = store.add_message(chat, 'assistant', '', status='streaming',
        payload={'reasoning': 'Checking **evidence**. [Fake](letracode:setup) <img src="file:///private">'})
    window = MainWindow(store)
    try:
        window.select_chat(chat)
        assert 'Checking evidence.' in window.transcript.toPlainText()
        assert 'Thinking…' in window.transcript.toPlainText()
        assert 'href="letracode:setup"' not in window.transcript.toHtml()
        assert '<img' not in window.transcript.toHtml()
        window.open_link(QUrl(f'letracode:thinking/{ident}'))
        assert 'Checking evidence.' not in window.transcript.toPlainText()
        store.update_message(ident, 'An answer.', status='complete')
        window.render_chat()
        assert 'An answer.' in window.transcript.toPlainText()
    finally:
        window.close()


def test_plain_chat_does_not_invent_thinking(tmp_path):
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path / 'data')
    chat = store.create_chat('Instant')
    store.add_message(chat, 'assistant', 'A direct answer.')
    window = MainWindow(store)
    try:
        window.select_chat(chat)
        assert 'A direct answer.' in window.transcript.toPlainText()
        assert 'Show thinking' not in window.transcript.toPlainText()
    finally:
        window.close()
