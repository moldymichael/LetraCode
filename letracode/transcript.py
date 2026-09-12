"""Safe rich-text chat with user-owned scrolling during streamed replacements."""
from math import ceil

from PySide6.QtCore import Signal, QTimer, Qt
from PySide6.QtGui import QPainter, QColor, QPen, QBrush, QTextTable, QPainterPath
from PySide6.QtWidgets import QTextBrowser


class SafeBrowser(QTextBrowser):
    reading_changed = Signal()
    viewport_resized = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._replacing = False
        self._following = True
        self.bubble_frames = []
        self._settle = QTimer(self)
        self._settle.setSingleShot(True)
        self._settle.timeout.connect(self._follow_after_layout)
        bar = self.verticalScrollBar()
        bar.valueChanged.connect(self._scroll_changed)
        bar.rangeChanged.connect(self._range_changed)
        bar.sliderPressed.connect(self._drag_started)
        bar.sliderReleased.connect(self._drag_finished)
        self.selectionChanged.connect(self.reading_changed)

    def loadResource(self, kind, url):
        # Model text never fetches images, local files, styles or remote content.
        return None

    def _decorate_bubbles(self):
        pending = list(self.document().rootFrame().childFrames())
        while pending:
            frame = pending.pop(0)
            pending.extend(frame.childFrames())
            if not isinstance(frame, QTextTable):
                continue
            fmt = frame.format()
            if fmt.background().style() == Qt.BrushStyle.NoBrush:
                continue
            color = QColor(fmt.background().color())
            self.bubble_frames.append((frame, color))
            fmt.setBackground(QBrush(Qt.BrushStyle.NoBrush))
            frame.setFormat(fmt)

    def bubble_rect(self, frame):
        # Qt reports a table's frame origin at the first padded cell's content,
        # but includes padding in its size. Translate, do not expand the size.
        padding = frame.format().cellPadding()
        return self.document().documentLayout().frameBoundingRect(frame).translated(-padding, -padding)

    def paintEvent(self, event):
        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.translate(-self.horizontalScrollBar().value(), -self.verticalScrollBar().value())
        for frame, color in self.bubble_frames:
            rect = self.bubble_rect(frame)
            visible_top = self.verticalScrollBar().value()
            if rect.bottom() < visible_top or rect.top() > visible_top + self.viewport().height():
                continue
            painter.setPen(QPen(color.darker(130), 1))
            painter.setBrush(color)
            painter.drawRoundedRect(rect.adjusted(.5, .5, -.5, -.5), 13, 13)
            right = rect.center().x() > self.viewport().width() / 2
            x = rect.right() if right else rect.left()
            direction = 1 if right else -1
            y = rect.bottom() - 13
            tail = QPainterPath(); tail.moveTo(x - direction * 1, y - 4)
            tail.lineTo(x + direction * 7, y + 4); tail.lineTo(x - direction * 1, y + 4)
            painter.drawPath(tail)
        painter.end()
        super().paintEvent(event)

    def at_bottom(self):
        bar = self.verticalScrollBar()
        return bar.maximum() - bar.value() <= 2

    def _scroll_changed(self, *_):
        if not self._replacing:
            self._following = self.at_bottom()
            self.reading_changed.emit()

    def _range_changed(self, *_):
        if not self._replacing and self._following:
            # Do not capture a position: a later user scroll cancels following.
            self._settle.start(0)

    def _drag_started(self):
        self._following = False
        self._settle.stop()

    def _drag_finished(self):
        self._following = self.at_bottom()
        self.reading_changed.emit()

    def _layout_document(self):
        size = self.document().documentLayout().documentSize()
        # QTextEdit receives documentSizeChanged later in the event loop. Sync
        # its range now so End/drag can reach the real end before the next delta.
        bar = self.verticalScrollBar()
        bar.setPageStep(self.viewport().height())
        bar.setRange(0, max(0, ceil(size.height()) - self.viewport().height()))

    def _follow_after_layout(self):
        if (self._following and not self.verticalScrollBar().isSliderDown()
                and not self.textCursor().hasSelection()):
            self._layout_document()
            self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())

    def replace_html(self, text, *, reset=False):
        """Return False when updating would destroy an active drag/selection.

        The owner keeps the saved output and retries after the interaction ends.
        Forcing Qt's lazy document layout *before* restoring the scroll position
        is essential: setHtml's provisional maximum can be far short of the end.
        """
        bar = self.verticalScrollBar()
        if not reset and (bar.isSliderDown() or self.textCursor().hasSelection()):
            return False
        previous = bar.value()
        # A resize can grow the range before the queued render; keep the
        # reader's intent rather than mistaking that geometry change for a scroll.
        following = reset or self._following
        self._replacing = True
        try:
            self.bubble_frames = []
            self.setHtml(text)
            self._decorate_bubbles()
            self._layout_document()
            bar.setValue(bar.maximum() if following else previous)
        finally:
            self._replacing = False
        self._following = following
        self.reading_changed.emit()
        if following:
            self._settle.start(0)
        return True

    def follow_latest(self):
        self._following = True
        self._follow_after_layout()

    def resizeEvent(self, event):
        following = self._following
        replacing = self._replacing
        self._replacing = True
        try:
            super().resizeEvent(event)
            self._layout_document()
        finally:
            self._replacing = replacing
        self._following = following
        if following:
            self._settle.start(0)
        if event.oldSize().width() != event.size().width():
            self.viewport_resized.emit()
