"""Native Qt messenger interface with neutral chrome and editable bubble colors."""
from __future__ import annotations

import dataclasses
import html
import json
import re
import shutil
import sqlite3
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QDesktopServices, QFontDatabase, QIcon, QKeySequence, QTextDocument
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox,
    QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QInputDialog, QLabel,
    QLineEdit, QMainWindow, QMenu, QMessageBox, QPlainTextEdit, QPushButton,
    QSpinBox, QSplitter, QTabWidget, QTextBrowser, QToolBar, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget, QToolButton, QSizePolicy, QScrollArea)

from . import __version__
from .transcript import SafeBrowser
from .messenger_theme import (MessengerTheme, AppearancePanel, neutral_palette,
    chrome_stylesheet, bubble_html, messenger_icon)
from .dialogs import ApprovalDialog, ModelDialog
from .engine import EngineConfig, LocalEngine
from .worker import ConversationWorker
from .dialogue import DialogueWorker, dialogue_participants
from .store import message_status
from .memory_ui import MemoryDialog
from .project_files import ProjectFilesPanel
from .platform import engine_setup_help, is_windows
from .training_ui import FineTuningPanel
from .experience import KnowledgePanel, SettingsPanel


def assistant_html(text, font):
    """Render model prose, allowing ordinary links but never application actions."""
    document = QTextDocument()
    document.setDefaultFont(font)
    document.setMarkdown(text, QTextDocument.MarkdownFeature.MarkdownDialectGitHub | QTextDocument.MarkdownFeature.MarkdownNoHTML)
    body = re.search(r'<body[^>]*>(.*)</body>', document.toHtml(), re.S)
    rendered = body.group(1) if body else html.escape(text)
    # Qt emits normalized double-quoted hrefs; source HTML is already disabled.
    # App-created receipt links are added separately after this boundary.
    return re.sub(r'<a\b[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
        lambda match: match.group(2) if QUrl(html.unescape(match.group(1))).scheme().lower() == 'letracode' else match.group(0), rendered, flags=re.S)


class Composer(QPlainTextEdit):
    submitted = Signal()

    def __init__(self):
        super().__init__()
        self.setAccessibleName('Message to Strand')
        self.setAccessibleDescription('Draft saves automatically. Control Enter sends; Enter adds a new line.')
        self.textChanged.connect(self.fit_draft)

    def fit_draft(self):
        lines = max(2, min(6, self.document().blockCount()))
        self.setFixedHeight(round(self.fontMetrics().lineSpacing() * lines + 18))

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.submitted.emit()
        else:
            super().keyPressEvent(event)


class MainWindow(QMainWindow):
    def __init__(self, store):
        super().__init__()
        self.store = store
        self.messenger_theme = MessengerTheme.from_settings(store.setting('messenger_theme', {}))
        self.chat_id = None
        self.project_id = None
        self.worker = None
        self.busy = False
        self.exchange_start_id = None
        self.approval_dialog = None
        self.loading = False
        self.thinking_expanded = {}
        self.history_limits = {}
        self.rendered_chat = None
        self.rendered_html = None
        self.selection_ready = False
        self.closing_when_stopped = False
        config_data = self.store.setting('engine',{})
        valid_fields = {f.name for f in dataclasses.fields(EngineConfig)}
        try:
            self.engine_config = EngineConfig(**{k:v for k,v in config_data.items() if k in valid_fields})
        except (TypeError,ValueError):
            self.engine_config = EngineConfig()
        if not self.engine_config.executable:
            self.engine_config.executable = shutil.which('llama-server.exe' if is_windows() else 'llama-server') or ''
        self.engine = LocalEngine(dataclasses.replace(self.engine_config, secondary_model_path=''),store.directory)
        self.setWindowTitle('LetraCode')
        available = self.screen().availableGeometry()
        self.resize(min(1260, available.width() - 32), min(820, available.height() - 64))
        self.setMinimumSize(850,570)
        icon_path = Path(__file__).parent / 'assets/io.letracode.LetraCode.svg'
        self.setWindowIcon(QIcon.fromTheme('io.letracode.LetraCode',QIcon(str(icon_path))))
        self.save_timer = QTimer(self)
        self.save_timer.setSingleShot(True); self.save_timer.setInterval(400)
        self.save_timer.timeout.connect(self.save_editors)
        self.render_timer = QTimer(self)
        self.render_timer.setSingleShot(True); self.render_timer.setInterval(80)
        self.render_timer.timeout.connect(self.render_chat)
        self.build_ui()
        self.build_menus()
        self.workspaces = QTabWidget()
        self.workspaces.setAccessibleName('Application areas')
        self.workspaces.addTab(self.takeCentralWidget(), 'Chat')
        self.knowledge_panel = KnowledgePanel(store, self.files_panel, self)
        self.workspaces.addTab(self.knowledge_panel, 'Knowledge')
        self.training_panel = FineTuningPanel(store, self)
        self.workspaces.addTab(self.training_panel, 'Improve')
        self.settings_panel = SettingsPanel(store, self)
        self.workspaces.addTab(self.settings_panel, 'Settings')
        self.settings_appearance = AppearancePanel(self.messenger_theme)
        self.settings_appearance.theme_changed.connect(self.apply_messenger_theme)
        self.settings_panel.widget().layout().insertWidget(0, self.settings_appearance)
        self.setCentralWidget(self.workspaces)
        self.training_panel.busy_changed.connect(self.set_busy)
        self.workspaces.currentChanged.connect(self.refresh_area)
        self.apply_messenger_theme(self.messenger_theme, persist=False)
        self.refresh_tree()
        previous = self.store.setting('last_chat')
        if previous and self.store.chat(previous):
            self.select_chat(previous)
        else:
            self.show_selection(None,None)
        geometry = self.store.setting('geometry')
        if geometry:
            from PySide6.QtCore import QByteArray
            self.restoreGeometry(QByteArray.fromHex(geometry.encode('ascii')))
        sizes = self.store.setting('chat_splitter_sizes')
        if isinstance(sizes, list) and len(sizes) == 3 and all(type(x) is int and x >= 0 for x in sizes):
            self.splitter.setSizes(sizes)
        self.set_context_visible(self.store.setting('show_chat_context', False))
        self.statusBar().showMessage('Ready · everything is saved on this computer')

    def build_ui(self):
        self.splitter = QSplitter()
        self.setCentralWidget(self.splitter)
        sidebar = QWidget(); sidebar.setObjectName('chatSidebar'); side = QVBoxLayout(sidebar)
        side.setContentsMargins(12,12,8,12)
        title = QLabel('LetraCode · Messenger')
        font = title.font(); font.setBold(True); font.setPointSizeF(font.pointSizeF()+3); title.setFont(font)
        side.addWidget(title)
        subtitle = QLabel('On this computer'); subtitle.setObjectName('muted')
        subtitle.setWordWrap(True); side.addWidget(subtitle)
        side.addSpacing(10)
        self.search = QLineEdit()
        self.search.setAccessibleName('Search saved chats')
        self.search.setPlaceholderText('Find a conversation…')
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _: self.refresh_tree())
        side.addWidget(self.search)
        self.tree = QTreeWidget()
        self.tree.setAccessibleName('Chats and workspaces')
        self.tree.setHeaderHidden(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.setIndentation(16)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.tree_menu)
        self.tree.currentItemChanged.connect(self.tree_selected)
        self.tree.setMinimumHeight(115)
        side.addWidget(self.tree,1)
        self.new_chat_button = QPushButton(messenger_icon('add', self.messenger_theme.neutral['ink']),'New chat')
        self.new_chat_button.clicked.connect(self.new_chat)
        self.new_project_button = QPushButton(messenger_icon('folder', self.messenger_theme.neutral['ink']),'New workspace…')
        self.new_project_button.clicked.connect(self.new_project)
        side.addWidget(self.new_chat_button); side.addWidget(self.new_project_button)
        self.appearance_panel = AppearancePanel(self.messenger_theme)
        self.appearance_panel.theme_changed.connect(self.apply_messenger_theme)
        side.addWidget(self.appearance_panel)
        sidebar_scroll = QScrollArea(); sidebar_scroll.setWidgetResizable(True)
        sidebar_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        sidebar_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        sidebar_scroll.setMinimumWidth(230); sidebar_scroll.setWidget(sidebar)
        self.splitter.addWidget(sidebar_scroll)

        main = QWidget(); main.setObjectName('chatRoom'); center = QVBoxLayout(main); center.setContentsMargins(14,12,14,12)
        header_widget = QWidget(); header_widget.setObjectName('contactHeader')
        header = QHBoxLayout(header_widget); header.setContentsMargins(3, 2, 3, 9)
        avatar = QLabel('>_'); avatar.setObjectName('contactAvatar')
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter); avatar.setFixedSize(51, 51)
        avatar.setAccessibleName('Local assistant')
        header.addWidget(avatar)
        contact = QVBoxLayout(); contact.setSpacing(2)
        self.contact_name = QLabel('Strand'); self.contact_name.setObjectName('contactName')
        self.contact_status = QLabel('Local assistant'); self.contact_status.setObjectName('muted')
        self.chat_title = QLabel('A little room to think.'); self.chat_title.setObjectName('chatTitle')
        self.chat_title.setTextFormat(Qt.TextFormat.PlainText)
        self.chat_title.setWordWrap(True)
        contact.addWidget(self.contact_name); contact.addWidget(self.contact_status); contact.addWidget(self.chat_title)
        header.addLayout(contact, 1)
        self.model_button = QPushButton('Model…')
        self.model_button.clicked.connect(self.model_setup)
        header.addWidget(self.model_button)
        center.addWidget(header_widget)
        self.model_label = QLabel()
        self.model_label.setTextFormat(Qt.TextFormat.PlainText)
        self.model_label.setWordWrap(True)
        center.addWidget(self.model_label)
        self.activity = QLabel()
        self.activity.setWordWrap(True)
        self.activity.setOpenExternalLinks(False)
        self.activity.linkActivated.connect(lambda _: self.return_to_active_chat())
        self.activity.setVisible(False)
        center.addWidget(self.activity)
        self.options_button = QToolButton()
        self.options_button.setText('Conversation options')
        self.options_button.setCheckable(True)
        self.options_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.options_button.setArrowType(Qt.ArrowType.RightArrow)
        center.addWidget(self.options_button)
        self.options_panel = QWidget()
        options_layout = QVBoxLayout(self.options_panel)
        options_layout.setContentsMargins(0, 0, 0, 0)
        exchange = QHBoxLayout()
        self.conversation_mode = QComboBox()
        self.conversation_mode.addItems(['Single model', 'Two models'])
        self.conversation_mode.setToolTip('Two local models take turns in one shared conversation. Each exchange stops after the selected number of replies.')
        exchange.addWidget(self.conversation_mode)
        self.speaker_label = QLabel('Next speaker')
        exchange.addWidget(self.speaker_label)
        self.first_speaker = QComboBox()
        self.first_speaker.addItems(['Model A', 'Model B'])
        exchange.addWidget(self.first_speaker)
        self.replies_label = QLabel('Replies')
        exchange.addWidget(self.replies_label)
        self.reply_count = QSpinBox()
        self.reply_count.setRange(1, 4)
        self.reply_count.setValue(2)
        self.reply_count.setToolTip('Total replies in this exchange, alternating between models. The exchange always stops at this limit.')
        exchange.addWidget(self.reply_count)
        exchange.addStretch()
        options_layout.addLayout(exchange)
        self.dialogue_hint = QLabel('Each exchange stops at the reply limit. Two-model mode has no tools or internet access; Computer can still include project files.')
        self.dialogue_hint.setWordWrap(True)
        options_layout.addWidget(self.dialogue_hint)
        center.addWidget(self.options_panel)
        self.options_panel.hide()
        self.options_button.toggled.connect(self.options_panel.setVisible)
        self.options_button.toggled.connect(lambda checked: self.options_button.setArrowType(Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow))
        self.load_older_button = QPushButton('Load earlier messages')
        self.load_older_button.clicked.connect(self.load_older_messages)
        self.load_older_button.hide(); center.addWidget(self.load_older_button)
        self.transcript = SafeBrowser()
        self.transcript.setObjectName('chatTranscript')
        self.transcript.document().setDocumentMargin(20)
        self.transcript.setAccessibleName('Saved conversation with Strand')
        self.transcript.setOpenLinks(False)
        self.transcript.setOpenExternalLinks(False)
        self.transcript.anchorClicked.connect(self.open_link)
        self.transcript.selectionChanged.connect(self.queue_render)
        self.transcript.verticalScrollBar().sliderReleased.connect(self.queue_render)
        self.transcript.viewport_resized.connect(self.queue_render)
        center.addWidget(self.transcript,1)
        row = QHBoxLayout()
        self.copy_button = QPushButton(messenger_icon('chat', self.messenger_theme.neutral['ink']),'Copy last reply')
        self.copy_button.clicked.connect(self.copy_reply)
        self.retry_button = QPushButton(messenger_icon('chat', self.messenger_theme.neutral['ink']),'Ask again')
        self.retry_button.clicked.connect(self.retry_reply)
        self.continue_button = QPushButton('Continue exchange')
        self.continue_button.setToolTip('Continue the shared conversation for the selected number of replies. Send or clear your draft first.')
        self.continue_button.clicked.connect(self.continue_exchange)
        self.learn_button = QPushButton('Create example…')
        self.learn_button.clicked.connect(self.learn_from_reply)
        # Keep secondary actions reachable by menu and per-message links.
        self.reply_menu = QToolButton(); self.reply_menu.setText('Reply actions')
        self.reply_menu.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(self.reply_menu)
        menu.addAction('Copy last reply', self.copy_reply)
        self.repeat_action = menu.addAction('Ask last question again', self.retry_reply)
        self.example_action = menu.addAction('Create example from last reply…', self.learn_from_reply)
        self.reply_menu.setMenu(menu)
        self.copy_button.hide(); self.retry_button.hide(); self.learn_button.hide()
        self.latest_button = QPushButton('↓ Latest message')
        self.latest_button.setAccessibleName('Jump to latest message')
        self.latest_button.setToolTip('Clear the reading selection and show the latest saved text.')
        self.latest_button.clicked.connect(self.jump_to_latest)
        self.transcript.reading_changed.connect(self.update_latest_button)
        self.latest_button.hide()
        row.addWidget(self.reply_menu); row.addWidget(self.continue_button)
        row.addWidget(self.latest_button); row.addStretch()
        self.mode = QComboBox(); self.mode.addItems(['Instant','Thinking'])
        self.mode.setToolTip('Thinking asks compatible models to reason before replying. Emitted thinking appears live in a separate, collapsible block. Support depends on your model and engine.')
        self.mode.setCurrentText(self.store.setting('mode','Instant'))
        row.addWidget(self.mode)
        center.addLayout(row)
        self.composer = Composer()
        self.composer.setPlaceholderText('Message Strand…   Ctrl+Enter to send; Enter for a new line.')
        self.composer.fit_draft()
        self.composer.textChanged.connect(self.schedule_save)
        self.composer.textChanged.connect(self.update_conversation_controls)
        self.composer.submitted.connect(self.send)
        self.composer_row = QHBoxLayout()
        self.composer_row.addWidget(self.composer, 1)
        center.addLayout(self.composer_row)
        controls = QHBoxLayout()
        self.computer = QCheckBox('Read local files')
        self.computer.setChecked(self.store.setting('computer',True))
        self.computer.setToolTip('Include automatic notes and source excerpts, and let Strand read supported files anywhere your account can read. Off excludes file-reading tools and automatic file text; saved conversation text remains.')
        self.internet = QCheckBox('Web research')
        self.internet.setChecked(self.store.setting('internet',True))
        self.internet.setToolTip('Permit web research. Each outgoing query or URL requires approval.')
        self.actions = QCheckBox('Model tools')
        self.actions.setChecked(self.store.setting('actions',True))
        self.actions.setToolTip('Advanced compatibility switch. Disable if this model cannot call tools. Automatic source excerpts still follow Read local files.')
        self.effects = QCheckBox('Edits && commands')
        self.effects.setChecked(self.store.setting('effects', True))
        self.effects.setToolTip('Allow proposals for changes and commands, each requiring approval. Approved commands run with your account and can also use the network.')
        for check in (self.computer,self.internet,self.effects):
            controls.addWidget(check)
        options_layout.addWidget(self.actions)
        controls.addStretch()
        self.stop_button = QPushButton(messenger_icon('stop', self.messenger_theme.neutral['ink']),'Stop')
        self.stop_button.setEnabled(False); self.stop_button.clicked.connect(self.stop)
        self.send_button = QPushButton(messenger_icon('send', self.messenger_theme.neutral['ink']),'Send')
        self.send_button.clicked.connect(self.send)
        controls.addWidget(self.stop_button)
        self.send_button.setMinimumWidth(78)
        self.send_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self.composer_row.addWidget(self.send_button)
        center.addLayout(controls)
        self.splitter.addWidget(main)

        self.context_panel = QWidget(); context = QVBoxLayout(self.context_panel)
        context.setContentsMargins(8,12,12,12)
        context_title = QLabel('Strand’s focus')
        font = context_title.font(); font.setBold(True); context_title.setFont(font)
        context.addWidget(context_title)
        self.context_hint = QLabel()
        self.context_hint.setWordWrap(True); context.addWidget(self.context_hint)
        self.files_panel = ProjectFilesPanel(self.store, self)
        knowledge = QPushButton('Open Knowledge')
        knowledge.clicked.connect(lambda: self.workspaces.setCurrentWidget(self.knowledge_panel))
        context.addWidget(knowledge)
        self.capability_hint = QLabel()
        self.capability_hint.setWordWrap(True)
        context.addWidget(self.capability_hint)
        context.addStretch()
        self.splitter.addWidget(self.context_panel)
        self.splitter.setSizes([260, 800, 250])
        self.splitter.setStretchFactor(1,1)
        self.conversation_mode.currentIndexChanged.connect(self.conversation_options_changed)
        self.first_speaker.currentIndexChanged.connect(self.conversation_options_changed)
        self.reply_count.valueChanged.connect(self.conversation_options_changed)
        for check in (self.computer, self.internet, self.effects, self.actions):
            check.toggled.connect(self.update_capability_hint)

    def apply_messenger_theme(self, theme, *, persist=True):
        theme = MessengerTheme.from_settings(theme.to_settings())
        if persist:
            try:
                self.store.set_setting('messenger_theme', theme.to_settings())
            except (OSError, sqlite3.Error) as error:
                self.appearance_panel.set_theme(self.messenger_theme)
                if hasattr(self, 'settings_appearance'):
                    self.settings_appearance.set_theme(self.messenger_theme)
                self.statusBar().showMessage('Colors could not be saved: ' + str(error))
                return
        self.messenger_theme = theme
        self.setPalette(neutral_palette(theme))
        self.setStyleSheet(chrome_stylesheet(theme))
        self.setWindowIcon(messenger_icon('chat', theme.neutral['ink']))
        for button, kind in ((self.new_chat_button, 'add'), (self.new_project_button, 'folder'),
                             (self.send_button, 'send'), (self.stop_button, 'stop')):
            button.setIcon(messenger_icon(kind, theme.neutral['ink']))
        self.appearance_panel.set_theme(theme)
        if hasattr(self, 'settings_appearance'):
            self.settings_appearance.set_theme(theme)
        self.refresh_tree()
        self.rendered_html = None
        self.render_chat()

    def show_bubble_colors(self):
        self.workspaces.setCurrentWidget(self.settings_panel)
        self.settings_panel.ensureWidgetVisible(self.settings_appearance)
        self.settings_appearance.you_picker.setFocus()

    def action(self, menu, title, callback, shortcut=None):
        action = QAction(title,self)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
        action.triggered.connect(callback)
        menu.addAction(action)
        return action

    def build_menus(self):
        file = self.menuBar().addMenu('&File')
        self.mutation_actions = [self.action(file,'New &chat',self.new_chat,'Ctrl+N'), self.action(file,'New &workspace…',self.new_project,'Ctrl+Shift+N')]
        self.action(file,'&Export chat as Markdown…',self.export_chat,'Ctrl+Shift+E')
        self.action(file,'Export Evaluation…',self.export_evaluation)
        self.action(file,'Back up chats, Memory and source backups…',self.backup)
        self.action(file,'Open data folder',lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.store.directory))))
        file.addSeparator(); self.action(file,'&Quit',self.close,'Ctrl+Q')
        chat = self.menuBar().addMenu('&Chat')
        self.mutation_actions.append(self.action(chat,'Rename selected…',self.rename_selected,'F2'))
        self.mutation_actions.append(self.action(chat,'Delete selected…',self.delete_selected))
        self.action(chat,'Search chats',lambda:self.search.setFocus(),'Ctrl+K')
        self.action(chat,'Find in conversation…',self.find_in_chat,'Ctrl+F')
        self.action(chat,'Start a fresh task in this chat', self.end_current_task)
        view = self.menuBar().addMenu('&View')
        self.show_context_action = self.action(view,'Focus details',self.set_context_visible)
        self.show_context_action.setCheckable(True); self.show_context_action.setChecked(False)
        self.action(view,'Jump to latest message',self.jump_to_latest,'Ctrl+End')
        self.action(view,'Bubble colors…',self.show_bubble_colors)
        self.action(view,'Zoom in',lambda:self.transcript.zoomIn(),'Ctrl++')
        self.action(view,'Zoom out',lambda:self.transcript.zoomOut(),'Ctrl+-')
        settings = self.menuBar().addMenu('&Settings')
        self.mutation_actions.append(self.action(settings,'Model Setup…',self.model_setup))
        self.mutation_actions.append(self.action(settings,'Files, saved drafts & history…',self.edit_memory))
        self.instructions_action = self.action(settings,'Workspace instructions…',self.edit_instructions)
        self.mutation_actions.append(self.instructions_action)
        self.action(settings,'Unload model from memory',self.unload_model)
        help_menu = self.menuBar().addMenu('&Help')
        self.action(help_menu,'Getting started',self.getting_started)
        self.action(help_menu,'Training guide',lambda: self.show_guide('FINE-TUNING.md', 'Training guide'))
        self.action(help_menu,'Engine log',self.show_log)
        self.action(help_menu,'About LetraCode',lambda:QMessageBox.about(self,'About LetraCode',f'LetraCode {__version__}\n\nLocal conversations, with your context.\nNative Qt desktop application for Windows and Linux.\nInference: local llama.cpp / GGUF\nStorage: local SQLite\nNo account, telemetry or cloud inference.'))

    def refresh_tree(self):
        selected = ('chat',self.chat_id) if self.chat_id else ('project',self.project_id) if self.project_id else ('global',None)
        self.tree.blockSignals(True); self.tree.clear()
        global_item = QTreeWidgetItem(self.tree,['Everyday']); global_item.setData(0,Qt.ItemDataRole.UserRole,('global',None))
        global_item.setIcon(0,messenger_icon('chat', self.messenger_theme.neutral['ink']))
        project_items = {}
        for project in self.store.projects():
            item = QTreeWidgetItem(self.tree,[project['title']]); item.setData(0,Qt.ItemDataRole.UserRole,('project',project['id']))
            item.setIcon(0,messenger_icon('folder', self.messenger_theme.neutral['ink']))
            project_items[project['id']] = item
        current = None
        for chat in self.store.chats(self.search.text()):
            parent = project_items.get(chat['project_id'],global_item)
            item = QTreeWidgetItem(parent,[chat['title']]); item.setData(0,Qt.ItemDataRole.UserRole,('chat',chat['id']))
            item.setToolTip(0,chat['title']); item.setIcon(0,messenger_icon('chat', self.messenger_theme.neutral['ink']))
            if selected == ('chat',chat['id']):
                current = item
        if selected[0] == 'project':
            current = project_items.get(selected[1])
        elif selected[0] == 'global':
            current = global_item
        self.tree.expandAll()
        if current:
            self.tree.setCurrentItem(current)
        self.tree.blockSignals(False)

    def tree_selected(self,current,previous):
        if not current:
            return
        kind, ident = current.data(0,Qt.ItemDataRole.UserRole)
        if kind == 'chat':
            self.select_chat(ident)
        else:
            self.show_selection(None,ident if kind=='project' else None)

    def select_chat(self, chat_id):
        chat = self.store.chat(chat_id)
        if chat:
            self.show_selection(chat_id,chat['project_id'])
            self.refresh_tree()

    def show_selection(self,chat_id,project_id):
        if self.selection_ready:
            self.save_editors()
        self.loading = True
        self.chat_id, self.project_id = chat_id, project_id
        project = self.store.project(project_id) if project_id else None
        chat = self.store.chat(chat_id) if chat_id else None
        self.chat_title.setText(chat['title'] if chat else project['title'] if project else 'Talk with Strand')
        self.composer.setPlainText(chat['draft'] if chat else self.store.setting('unbound_draft_' + (project_id or 'global'),''))
        self.load_conversation_options()
        self.files_panel.set_project(project_id)
        self.context_hint.setText('Workspace: ' + (project['title'] if project else 'Everyday') +
            '\nThe same Strand, with shared notes and this workspace’s focus. Sources help it find relevant material; they are not reading-access boundaries.')
        self.knowledge_panel.refresh()
        self.update_capability_hint()
        self.instructions_action.setEnabled(project is not None and not self.busy)
        self.loading = False
        self.selection_ready = True
        self.store.set_setting('last_chat',chat_id)
        self.sync_engine()
        self.render_chat()

    def schedule_save(self):
        if not self.loading:
            self.save_timer.start()

    def save_editors(self):
        if self.loading or not self.selection_ready:
            return
        self.save_timer.stop()
        if self.chat_id:
            self.store.set_draft(self.chat_id,self.composer.toPlainText())
        else:
            self.store.set_setting('unbound_draft_' + (self.project_id or 'global'),self.composer.toPlainText())
        return True

    def conversation_options_key(self):
        return 'dialogue_' + self.chat_id if self.chat_id else 'dialogue_unbound_' + (self.project_id or 'global')

    def conversation_options(self):
        return {'enabled': self.conversation_mode.currentIndex() == 1,
                'first_speaker': self.first_speaker.currentIndex(), 'reply_count': self.reply_count.value()}

    def load_conversation_options(self):
        options = self.store.setting(self.conversation_options_key(), {})
        if not isinstance(options, dict):
            options = {}
        self.conversation_mode.setCurrentIndex(1 if options.get('enabled') is True else 0)
        self.first_speaker.setCurrentIndex(1 if options.get('first_speaker') == 1 else 0)
        count = options.get('reply_count', 2)
        self.reply_count.setValue(count if type(count) is int and 1 <= count <= 4 else 2)

    def conversation_options_changed(self, *_):
        if self.loading:
            return
        self.store.set_setting(self.conversation_options_key(), self.conversation_options())
        self.sync_engine()
        self.render_chat()

    def sync_engine(self):
        if self.busy or self.worker or self.training_panel.job is not None:
            return
        config = self.engine_config if self.conversation_mode.currentIndex() == 1 else dataclasses.replace(self.engine_config, secondary_model_path='')
        if config != self.engine.config:
            self.engine.stop()
            self.engine = LocalEngine(config, self.store.directory)

    def update_conversation_controls(self):
        multi = self.conversation_mode.currentIndex() == 1
        for widget in (self.speaker_label, self.first_speaker, self.replies_label, self.reply_count, self.dialogue_hint, self.continue_button):
            widget.setVisible(multi)
        for widget in (self.conversation_mode, self.first_speaker, self.reply_count):
            widget.setEnabled(not self.busy)
        self.actions.setEnabled(not self.busy and not multi)
        self.internet.setEnabled(not self.busy and not multi)
        self.effects.setEnabled(not self.busy and not multi)
        has_prompt = bool(self.chat_id and self.store.rows('SELECT id FROM messages WHERE chat_id=? AND role=? LIMIT 1', (self.chat_id, 'user')))
        self.continue_button.setEnabled(not self.busy and multi and has_prompt and not self.composer.toPlainText().strip())
        self.retry_button.setEnabled(not self.busy and not multi and has_prompt)
        self.repeat_action.setEnabled(not self.busy and not multi and has_prompt)
        self.example_action.setEnabled(not self.busy and has_prompt)
        self.retry_button.setToolTip('Use Continue exchange to add model replies without repeating your prompt.' if multi else 'Repeat your last prompt as a new turn; previous replies are kept.')

    def edit_memory(self):
        if not self.busy:
            self.save_editors()
            self.files_panel.manage_files()

    def edit_instructions(self):
        if self.busy or self.project_id is None:
            return
        project = self.store.project(self.project_id)
        dialog = QDialog(self)
        dialog.setWindowTitle('Workspace instructions')
        dialog.resize(640, 440)
        layout = QVBoxLayout(dialog)
        note = QLabel('Focus Strand on this workspace. Shared notes still follow you. These instructions are included in chats here; reference files are consulted separately.')
        note.setWordWrap(True); layout.addWidget(note)
        editor = QPlainTextEdit(project['instructions']); layout.addWidget(editor)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept); buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.store.update_project(self.project_id, instructions=editor.toPlainText())

    def new_chat(self, checked=False):
        self.save_editors()
        chat = self.store.create_chat('New chat',self.project_id)
        self.store.set_setting('dialogue_' + chat, self.conversation_options())
        self.select_chat(chat); self.composer.setFocus()

    def new_project(self, checked=False):
        if self.busy:
            return
        title,ok = QInputDialog.getText(self,'New workspace','Name this focus for Strand (for example, Writing or LetraCode):')
        if ok and title.strip():
            p = self.store.create_project(title)
            c = self.store.create_chat('New chat',p)
            self.select_chat(c)

    def tree_menu(self,point):
        if self.busy:
            return
        item = self.tree.itemAt(point)
        if not item:
            return
        self.tree.setCurrentItem(item)
        kind,_ = item.data(0,Qt.ItemDataRole.UserRole)
        menu = QMenu(self)
        menu.addAction('New chat',self.new_chat)
        if kind != 'global':
            menu.addAction('Rename…',self.rename_selected)
            menu.addAction('Delete…',self.delete_selected)
        menu.exec(self.tree.viewport().mapToGlobal(point))

    def rename_selected(self):
        if self.busy:
            return
        item = self.tree.currentItem()
        if not item:
            return
        kind,ident = item.data(0,Qt.ItemDataRole.UserRole)
        if kind == 'global':
            return
        title,ok = QInputDialog.getText(self,'Rename','Name:',text=item.text(0))
        if ok and title.strip():
            if kind == 'project': self.store.update_project(ident,title=title.strip())
            else: self.store.rename_chat(ident,title)
            self.chat_title.setText(title.strip())
            self.refresh_tree()

    def delete_selected(self):
        if self.busy:
            return
        item = self.tree.currentItem()
        if not item:
            return
        kind,ident = item.data(0,Qt.ItemDataRole.UserRole)
        if kind == 'global':
            return
        message = 'Delete this project and all its chats and context? Its Memory files and folders, if present, will be archived for recovery. Linked files will stay where they are.' if kind=='project' else 'Delete this chat and its messages?'
        if QMessageBox.question(self,'Delete '+kind+'?',message,QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        self.save_editors()
        archive = None
        try:
            if kind == 'project': archive = self.store.delete_project(ident)
            else: self.store.delete_chat(ident)
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as error:
            QMessageBox.warning(self,'Unable to delete '+kind,str(error))
            return
        self.chat_id = None; self.project_id = None
        self.selection_ready = False
        self.show_selection(None,None); self.refresh_tree()
        if archive:
            self.statusBar().showMessage('Project deleted · memory archived at ' + str(archive['path']))
            if archive.get('warning'):
                QMessageBox.warning(self,'Project deleted · recovery record warning',archive['warning'])
        elif kind == 'project':
            self.statusBar().showMessage('Project deleted · no Memory files were present; Memory was not archived')

    def model_setup(self):
        if self.busy or self.worker or self.training_panel.job is not None:
            return
        dialog = ModelDialog(self.engine_config,self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.engine.stop()
            self.engine_config = dialog.config()
            config = self.engine_config if self.conversation_mode.currentIndex() == 1 else dataclasses.replace(self.engine_config, secondary_model_path='')
            self.engine = LocalEngine(config,self.store.directory)
            self.store.set_setting('engine',dataclasses.asdict(self.engine_config))
            self.store.set_setting('training_active_version', None)
            self.render_chat()
            self.settings_panel.refresh()
            self.statusBar().showMessage('Models configured. Send a message to load the selected conversation mode.')

    def prepare_engine(self):
        multi = self.conversation_mode.currentIndex() == 1
        if not self.engine_config.model_path or not self.engine_config.executable or (multi and not self.engine_config.secondary_model_path):
            self.model_setup()
            if not self.engine_config.model_path or not self.engine_config.executable or (multi and not self.engine_config.secondary_model_path):
                if multi:
                    self.statusBar().showMessage('Choose two different local GGUF models in Model Setup before starting an exchange.')
                return False
        self.sync_engine()
        return True

    def send(self):
        if self.busy or self.worker or self.training_panel.job is not None:
            return
        text = self.composer.toPlainText().strip()
        if not text:
            return
        if not self.prepare_engine():
            return
        # Save the unbound conversation draft before selecting
        # a new chat, whose composer would otherwise start empty.
        if self.save_editors() is False:
            return
        if not self.chat_id:
            scope = self.project_id or 'global'
            c = self.store.create_chat('New chat',self.project_id)
            self.store.set_setting('dialogue_' + c, self.conversation_options())
            self.store.set_draft(c,self.composer.toPlainText())
            self.select_chat(c)
            self.store.set_setting('unbound_draft_' + scope,'')
        if self.save_editors() is False:
            return
        chat = self.store.chat(self.chat_id)
        if chat['title'] == 'New chat':
            self.store.rename_chat(self.chat_id,text.splitlines()[0][:60])
        self.store.add_message(self.chat_id,'user',text)
        self.composer.clear(); self.store.set_draft(self.chat_id,'')
        self.start_worker()

    def continue_exchange(self):
        if self.busy or self.worker or self.training_panel.job is not None or not self.chat_id or self.conversation_mode.currentIndex() != 1:
            return
        if self.composer.toPlainText().strip():
            self.statusBar().showMessage('Send or clear your draft before continuing the exchange.')
            return
        if not any(m['role'] == 'user' for m in self.store.messages(self.chat_id)) or not self.prepare_engine():
            return
        if self.save_editors() is False:
            return
        self.start_worker()

    def start_worker(self):
        if self.busy or self.worker or self.training_panel.job is not None:
            return
        self.store.set_setting('mode',self.mode.currentText())
        for key,widget in [('computer',self.computer),('internet',self.internet),('actions',self.actions),('effects',self.effects)]:
            self.store.set_setting(key,widget.isChecked())
        self.store.set_setting(self.conversation_options_key(), self.conversation_options())
        if self.conversation_mode.currentIndex() == 1:
            rows = self.store.messages(self.chat_id)
            self.exchange_start_id = rows[-1]['id'] if rows else 0
            self.worker = DialogueWorker(self.store,self.chat_id,self.engine,thinking=self.mode.currentText()=='Thinking',computer_enabled=self.computer.isChecked(),first_speaker=self.first_speaker.currentIndex(),reply_count=self.reply_count.value())
        else:
            self.exchange_start_id = None
            self.worker = ConversationWorker(self.store,self.chat_id,self.engine,self.mode.currentText()=='Thinking',self.internet.isChecked(),self.computer.isChecked(),self.actions.isChecked(),actions_enabled=self.effects.isChecked())
        self.worker.changed.connect(self.queue_render)
        self.worker.status.connect(self.statusBar().showMessage)
        self.worker.status.connect(self.queue_render)
        self.worker.approval_needed.connect(self.show_approval)
        self.worker.finished.connect(self.worker_finished)
        self.set_busy(True); self.refresh_tree(); self.render_chat()
        self.worker.start()

    def set_busy(self,busy):
        self.busy = busy
        for widget in (self.new_project_button,self.model_button,self.send_button,self.retry_button,self.mode,self.computer,self.internet,self.actions,self.effects):
            widget.setEnabled(not busy)
        for widget in (self.tree, self.search, self.composer, self.new_chat_button):
            widget.setEnabled(True)
        self.files_panel.set_busy(busy)
        for action in self.mutation_actions:
            action.setEnabled(not busy)
        self.mutation_actions[0].setEnabled(True)
        self.instructions_action.setEnabled(not busy and self.project_id is not None)
        self.stop_button.setEnabled(self.worker is not None)
        self.learn_button.setEnabled(not busy)
        self.training_panel.set_chat_busy(busy)
        if hasattr(self, 'settings_panel'): self.settings_panel.refresh()
        if hasattr(self, 'knowledge_panel'): self.knowledge_panel.refresh()
        self.activity.setVisible(busy)
        origin = self.store.chat(getattr(self.worker, 'chat_id', None)) if self.worker else None
        self.activity.setText(('Strand is working in <a href="active">' + html.escape(origin['title']) + '</a>.' if origin else
                              'A local model job is running.') + ' You can keep reading and prepare a saved draft.')
        self.update_conversation_controls()

    def queue_render(self):
        if not self.render_timer.isActive():
            self.render_timer.start()

    def render_chat(self):
        loaded = getattr(self.engine, 'loaded_models', ())
        self.contact_status.setText('● Generating locally' if self.worker else
                                    '● Local model loaded' if loaded else '○ Local model not loaded')
        model = Path(self.engine_config.model_path).name if self.engine_config.model_path else 'No model selected'
        if self.engine_config.lora_path:
            model += ' + adapter ' + Path(self.engine_config.lora_path).name
        if self.conversation_mode.currentIndex() == 1:
            loaded = getattr(self.engine, 'loaded_models', ())
            a_state = 'loaded' if 'local' in loaded else 'not loaded'
            b_state = 'loaded' if 'local-b' in loaded else 'not loaded'
            second = Path(self.engine_config.secondary_model_path).name if self.engine_config.secondary_model_path else 'Choose in Model Setup'
            state = 'Exchange running' if self.worker else 'Paused · Send or Continue exchange'
            if self.worker and not all(alias in loaded for alias in ('local', 'local-b')):
                state = 'Loading models…'
            self.model_label.setText(f'Model A · {model} · {a_state}\nModel B · {second} · {b_state}\n{state} · {self.reply_count.value()} replies per exchange')
        else:
            self.model_label.setText(f'{model}  ·  Local inference' if self.engine_config.model_path else 'Choose a local model in Model Setup to begin.')
        limit = self.history_limits.get(self.chat_id, 200)
        # Query only the displayed tail, not the complete history on every streamed delta.
        messages = self.store.rows('SELECT * FROM (SELECT * FROM messages WHERE chat_id=? ORDER BY id DESC LIMIT ?) ORDER BY id',
                                   (self.chat_id, limit)) if self.chat_id else []
        first = messages[0]['id'] if messages else 0
        older = bool(first and self.store.rows('SELECT id FROM messages WHERE chat_id=? AND id<? LIMIT 1', (self.chat_id, first)))
        self.load_older_button.setVisible(older)
        chunks = []
        if not messages:
            chunks.append('<h2>Hello, I’m Strand.</h2><p>Ask for help with writing, learning, a file, or a project. '
                'Your conversations and unfinished messages are saved on this computer.</p>'
                '<p><b>Start with a question.</b> A workspace is optional: it focuses the same assistant on work you return to.</p>'
                '<p><a href="letracode:knowledge">Add useful files or review saved notes</a> · '
                '<a href="letracode:improve">Teach with examples</a></p>'
                '<p>Strand asks before changing files, running commands, or making web requests. '
                'You can inspect what was used for each reply.</p>')
            if not self.engine_config.model_path:
                chunks.append('<p><a href="letracode:setup">Choose your local model →</a></p>')
        elif not self.busy:
            from .pause_context import active_checkpoint
            try:
                paused = active_checkpoint(self.store.messages(self.chat_id))
            except (ValueError, RuntimeError):
                paused = None
                chunks.append('<p><b>A saved context record needs attention.</b> The conversation remains readable. '
                              'Back up your data before repairing it; see Settings for recovery tools.</p>')
            if paused:
                chunks.append('<p><b>This task is paused.</b> Review the saved actions below before continuing. '
                    'A missing outcome does not mean an action failed to run. '
                    '<a href="letracode:end-task">Start a fresh task in this chat</a> keeps the record and repeats no actions.</p>')
        for message in messages:
            role = message['role']
            if role == 'tool':
                payload = json.loads(message['payload']).get('message',{})
                result = payload.get('content','')
                state = message_status(message)
                chunks.append(f'<p><b>Action · {html.escape(state)}</b> — {html.escape(payload.get("name","tool"))} &nbsp; <a href="letracode:action/{message["id"]}">View details</a></p>')
                if payload.get('name') == 'remember':
                    try:
                        receipt = json.loads(result)
                        ident = receipt.get('receipt_id') or receipt.get('id')
                        if ident:
                            chunks.append('<p><b>Memory saved</b> · ' + html.escape(str(receipt.get('path', ''))) + '<br>' + html.escape(receipt.get('saved_text', '')).replace('\n', '<br>') + f'<br><a href="letracode:undo-memory/{html.escape(ident)}">Undo this save</a></p>')
                    except (ValueError, TypeError):
                        pass
                continue
            name = {'user':'You','assistant':'Strand','notice':'Notice'}.get(role,role)
            if role == 'assistant':
                try:
                    metadata = json.loads(message.get('payload') or '{}')
                except (TypeError, ValueError):
                    metadata = {}
                speaker = metadata.get('speaker') if isinstance(metadata, dict) else None
                if isinstance(speaker, dict) and isinstance(speaker.get('label'), str):
                    name = speaker['label']
            status = message_status(message)
            if status == 'Response saved · Task outcome unverified': status = ''
            state = f' · {status}' if status else ''
            message_start = len(chunks)
            chunks.append(f'<hr><p><b>{html.escape(name)}{html.escape(state)}</b></p>')
            reasoning = ''
            if role == 'assistant':
                try:
                    payload = json.loads(message['payload'])
                    reasoning = payload.get('reasoning', '') if isinstance(payload, dict) else ''
                except (ValueError, TypeError):
                    pass
                if not isinstance(reasoning, str):
                    reasoning = ''
                if reasoning:
                    expanded = self.thinking_expanded.get(message['id'], message['status'] == 'streaming')
                    label = 'Thinking…' if message['status'] == 'streaming' and not message['content'] else 'Thinking'
                    toggle = 'Hide thinking' if expanded else 'Show thinking'
                    chunks.append(f'<p><b>{label}</b> · <a href="letracode:thinking/{message["id"]}">{toggle}</a></p>')
                    if expanded:
                        chunks.append('<blockquote>' + assistant_html(reasoning, self.transcript.font()) + '</blockquote>')
            text = message['content']
            if role == 'user' or role == 'notice':
                chunks.append('<p>'+html.escape(text).replace('\n','<br>')+'</p>')
            elif text:
                chunks.append(assistant_html(text, self.transcript.font()))
            elif message['status']=='streaming' and not reasoning:
                chunks.append('<p>Working locally…</p>')
            if role == 'assistant' and message['status'] != 'streaming':
                chunks.append(f'<p><small><a href="letracode:receipt/{message["id"]}">Used for this reply</a> · '
                              f'<a href="letracode:copy/{message["id"]}">Copy</a> · '
                              f'<a href="letracode:example/{message["id"]}">Create example</a></small></p>')
            if role in ('user', 'assistant'):
                body = '\n'.join(chunks[message_start + 1:])
                del chunks[message_start:]
                chunks.append(bubble_html(message, name, state, body, self.messenger_theme,
                                          self.transcript.fontMetrics(), self.transcript.viewport().width()))
        rendered = ('<html><head><style>a { color:' + self.messenger_theme.neutral['ink'] + '; text-decoration: underline; } '
                    'pre { white-space: pre-wrap; }</style></head><body>' + '\n'.join(chunks) + '</body></html>')

        if rendered != self.rendered_html or self.rendered_chat != self.chat_id:
            if self.transcript.replace_html(rendered, reset=self.rendered_chat != self.chat_id):
                self.rendered_html, self.rendered_chat = rendered, self.chat_id
        self.update_latest_button()
        if self.chat_id:
            chat = self.store.chat(self.chat_id)
            if chat: self.chat_title.setText(chat['title'])
        self.copy_button.setEnabled(any(m['role']=='assistant' and m['content'] for m in messages))
        self.update_conversation_controls()

    def update_latest_button(self):
        self.latest_button.setVisible(not self.transcript.at_bottom() or self.transcript.textCursor().hasSelection())

    def jump_to_latest(self):
        cursor = self.transcript.textCursor()
        cursor.clearSelection()
        self.transcript.setTextCursor(cursor)
        self.render_chat()
        self.transcript.follow_latest()
        self.update_latest_button()

    def show_approval(self,pending):
        if pending.event.is_set() or not self.worker:
            pending.decide(False); return
        dialog = ApprovalDialog(pending,self)
        dialog.stop_requested.connect(self.stop)
        self.approval_dialog = dialog
        dialog.finished.connect(lambda _:setattr(self,'approval_dialog',None))
        dialog.open()

    def stop(self):
        if self.worker:
            self.stop_button.setEnabled(False)
            self.statusBar().showMessage('Stopping…')
            self.worker.request_stop()
            if self.approval_dialog: self.approval_dialog.reject()

    def worker_finished(self):
        worker = self.worker
        origin = getattr(worker, 'chat_id', self.chat_id)
        self.worker = None
        if self.exchange_start_id is not None:
            participants = dialogue_participants(self.engine_config)
            rows = self.store.messages(origin)
            completed = [m for m in rows if m['id'] > self.exchange_start_id and m['role'] == 'assistant' and m['status'] == 'complete']
            if completed:
                speaker = json.loads(completed[-1]['payload']).get('speaker', {})
                for index, participant in enumerate(participants):
                    if speaker.get('id') == participant['id']:
                        options = self.store.setting('dialogue_' + origin, {})
                        options['first_speaker'] = 1 - index
                        self.store.set_setting('dialogue_' + origin, options)
                        if self.chat_id == origin: self.first_speaker.setCurrentIndex(1 - index)
                        break
            self.exchange_start_id = None
        if self.approval_dialog: self.approval_dialog.reject()
        self.set_busy(False); self.files_panel.refresh(); self.render_chat(); self.refresh_tree()
        self.save_editors()
        if worker: worker.deleteLater()
        if self.closing_when_stopped: self.close()
        else: self.composer.setFocus()

    def retry_reply(self):
        if self.busy or self.worker or self.training_panel.job is not None or not self.chat_id or self.conversation_mode.currentIndex() == 1:
            return
        if self.save_editors() is False:
            return
        rows = self.store.messages(self.chat_id)
        user = next((r for r in reversed(rows) if r['role']=='user'),None)
        if not user:
            return
        # Preserve previous results as a separate turn, so retry never erases work.
        self.store.add_message(self.chat_id,'user',user['content'])
        self.start_worker()

    def copy_reply(self):
        if self.chat_id:
            for message in reversed(self.store.messages(self.chat_id)):
                if message['role']=='assistant' and message['content']:
                    QApplication.clipboard().setText(message['content']); self.statusBar().showMessage('Reply copied'); break

    def refresh_area(self, *_):
        self.knowledge_panel.refresh()
        self.settings_panel.refresh()
        if self.workspaces.currentWidget() is self.knowledge_panel:
            self.files_panel.refresh()

    def set_context_visible(self, visible):
        visible = bool(visible)
        self.context_panel.setVisible(visible)
        self.show_context_action.setChecked(visible)
        self.store.set_setting('show_chat_context', visible)

    def update_capability_hint(self, *_):
        if not hasattr(self, 'capability_hint'): return
        self.capability_hint.setText(
            ('Local files and automatic notes available. ' if self.computer.isChecked() else 'Automatic file text and file reading off. ') +
            ('Changes and commands require approval. ' if self.effects.isChecked() else 'Changes and commands off. ') +
            ('Web requests require approval. ' if self.internet.isChecked() else 'Web research off. ') +
            'Saved conversation text and workspace instructions remain available.' +
            (' Model tools are disabled; only automatic context can be included.' if not self.actions.isChecked() else ''))

    def return_to_active_chat(self):
        origin = getattr(self.worker, 'chat_id', None)
        if origin:
            self.select_chat(origin)
            self.workspaces.setCurrentIndex(0)

    def load_older_messages(self):
        self.history_limits[self.chat_id] = self.history_limits.get(self.chat_id, 200) + 200
        self.render_chat()

    def end_current_task(self):
        if self.busy or not self.chat_id: return
        if not hasattr(self.store, 'end_task'): return
        self.save_editors()
        try:
            self.store.end_task(self.chat_id)
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as error:
            self.statusBar().showMessage(str(error)); return
        self.render_chat()
        self.statusBar().showMessage('Fresh task ready. Earlier work and unresolved action records remain saved; no action was repeated.')

    def reply_details(self, message):
        try:
            payload = json.loads(message.get('payload') or '{}')
            payload = payload if isinstance(payload, dict) else {}
        except (ValueError, TypeError): payload = {}
        context = payload.get('context')
        intro = ('This is a saved record of material prepared for the model. It does not prove that Strand understood it.\n\n'
                 if context else 'This older reply has no automatic-context receipt. Its saved actions remain available below.\n\n')
        details = intro + 'Reply status: ' + message_status(message) + '\n\n'
        if context:
            workspace = context.get('workspace') or {}
            details += 'Focus: ' + str(workspace.get('title') or 'Everyday') + '\n'
            capabilities = context.get('capabilities') or {}
            details += 'Available for this request: ' + ', '.join(label + (' on' if capabilities.get(key) else ' off')
                for key, label in [('read_files', 'Local files'), ('actions', 'Edits and commands'), ('web', 'Web research'), ('tools', 'Model tools')]) + '\n\n'
            details += 'Notes included automatically\n'
            notes = context.get('memory') or []
            details += '\n'.join(str(note.get('path') or note.get('relative_path')) + ' · ' + str(note.get('scope', '')) for note in notes) or 'None'
            details += '\n\nSource excerpts included\n'
            sources = context.get('sources') or []
            details += '\n'.join(str(source.get('path', '')) + ' · line ' + str(source.get('line', '?')) +
                ' · ' + str(source.get('included_chars', len(source.get('text', '')))) + ' characters' +
                (' · partial' if source.get('partial') else '') for source in sources) or 'None'
            details += '\n\nConversation\n' + ('Earlier conversation was reduced to fit. All messages remain saved.' if context.get('history_reduced') else 'The selected conversation context fit without reducing history.')
            omissions = context.get('omissions') or []
            if omissions: details += '\n\nLimits and omissions\n' + '\n'.join(str(item) for item in omissions)
            retrieval = context.get('retrieval') or {}
            if retrieval.get('inventory_truncated'): details += '\nSource inventory reached its limit; some files were not searched.'
            failed = retrieval.get('failed_sources') or []
            if failed: details += '\nSome source files could not be read:\n' + '\n'.join(str(item) for item in failed)
        actions = []
        for row in self.store.messages(self.chat_id):
            if row['id'] >= message['id']: break
            if row['role'] == 'user': actions = []
            elif row['role'] == 'tool': actions.append(row['content'])
        if actions: details += '\n\nSaved actions in this turn\n\n' + '\n\n'.join(actions)
        dialog = QDialog(self); dialog.setWindowTitle('Used for this reply'); dialog.resize(760, 580)
        layout = QVBoxLayout(dialog)
        editor = QPlainTextEdit(details); editor.setReadOnly(True); editor.setAccessibleName('Reply context receipt'); layout.addWidget(editor, 1)
        technical = QCheckBox('Show saved context receipt (technical details)')
        technical.toggled.connect(lambda checked: editor.setPlainText(json.dumps(context, ensure_ascii=False, indent=2) if checked else details))
        technical.setEnabled(bool(context)); layout.addWidget(technical)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close); buttons.rejected.connect(dialog.reject); layout.addWidget(buttons)
        dialog.exec()

    def open_link(self,url):
        text = url.toString()
        if text == 'letracode:knowledge': self.workspaces.setCurrentWidget(self.knowledge_panel); return
        if text == 'letracode:improve': self.workspaces.setCurrentWidget(self.training_panel); return
        if text == 'letracode:end-task': self.end_current_task(); return
        for action in ('receipt', 'copy', 'example'):
            if text.startswith('letracode:' + action + '/'):
                ident = text.rsplit('/', 1)[-1]
                rows = self.store.rows('SELECT * FROM messages WHERE chat_id=? AND id=? AND role=?', (self.chat_id, ident, 'assistant'))
                if rows:
                    message = rows[0]
                    if action == 'receipt': self.reply_details(message)
                    elif action == 'copy': QApplication.clipboard().setText(message['content'])
                    else: self.learn_from_reply(message_id=message['id'])
                return
        if text.startswith('letracode:thinking/'):
            ident = text.removeprefix('letracode:thinking/')
            message = next((m for m in self.store.messages(self.chat_id)
                            if str(m['id']) == ident and m['role'] == 'assistant'), None) if self.chat_id else None
            if message:
                expanded = self.thinking_expanded.get(message['id'], message['status'] == 'streaming')
                self.thinking_expanded[message['id']] = not expanded
                self.render_chat()
            return
        if text.startswith('letracode:undo-memory/'):
            ident = text.removeprefix('letracode:undo-memory/')
            # Only real saved tool receipts in this chat create Undo authority;
            # model-written Markdown cannot address arbitrary receipt IDs.
            allowed = False
            for row in self.store.messages(self.chat_id) if self.chat_id else []:
                if row['role'] != 'tool':
                    continue
                try:
                    message = json.loads(row['payload']).get('message', {})
                    receipt = json.loads(message.get('content', '{}'))
                    allowed |= message.get('name') == 'remember' and ident == (receipt.get('receipt_id') or receipt.get('id'))
                except (ValueError, TypeError, AttributeError):
                    pass
            if not allowed or self.busy:
                return
            try:
                receipt = self.store.strand.receipt(ident)
                # A saved editor draft must survive task Undo unchanged. The
                # file dialog includes missing paths and legacy aliases as well.
                dialog = MemoryDialog(self.store, receipt.get('project_id'), self)
                dialog.scope.setCurrentIndex(dialog.scope.findData(receipt.get('project_id')))
                root = self.store.memory.root_for(receipt.get('project_id'))
                try:
                    relative = Path(receipt['path']).relative_to(root).as_posix()
                    blocked = relative in dialog.saved_drafts()
                except (KeyError, ValueError):
                    blocked = bool(dialog.saved_drafts())
                dialog.deleteLater()
                if blocked:
                    self.context_hint.setText('Your editor draft is kept separately. Save or Reload it in Files, saved drafts & history before undoing this change.')
                    return
                self.store.strand.undo(ident)
                self.store.add_message(self.chat_id, 'notice', 'Memory save undone. Previous file contents restored.')
                self.files_panel.refresh()
                self.render_chat()
            except (OSError, ValueError, RuntimeError) as error:
                QMessageBox.warning(self, 'Memory could not be undone', str(error))
            return
        if text == 'letracode:setup': self.model_setup(); return
        if text.startswith('letracode:action/'):
            ident = text.rsplit('/',1)[-1]
            message = next((m for m in self.store.messages(self.chat_id) if str(m['id'])==ident),None) if self.chat_id else None
            if message: self.text_dialog('Action details',message['content'])
            return
        if url.scheme() not in ('http','https','file'):
            QMessageBox.information(self,'Unsupported link','Only web and local file links can be opened.'); return
        if QMessageBox.question(self,'Open link?',text,QMessageBox.StandardButton.Open|QMessageBox.StandardButton.Cancel,QMessageBox.StandardButton.Cancel)==QMessageBox.StandardButton.Open:
            QDesktopServices.openUrl(url)

    def text_dialog(self,title,text):
        dialog = QDialog(self); dialog.setWindowTitle(title); dialog.resize(800,580)
        layout = QVBoxLayout(dialog)
        editor = QPlainTextEdit(text); editor.setReadOnly(True); editor.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)); layout.addWidget(editor)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close); buttons.rejected.connect(dialog.reject); layout.addWidget(buttons)
        dialog.exec()

    def find_in_chat(self):
        text,ok = QInputDialog.getText(self,'Find in conversation','Find:')
        if ok and text and not self.transcript.find(text):
            # Explicit Find replaces the reading selection before expanding
            # history; otherwise render_chat correctly defers that expansion.
            cursor = self.transcript.textCursor(); cursor.clearSelection(); self.transcript.setTextCursor(cursor)
            if self.chat_id:
                matches = self.store.rows('SELECT id FROM messages WHERE chat_id=? AND instr(lower(content),lower(?))>0 ORDER BY id LIMIT 1', (self.chat_id, text))
                if matches:
                    count = self.store.rows('SELECT COUNT(*) AS n FROM messages WHERE chat_id=? AND id>=?', (self.chat_id, matches[0]['id']))[0]['n']
                    self.history_limits[self.chat_id] = max(200, count)
                    self.render_chat()
            cursor = self.transcript.textCursor(); cursor.movePosition(cursor.MoveOperation.Start); self.transcript.setTextCursor(cursor)
            self.transcript.find(text)

    def export_evaluation(self):
        if not self.chat_id:
            return
        notes, accepted = QInputDialog.getMultiLineText(self, 'Export Evaluation',
            'Optional short notes for the evaluator (up to 8,000 characters):\n'
            'The ZIP includes this saved conversation with privacy omissions.\n'
            'Review transcript prose and notes before sharing.')
        if not accepted:
            return
        name, _ = QFileDialog.getSaveFileName(self, 'Export Evaluation',
            str(Path.home() / 'LetraCode-evaluation.zip'), 'ZIP archive (*.zip)')
        if name:
            try:
                self.store.export_evaluation(self.chat_id, Path(name), notes)
                self.statusBar().showMessage('Evaluation ZIP saved · review before sharing')
            except (OSError, ValueError, sqlite3.Error) as error:
                QMessageBox.warning(self, 'Evaluation export failed', str(error))

    def export_chat(self):
        if not self.chat_id:
            return
        name,_ = QFileDialog.getSaveFileName(self,'Export chat',str(Path.home()/'LetraCode-chat.md'),'Markdown (*.md)')
        if name:
            try:
                Path(name).write_text(self.store.export_markdown(self.chat_id),encoding='utf-8')
                self.statusBar().showMessage('Chat exported')
            except OSError as error:
                QMessageBox.warning(self,'Export failed',str(error))

    def backup(self):
        self.save_editors()
        self.training_panel.persist_draft()
        name,_ = QFileDialog.getSaveFileName(self,'Back up LetraCode',str(Path.home()/'LetraCode-backup.zip'),'ZIP archive (*.zip)')
        if name:
            try:
                self.store.backup(Path(name)); self.statusBar().showMessage('Backup saved · linked originals and model weights stay in place')
            except (OSError,ValueError) as error:
                QMessageBox.warning(self,'Backup failed',str(error))

    def unload_model(self):
        if self.busy:
            QMessageBox.information(self,'Model is busy','Stop the current reply before unloading the model.'); return
        self.engine.stop(); self.render_chat(); self.statusBar().showMessage('Models unloaded from memory')

    def show_log(self):
        path = self.store.directory/'engine.log'
        self.text_dialog('Engine log',path.read_text(encoding='utf-8',errors='replace')[-100000:] if path.exists() else 'The local engine has not written a log yet.')

    def getting_started(self):
        self.show_guide('STRAND-EXPERIENCE.md', 'Getting started with Strand')

    def show_guide(self, filename, title):
        path = Path(__file__).resolve().parent.parent / 'docs' / filename
        if path.is_file():
            self.text_dialog(title, path.read_text(encoding='utf-8'))
        else:
            self.text_dialog(title, 'Start in Settings to choose and test a local model. Chat with Strand; use Knowledge for useful files and saved notes, and Improve for reviewed examples and version comparisons.\n\n' + engine_setup_help())

    def closeEvent(self,event):
        if self.settings_panel.job is not None:
            self.closing_when_stopped = True
            self.settings_panel.stop_check()
            event.ignore(); return
        if self.training_panel.job is not None:
            if not self.closing_when_stopped:
                answer = QMessageBox.question(self, 'Stop and quit?', 'A training or model change is running. Stop it and quit?', QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
                if answer == QMessageBox.StandardButton.Yes:
                    self.closing_when_stopped = True; self.training_panel.stop()
            event.ignore(); return
        if self.worker:
            if not self.closing_when_stopped:
                answer = QMessageBox.question(self,'Stop and quit?','A reply or action is running. Stop it and quit?',QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,QMessageBox.StandardButton.No)
                if answer == QMessageBox.StandardButton.Yes:
                    self.closing_when_stopped = True; self.stop()
            event.ignore(); return
        self.save_editors()
        self.training_panel.persist_draft()
        self.store.set_setting('geometry',bytes(self.saveGeometry().toHex()).decode('ascii'))
        self.store.set_setting('chat_splitter_sizes', self.splitter.sizes())
        self.engine.stop()
        event.accept()

    def learn_from_reply(self, checked=False, message_id=None):
        if self.busy or self.worker or self.training_panel.job is not None or not self.chat_id:
            return
        messages = self.store.messages(self.chat_id)
        for index in range(len(messages) - 1, -1, -1):
            row = messages[index]
            if message_id is not None and row['id'] != message_id: continue
            if row['role'] != 'assistant' or row['status'] != 'complete' or not row['content'].strip():
                continue
            try:
                payload = json.loads(row['payload'])
                if (payload.get('message') or {}).get('tool_calls'):
                    continue
            except (ValueError, TypeError):
                continue
            prompt = next((m['content'] for m in reversed(messages[:index]) if m['role'] == 'user'), '')
            if prompt:
                panel = self.training_panel
                preceding = [m for m in messages[:index] if m['role'] in ('user', 'assistant') and m['status'] == 'complete']
                # Capture dialogue as editable background; do not silently import source/tool bodies.
                if preceding and preceding[-1]['role'] == 'user': preceding = preceding[:-1]
                background = '\n\n'.join(('You: ' if m['role'] == 'user' else 'Strand: ') + m['content'] for m in preceding[-6:])[-12000:]
                project = self.store.project(self.project_id) if self.project_id else None
                if project and project.get('instructions'):
                    background = 'Workspace instructions:\n' + project['instructions'][:4000] + '\n\n' + background
                source = f"chat:{self.chat_id}/message:{row['id']}"
                if hasattr(panel, 'capture_example'):
                    panel.capture_example(prompt, row['content'], source=source, context=background)
                else:
                    panel.new_example(); panel.prompt.setPlainText(prompt); panel.response.setPlainText(row['content'])
                    panel.example_source = source; panel.persist_draft(); panel.tabs.setCurrentIndex(0)
                    panel.progress.setText('Review this draft. Earlier dialogue and source files may be needed to make the request self-contained.')
                self.workspaces.setCurrentWidget(panel)
            return
