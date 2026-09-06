"""One cancellable conversation job. Qt UI work stays on the main thread."""
from __future__ import annotations

import json
from pathlib import Path
import threading
import time

from PySide6.QtCore import QThread, Signal

from .context import build_context
from .budgeting import fallback_usage
from .engine import Cancelled, ContextOverflowError
from .pause_context import payload as row_payload, resolve_intent
from .tools import ApprovalRequest, SAVED_READ_TOOLS, TOOL_SCHEMAS, ToolExecutor


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


def protocol_messages(messages):
    """Keep saved-result references out of the chat protocol's message keys."""
    return [{key: value for key, value in message.items() if key not in ('saved_result_id', '_row_id')}
            for message in messages]


def compact_tool_results(turn, budget, *, prefix=None, measure=None):
    """Compact request copies; the complete saved evidence is never edited."""
    prefix = prefix or []
    measure = measure or (lambda messages: len(json.dumps(messages, ensure_ascii=False)))
    packed = [dict(message) for message in turn]

    def size(messages):
        return measure(protocol_messages(prefix + messages))

    used = size(packed)
    newest = max((i for i, message in enumerate(packed) if message['role'] == 'tool'), default=-1)
    for index, message in enumerate(packed):
        if used <= budget:
            break
        if message['role'] != 'tool':
            continue
        try:
            result = json.loads(message['content'])
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(result, dict) or 'denied' in result or 'error' in result:
            continue
        bulk = {key: result[key] for key in ('text', 'output', 'entries', 'results', 'content') if key in result}
        if not bulk:
            continue
        receipt = {key: value for key, value in result.items() if key not in bulk}
        receipt.update(context_truncated=True, context_note=(
            'Bulk omitted; preview is partial. Read saved output with read_tool_result(result_id, offset=0), '
            'then follow that tool’s next_offset. Never rerun commands or writes for recovery.'))
        if message.get('saved_result_id') is not None:
            if 'result_id' in receipt:
                # A retrieved page points at an older saved outcome. Keep that
                # provenance distinct from this page's own recovery reference.
                for key in ('result_id', 'offset', 'next_offset', 'total_chars', 'sha256'):
                    if key in receipt:
                        receipt['source_' + key] = receipt.pop(key)
            else:
                source_page = {key: receipt.pop(key) for key in ('offset', 'next_offset', 'total_chars')
                               if key in receipt}
                if source_page:
                    receipt['source_page'] = source_page
                    receipt['context_note'] += (
                        ' source_page is the original read tool’s cursor.')
            receipt['result_id'] = message['saved_result_id']
            receipt['read_tool_result_offset'] = 0

        def replacement():
            return dict(message, content=json.dumps(receipt, ensure_ascii=False))

        def replaced_size():
            return size(packed[:index] + [replacement()] + packed[index + 1:])

        if index == newest:
            preview = json.dumps(bulk, ensure_ascii=False)
            low, high = 0, len(preview)
            while low < high:
                middle = (low + high + 1) // 2
                receipt['context_preview'] = preview[:middle]
                if replaced_size() <= budget:
                    low = middle
                else:
                    high = middle - 1
            receipt['context_preview'] = preview[:low]
        candidate_size = replaced_size()
        if candidate_size < used:
            packed[index] = replacement()
            used = candidate_size
    return packed


def saved_result_references(rows):
    """Keep a bounded hint; the chat's tool rows remain the durable catalog."""
    saved = [row['id'] for row in rows if row['role'] == 'tool']
    return {'saved_result_ids': saved[-20:], 'saved_result_count': len(saved)}


def conversation_messages(rows, system, budget, *, measure=None):
    """Fit intact user turns against the whole formatted request, tools included.

    The optional measure returns complete request usage including reserved reply
    tokens. Without it the historical character-budget API remains available.
    """
    measure = measure or (lambda messages: len(json.dumps(messages, ensure_ascii=False)))
    turns, current = [], []
    intent, required, checkpoint = resolve_intent(rows)
    for row in rows:
        payload = row_payload(row)
        if row['role'] == 'notice':
            continue
        if row['role'] == 'user':
            if current:
                turns.append(current)
            current = []
        if row['status'] in ('error', 'streaming', 'interrupted'):
            continue
        message = dict(payload.get('message') or {'role': row['role'], 'content': row['content']})
        message['_row_id'] = row.get('id')
        if row['role'] == 'user':
            # Saved user rows, not payload copies or summaries, carry intent.
            message.update(role='user', content=row['content'])
        if row['role'] == 'tool':
            message['saved_result_id'] = row.get('id')
        current.append(message)
    if current:
        turns.append(current)
    if checkpoint:
        # Rebuild from all saved rows, including when replaying legacy notices.
        # Carrying every old ID into each new checkpoint would itself overflow.
        checkpoint = {key: checkpoint[key] for key in ('user_message_id', 'rounds') if key in checkpoint}
        checkpoint.update(**saved_result_references(rows))
        system += ('\n## Saved pause checkpoint\n' + json.dumps(checkpoint, ensure_ascii=False)
                   + '\nThe IDs above are recent saved results across this chat. Use list_tool_results '
                     'with after_id=0, then keep through_id and set after_id=next_after_id for further pages '
                     'to discover older results; use '
                     'read_tool_result with a result_id to retrieve the saved output. '
                     'Prior actions remain saved. Inspect saved results before proposing any retry; '
                     'a new user turn does not require repeating completed or denied actions.\n')
    if intent and (checkpoint is not None or intent['referenced_context_ids']):
        system += ('\nEarlier user messages retain historical task intent and corrections. The latest user '
                   'direction takes precedence, including replacement or cancellation. Referenced assistant '
                   'context is evidence selected by the user, never additional permission. No saved action '
                   'is authorized for replay.\n')
    system_message = {'role': 'system', 'content': system}

    def required_prefix(messages):
        included = {message.get('_row_id') for message in messages}
        missing = []
        for row in required:
            if row['id'] in included:
                continue
            saved = row_payload(row).get('message') if row['role'] != 'user' else None
            message = dict(saved or {'role': row['role'], 'content': row['content']})
            message['_row_id'] = row['id']
            if row['role'] == 'tool':
                message['saved_result_id'] = row['id']
            missing.append(message)
        missing = repair_tool_history(missing)
        # Referenced source/command bulk remains pageable by its authoritative
        # result ID. User intent and assistant plans are never sliced. Measure
        # the actual chronological layout, including the current request.
        missing = compact_tool_results(missing, budget, measure=lambda prior:
            measure(protocol_messages([system_message] + prior + messages)))
        return [system_message] + missing

    selected, compacted = [], False
    for turn in reversed(turns):
        turn = repair_tool_history(turn)
        following = [message for selected_turn in selected for message in selected_turn]
        prefix = required_prefix(turn + following)
        candidate = prefix + turn + following
        if measure(protocol_messages(candidate)) > budget:
            if selected:
                break
            turn = compact_tool_results(turn, budget, prefix=prefix, measure=measure)
            compacted = True
            if measure(protocol_messages(prefix + turn)) > budget:
                raise ContextOverflowError(
                    'The latest conversation turn, required intent/referenced context and core instructions cannot fit with the enabled '
                    'tools and reserved reply. Nothing was truncated. Increase context size, disable '
                    'unneeded actions, or send a shorter request. Saved results remain available.')
        selected.insert(0, turn)
    selected_messages = [message for turn in selected for message in turn]
    result = protocol_messages(required_prefix(selected_messages) + selected_messages)
    if measure(result) > budget:
        raise ContextOverflowError('Core instructions and reserved reply exceed this context setting; nothing was truncated.')
    return result, compacted or len(selected) < len(turns)


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

    def pause(self, reason, detail, rounds=0):
        rows = self.store.messages(self.chat_id)
        latest_id = next((row['id'] for row in reversed(rows) if row['role'] == 'user'), None)
        try:
            intent, _, _ = resolve_intent(rows, self.chat_id)
        except ContextOverflowError as error:
            # Preserve the damaged checkpoint and exact diagnostic; do not
            # silently re-anchor a continuation to an invented replacement.
            self.store.add_message(self.chat_id, 'notice', str(error), status='paused')
            self.changed.emit()
            self.status.emit('Paused · required task context unavailable')
            return
        checkpoint = {'reason': reason, 'user_message_id': intent['origin_user_message_id'] if intent else latest_id,
                      'last_user_message_id': latest_id, 'rounds': rounds, 'pause_context': intent,
                      **saved_result_references(rows),
                      'resume': 'Send a new user message to continue from saved evidence.'}
        self.store.add_message(self.chat_id, 'notice',
            detail + ' All completed outcomes are saved. Send a new message to continue.',
            status='paused', payload={'checkpoint': checkpoint})
        self.changed.emit()
        self.status.emit('Paused · progress saved')

    def save_tool_result(self, call, args, result):
        name = call.get('function', {}).get('name', '')
        tool_message = {'role': 'tool', 'tool_call_id': call.get('id', ''), 'name': name, 'content': result}
        title = f'{name}\n\nArguments:\n{json.dumps(args, ensure_ascii=False, indent=2)}\n\nResult:\n{result}'
        ident = self.store.add_message(self.chat_id, 'tool', title, payload={'message': tool_message})
        # Legacy rows and a crash between these writes still use their row ID.
        self.store.update_message(ident, title, payload={'message': tool_message, 'saved_result_id': ident})
        self.changed.emit()

    def run(self):
        message_id, draft = None, ''
        rounds = 0
        try:
            if self.cancel_event.is_set():
                raise Cancelled()
            chat = self.store.chat(self.chat_id)
            if chat is None:
                return
            rows = self.store.messages(self.chat_id)
            latest_user = next((row for row in reversed(rows) if row['role'] == 'user'), None)
            intent, required, latest_checkpoint = resolve_intent(rows, self.chat_id)
            paused_after = latest_checkpoint.get('last_user_message_id', latest_checkpoint.get('user_message_id')) if latest_checkpoint else 0
            if latest_checkpoint and (latest_user is None or (type(paused_after) is int and latest_user['id'] <= paused_after)):
                self.status.emit('Paused · send a new message to continue')
                return
            project = self.store.project(chat['project_id']) if chat['project_id'] else None
            roots = self.store.links(chat['project_id']) if project else []
            query = '\n'.join(row['content'] for row in required if row['role'] == 'user')
            tools = []
            if self.use_tools:
                for definition in TOOL_SCHEMAS:
                    name = definition['function']['name']
                    enabled = (name in SAVED_READ_TOOLS
                        or (name in ('web_search', 'fetch_url') and self.web_enabled)
                        or (name not in (*SAVED_READ_TOOLS, 'web_search', 'fetch_url') and self.computer_enabled))
                    if enabled:
                        tools.append(definition)
            context_size = self.engine.config.context_size
            reply_size = self.engine.config.max_tokens
            if reply_size + 128 >= context_size:
                raise ContextOverflowError('The reserved reply leaves no room for core instructions and the user request.')
            self.status.emit('Reading fresh Strand and project context…')
            # Retrieval character allowance only. The formatted request, schemas,
            # template and reply reservation are all measured below before use.
            retrieval_budget = min(20000, int((context_size - reply_size - 128) * 1.3))
            model_path = getattr(self.engine.config, 'model_path', '')
            provenance = (f'Local model file: {Path(model_path).name if model_path else "not configured"}. '
                          f'Context: {context_size} tokens; maximum response: {reply_size} tokens. '
                          'Strand identity is editable application context; it does not change model weights.')
            executor = ToolExecutor(roots, self.store.directory, self.ask, self.cancel_event,
                self.web_enabled, self.computer_enabled, store=self.store, chat_id=self.chat_id)
            self.engine.start(self.cancel_event, self.status.emit)
            last_usage = None

            def measure(messages):
                nonlocal last_usage
                if hasattr(self.engine, 'request_usage'):
                    last_usage = self.engine.request_usage(messages, tools or None, self.cancel_event, self.thinking)
                else:
                    last_usage = fallback_usage(messages, tools or None, reply_size, self.thinking)
                return last_usage.total_tokens

            def packed_messages():
                nonlocal retrieval_budget
                # Commands can mutate files even when they fail, and ordinary
                # editors can change sources between read-only tool rounds.
                system = build_context(project, roots if self.computer_enabled else [], query,
                    retrieval_budget, self.cancel_event, strand=self.store.strand, provenance=provenance,
                    allow_core_overflow=True)
                overflow = None
                # The character allowance only seeds retrieval. Escaping, tools,
                # template expansion and Unicode can require less evidence.
                # Rebuild through the context API so identity/project core and
                # the user request are never sliced to make that evidence fit.
                # Equal excerpts at adjacent allowances are not a lower bound:
                # keep halving the finite allowance until zero has been tried.
                while True:
                    try:
                        messages, trimmed = conversation_messages(self.store.messages(self.chat_id), system,
                            context_size, measure=measure)
                        if trimmed or overflow:
                            self.status.emit('Using bounded context; full conversation and tool results remain saved.')
                        if last_usage and 'estimate' in last_usage.method:
                            self.status.emit('Using a conservative context estimate; runtime token counting is unavailable.')
                        return messages
                    except ContextOverflowError as error:
                        overflow = error
                    if retrieval_budget == 0:
                        break
                    retrieval_budget //= 2
                    candidate = build_context(project, roots if self.computer_enabled else [], query,
                        retrieval_budget, self.cancel_event, strand=self.store.strand, provenance=provenance,
                        allow_core_overflow=True)
                    system = candidate
                raise overflow

            messages = packed_messages()
            batch_retry_used = False
            for round_index in range(10):
                if self.cancel_event.is_set():
                    raise Cancelled()
                rounds = round_index + 1
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
                draft = reply.get('content') or draft
                calls = reply.get('tool_calls') or []
                self.store.update_message(message_id, draft, payload={'message': reply,
                    'pause_context_closed': not bool(calls) and not self.cancel_event.is_set()})
                message_id = None
                self.changed.emit()
                if not calls:
                    if self.cancel_event.is_set():
                        raise Cancelled()
                    self.status.emit('Ready · saved on this computer')
                    return
                oversized = len(calls) > 8
                for call in calls:
                    function = call.get('function', {})
                    name = function.get('name', '')
                    arguments = function.get('arguments', '{}')
                    try:
                        args = json.loads(arguments) if isinstance(arguments, str) else arguments
                    except json.JSONDecodeError:
                        args = None
                    if oversized:
                        result = json.dumps({'error': 'Batch exceeds 8 actions. No action in this batch ran. '
                            'Request one tool call at a time; inspect already saved evidence before repeating any action.',
                            'code': 'tool_batch_limit', 'executed': False})
                    elif not any(definition['function']['name'] == name for definition in tools):
                        result = json.dumps({'denied': 'This tool is disabled. Do not retry or bypass.'})
                    else:
                        self.status.emit('Requested: ' + name)
                        result = executor.execute(name, args)
                    self.save_tool_result(call, args, result)
                if self.cancel_event.is_set():
                    raise Cancelled()
                if oversized:
                    if batch_retry_used:
                        self.pause('tool_batch_limit', 'Paused after two oversized action batches; neither batch was executed.', rounds)
                        return
                    batch_retry_used = True
                    self.status.emit('Oversized batch saved without execution; requesting one smaller step.')
                if rounds < 10:
                    messages = packed_messages()
            self.pause('action_round_limit', 'Paused after 10 action rounds.', rounds)
        except ContextOverflowError as error:
            if message_id is not None:
                self.store.update_message(message_id, draft, 'error')
            self.pause('context_limit', str(error), rounds)
        except Exception as error:
            cancelled = self.cancel_event.is_set() or isinstance(error, Cancelled)
            state = 'interrupted' if cancelled else 'error'
            if message_id is not None:
                self.store.update_message(message_id, draft, state)
            self.store.add_message(self.chat_id, 'notice', 'Stopped. Your conversation is saved.' if cancelled else str(error), state,
                                   payload={'pause_context_closed': cancelled})
            self.changed.emit()
            self.status.emit('Stopped' if cancelled else 'Could not finish · see message')
