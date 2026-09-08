"""Native Qt Widgets interface; no fixed palette, style or application font."""
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
    QSplitter, QTabWidget, QTextBrowser, QToolBar, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget)

from . import __version__
from .dialogs import ApprovalDialog, LinksDialog, ModelDialog
from .engine import EngineConfig, LocalEngine
from .worker import ConversationWorker
from .store import message_status
from .strand_ui import MemoryEditorState, StrandDialog
from .memory_ui import MemoryDialog


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


class SafeBrowser(QTextBrowser):
    def loadResource(self, kind, url):
        # Model text must never fetch images, local files, styles, or remote content.
        return None


class Composer(QPlainTextEdit):
    submitted = Signal()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.submitted.emit()
        else:
            super().keyPressEvent(event)


class MainWindow(QMainWindow):
    def __init__(self, store):
        super().__init__()
        self.store = store
        self.chat_id = None
        self.project_id = None
        self.worker = None
        self.approval_dialog = None
        self.loading = False
        self.selection_ready = False
        self.memory_state = None
        self.closing_when_stopped = False
        config_data = self.store.setting('engine',{})
        valid_fields = {f.name for f in dataclasses.fields(EngineConfig)}
        try:
            self.engine_config = EngineConfig(**{k:v for k,v in config_data.items() if k in valid_fields})
        except (TypeError,ValueError):
            self.engine_config = EngineConfig()
        if not self.engine_config.executable:
            self.engine_config.executable = shutil.which('llama-server') or ''
        self.engine = LocalEngine(self.engine_config,store.directory)
        self.setWindowTitle('LetraCode')
        self.resize(1260,820)
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
        self.statusBar().showMessage('Ready · everything is saved on this computer')

    def build_ui(self):
        self.splitter = QSplitter()
        self.setCentralWidget(self.splitter)
        sidebar = QWidget(); side = QVBoxLayout(sidebar)
        side.setContentsMargins(12,12,8,12)
        title = QLabel('LetraCode')
        font = title.font(); font.setBold(True); font.setPointSizeF(font.pointSizeF()+3); title.setFont(font)
        side.addWidget(title)
        subtitle = QLabel('Local conversations. Your context.')
        subtitle.setWordWrap(True); side.addWidget(subtitle)
        side.addSpacing(10)
        self.search = QLineEdit()
        self.search.setPlaceholderText('Search chats…')
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _: self.refresh_tree())
        side.addWidget(self.search)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.setIndentation(16)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.tree_menu)
        self.tree.currentItemChanged.connect(self.tree_selected)
        side.addWidget(self.tree,1)
        self.new_chat_button = QPushButton(QIcon.fromTheme('list-add'),'New chat')
        self.new_chat_button.clicked.connect(self.new_chat)
        self.new_project_button = QPushButton(QIcon.fromTheme('folder-new'),'New project…')
        self.new_project_button.clicked.connect(self.new_project)
        side.addWidget(self.new_chat_button); side.addWidget(self.new_project_button)
        self.splitter.addWidget(sidebar)

        main = QWidget(); center = QVBoxLayout(main); center.setContentsMargins(12,12,12,12)
        header = QHBoxLayout()
        self.chat_title = QLabel('A little room to think.')
        font = self.chat_title.font(); font.setBold(True); font.setPointSizeF(font.pointSizeF()+2); self.chat_title.setFont(font)
        self.chat_title.setTextFormat(Qt.TextFormat.PlainText)
        header.addWidget(self.chat_title,1)
        self.model_button = QPushButton(QIcon.fromTheme('configure'),'Model Setup…')
        self.model_button.clicked.connect(self.model_setup)
        header.addWidget(self.model_button)
        center.addLayout(header)
        self.model_label = QLabel()
        self.model_label.setTextFormat(Qt.TextFormat.PlainText)
        self.model_label.setWordWrap(True)
        center.addWidget(self.model_label)
        self.transcript = SafeBrowser()
        self.transcript.setOpenLinks(False)
        self.transcript.setOpenExternalLinks(False)
        self.transcript.anchorClicked.connect(self.open_link)
        center.addWidget(self.transcript,1)
        row = QHBoxLayout()
        self.copy_button = QPushButton(QIcon.fromTheme('edit-copy'),'Copy last reply')
        self.copy_button.clicked.connect(self.copy_reply)
        self.retry_button = QPushButton(QIcon.fromTheme('view-refresh'),'Retry reply')
        self.retry_button.clicked.connect(self.retry_reply)
        row.addWidget(self.copy_button); row.addWidget(self.retry_button); row.addStretch()
        self.mode = QComboBox(); self.mode.addItems(['Instant','Thinking'])
        self.mode.setToolTip('Thinking asks compatible models to reason before replying. Support depends on your model.')
        self.mode.setCurrentText(self.store.setting('mode','Instant'))
        row.addWidget(self.mode)
        center.addLayout(row)
        self.composer = Composer()
        self.composer.setPlaceholderText('Ask a question…   Ctrl+Enter to send; Enter for a new line.')
        self.composer.setMinimumHeight(90); self.composer.setMaximumHeight(180)
        self.composer.textChanged.connect(self.schedule_save)
        self.composer.submitted.connect(self.send)
        center.addWidget(self.composer)
        controls = QHBoxLayout()
        self.computer = QCheckBox('Computer')
        self.computer.setChecked(self.store.setting('computer',True))
        self.computer.setToolTip('Use linked project files and computer tools. Commands, edits and access outside project links require approval.')
        self.internet = QCheckBox('Internet')
        self.internet.setChecked(self.store.setting('internet',True))
        self.internet.setToolTip('Permit web research. Each outgoing query or URL requires approval.')
        self.actions = QCheckBox('Actions')
        self.actions.setChecked(self.store.setting('actions',True))
        self.actions.setToolTip('Allow model tool calls. Turn off for plain chat with a model that does not support tools; linked evidence can still be included when Computer is on.')
        for check in (self.computer,self.internet,self.actions):
            controls.addWidget(check)
        controls.addStretch()
        self.stop_button = QPushButton(QIcon.fromTheme('process-stop'),'Stop')
        self.stop_button.setEnabled(False); self.stop_button.clicked.connect(self.stop)
        self.send_button = QPushButton(QIcon.fromTheme('mail-send'),'Send')
        self.send_button.clicked.connect(self.send)
        controls.addWidget(self.stop_button); controls.addWidget(self.send_button)
        center.addLayout(controls)
        self.splitter.addWidget(main)

        self.context_panel = QWidget(); context = QVBoxLayout(self.context_panel)
        context.setContentsMargins(8,12,12,12)
        context_title = QLabel('Context and Memory')
        font = context_title.font(); font.setBold(True); context_title.setFont(font)
        context.addWidget(context_title)
        self.context_hint = QLabel('Shared with every chat in this project.\nEdits save automatically.')
        self.context_hint.setWordWrap(True); context.addWidget(self.context_hint)
        tabs = QTabWidget()
        self.context_editors = {}
        fields = [('Memory','memory','Durable facts, decisions and preferences.'),('Current Context','current_context','What you are working on now, open questions and next steps.'),('Instructions','instructions','How the AI should work with this project.')]
        for label,key,placeholder in fields:
            edit = QPlainTextEdit(); edit.setPlaceholderText(placeholder)
            edit.textChanged.connect(self.schedule_save)
            tabs.addTab(edit,label); tabs.setTabToolTip(tabs.count()-1,'Current Context' if key=='current_context' else label)
            self.context_editors[key] = edit
        context.addWidget(tabs,1)
        self.memory_reload = QPushButton('Reload memory file')
        self.memory_reload.setToolTip('Use the external file version. Copy any unsaved editor text first; Reload discards that draft.')
        self.memory_reload.clicked.connect(self.reload_memory)
        context.addWidget(self.memory_reload)
        self.memory_button = QPushButton('Memory folders…')
        self.memory_button.clicked.connect(self.edit_memory)
        self.strand_button = self.memory_button  # Compatibility for busy-state handling.
        context.addWidget(self.memory_button)
        self.links_summary = QLabel(); self.links_summary.setWordWrap(True); context.addWidget(self.links_summary)
        self.links_button = QPushButton(QIcon.fromTheme('insert-link'),'Linked files & folders…')
        self.links_button.clicked.connect(self.manage_links)
        context.addWidget(self.links_button)
        self.open_project_button = QPushButton(QIcon.fromTheme('folder-open'),'Open linked folder')
        self.open_project_button.clicked.connect(self.open_project_folder)
        context.addWidget(self.open_project_button)
        self.splitter.addWidget(self.context_panel)
        self.splitter.setSizes([240,700,320])
        self.splitter.setStretchFactor(1,1)

    def action(self, menu, title, callback, shortcut=None):
        action = QAction(title,self)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
        action.triggered.connect(callback)
        menu.addAction(action)
        return action

    def build_menus(self):
        file = self.menuBar().addMenu('&File')
        self.mutation_actions = [self.action(file,'New &chat',self.new_chat,'Ctrl+N'), self.action(file,'New &project…',self.new_project,'Ctrl+Shift+N')]
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
        view = self.menuBar().addMenu('&View')
        self.show_context_action = self.action(view,'Project context',lambda checked:self.context_panel.setVisible(checked))
        self.show_context_action.setCheckable(True); self.show_context_action.setChecked(True)
        self.action(view,'Zoom in',lambda:self.transcript.zoomIn(),'Ctrl++')
        self.action(view,'Zoom out',lambda:self.transcript.zoomOut(),'Ctrl+-')
        settings = self.menuBar().addMenu('&Settings')
        self.mutation_actions.append(self.action(settings,'Model Setup…',self.model_setup))
        self.mutation_actions.append(self.action(settings,'Memory folders…',self.edit_memory))
        self.action(settings,'Unload model from memory',self.unload_model)
        help_menu = self.menuBar().addMenu('&Help')
        self.action(help_menu,'Getting started',self.getting_started)
        self.action(help_menu,'Engine log',self.show_log)
        self.action(help_menu,'About LetraCode',lambda:QMessageBox.about(self,'About LetraCode',f'LetraCode {__version__}\n\nLocal conversations, with your context.\nNative Qt desktop application for Fedora KDE.\nInference: local llama.cpp / GGUF\nStorage: local SQLite\nNo account, telemetry or cloud inference.'))

    def refresh_tree(self):
        selected = ('chat',self.chat_id) if self.chat_id else ('project',self.project_id) if self.project_id else ('global',None)
        self.tree.blockSignals(True); self.tree.clear()
        global_item = QTreeWidgetItem(self.tree,['Global chats']); global_item.setData(0,Qt.ItemDataRole.UserRole,('global',None))
        global_item.setIcon(0,QIcon.fromTheme('mail-message'))
        project_items = {}
        for project in self.store.projects():
            item = QTreeWidgetItem(self.tree,[project['title']]); item.setData(0,Qt.ItemDataRole.UserRole,('project',project['id']))
            item.setIcon(0,QIcon.fromTheme('folder'))
            project_items[project['id']] = item
        current = None
        for chat in self.store.chats(self.search.text()):
            parent = project_items.get(chat['project_id'],global_item)
            item = QTreeWidgetItem(parent,[chat['title']]); item.setData(0,Qt.ItemDataRole.UserRole,('chat',chat['id']))
            item.setToolTip(0,chat['title']); item.setIcon(0,QIcon.fromTheme('text-x-generic'))
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
        if not current or self.worker:
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
        self.chat_title.setText(chat['title'] if chat else project['title'] if project else 'A little room to think.')
        self.composer.setPlainText(chat['draft'] if chat else self.store.setting('unbound_draft_' + (project_id or 'global'),''))
        for key,edit in self.context_editors.items():
            edit.setPlainText(project.get(key,'') if project else '')
            edit.setEnabled(project is not None or key == 'memory')
        self.memory_state = MemoryEditorState(self.store, 'project' if project else 'global', project_id)
        self.context_editors['memory'].setPlainText(self.memory_state.text)
        self.context_editors['memory'].setReadOnly(not self.memory_state.available)
        self.links_button.setEnabled(project is not None)
        self.open_project_button.setEnabled(project is not None)
        self.context_hint.setText(self.memory_state.error or (('Project memory' if project else 'Global memory') + ' · edits save automatically.\n' + str(self.memory_state.snapshot['path'])))
        self.refresh_links()
        self.loading = False
        self.selection_ready = True
        self.store.set_setting('last_chat',chat_id)
        self.render_chat()

    def refresh_links(self):
        links = self.store.links(self.project_id) if self.project_id else []
        missing = sum(not Path(p).exists() for p in links)
        self.links_summary.setText(f'{len(links)} linked file/folder' + ('s' if len(links)!=1 else '') + (f' · {missing} unavailable' if missing else ''))

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
        if self.project_id:
            self.store.update_project(self.project_id,**{key:edit.toPlainText() for key,edit in self.context_editors.items() if key != 'memory'})
        if self.memory_state:
            ok = self.memory_state.save(self.context_editors['memory'].toPlainText())
            self.context_editors['memory'].setReadOnly(not self.memory_state.available)
            if not ok:
                self.context_hint.setText(self.memory_state.error)
                self.statusBar().showMessage('Memory conflict · external file preserved; editor draft saved separately' if self.memory_state.available else 'Memory unavailable · repair the file, then Reload memory file')
                return False
            self.loading = True
            if self.context_editors['memory'].toPlainText() != self.memory_state.text:
                self.context_editors['memory'].setPlainText(self.memory_state.text)
            self.loading = False
        return True

    def reload_memory(self):
        if not self.memory_state or self.worker:
            return
        ok = self.memory_state.reload(self.context_editors['memory'].toPlainText())
        self.loading = True
        self.context_editors['memory'].setPlainText(self.memory_state.text)
        self.context_editors['memory'].setReadOnly(not self.memory_state.available)
        self.loading = False
        self.context_hint.setText('Reloaded memory from ' + str(self.memory_state.snapshot['path']) if ok else self.memory_state.error)

    def edit_strand(self):
        if self.worker:
            return
        self.save_editors()
        dialog = StrandDialog(self.store, self.project_id, self)
        dialog.exec()
        # The dialog can resolve this pane's saved conflict. Reopen the state
        # instead of reviving a stale buffer after a deliberate resolution.
        self.memory_state = MemoryEditorState(self.store, 'project' if self.project_id else 'global', self.project_id)
        self.loading = True
        self.context_editors['memory'].setPlainText(self.memory_state.text)
        self.context_editors['memory'].setReadOnly(not self.memory_state.available)
        self.loading = False
        self.context_hint.setText(self.memory_state.error or str(self.memory_state.snapshot['path']))

    def edit_memory(self):
        if self.worker:
            return
        self.save_editors()
        dialog = MemoryDialog(self.store, self.project_id, self)
        dialog.exec()
        # Reopen the compatibility pane after tree edits, moves or resolutions.
        # A removed alias remains unavailable instead of being recreated.
        self.memory_state = MemoryEditorState(self.store, 'project' if self.project_id else 'global', self.project_id)
        self.loading = True
        self.context_editors['memory'].setPlainText(self.memory_state.text)
        self.context_editors['memory'].setReadOnly(not self.memory_state.available)
        self.loading = False
        self.context_hint.setText(self.memory_state.error or str(self.memory_state.snapshot['path']))

    def new_chat(self, checked=False):
        if self.worker:
            return
        self.save_editors()
        chat = self.store.create_chat('New chat',self.project_id)
        self.select_chat(chat); self.composer.setFocus()

    def new_project(self, checked=False):
        if self.worker:
            return
        title,ok = QInputDialog.getText(self,'New project','Project name:')
        if ok and title.strip():
            p = self.store.create_project(title)
            c = self.store.create_chat('New chat',p)
            self.select_chat(c)

    def tree_menu(self,point):
        if self.worker:
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
        if self.worker:
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
        if self.worker:
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
        self.memory_state = None
        self.show_selection(None,None); self.refresh_tree()
        if archive:
            self.statusBar().showMessage('Project deleted · memory archived at ' + str(archive['path']))
            if archive.get('warning'):
                QMessageBox.warning(self,'Project deleted · recovery record warning',archive['warning'])
        elif kind == 'project':
            self.statusBar().showMessage('Project deleted · no Memory files were present; Memory was not archived')

    def manage_links(self):
        if self.project_id and not self.worker:
            LinksDialog(self.store,self.project_id,self).exec(); self.refresh_links()

    def open_project_folder(self):
        if not self.project_id:
            return
        links = self.store.links(self.project_id)
        if links:
            path = Path(links[0]); path = path if path.is_dir() else path.parent
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def model_setup(self):
        if self.worker:
            return
        dialog = ModelDialog(self.engine_config,self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.engine.stop()
            self.engine_config = dialog.config()
            self.engine = LocalEngine(self.engine_config,self.store.directory)
            self.store.set_setting('engine',dataclasses.asdict(self.engine_config))
            self.render_chat()
            self.statusBar().showMessage('Model configured. Send a message to load it.')

    def send(self):
        if self.worker:
            return
        text = self.composer.toPlainText().strip()
        if not text:
            return
        if not self.engine_config.model_path or not self.engine_config.executable:
            self.model_setup()
            if not self.engine_config.model_path or not self.engine_config.executable:
                return
        # Save the unbound draft and resolve memory conflicts before selecting
        # a new chat, whose composer would otherwise start empty.
        if self.save_editors() is False:
            return
        if not self.chat_id:
            scope = self.project_id or 'global'
            c = self.store.create_chat('New chat',self.project_id)
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

    def start_worker(self):
        self.store.set_setting('mode',self.mode.currentText())
        for key,widget in [('computer',self.computer),('internet',self.internet),('actions',self.actions)]:
            self.store.set_setting(key,widget.isChecked())
        self.worker = ConversationWorker(self.store,self.chat_id,self.engine,self.mode.currentText()=='Thinking',self.internet.isChecked(),self.computer.isChecked(),self.actions.isChecked())
        self.worker.changed.connect(self.queue_render)
        self.worker.status.connect(self.statusBar().showMessage)
        self.worker.approval_needed.connect(self.show_approval)
        self.worker.finished.connect(self.worker_finished)
        self.set_busy(True); self.refresh_tree(); self.render_chat()
        self.worker.start()

    def set_busy(self,busy):
        for widget in (self.tree,self.search,self.new_chat_button,self.new_project_button,self.model_button,self.composer,self.send_button,self.retry_button,self.mode,self.computer,self.internet,self.actions):
            widget.setEnabled(not busy)
        for edit in self.context_editors.values():
            edit.setEnabled(not busy and (self.project_id is not None or edit is self.context_editors['memory']))
        self.strand_button.setEnabled(not busy)
        self.memory_reload.setEnabled(not busy)
        self.links_button.setEnabled(not busy and self.project_id is not None)
        for action in self.mutation_actions:
            action.setEnabled(not busy)
        self.stop_button.setEnabled(busy)

    def queue_render(self):
        if not self.render_timer.isActive():
            self.render_timer.start()

    def render_chat(self):
        model = Path(self.engine_config.model_path).name if self.engine_config.model_path else 'No model selected'
        self.model_label.setText(f'{model}  ·  Local inference' if self.engine_config.model_path else 'Choose a local model in Model Setup to begin.')
        scrollbar = self.transcript.verticalScrollBar()
        previous = scrollbar.value(); bottom = previous >= scrollbar.maximum()-50
        messages = self.store.messages(self.chat_id) if self.chat_id else []
        chunks = []
        if not messages:
            chunks.append('<h2>What are we working on?</h2><p>Start a conversation, or create a project for work you want to return to.</p><p><b>Projects remember the context you give them.</b><br>Link your files and edit Memory, Current Context and Instructions. Every chat in that project can use them.</p><p><b>You control computer access.</b><br>Review commands, file edits and outgoing web requests before they run.</p>')
            if not self.engine_config.model_path:
                chunks.append('<p><a href="letracode:setup">Choose your local model →</a></p>')
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
            name = {'user':'You','assistant':'LetraCode','notice':'Notice'}.get(role,role)
            status = message_status(message)
            state = f' · {status}' if status else ''
            chunks.append(f'<hr><p><b>{name}{html.escape(state)}</b></p>')
            text = message['content']
            if role == 'user' or role == 'notice':
                chunks.append('<p>'+html.escape(text).replace('\n','<br>')+'</p>')
            elif text:
                chunks.append(assistant_html(text, self.transcript.font()))
            elif message['status']=='streaming':
                chunks.append('<p>Working locally…</p>')
        self.transcript.setHtml('\n'.join(chunks))
        scrollbar.setValue(scrollbar.maximum() if bottom else previous)
        if self.chat_id:
            chat = self.store.chat(self.chat_id)
            if chat: self.chat_title.setText(chat['title'])
        self.copy_button.setEnabled(any(m['role']=='assistant' and m['content'] for m in messages))
        self.retry_button.setEnabled(not self.worker and any(m['role']=='user' for m in messages))

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
        self.worker = None
        if self.approval_dialog: self.approval_dialog.reject()
        self.set_busy(False); self.render_chat(); self.refresh_tree()
        self.save_editors()
        if worker: worker.deleteLater()
        if self.closing_when_stopped: self.close()
        else: self.composer.setFocus()

    def retry_reply(self):
        if self.worker or not self.chat_id:
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

    def open_link(self,url):
        text = url.toString()
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
            if not allowed or self.worker:
                return
            try:
                receipt = self.store.strand.receipt(ident)
                affected = self.memory_state and (receipt['scope'], receipt.get('project_id')) == (self.memory_state.scope, self.memory_state.project_id)
                if self.memory_state and receipt.get('project_id') == self.memory_state.project_id:
                    # Generic remember uses a stable file identity; the quick
                    # pane may address that same file through a legacy alias.
                    current_path = self.store.strand.path(self.memory_state.scope, self.memory_state.project_id)
                    affected = affected or str(current_path) == receipt.get('path')
                if affected:
                    text = self.context_editors['memory'].toPlainText()
                    if self.memory_state.tree_draft or text != self.memory_state.snapshot['text']:
                        self.memory_state.keep_draft(text)
                        self.context_hint.setText('Your editor draft is kept separately. Save or Reload it in Memory folders before undoing a saved change.'
                            if self.memory_state.tree_draft else 'Your editor draft is kept separately. Save or Reload memory file before undoing a saved change.')
                        return
                self.store.strand.undo(ident)
                self.store.add_message(self.chat_id, 'notice', 'Memory save undone. Previous file contents restored.')
                if affected:
                    self.reload_memory()
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
        name,_ = QFileDialog.getSaveFileName(self,'Back up LetraCode',str(Path.home()/'LetraCode-backup.zip'),'ZIP archive (*.zip)')
        if name:
            try:
                self.store.backup(Path(name)); self.statusBar().showMessage('Backup saved · linked originals and model weights stay in place')
            except (OSError,ValueError) as error:
                QMessageBox.warning(self,'Backup failed',str(error))

    def unload_model(self):
        if self.worker:
            QMessageBox.information(self,'Model is busy','Stop the current reply before unloading the model.'); return
        self.engine.stop(); self.statusBar().showMessage('Model unloaded from memory')

    def show_log(self):
        path = self.store.directory/'engine.log'
        self.text_dialog('Engine log',path.read_text(encoding='utf-8',errors='replace')[-100000:] if path.exists() else 'The local engine has not written a log yet.')

    def getting_started(self):
        self.text_dialog('Getting started','1. Open Model Setup. Choose llama-server and a local instruction/chat GGUF model.\n\nOn Fedora, the installer installs the system Qt dependency. Install the inference engine with:\n  sudo dnf install llama-cpp\n\nCPU mode works without GPU configuration. For an NVIDIA GPU, use a compatible llama.cpp CUDA or Vulkan build and choose it in Model Setup, then increase GPU layers. New models may need a newer llama.cpp version.\n\n2. Create a chat and type a question. Ctrl+Enter sends.\n\n3. Create a project for shared work. Link files or folders. Current Context, Instructions and the quick Memory pane save automatically. Use Memory folders to organize Markdown and text files in nested folders; its Save file button applies edits and always-active choices. Navigation and Close keep tree drafts separately. Only always-active files are included automatically; other Memory is available to list, search and read when relevant.\n\n4. Review action dialogs. Every command and file edit needs your approval. Internet requests show the exact outgoing query or URL. Deny anything you do not want.\n\n5. If your model does not support tool calls, turn off Actions. Computer still controls whether linked evidence is included. Turn Internet off to prevent web tools.\n\n6. Use File → Export Evaluation for a privacy-filtered ZIP of one saved conversation, recorded actions, errors, evidence and run metadata. It omits private source and Memory tool bodies; review the transcript and optional notes before sharing. For recovery, use Back up chats, Memory and source backups instead. Backups include a recovery guide; logs and migration snapshots are omitted. Linked originals and model weights are separate.\n\nLimits: text/source, PDF and DOCX extraction are bounded; images, scanned PDF OCR, audio and video are not interpreted. Some websites block automated retrieval. Small local models may need smaller, clearer tasks. LetraCode does not guarantee the correctness of a model’s reasoning.\n\nUninstalling the app retains your local conversations and projects.')

    def closeEvent(self,event):
        if self.worker:
            if not self.closing_when_stopped:
                answer = QMessageBox.question(self,'Stop and quit?','A reply or action is running. Stop it and quit?',QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,QMessageBox.StandardButton.No)
                if answer == QMessageBox.StandardButton.Yes:
                    self.closing_when_stopped = True; self.stop()
            event.ignore(); return
        self.save_editors()
        self.store.set_setting('geometry',bytes(self.saveGeometry().toHex()).decode('ascii'))
        self.engine.stop()
        event.accept()
