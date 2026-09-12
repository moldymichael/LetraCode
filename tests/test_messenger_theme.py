"""The native messenger keeps chrome neutral and preferences out of chat data."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pytest
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QColorDialog
from letracode.store import Store
from letracode.ui import MainWindow


@pytest.fixture
def messenger(tmp_path):
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path / 'data')
    chat = store.create_chat('A conversation')
    store.add_message(chat, 'assistant', 'Hello. This is **saved text**.')
    store.add_message(chat, 'user', 'My message <not HTML>.')
    window = MainWindow(store)
    window.resize(1100, 780)
    window.show(); window.select_chat(chat); app.processEvents()
    yield app, store, window, chat
    window.close(); app.processEvents()


def test_bubbles_have_two_colors_and_the_window_has_only_neutral_chrome(messenger):
    app, store, window, chat = messenger
    assert hasattr(window, 'appearance_panel'), 'Native bubble color controls are missing'
    from letracode.messenger_theme import MessengerTheme
    theme = MessengerTheme(you='#ff7722', strand='#7755ff', surface='paper')
    window.apply_messenger_theme(theme)
    app.processEvents()
    for role in (QPalette.ColorRole.Window, QPalette.ColorRole.Base, QPalette.ColorRole.Button,
                 QPalette.ColorRole.Highlight, QPalette.ColorRole.Link, QPalette.ColorRole.Accent):
        color = window.palette().color(role)
        assert color.red() == color.green() == color.blue()
    assert '#ff7722' not in window.styleSheet() and '#7755ff' not in window.styleSheet()
    assert {color.name() for _, color in window.transcript.bubble_frames} == {'#ff7722', '#7755ff'}
    assert 'My message <not HTML>.' in window.transcript.toPlainText()


def test_hex_edits_swap_and_reopen_preserve_theme_and_chat(messenger):
    app, store, window, chat = messenger
    assert hasattr(window, 'appearance_panel')
    original = store.export_markdown(chat)
    panel = window.appearance_panel
    panel.you_hex.setText('#123456'); panel.you_hex.editingFinished.emit()
    panel.strand_hex.setText('#ABCDEF'); panel.strand_hex.editingFinished.emit()
    panel.swap_button.click()
    panel.surface.setCurrentIndex(panel.surface.findData('graphite'))
    assert window.messenger_theme.you == '#abcdef'
    assert window.messenger_theme.strand == '#123456'
    assert window.settings_appearance.you_hex.text().lower() == '#abcdef'
    assert store.export_markdown(chat) == original
    other = MainWindow(Store(store.directory))
    try:
        assert other.messenger_theme == window.messenger_theme
        assert other.messenger_theme.surface == 'graphite'
    finally:
        other.close()


def test_invalid_hex_and_cancelled_picker_do_not_change_saved_colors(messenger, monkeypatch):
    app, store, window, chat = messenger
    assert hasattr(window, 'appearance_panel')
    panel = window.appearance_panel
    original = window.messenger_theme
    panel.you_hex.setText('badhex'); panel.you_hex.editingFinished.emit()
    assert window.messenger_theme == original
    assert panel.you_hex.text().lower() == original.you
    monkeypatch.setattr(QColorDialog, 'getColor', lambda *a, **k: QColor())
    panel.you_picker.click()
    assert window.messenger_theme == original


def test_corrupt_theme_settings_fall_back_without_changing_other_settings(tmp_path):
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path / 'data')
    store.set_setting('messenger_theme', {'you': 'red; image:url(secret)', 'strand': None, 'surface': []})
    store.set_setting('computer', False)
    window = MainWindow(store)
    try:
        assert hasattr(window, 'messenger_theme')
        assert window.messenger_theme.you == '#dce5f3'
        assert window.messenger_theme.strand == '#eedce7'
        assert window.messenger_theme.surface == 'paper'
        assert store.setting('computer') is False
    finally:
        window.close()


def test_selected_text_and_unsent_draft_survive_theme_change(messenger):
    from PySide6.QtGui import QTextCursor
    app, store, window, chat = messenger
    assert hasattr(window, 'appearance_panel')
    window.composer.setPlainText('An unsent draft')
    cursor = window.transcript.textCursor(); cursor.select(QTextCursor.SelectionType.Document)
    window.transcript.setTextCursor(cursor)
    selected = window.transcript.textCursor().selectedText()
    window.appearance_panel.swap_button.click()
    assert window.composer.toPlainText() == 'An unsent draft'
    assert window.transcript.textCursor().selectedText() == selected


def test_normal_bubbles_keep_inset_text_and_fit_the_canvas(messenger):
    from PySide6.QtGui import QTextCursor
    app, store, window, chat = messenger
    browser = window.transcript
    assert hasattr(browser, 'bubble_rect')
    for frame, color in browser.bubble_frames:
        rect = browser.bubble_rect(frame)
        assert rect.left() >= 8
        assert rect.right() <= browser.viewport().width() - 8
        cursor = QTextCursor(browser.document()); cursor.setPosition(frame.firstPosition())
        text = browser.document().documentLayout().blockBoundingRect(cursor.block())
        assert text.left() >= rect.left() + 8
        assert text.top() >= rect.top() + 8


def test_graphite_permissions_and_selected_icons_remain_neutral_and_readable(messenger):
    from dataclasses import replace
    from PySide6.QtGui import QIcon
    app, store, window, chat = messenger
    window.apply_messenger_theme(replace(window.messenger_theme, surface='graphite'))
    app.processEvents()
    assert window.computer.palette().color(QPalette.ColorRole.WindowText).lightness() > 150
    image = window.new_chat_button.icon().pixmap(24, 24, QIcon.Mode.Selected).toImage()
    pixels = [image.pixelColor(x, y) for x in range(24) for y in range(24) if image.pixelColor(x, y).alpha() > 200]
    assert pixels
    assert all(c.red() == c.green() == c.blue() and c.lightness() > 150 for c in pixels)


def test_dark_and_light_bubbles_keep_text_and_links_readable(messenger):
    from dataclasses import replace
    app, store, window, chat = messenger
    window.apply_messenger_theme(replace(window.messenger_theme, strand='#000000', you='#ffffff'))
    store.add_message(chat, 'assistant', '[A site](https://example.com) with `color:#123456;` unchanged.')
    window.render_chat()
    doc = window.transcript.document()
    cursor = doc.find('A site')
    assert cursor.charFormat().foreground().color().name() == '#ffffff'
    assert 'color:#123456;' in window.transcript.toPlainText()
    cursor = doc.find('Hello.')
    assert cursor.charFormat().foreground().color().name() == '#ffffff'
    cursor = doc.find('My message')
    assert cursor.charFormat().foreground().color().name() == '#000000'
    cursor = doc.find('Used for this reply')
    assert cursor.charFormat().foreground().color().name() == '#ffffff'


def test_reset_keeps_neutral_mode_and_does_not_edit_messages(messenger):
    from dataclasses import replace
    app, store, window, chat = messenger
    original = store.export_markdown(chat)
    window.apply_messenger_theme(replace(window.messenger_theme, strand='#123456', you='#abcdef', surface='graphite'))
    window.appearance_panel.reset_button.click()
    assert window.messenger_theme.you == '#dce5f3'
    assert window.messenger_theme.strand == '#eedce7'
    assert window.messenger_theme.surface == 'graphite'
    assert store.export_markdown(chat) == original
