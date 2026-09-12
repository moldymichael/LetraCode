"""Exercise the real transcript and scrollbar during saved streaming updates."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QAbstractSlider
from letracode.store import Store
from letracode.ui import MainWindow


@pytest.fixture
def chat(tmp_path):
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path / 'data')
    ident = store.create_chat('Streaming scroll regression')
    for i in range(12):
        store.add_message(ident, 'user' if i % 2 else 'assistant',
                          f'Message {i}\n' + ('A paragraph with wrapping text. ' * 12))
    message = store.add_message(ident, 'assistant', 'Beginning of response.', status='streaming')
    window = MainWindow(store)
    window.resize(1000, 720)
    window.show()
    window.select_chat(ident)
    app.processEvents()
    yield app, store, window, message
    window.worker = None
    window.close()
    app.processEvents()


def test_streaming_does_not_reset_the_document_under_a_dragged_thumb(chat):
    app, store, window, message = chat
    bar = window.transcript.verticalScrollBar()
    bar.setValue(bar.maximum() // 2)
    original = window.transcript.toPlainText()
    old_range = bar.maximum()
    bar.setSliderDown(True)
    try:
        for i in range(6):
            bar.setSliderPosition(min(bar.maximum(), bar.value() + 120))
            store.update_message(message, 'More streaming text. ' * (i + 10), status='streaming')
            window.render_chat()
            app.processEvents()
            # Losing the document/range here moves the drag target every frame.
            assert window.transcript.toPlainText() == original
            assert bar.maximum() == old_range
        bar.setSliderPosition(bar.maximum())
    finally:
        bar.setSliderDown(False)
    QTest.qWait(130)
    assert 'More streaming text.' in window.transcript.toPlainText()
    assert bar.value() == bar.maximum()


def test_manual_end_reaches_and_follows_the_real_bottom_during_streaming(chat):
    app, store, window, message = chat
    bar = window.transcript.verticalScrollBar()
    bar.setValue(0)
    for i in range(4):
        store.update_message(message, ('**Paragraph** with wrapping words. ' * 40 + '\n\n') * (30 + i), status='streaming')
        window.render_chat()
        # Native End action, not a test implementation of scroll logic.
        bar.triggerAction(QAbstractSlider.SliderAction.SliderToMaximum)
        app.processEvents()
        assert bar.maximum() - bar.value() <= 1
    store.update_message(message, ('A line of text\n\n' * 400) + 'THE ACTUAL END', status='complete')
    window.render_chat()
    app.processEvents()
    assert bar.value() == bar.maximum()
    cursor = window.transcript.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.End)
    assert window.transcript.cursorRect(cursor).bottom() <= window.transcript.viewport().height()


def test_streaming_keeps_the_readers_position_when_scrolled_up(chat):
    app, store, window, message = chat
    bar = window.transcript.verticalScrollBar()
    bar.setValue(bar.maximum() // 3)
    original = bar.value()
    for i in range(5):
        store.update_message(message, 'New text. ' * (100 + i * 30), status='streaming')
        window.render_chat()
        app.processEvents()
        assert abs(bar.value() - original) <= 1


def test_jump_to_latest_releases_a_selection_and_renders_pending_text(chat):
    app, store, window, message = chat
    cursor = window.transcript.textCursor()
    cursor.select(QTextCursor.SelectionType.Document)
    window.transcript.setTextCursor(cursor)
    store.update_message(message, 'The latest saved text.', status='streaming')
    window.render_chat()
    assert 'The latest saved text.' not in window.transcript.toPlainText()
    assert hasattr(window, 'latest_button'), 'A visible route out of deferred reading is required'
    window.latest_button.click()
    app.processEvents()
    assert not window.transcript.textCursor().hasSelection()
    assert 'The latest saved text.' in window.transcript.toPlainText()
    bar = window.transcript.verticalScrollBar()
    assert bar.value() == bar.maximum()


def test_real_mouse_drag_reaches_bottom_while_streaming(chat):
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QStyle, QStyleOptionSlider
    app, store, window, message = chat
    bar = window.transcript.verticalScrollBar()
    bar.setValue(bar.maximum() // 3)
    option = QStyleOptionSlider(); bar.initStyleOption(option)
    thumb = bar.style().subControlRect(QStyle.ComplexControl.CC_ScrollBar, option,
                                     QStyle.SubControl.SC_ScrollBarSlider, bar)
    QTest.mousePress(bar, Qt.MouseButton.LeftButton, pos=thumb.center())
    assert bar.isSliderDown()
    for index in range(4):
        store.update_message(message, 'New saved paragraph.\n\n' * (100 + index), status='streaming')
        window.render_chat()
        QTest.mouseMove(bar, QPoint(thumb.center().x(), bar.height() - 1))
        app.processEvents()
        assert bar.isSliderDown()
    QTest.mouseRelease(bar, Qt.MouseButton.LeftButton, pos=QPoint(thumb.center().x(), bar.height() - 1))
    QTest.qWait(140)
    assert not bar.isSliderDown()
    assert 'New saved paragraph.' in window.transcript.toPlainText()
    assert bar.value() == bar.maximum()


def test_user_wheel_scroll_cancels_pending_follow(chat):
    from PySide6.QtCore import QPoint, QPointF
    from PySide6.QtGui import QWheelEvent
    app, store, window, message = chat
    window.jump_to_latest()
    store.update_message(message, 'Growing response.\n\n' * 100, status='streaming')
    window.render_chat()
    viewport = window.transcript.viewport()
    center = viewport.rect().center()
    wheel = QWheelEvent(QPointF(center), QPointF(viewport.mapToGlobal(center)), QPoint(),
                        QPoint(0, 120), Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                        Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(viewport, wheel)
    position = window.transcript.verticalScrollBar().value()
    app.processEvents()
    assert not window.transcript.at_bottom()
    assert window.transcript.verticalScrollBar().value() == position
    store.update_message(message, 'Growing response.\n\n' * 130, status='streaming')
    window.render_chat()
    QTest.qWait(100)
    assert window.transcript.verticalScrollBar().value() == position


def test_resize_at_bottom_and_ctrl_end_reach_last_line(chat):
    app, store, window, message = chat
    store.update_message(message, 'Resizing a long reply.\n\n' * 150 + 'Final line.', status='streaming')
    window.render_chat()
    window.jump_to_latest()
    window.resize(850, 570)
    QTest.qWait(150)
    assert window.transcript.at_bottom()
    window.transcript.verticalScrollBar().setValue(0)
    QTest.keyClick(window, Qt.Key.Key_End, Qt.KeyboardModifier.ControlModifier)
    app.processEvents()
    assert window.transcript.at_bottom()
    cursor = window.transcript.textCursor(); cursor.movePosition(QTextCursor.MoveOperation.End)
    assert window.transcript.cursorRect(cursor).bottom() <= window.transcript.viewport().height()
