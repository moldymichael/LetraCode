"""One cancellable conversation job. Qt UI work stays on the main thread."""
from __future__ import annotations

import json
import threading
import time

from PySide6.QtCore import QThread, Signal

from .context import build_context
from .engine import Cancelled
from .tools import ApprovalRequest, TOOL_SCHEMAS, ToolExecutor


class PendingApproval:
    def __init__(self, request: ApprovalRequest):
        self.request = request
        self.event = threading.Event()
        self.approved = False

    def decide(self, approved: bool):
        self.approved = bool(approved)
        self.event.set()


def repair_tool_history(turn):
    """Represent missing outcomes honestly after a crash, without rerunning actions."""
    result = []
    index = 0
    while index < len(turn):
        message = turn[index]
        index += 1
        if message['role'] == 'tool':
            # Orphans have no request to pair with; the original stays in the UI log.
            continue
        result.append(message)
        calls = message.get('tool_calls') or []
        if message['role'] != 'assistant' or not calls:
            continue
        outcomes = {}
        while index < len(turn) and turn[index]['role'] == 'tool':
            outcome = turn[index]; outcomes[outcome.get('tool_call_id')] = outcome; index += 1
        for call in calls:
            ident = call['id']
            result.append(outcomes.get(ident) or {
                'role':'tool','tool_call_id':ident,'name':call['function']['name'],
                'content':json.dumps({'error':'Outcome unknown: this action was interrupted before a result was saved. It may or may not have run. Inspect the current state before proposing any retry; never assume success.'})})
    return result


def conversation_messages(rows, system, budget):
    """Keep whole user turns so tool results never become orphaned."""
    turns, current = [], []
    for row in rows:
        if row['role'] == 'notice':
            continue
        if row['role'] == 'user':
            if current:
                turns.append(current)
            current = []
        if row['status'] in ('error','streaming','interrupted'):
            continue
        payload = json.loads(row.get('payload','{}'))
        message = payload.get('message') or {'role':row['role'],'content':row['content']}
        current.append(message)
    if current:
        turns.append(current)
    selected, used = [], len(system)
    for turn in reversed(turns):
        turn = repair_tool_history(turn)
        size = len(json.dumps(turn, ensure_ascii=False))
        if used + size > budget:
            if not selected:
                raise ValueError('The latest conversation turn is too large for this context setting. Increase the context size in Model Setup or start a new chat with a shorter prompt.')
            break
        selected.insert(0, turn); used += size
    return [{'role':'system','content':system}] + [m for turn in selected for m in turn], len(selected) < len(turns)


class ConversationWorker(QThread):
    changed = Signal()
    status = Signal(str)
    approval_needed = Signal(object)

    def __init__(self, store, chat_id, engine, thinking=False, web_enabled=True, computer_enabled=True, use_tools=True):
        super().__init__()
        self.store, self.chat_id, self.engine = store, chat_id, engine
        self.thinking = thinking
        self.web_enabled, self.computer_enabled = web_enabled, computer_enabled
        self.use_tools = use_tools
        self.cancel_event = threading.Event()
        self.pending = None

    def ask(self, request):
        pending = PendingApproval(request)
        self.pending = pending
        self.status.emit('Waiting for your approval…')
        self.approval_needed.emit(pending)
        while not pending.event.wait(0.1):
            if self.cancel_event.is_set():
                pending.decide(False)
        self.pending = None
        return pending.approved and not self.cancel_event.is_set()

    def request_stop(self):
        self.cancel_event.set()
        if self.pending:
            self.pending.decide(False)
        # Engine cancellation may wait for subprocess teardown. Never block Qt.
        threading.Thread(target=self.engine.cancel, daemon=True).start()

    def run(self):
        message_id, draft = None, ''
        try:
            if self.cancel_event.is_set():
                raise Cancelled()
            chat = self.store.chat(self.chat_id)
            if chat is None:
                return
            project = self.store.project(chat['project_id']) if chat['project_id'] else None
            roots = self.store.links(chat['project_id']) if project else []
            rows = self.store.messages(self.chat_id)
            query = next((m['content'] for m in reversed(rows) if m['role']=='user'), '')
            self.status.emit('Reading project context…')
            # Conservative character budget; tokenizer differences still depend on model.
            total_budget = max(8000, (self.engine.config.context_size - self.engine.config.max_tokens - 1000) * 2)
            system = build_context(project, roots if self.computer_enabled else [], query, min(20000, int(total_budget * 0.65)), self.cancel_event)
            messages, trimmed = conversation_messages(rows, system, total_budget)
            if trimmed:
                self.status.emit('Using recent turns; older messages remain saved.')
            executor = ToolExecutor(roots, self.store.directory, self.ask, self.cancel_event, self.web_enabled, self.computer_enabled)
            tools = [s for s in TOOL_SCHEMAS if (s['function']['name'] in ('web_search','fetch_url') and self.web_enabled) or (s['function']['name'] not in ('web_search','fetch_url') and self.computer_enabled)] if self.use_tools else []
            self.engine.start(self.cancel_event, self.status.emit)
            for round_index in range(10):
                if self.cancel_event.is_set():
                    raise Cancelled()
                draft = ''
                message_id = self.store.add_message(self.chat_id, 'assistant', '', status='streaming')
                self.changed.emit()
                last_save = 0.0

                def delta(text):
                    nonlocal draft, last_save
                    draft += text
                    if time.monotonic() - last_save > 0.12:
                        self.store.update_message(message_id, draft, 'streaming')
                        self.changed.emit()
                        last_save = time.monotonic()

                self.status.emit('Thinking locally…' if self.thinking else 'Replying locally…')
                reply = self.engine.complete(messages, tools or None, self.cancel_event, delta, self.thinking)
                if self.cancel_event.is_set():
                    raise Cancelled()
                draft = reply.get('content') or draft
                calls = reply.get('tool_calls') or []
                if len(calls) > 8:
                    raise ValueError('The model requested too many actions at once. Ask for a smaller step.')
                self.store.update_message(message_id, draft, payload={'message':reply})
                message_id = None
                self.changed.emit()
                if not calls:
                    self.status.emit('Ready · saved on this computer')
                    return
                messages.append(reply)
                for call in calls:
                    function = call.get('function',{})
                    name = function.get('name','')
                    arguments = function.get('arguments','{}')
                    try:
                        args = json.loads(arguments) if isinstance(arguments,str) else arguments
                    except json.JSONDecodeError:
                        args = None
                    self.status.emit('Requested: ' + name)
                    if not any(s['function']['name'] == name for s in tools):
                        result = json.dumps({'denied':'This tool is disabled. Do not retry or bypass.'})
                    else:
                        result = executor.execute(name, args)
                    tool_message = {'role':'tool','tool_call_id':call.get('id',''), 'name':name,'content':result}
                    messages.append(tool_message)
                    # Record complete tool outcomes, capped by each tool boundary.
                    title = f'{name}\n\nArguments:\n{json.dumps(args,ensure_ascii=False,indent=2)}\n\nResult:\n{result}'
                    self.store.add_message(self.chat_id,'tool',title,payload={'message':tool_message})
                    self.changed.emit()
                if len(json.dumps(messages,ensure_ascii=False)) > total_budget:
                    raise ValueError('The retrieved material filled the model context. Everything is saved. Start a new chat or increase context size in Model Setup.')
            raise ValueError('Stopped after 10 action rounds. Review the results, then ask to continue if needed.')
        except Exception as error:
            cancelled = self.cancel_event.is_set() or isinstance(error, Cancelled)
            state = 'interrupted' if cancelled else 'error'
            if message_id is not None:
                self.store.update_message(message_id, draft, state)
            self.store.add_message(self.chat_id, 'notice', 'Stopped. Your conversation is saved.' if cancelled else str(error), state)
            self.changed.emit()
            self.status.emit('Stopped' if cancelled else 'Could not finish · see message')
