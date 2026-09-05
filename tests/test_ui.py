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
