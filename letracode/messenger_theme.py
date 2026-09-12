"""Neutral messenger chrome and two independently chosen bubble colors."""
from __future__ import annotations

from dataclasses import dataclass, asdict, replace
from datetime import datetime
import html
import re

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPalette, QPainter, QPen, QPainterPath, QIcon, QPixmap
from PySide6.QtWidgets import (QColorDialog, QComboBox, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout)


def valid_hex(value):
    return isinstance(value, str) and re.fullmatch(r'#[0-9a-fA-F]{6}', value) is not None


@dataclass(frozen=True)
class MessengerTheme:
    you: str = '#dce5f3'
    strand: str = '#eedce7'
    surface: str = 'paper'

    @classmethod
    def from_settings(cls, raw):
        raw = raw if isinstance(raw, dict) else {}
        defaults = cls()
        return cls(**{key: raw[key].lower() if valid_hex(raw.get(key)) else getattr(defaults, key)
                      for key in ('you', 'strand')},
                   surface=raw.get('surface') if raw.get('surface') in ('paper', 'graphite') else 'paper')

    def to_settings(self):
        return asdict(self)

    @property
    def neutral(self):
        if self.surface == 'graphite':
            return dict(window='#303030', canvas='#242424', field='#202020', ink='#eeeeee',
                        muted='#b4b4b4', line='#575757', top='#4c4c4c', bottom='#363636',
                        selected='#505050', highlight='#777777')
        return dict(window='#eeeeee', canvas='#fafafa', field='#ffffff', ink='#282828',
                    muted='#666666', line='#b4b4b4', top='#ffffff', bottom='#e3e3e3',
                    selected='#dedede', highlight='#ffffff')


def bubble_ink(value):
    color = QColor(value)
    channels = [v / 255 for v in (color.red(), color.green(), color.blue())]
    linear = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in channels]
    luminance = sum(v * w for v, w in zip(linear, (.2126, .7152, .0722)))
    return '#000000' if (luminance + .05) / .05 >= 1.05 / (luminance + .05) else '#ffffff'


def neutral_palette(theme):
    n = theme.neutral
    palette = QPalette()
    roles = {'Window': 'window', 'WindowText': 'ink', 'Base': 'field', 'AlternateBase': 'window',
             'Text': 'ink', 'Button': 'window', 'ButtonText': 'ink', 'Highlight': 'selected',
             'Accent': 'selected', 'PlaceholderText': 'muted',
             'HighlightedText': 'ink', 'Link': 'ink', 'LinkVisited': 'muted',
             'ToolTipBase': 'canvas', 'ToolTipText': 'ink', 'BrightText': 'ink',
             'Light': 'highlight', 'Midlight': 'top', 'Mid': 'line', 'Dark': 'muted', 'Shadow': 'muted'}
    for role, key in roles.items():
        palette.setColor(getattr(QPalette.ColorRole, role), QColor(n[key]))
    for role in ('Text', 'WindowText', 'ButtonText'):
        palette.setColor(QPalette.ColorGroup.Disabled, getattr(QPalette.ColorRole, role), QColor(n['muted']))
    return palette


def chrome_stylesheet(theme):
    n = theme.neutral
    return '''
    QMainWindow, QWidget#chatSidebar, QWidget#chatRoom { background: %(window)s; color: %(ink)s; }
    QLabel, QCheckBox, QRadioButton, QSpinBox, QDoubleSpinBox { color: %(ink)s; }
    QLabel#contactName { font-size: 23px; font-weight: 600; }
    QLabel#muted, QLabel#chatTitle { color: %(muted)s; }
    QLabel#contactAvatar { border: 1px solid %(line)s; border-radius: 5px;
        background: qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 %(top)s,stop:1 %(bottom)s); padding: 8px; }
    QWidget#contactHeader { border-bottom: 1px solid %(line)s; }
    QLineEdit, QPlainTextEdit, QTextBrowser, QTreeWidget { background: %(field)s; color: %(ink)s;
        border: 1px solid %(line)s; border-radius: 3px; selection-background-color: %(selected)s;
        selection-color: %(ink)s; }
    QTextBrowser#chatTranscript { background: %(canvas)s; border: 1px solid %(line)s; border-radius: 5px; }
    QLineEdit { padding: 5px; }
    QPlainTextEdit { padding: 6px; }
    QPushButton, QToolButton, QComboBox { color: %(ink)s; border: 1px solid %(line)s;
        border-top-color: %(highlight)s; border-radius: 3px; padding: 5px 9px;
        background: qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 %(top)s,stop:1 %(bottom)s); }
    QPushButton:hover, QToolButton:hover, QComboBox:hover { background: %(top)s; }
    QPushButton:pressed, QToolButton:pressed, QPushButton:checked { background: %(selected)s; }
    QPushButton:disabled, QToolButton:disabled { color: %(muted)s; }
    QPushButton:focus, QToolButton:focus, QLineEdit:focus, QPlainTextEdit:focus,
    QComboBox:focus { border: 2px solid %(muted)s; }
    QGroupBox { color: %(ink)s; border: 1px solid %(line)s; border-radius: 4px;
        margin-top: 12px; padding-top: 7px; }
    QGroupBox::title { subcontrol-origin: margin; left: 9px; padding: 0 4px; }
    QTreeWidget { padding: 3px; }
    QTreeWidget::item { padding: 7px 4px; }
    QTreeWidget::item:selected { color: %(ink)s; background: %(selected)s; border-radius: 3px; }
    QTreeWidget::item:hover { background: %(window)s; }
    QTabWidget::pane { border: 1px solid %(line)s; background: %(window)s; }
    QTabBar::tab { background: qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 %(top)s,stop:1 %(bottom)s);
        color: %(muted)s; padding: 7px 15px; border: 1px solid %(line)s;
        border-top-left-radius: 4px; border-top-right-radius: 4px; margin-right: 3px; }
    QTabBar::tab:selected { background: %(canvas)s; color: %(ink)s; }
    QMenu, QMenuBar, QStatusBar { color: %(ink)s; background: %(window)s; }
    QMenu::item:selected, QMenuBar::item:selected { background: %(selected)s; }
    QSplitter::handle { background: %(line)s; width: 1px; }
    QScrollBar:vertical { background: %(window)s; width: 16px; margin: 0; }
    QScrollBar::handle:vertical { background: %(selected)s; border: 1px solid %(line)s;
        border-radius: 3px; min-height: 32px; }
    QScrollBar::handle:vertical:hover { background: %(muted)s; }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: %(window)s; }
    ''' % n


def messenger_icon(kind, color='#555555'):
    """Small, self-contained monochrome icons; no desktop icon-theme dependency."""
    pixmap = QPixmap(24, 24); pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap); painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor(color), 1.5))
    if kind == 'folder':
        path = QPainterPath(); path.moveTo(3, 7); path.lineTo(10, 7); path.lineTo(12, 10)
        path.lineTo(21, 10); path.lineTo(21, 20); path.lineTo(3, 20); path.closeSubpath(); painter.drawPath(path)
    elif kind == 'add':
        painter.drawLine(12, 5, 12, 19); painter.drawLine(5, 12, 19, 12)
    elif kind == 'send':
        path = QPainterPath(); path.moveTo(4, 4); path.lineTo(21, 12); path.lineTo(4, 20)
        path.lineTo(7, 12); path.closeSubpath(); painter.drawPath(path); painter.drawLine(7, 12, 21, 12)
    elif kind == 'stop':
        painter.drawRect(6, 6, 12, 12)
    else:
        path = QPainterPath(); path.moveTo(4, 4); path.lineTo(20, 4); path.lineTo(20, 17)
        path.lineTo(10, 17); path.lineTo(4, 21); path.closeSubpath(); painter.drawPath(path)
        painter.drawLine(8, 8, 16, 8); painter.drawLine(8, 12, 14, 12)
    painter.end()
    icon = QIcon()
    for mode in (QIcon.Mode.Normal, QIcon.Mode.Active, QIcon.Mode.Selected, QIcon.Mode.Disabled):
        icon.addPixmap(pixmap, mode)
    return icon


class BubblePicker(QPushButton):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.color = '#ffffff'
        self.setFixedSize(38, 30)

    def paintEvent(self, event):
        painter = QPainter(self); painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor(self.color)
        painter.setPen(QPen(color.darker(130), 1))
        painter.setBrush(color); painter.drawRoundedRect(2, 2, 32, 23, 7, 7)
        painter.setPen(QColor(bubble_ink(self.color)))
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, '···')
        if self.hasFocus():
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(self.palette().color(QPalette.ColorRole.Text), 1, Qt.PenStyle.DotLine))
            painter.drawRect(self.rect().adjusted(0, 0, -1, -1))


class AppearancePanel(QGroupBox):
    theme_changed = Signal(object)

    def __init__(self, theme, parent=None):
        super().__init__('Bubble colors', parent)
        self.theme = theme
        layout = QVBoxLayout(self); layout.setSpacing(6)
        note = QLabel('Two colors. Everything else stays neutral.')
        note.setWordWrap(True); note.setObjectName('muted'); layout.addWidget(note)
        for key, label in [('you', 'You'), ('strand', 'Strand')]:
            row = QHBoxLayout(); row.setSpacing(5)
            row.addWidget(QLabel(label), 1)
            picker = BubblePicker(); picker.setAccessibleName(label + ' bubble color picker')
            picker.setToolTip('Choose ' + label.lower() + ' bubble color')
            field = QLineEdit(); field.setMaxLength(7); field.setFixedWidth(85)
            field.setAccessibleName(label + ' bubble hex color')
            field.setToolTip('Six-digit hex color, for example #DCE5F3')
            setattr(self, key + '_picker', picker); setattr(self, key + '_hex', field)
            picker.clicked.connect(lambda checked=False, side=key: self.choose(side))
            field.editingFinished.connect(lambda side=key: self.commit_hex(side))
            row.addWidget(picker); row.addWidget(field); layout.addLayout(row)
        row = QHBoxLayout()
        self.swap_button = QPushButton('Swap'); self.reset_button = QPushButton('Reset')
        self.swap_button.clicked.connect(lambda: self.theme_changed.emit(replace(self.theme, you=self.theme.strand, strand=self.theme.you)))
        self.reset_button.clicked.connect(lambda: self.theme_changed.emit(replace(self.theme, you=MessengerTheme().you, strand=MessengerTheme().strand)))
        row.addWidget(self.swap_button); row.addWidget(self.reset_button); layout.addLayout(row)
        self.surface = QComboBox(); self.surface.addItem('Paper', 'paper'); self.surface.addItem('Graphite', 'graphite')
        self.surface.setAccessibleName('Neutral background')
        self.surface.currentIndexChanged.connect(lambda: self.theme_changed.emit(replace(self.theme, surface=self.surface.currentData())))
        layout.addWidget(self.surface)
        self.set_theme(theme)

    def set_theme(self, theme):
        self.theme = theme
        for key in ('you', 'strand'):
            field = getattr(self, key + '_hex'); field.setText(getattr(theme, key).upper())
            picker = getattr(self, key + '_picker'); picker.color = getattr(theme, key); picker.update()
        self.surface.blockSignals(True)
        self.surface.setCurrentIndex(self.surface.findData(theme.surface))
        self.surface.blockSignals(False)

    def choose(self, side):
        color = QColorDialog.getColor(QColor(getattr(self.theme, side)), self, 'Choose bubble color')
        if color.isValid():
            self.theme_changed.emit(replace(self.theme, **{side: color.name()}))

    def commit_hex(self, side):
        field = getattr(self, side + '_hex'); value = field.text().strip()
        if valid_hex(value):
            if value.lower() != getattr(self.theme, side):
                self.theme_changed.emit(replace(self.theme, **{side: value.lower()}))
        else:
            field.setText(getattr(self.theme, side).upper())
            field.setToolTip('Not saved: use # followed by six hexadecimal digits, such as #DCE5F3.')


def bubble_html(message, name, state, body, theme, font_metrics, viewport_width):
    side = 'you' if message['role'] == 'user' else 'strand'
    fill = getattr(theme, side); ink = bubble_ink(fill)
    align = 'right' if side == 'you' else 'left'
    # Qt rich text does not inherit link colors reliably. Recolor generated
    # tags, never prose/code (which could itself contain a CSS color literal).
    def recolor_tag(match):
        tag = re.sub(r'(?<![-\w])color\s*:\s*[^;"]+;?', '', match.group(0))
        if 'style="' in tag:
            return tag.replace('style="', f'style="color:{ink}; ')
        return tag[:-1] + f' style="color:{ink};">'
    body = re.sub(r'<(?:a|span)\b[^>]*>', recolor_tag, body)
    raw = message.get('content', '')
    # Bound width to the actual viewport, not a fixed desktop or font size.
    maximum = max(120, int((viewport_width - 56) * .84))
    longest = max((font_metrics.horizontalAdvance(line[:2000]) for line in raw.splitlines()), default=180)
    width = min(maximum, max(150, longest + 34))
    if 'letracode:receipt/' in body:
        width = max(width, min(maximum, font_metrics.horizontalAdvance('Used for this reply · Copy · Create example') + 34))
    if 'letracode:thinking/' in body:
        width = maximum
    try:
        stamp = datetime.fromisoformat(message.get('created', '')).astimezone().strftime('%H:%M')
    except (ValueError, TypeError):
        stamp = ''
    header = f'<p align="{align}" style="margin-top:14px; margin-bottom:5px; color:{theme.neutral["muted"]}">' \
             f'<b>{html.escape(name)}{html.escape(state)}</b> &nbsp; <small>{stamp}</small></p>'
    # Only app-generated tables get a background. SafeBrowser turns these into
    # rounded native-painted bubbles; model HTML remains disabled upstream.
    bubble = f'<table width="{width}" border="0" cellspacing="0" cellpadding="12" bgcolor="{fill}">' \
             f'<tr><td style="color:{ink};">{body}</td></tr></table>'
    # Qt's right-aligned tables ignore the document's right margin. A neutral
    # two-cell row aligns bubbles without clipping their padding or tail.
    canvas = max(width, viewport_width - 40)
    spacer = f'<td width="{canvas - width}"></td>'
    cell = f'<td width="{width}">{bubble}</td>'
    cells = spacer + cell if side == 'you' else cell + spacer
    return header + f'<table width="{canvas}" border="0" cellspacing="0" cellpadding="0"><tr>{cells}</tr></table>' \
                    '<p style="font-size:2px; margin:0;"> </p>'
