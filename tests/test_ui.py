import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')

from PySide6.QtWidgets import QApplication
from letracode.store import Store
from letracode.ui import MainWindow


def test_native_project_context_and_draft_survive_reopen(tmp_path):
    app = QApplication.instance() or QApplication([])
    s = Store(tmp_path / 'data')
    p = s.create_project('Film notes')
    c = s.create_chat('First chat',p)
    w = MainWindow(s)
    w.select_chat(c)
    w.context_editors['memory'].setPlainText('A durable fact')
    w.composer.setPlainText('An unfinished question')
    w.save_editors()
    w.close()
    again = MainWindow(Store(tmp_path / 'data'))
    again.select_chat(c)
    assert again.context_editors['memory'].toPlainText() == 'A durable fact'
    assert again.composer.toPlainText() == 'An unfinished question'
    assert again.styleSheet() == ''
    again.close()


def test_gui_streams_from_managed_local_peer_and_saves_answer(tmp_path):
    import time
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location('engine_fixtures',Path(__file__).with_name('test_engine.py'))
    fixtures = importlib.util.module_from_spec(spec); spec.loader.exec_module(fixtures)
    binary = tmp_path/'llama-server-peer'; binary.write_text(fixtures.PEER_SOURCE); binary.chmod(0o700)
    model = tmp_path/'model.gguf'; model.write_bytes(b'GGUF'+bytes(64))
    app = QApplication.instance() or QApplication([])
    s = Store(tmp_path/'data')
    s.set_setting('engine',{'executable':str(binary),'model_path':str(model)})
    w = MainWindow(s); w.show()
    w.composer.setPlainText('hello')
    w.send()
    deadline = time.monotonic()+5
    while w.worker and time.monotonic()<deadline:
        app.processEvents(); time.sleep(0.01)
    assert w.worker is None
    assert s.messages(w.chat_id)[-1]['content']=='ok'
    assert 'ok' in w.transcript.toPlainText()
    assert w.send_button.isEnabled()
    w.close()


def test_stop_interrupts_silent_model_and_keeps_prompt(tmp_path):
    import time
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location('engine_fixtures',Path(__file__).with_name('test_engine.py'))
    fixtures = importlib.util.module_from_spec(spec); spec.loader.exec_module(fixtures)
    binary = tmp_path/'llama-server-peer'; binary.write_text(fixtures.PEER_SOURCE); binary.chmod(0o700)
    model = tmp_path/'model.gguf'; model.write_bytes(b'GGUF'+bytes(64))
    app = QApplication.instance() or QApplication([])
    s = Store(tmp_path/'data'); s.set_setting('engine',{'executable':str(binary),'model_path':str(model)})
    w = MainWindow(s); w.composer.setPlainText('slow'); w.send()
    deadline = time.monotonic()+5
    while not binary.with_suffix('.requests.json').exists() and time.monotonic()<deadline:
        app.processEvents(); time.sleep(0.01)
    w.stop()
    while w.worker and time.monotonic()<deadline:
        app.processEvents(); time.sleep(0.01)
    assert w.worker is None
    assert s.messages(w.chat_id)[0]['content']=='slow'
    assert any(m['status']=='interrupted' for m in s.messages(w.chat_id))
    assert not w.engine.running
    w.close()


def test_command_approval_requires_acknowledgement_and_defaults_to_deny():
    from PySide6.QtWidgets import QPushButton
    from letracode.dialogs import ApprovalDialog
    from letracode.tools import ApprovalRequest
    from letracode.worker import PendingApproval
    app=QApplication.instance() or QApplication([])
    pending=PendingApproval(ApprovalRequest('Run command?','printf hello','command','Runs with your account'))
    dialog=ApprovalDialog(pending)
    buttons=dialog.findChildren(QPushButton)
    allow=next(b for b in buttons if b.text()=='Approve once')
    deny=next(b for b in buttons if b.text()=='Deny')
    assert not allow.isEnabled()
    assert deny.isDefault()
    dialog.confirm.setChecked(True)
    assert allow.isEnabled()
    dialog.reject()
    assert pending.event.is_set() and not pending.approved


def test_unfinished_prompt_on_welcome_screen_survives_reopen(tmp_path):
    app=QApplication.instance() or QApplication([])
    store=Store(tmp_path/'data')
    w=MainWindow(store)
    assert w.chat_id is None
    w.composer.setPlainText('A thought before creating a chat')
    w.close()
    again=MainWindow(Store(tmp_path/'data'))
    assert again.composer.toPlainText()=='A thought before creating a chat'
    again.close()


def test_approval_dialog_can_stop_the_whole_task():
    from PySide6.QtWidgets import QPushButton
    from letracode.dialogs import ApprovalDialog
    from letracode.tools import ApprovalRequest
    from letracode.worker import PendingApproval
    app=QApplication.instance() or QApplication([])
    pending=PendingApproval(ApprovalRequest('Read file?','/path','read'))
    dialog=ApprovalDialog(pending)
    stop=next((b for b in dialog.findChildren(QPushButton) if b.text()=='Stop task'),None)
    assert stop is not None
    stopped=[]
    dialog.stop_requested.connect(lambda:stopped.append(True))
    stop.click()
    assert stopped==[True]
    assert pending.event.is_set() and not pending.approved


def test_deleting_chat_preserves_unbound_welcome_draft(tmp_path,monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    app=QApplication.instance() or QApplication([])
    store=Store(tmp_path/'data'); w=MainWindow(store)
    w.composer.setPlainText('GLOBAL DRAFT TO KEEP'); w.save_editors()
    c=store.create_chat('Disposable'); w.select_chat(c)
    w.composer.setPlainText('DRAFT IN DELETED CHAT')
    monkeypatch.setattr(QMessageBox,'question',lambda *a,**k:QMessageBox.StandardButton.Yes)
    w.delete_selected()
    assert store.chat(c) is None
    assert w.composer.toPlainText()=='GLOBAL DRAFT TO KEEP'
    w.close()


def test_blocked_project_deletion_keeps_selection_context_and_draft(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path / 'data'); project = store.create_project('Keep this project')
    chat = store.create_chat('Keep this chat', project)
    w = MainWindow(store); w.show_selection(None, project); w.refresh_tree()
    w.composer.setPlainText('Keep this unsent draft')
    w.context_editors['memory'].setPlainText('Keep this memory')
    monkeypatch.setattr(QMessageBox, 'question', lambda *args:QMessageBox.StandardButton.Yes)
    warnings = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda parent, title, message:warnings.append(message))
    def blocked(ident):
        raise ValueError('Memory changed before it could be archived; reload first')
    monkeypatch.setattr(store, 'delete_project', blocked)

    w.delete_selected()

    assert w.project_id == project
    assert w.selection_ready
    assert w.memory_state.project_id == project
    assert w.composer.toPlainText() == 'Keep this unsent draft'
    assert w.context_editors['memory'].toPlainText() == 'Keep this memory'
    assert store.project(project) is not None and store.chat(chat) is not None
    assert store.setting('unbound_draft_' + project) == 'Keep this unsent draft'
    assert warnings and 'Memory changed' in warnings[0]
    w.close()


def test_project_deletion_explains_archive_and_shows_recovery_location(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path / 'data'); project = store.create_project('Delete this project')
    w = MainWindow(store); w.show_selection(None, project); w.refresh_tree()
    w.context_editors['memory'].setPlainText('Archive this memory')
    confirmations = []
    def confirm(parent, title, message, *args):
        confirmations.append(message)
        return QMessageBox.StandardButton.Yes
    monkeypatch.setattr(QMessageBox, 'question', confirm)

    w.delete_selected()

    assert 'archiv' in confirmations[0].lower()
    assert store.project(project) is None
    assert w.project_id is None and w.chat_id is None
    archived = list((store.directory / 'strand' / '.deleted-projects' / project).glob('*.md'))
    assert len(archived) == 1
    assert str(archived[0]) in w.statusBar().currentMessage()
    w.close()


def test_project_deletion_surfaces_archive_record_warning_after_success(tmp_path, monkeypatch):
    import json
    import letracode.store as store_module
    from PySide6.QtWidgets import QMessageBox
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path / 'data'); project = store.create_project('Delete this project')
    w = MainWindow(store); w.show_selection(None, project); w.refresh_tree()
    monkeypatch.setattr(QMessageBox, 'question', lambda *args:QMessageBox.StandardButton.Yes)
    warnings = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda parent, title, message:warnings.append(message))
    safe_write = store_module.safe_write
    def fail_final_record(path, data, *args, **kwargs):
        if '.deleted-projects' in path.parts and path.suffix == '.json' and json.loads(data)['status'] == 'deleted':
            raise OSError('Recovery record write blocked')
        return safe_write(path, data, *args, **kwargs)
    monkeypatch.setattr(store_module, 'safe_write', fail_final_record)

    w.delete_selected()

    assert store.project(project) is None
    assert w.project_id is None
    assert warnings and 'Recovery record write blocked' in warnings[0]
    archive = next((store.directory / 'strand' / '.deleted-projects' / project).glob('*.md'))
    assert str(archive) in w.statusBar().currentMessage()
    w.close()


def test_project_deletion_reports_when_missing_memory_could_not_be_archived(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path / 'data'); project = store.create_project('Delete this project')
    w = MainWindow(store); w.show_selection(None, project); w.refresh_tree()
    store.strand.path('project', project).unlink()
    monkeypatch.setattr(QMessageBox, 'question', lambda *args:QMessageBox.StandardButton.Yes)

    w.delete_selected()

    assert store.project(project) is None
    assert 'not archived' in w.statusBar().currentMessage().lower()
    assert not store.strand.path('project', project).exists()
    w.close()
