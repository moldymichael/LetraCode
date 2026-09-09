"""Native UI through real worker/storage/engine to a local router protocol peer."""
import dataclasses
import json
import os
import time
import zipfile

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtWidgets import QApplication

from letracode.store import Store
from letracode.ui import MainWindow
from test_multimodel_engine import router  # Shared local HTTP fixture.


pytestmark = pytest.mark.usefixtures('python_engine_peer')


def test_gui_router_exchange_continue_and_backup_preserve_shared_transcript(router, tmp_path):
    engine, peer, _, _ = router
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path / 'conversation')
    store.set_setting('engine', dataclasses.asdict(engine.config))
    window = MainWindow(store)

    def finish():
        deadline = time.monotonic() + 5
        while window.worker and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        assert window.worker is None

    try:
        window.conversation_mode.setCurrentIndex(1)
        window.composer.setPlainText('Discuss this together.')
        window.send()
        finish()
        rows = store.messages(window.chat_id)
        assert [r['content'] for r in rows] == [
            'Discuss this together.', 'local response', 'local-b response']
        assert window.engine.loaded_models == ('local', 'local-b')
        assert 'Model B' in window.transcript.toPlainText()
        assert 'loaded' in window.model_label.text()
        requests = json.loads(peer.with_suffix('.requests.json').read_text())
        completions = [r['body'] for r in requests if r['path'] == '/v1/chat/completions']
        assert [r['model'] for r in completions] == ['local', 'local-b']
        transcript = json.loads(completions[1]['messages'][1]['content'])['conversation']
        assert transcript[-1]['role'] == 'assistant'
        assert transcript[-1]['content'] == 'local response'
        assert transcript[-1]['speaker'].startswith('Model A')
        assert all('tools' not in r for r in completions)

        # Idle Qt event processing does not schedule another exchange.
        for _ in range(5):
            app.processEvents()
            time.sleep(0.01)
        assert store.messages(window.chat_id) == rows
        window.continue_button.click()
        finish()
        saved = store.messages(window.chat_id)
        assert len(saved) == 5
        assert sum(r['role'] == 'user' for r in saved) == 1
        backup = tmp_path / 'conversation.zip'
        store.backup(backup)
        with zipfile.ZipFile(backup) as archive:
            data = json.loads(archive.read('letracode.json'))
        exported = next(r for r in data['messages'] if r['id'] == saved[-1]['id'])
        assert json.loads(exported['payload'])['speaker'] == json.loads(saved[-1]['payload'])['speaker']
        window.unload_model()
        assert window.engine.loaded_models == ()
    finally:
        if window.worker:
            window.stop()
            finish()
        window.close()
