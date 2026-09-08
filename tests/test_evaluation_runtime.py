"""Future runs retain the configuration and explicit decisions actually used."""
import json

from letracode.store import Store
from letracode.worker import ConversationWorker


class Engine:
    config = type('Config', (), {'context_size': 16384, 'max_tokens': 1024,
        'model_path': '/home/private/models/fixture.gguf',
        'executable': '/home/private/bin/llama-server', 'gpu_layers': 0})()

    def start(self, *args):
        pass

    def cancel(self):
        pass

    def complete(self, *args):
        return {'role': 'assistant', 'content': 'Saved reply'}


def test_run_configuration_is_recorded_and_survives_later_settings(tmp_path):
    store = Store(tmp_path / 'data')
    chat = store.create_chat()
    store.add_message(chat, 'user', 'Hello')
    worker = ConversationWorker(store, chat, Engine(), thinking=True, web_enabled=False)
    worker.run()
    store.set_setting('engine', {'model_path': '/unrelated/new.gguf'})
    data = json.loads(next(row for row in store.messages(chat) if row['role'] == 'assistant')['payload'])
    config = data['run_configuration']
    assert config['model_name'] == 'fixture.gguf'
    assert config['engine_name'] == 'llama-server'
    assert config['thinking'] is True and config['mode'] == 'Thinking'
    assert config['context_size'] == 16384 and config['max_tokens'] == 1024
    assert config['web_enabled'] is False
    assert '/home/' not in json.dumps(config)


def test_explicit_approval_decision_saved_with_tool_result(tmp_path):
    class ActionEngine(Engine):
        def complete(self, *args):
            return {'role': 'assistant', 'content': '', 'tool_calls': [
                {'id': 'command', 'type': 'function', 'function': {
                    'name': 'run_command', 'arguments': json.dumps({
                        'command': 'printf hello', 'cwd': str(tmp_path)})}}]}

    store = Store(tmp_path / 'data'); chat = store.create_chat()
    store.add_message(chat, 'user', 'Try this command')
    worker = ConversationWorker(store, chat, ActionEngine())
    worker.approval_needed.connect(lambda pending: pending.decide(False))
    worker.run()
    row = next(row for row in store.messages(chat) if row['role'] == 'tool')
    approval = json.loads(row['payload'])['approvals'][0]
    assert approval['decision'] == 'denied'
    assert approval['kind'] == 'command'
    assert approval['requested_at'] <= approval['decided_at']
    assert 'details' not in approval
