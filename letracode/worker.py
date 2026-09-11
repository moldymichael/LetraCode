"""One cancellable conversation job. Qt UI work stays on the main thread."""
from __future__ import annotations

import inspect
import json
from pathlib import Path
import threading
import time
import uuid
from dataclasses import asdict

from PySide6.QtCore import QThread, Signal

from .context import application_info, build_context
from .budgeting import fallback_usage
from .engine import Cancelled, ContextOverflowError
from .pause_context import payload as row_payload, resolve_intent
from .continuation import RunHalted, RunLimits, RunProgress, interrupted_outcome
from .evidence import evidence_state, request_exposure, source_evidence, summary as evidence_summary
from .reading import SourceReadRecovery
from .tools import ApprovalRequest, TOOL_SCHEMAS, ToolExecutor, tool_enabled
from .store import now


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
    result = []
    for message in messages:
        clean = {key: value for key, value in message.items()
                 if key not in ('saved_result_id', '_row_id', '_speaker')}
        if (message.get('_speaker') and result and result[-1]['role'] == 'assistant'
                and not result[-1].get('tool_calls')):
            # Keep per-row IDs during packing; merge only at the wire boundary
            # so strict templates can alternate user/assistant roles.
            result[-1]['content'] = (result[-1].get('content') or '') + '\n\n' + clean['content']
        else:
            result.append(clean)
    return result


def attributed_message(row, payload):
    speaker = payload.get('speaker')
    if row['role'] == 'assistant' and isinstance(speaker, dict):
        return {'role': 'assistant', '_speaker': True, 'content':
                f'Conversation contribution from {speaker.get("label", "Other model")}:\n{row["content"]}'}
    return dict(payload.get('message') or {'role': row['role'], 'content': row['content']})


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


def conversation_messages(rows, system, budget, *, measure=None, receipt=None):
    """Fit intact user turns against the whole formatted request, tools included.

    The optional measure returns complete request usage including reserved reply
    tokens. Without it the historical character-budget API remains available.
    """
    measure = measure or (lambda messages: len(json.dumps(messages, ensure_ascii=False)))
    turns, current = [], []
    at_segment_boundary = False
    intent, required, checkpoint = resolve_intent(rows)
    for row in rows:
        payload = row_payload(row)
        if row['role'] == 'notice':
            # A saved application boundary permits older segment history to
            # yield context space. Authoritative user intent remains required.
            if payload.get('segment_boundary') is True:
                boundary = payload.get('checkpoint', {})
                if (boundary.get('reason') != 'action_round_limit'
                        or boundary.get('continuation', {}).get('version') != 1):
                    raise ContextOverflowError('Automatic segment checkpoint is malformed.')
                if current:
                    turns.append(current)
                    current = []
                at_segment_boundary = True
            continue
        if row['role'] == 'user':
            if current:
                turns.append(current)
            current = []
        if row['status'] in ('error', 'streaming', 'interrupted'):
            continue
        if (row['role'] == 'assistant'
                and payload.get('application_generated') == 'source_read_recovery'):
            # Every controller read is a durable, paired context boundary.
            # Keep the original intent, but let older receipts yield room for
            # the next full page before the ten-round segment is exhausted.
            if current:
                turns.append(current)
                current = []
        message = attributed_message(row, payload)
        if (row['role'] == 'assistant' and row['status'] == 'incomplete'
                and payload.get('task_outcome') == 'source_incomplete'
                and not message.get('tool_calls')):
            # Keep rejected answers and their exposure records in storage, not
            # as assistant prefills (or consecutive assistant tails) on retry.
            continue
        message['_row_id'] = row.get('id')
        if row['role'] == 'user':
            # Saved user rows, not payload copies or summaries, carry intent.
            message.update(role='user', content=row['content'])
        if row['role'] == 'tool':
            message['saved_result_id'] = row.get('id')
        current.append(message)
        at_segment_boundary = False
    if current or at_segment_boundary:
        # The first request after rollover already has a new, empty segment.
        # Older completed segments may yield space before its first reply exists.
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
            message = attributed_message(row, row_payload(row)) if row['role'] != 'user' else {
                'role': 'user', 'content': row['content']}
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
            if selected and any(selected):
                break
            # At an empty rollover, prefer pageable receipts that retain the
            # latest result for exposure before dropping the completed segment.
            turn = compact_tool_results(turn, budget, prefix=prefix, measure=measure)
            compacted = True
            if measure(protocol_messages(prefix + turn)) > budget:
                if selected:
                    break  # Required context in the empty active segment fits.
                raise ContextOverflowError(
                    'The latest conversation turn, required intent/referenced context and core instructions cannot fit with the enabled '
                    'tools and reserved reply. Nothing was truncated. Increase context size, disable '
                    'unneeded actions, or send a shorter request. Saved results remain available.')
        selected.insert(0, turn)
    selected_messages = [message for turn in selected for message in turn]
    packed = required_prefix(selected_messages) + selected_messages
    result = protocol_messages(packed)
    if measure(result) > budget:
        raise ContextOverflowError('Core instructions and reserved reply exceed this context setting; nothing was truncated.')
    if receipt is not None:
        receipt.update(system_text=result[0]['content'], history_reduced=compacted or len(selected) < len(turns),
                       message_ids=[message['_row_id'] for message in packed if message.get('_row_id')],
                       saved_result_ids=[message['saved_result_id'] for message in packed if message.get('saved_result_id')],
                       conversation_message_count=len(result) - 1)
    return result, compacted or len(selected) < len(turns)


class ConversationWorker(QThread):
    changed = Signal()
    status = Signal(str)
    approval_needed = Signal(object)

    def __init__(self, store, chat_id, engine, thinking=False, web_enabled=True, computer_enabled=True, use_tools=True, *, limits=None, actions_enabled=True):
        super().__init__()
        self.store, self.chat_id, self.engine = store, chat_id, engine
        self.thinking = thinking
        self.web_enabled, self.computer_enabled = web_enabled, computer_enabled
        self.use_tools = use_tools
        self.actions_enabled = actions_enabled
        self.cancel_event = threading.Event()
        self.pending = None
        self._pinned_intent = None
        self.limits = limits if limits is not None else RunLimits()
        self.progress = None
        self.run_id = uuid.uuid4().hex
        self.stop_reason = None
        self._stop_lock = threading.Lock()
        self._finished = False
        self._cancel_thread = None
        self._check_dispatch = None
        self._approval_halted = None
        self._approvals = []
        config = getattr(engine, 'config', None)
        self.run_configuration = {
            'model_name': Path(getattr(config, 'model_path', '')).name,
            'engine_name': Path(getattr(config, 'executable', '')).name,
            'thinking': bool(thinking), 'mode': 'Thinking' if thinking else 'Instant',
            'web_enabled': bool(web_enabled), 'computer_enabled': bool(computer_enabled),
            'use_tools': bool(use_tools), 'actions_enabled': bool(actions_enabled),
        }
        adapter = getattr(config, 'lora_path', '')
        if isinstance(adapter, str) and adapter.strip():
            self.run_configuration['adapter_name'] = Path(adapter).name
            saved_engine = self.store.setting('engine', {})
            version = self.store.setting('training_active_version')
            if (isinstance(saved_engine, dict) and saved_engine.get('lora_path') == adapter
                    and isinstance(version, str) and version):
                self.run_configuration['training_version'] = version
        for key in ('context_size', 'max_tokens', 'gpu_layers', 'threads', 'temperature'):
            value = getattr(config, key, None)
            if type(value) in (int, float):
                self.run_configuration[key] = value

    def ask(self, request):
        decision = {'kind': request.kind, 'title': request.title, 'requested_at': now()}
        pending = PendingApproval(request)
        self.pending = pending
        self.status.emit('Waiting for your approval…')
        self.approval_needed.emit(pending)
        while not pending.event.wait(0.1):
            if self.cancel_event.is_set():
                pending.decide(False)
        self.pending = None
        if self._check_dispatch is not None and not self.cancel_event.is_set():
            try:
                self._check_dispatch()
            except RunHalted as error:
                # ToolExecutor owns approval execution, but only this worker
                # owns the saved input cursor. Carry the halt back to its batch
                # pairing path instead of letting a stale approval authorize it.
                self._approval_halted = error
                self._approvals.append({**decision, 'decided_at': now(), 'decision': 'stale_input'})
                return False
        self._approvals.append({**decision, 'decided_at': now(), 'decision': (
            'cancelled' if self.cancel_event.is_set() else 'approved' if pending.approved else 'denied')})
        return pending.approved and not self.cancel_event.is_set()

    def request_stop(self):
        self._cancel('user_stop')

    def wait_for_stop(self):
        with self._stop_lock:
            self._finished = True
            cancel_thread = self._cancel_thread
        if cancel_thread is not None:
            cancel_thread.join()

    def _cancel(self, reason):
        with self._stop_lock:
            if self._finished:
                return
            self.stop_reason = self.stop_reason or reason
            self.cancel_event.set()
            # Launch once under the same lock used by finalization. Starting
            # here ensures run() can always join an already-started thread.
            if self._cancel_thread is None:
                self._cancel_thread = threading.Thread(target=self.engine.cancel,
                    name='letracode-worker-cancel', daemon=True)
                self._cancel_thread.start()
        if self.pending:
            self.pending.decide(False)

    def run_record(self):
        return {'version': 1, 'run_id': self.run_id, 'limits': asdict(self.limits),
                **(self.progress.snapshot() if self.progress else {})}

    def pause(self, reason, detail, rounds=0, *, automatic=False):
        rows = self.store.messages(self.chat_id)
        latest_id = next((row['id'] for row in reversed(rows) if row['role'] == 'user'), None)
        if latest_id is not None and any(row['id'] > latest_id and row['role'] == 'notice'
                and row_payload(row).get('task_outcome') == 'ended_by_user'
                and row_payload(row).get('pause_context_closed') is True for row in rows):
            # Explicit closure wins over a late response, timeout or exception.
            # Never pin the ended objective again while reporting a run failure.
            self.changed.emit()
            self.status.emit('Task ended · saved outcomes retained')
            return
        try:
            intent, _, _ = resolve_intent(rows, self.chat_id, starting_task=True,
                                          pinned_intent=self._pinned_intent)
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
                      'continuation': self.run_record(),
                      'resume': ('Next bounded segment in this active run only.' if automatic else
                                 'Send a new user message to continue from saved evidence.')}
        self.store.add_message(self.chat_id, 'notice',
            detail + (' Saved evidence carries into the next bounded segment.' if automatic else
                      ' All completed outcomes are saved. The task is not marked complete.'),
            status='continuing' if automatic else 'paused',
            payload={'checkpoint': checkpoint, 'segment_boundary': automatic})
        self.changed.emit()
        self.status.emit('Continuing · progress saved' if automatic else 'Paused · progress saved')

    def save_tool_result(self, call, args, result):
        name = call.get('function', {}).get('name', '')
        tool_message = {'role': 'tool', 'tool_call_id': call.get('id', ''), 'name': name, 'content': result}
        title = f'{name}\n\nArguments:\n{json.dumps(args, ensure_ascii=False, indent=2)}\n\nResult:\n{result}'
        data = {'message': tool_message, 'arguments': args, 'approvals': self._approvals}
        self._approvals = []
        try:
            data['source_evidence'] = source_evidence(name, json.loads(result))
        except (TypeError, ValueError) as error:
            data['source_evidence_error'] = str(error)
        ident = self.store.add_message(self.chat_id, 'tool', title, payload=data)
        # Legacy rows and a crash between these writes still use their row ID.
        data['saved_result_id'] = ident
        self.store.update_message(ident, title, payload=data)
        self.changed.emit()
        if 'source_evidence_error' in data:
            raise ValueError('Saved source evidence could not be verified: ' + data['source_evidence_error'])
        return ident

    def run(self):
        message_id, draft, reasoning = None, '', ''
        stream_payload = {}

        def save_partial(status='streaming'):
            if message_id is not None:
                data = dict(stream_payload)
                if reasoning:
                    data['reasoning'] = reasoning
                self.store.update_message(message_id, draft, status, payload=data)

        rounds = 0
        timer = None
        try:
            if self.cancel_event.is_set():
                raise Cancelled()
            # Preserve compatibility with integrations implementing the original
            # completion signature. Never retry a call after a TypeError: it may
            # have already sent a request or invoked a callback.
            try:
                parameters = inspect.signature(self.engine.complete).parameters
                accepts_reasoning = ('on_reasoning' in parameters or any(
                    parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()))
            except (TypeError, ValueError):
                accepts_reasoning = False
            chat = self.store.chat(self.chat_id)
            if chat is None:
                return
            rows = self.store.messages(self.chat_id)
            latest_user = next((row for row in reversed(rows) if row['role'] == 'user'), None)
            # Context closure and permission to start another run are separate.
            # A stopped/finished run keeps its admission cursor even though its
            # old objective is no longer required by resolve_intent().
            for row in reversed(rows):
                data = row_payload(row)
                if row['role'] not in ('notice', 'assistant') or 'terminal_input_cursor' not in data:
                    continue
                cursor = data['terminal_input_cursor']
                if (type(cursor) is not int or not any(saved['id'] == cursor
                        and saved['role'] == 'user' and saved['status'] == 'complete'
                        and saved['id'] < row['id'] for saved in rows)):
                    raise ContextOverflowError('Terminal run input cursor is invalid or unavailable in this chat.')
                if latest_user is None or latest_user['id'] <= cursor:
                    self.status.emit('Stopped · send a new message to continue')
                    return
                break
            intent, required, latest_checkpoint = resolve_intent(rows, self.chat_id)
            paused_after = latest_checkpoint.get('last_user_message_id', latest_checkpoint.get('user_message_id')) if latest_checkpoint else 0
            if latest_checkpoint and (latest_user is None or (type(paused_after) is int and latest_user['id'] <= paused_after)):
                self.status.emit('Paused · send a new message to continue')
                return
            if intent is None:
                return
            self._pinned_intent = resolve_intent(rows, self.chat_id, starting_task=True)[0]
            origin = intent['origin_user_message_id']
            input_cursor = latest_user['id']
            self.progress = RunProgress(self.limits)
            timer = threading.Timer(self.limits.max_seconds, lambda: self._cancel('time_budget'))
            timer.daemon = True
            timer.start()

            def check_run():
                if self.cancel_event.is_set():
                    if self.stop_reason == 'time_budget':
                        raise RunHalted('time_budget', f'Run reached its {self.limits.max_seconds:g}-second wall limit, including approval wait.')
                    raise Cancelled()
                self.progress.check_time()
                current = self.store.messages(self.chat_id)
                if any(row['id'] > input_cursor and row['role'] == 'notice'
                       and row_payload(row).get('task_outcome') == 'ended_by_user' for row in current):
                    raise RunHalted('user_stop', 'The user ended this task. Its saved action outcomes remain unchanged.')
                if next((row['id'] for row in reversed(current) if row['role'] == 'user'), None) != input_cursor:
                    raise RunHalted('new_input', 'New user input was saved. This run stopped before another dispatch; review that input before continuing.')
                return current

            self._check_dispatch = check_run

            # A crashed action must not be inferred as safe to replay. Explicit
            # task closure can release an old objective, never invent its outcome.
            pending = set()
            ended_after = next((row['id'] for row in reversed(rows) if row['role'] == 'notice'
                and row_payload(row).get('task_outcome') == 'ended_by_user'
                and row_payload(row).get('pause_context_closed') is True), 0)
            for row in rows:
                if row['id'] <= ended_after:
                    continue
                message = row_payload(row).get('message', {})
                if row['role'] == 'assistant':
                    pending.update(call.get('id') for call in message.get('tool_calls') or [])
                elif row['role'] == 'tool':
                    pending.discard(message.get('tool_call_id'))
            if pending:
                raise RunHalted('unknown_outcome', 'A saved action has no recorded outcome. It may have run. Inspect its saved details, then use End task to close this objective before starting new work. Unknown effects remain unknown; automatic replay is blocked.')

            project = self.store.project_for_context(chat['project_id'], read_files=self.computer_enabled) if chat['project_id'] else None
            roots = self.store.links(chat['project_id']) if project else []
            shared_roots = self.store.setting('source_roots', [])
            if isinstance(shared_roots, list):
                roots = list(dict.fromkeys([root for root in shared_roots if isinstance(root, str)] + roots))
            query = '\n'.join(row['content'] for row in required if row['role'] == 'user')
            tools = []
            if self.use_tools:
                for definition in TOOL_SCHEMAS:
                    name = definition['function']['name']
                    enabled = tool_enabled(name, computer_enabled=self.computer_enabled,
                                           actions_enabled=self.actions_enabled, web_enabled=self.web_enabled)
                    if enabled:
                        tools.append(definition)
            context_size = self.engine.config.context_size
            reply_size = self.engine.config.max_tokens
            if reply_size + 128 >= context_size:
                raise ContextOverflowError('The reserved reply leaves no room for core instructions and the user request.')
            self.status.emit('Reading current Memory and project context…')
            retrieval_budget = min(20000, int((context_size - reply_size - 128) * 1.3))
            initial_retrieval_budget = retrieval_budget
            model_path = getattr(self.engine.config, 'model_path', '')
            provenance = (f'Local model file: {Path(model_path).name if model_path else "not configured"}. '
                          f'Context: {context_size} tokens; maximum response: {reply_size} tokens. '
                          'Memory is editable user context; it does not change model weights.')
            runtime = application_info(self.store) if self.computer_enabled else None
            if runtime:
                provenance += '\n' + json.dumps({key: runtime[key] for key in
                    ('version', 'runtime_root', 'development_root')}, ensure_ascii=False)
                provenance += '\nUse app_info for observed installation/docs/history; paths alone are not consulted evidence.'
            executor = ToolExecutor(roots, self.store.directory, self.ask, self.cancel_event,
                self.web_enabled, self.computer_enabled, store=self.store, chat_id=self.chat_id,
                actions_enabled=self.actions_enabled)
            self.engine.start(self.cancel_event, self.status.emit)
            last_usage = None

            def measure(messages):
                nonlocal last_usage
                if hasattr(self.engine, 'request_usage'):
                    last_usage = self.engine.request_usage(messages, tools or None, self.cancel_event, self.thinking)
                else:
                    last_usage = fallback_usage(messages, tools or None, reply_size, self.thinking)
                return last_usage.total_tokens

            def run_context(current):
                state = evidence_state(current, origin)
                snapshot = self.progress.snapshot()
                # Full outcomes remain in the checkpoint and saved result pages.
                brief = []
                for item in snapshot['last_outcomes'][-3:]:
                    brief.append({**item, 'outcome': {key: value for key, value in item['outcome'].items()
                        if key != 'output_tail'}})
                return ('\n## Active bounded run (application evidence)\n'
                    f'Original user message: {origin}; segment {snapshot["segments"]}; '
                    f'requests {snapshot["requests"]}/{self.limits.max_requests}; '
                    f'actions {snapshot["actions"]}/{self.limits.max_actions}; '
                    f'wall limit {self.limits.max_seconds:g}s; consecutive stalls {snapshot["stalls"]}/{self.limits.max_stalls}.\n'
                    'Ten action rounds form one segment; safe progress continues automatically. '
                    'This grants no new user permission. Preserve the original question and latest steering. '
                    'Use saved results, not repeated commands/edits, to recover earlier evidence. '
                    'A tool-free answer is not proof of task completion. If source coverage is incomplete, '
                    'retrieve missing pages needed for the original question or explicitly report the limitation; '
                    'never claim whole-work inspection from partial pages.\n'
                    + 'Recent action/verification outcomes: ' + json.dumps(brief, ensure_ascii=False)
                    + '\n' + evidence_summary(state, max_files=3))

            context_receipt = {}
            def packed_messages():
                nonlocal retrieval_budget
                current = check_run()
                extra = run_context(current)
                def context():
                    return build_context(project, roots if self.computer_enabled else [], query,
                        retrieval_budget, self.cancel_event, strand=self.store.strand if self.computer_enabled else None,
                        provenance=provenance, allow_core_overflow=True, receipt=context_receipt) + extra
                system = context()
                overflow = None
                while True:
                    try:
                        messages, trimmed = conversation_messages(current, system, context_size, measure=measure,
                                                                  receipt=context_receipt)
                        context_receipt.update(runtime=runtime,
                            capabilities={'read_files': bool(self.computer_enabled), 'actions': bool(self.actions_enabled),
                                          'web': bool(self.web_enabled), 'tools': bool(self.use_tools)},
                            available_tools=[tool['function']['name'] for tool in tools],
                            retrieval_reduced=retrieval_budget < initial_retrieval_budget,
                            included_tool_results=[dict(message) for message in messages if message['role'] == 'tool'])
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
                    system = context()
                raise overflow

            def source_signature(current, field='retrieved_ranges'):
                return json.dumps([{key: item.get(key) for key in
                    ('path', 'source_sha256', 'extractor_version', 'total_chars', field,
                     *(['exposure_observed'] if field == 'exposed_ranges' else []))}
                    for item in evidence_state(current, origin)['files']], sort_keys=True)

            reading = SourceReadRecovery()
            recovery_observation = None

            def recover_source(*, path=None, retry=None):
                """Deliver one page, under the same permissions and budgets.

                Each page must pass through a subsequent completed model request
                before the coverage ledger can advance. Bounded sizing attempts
                are saved too; a compacted preview cannot count as delivery.
                """
                nonlocal recovery_observation, retrieval_budget
                current = check_run()
                if not any(tool['function']['name'] == 'read_file' for tool in tools):
                    return False
                state = evidence_state(current, origin)
                arguments = reading.next_read(state, path)
                if arguments is None:
                    arguments = retry
                if arguments is None:
                    return False
                pagination_retry_used = False
                # At most one cursor restart and log2(16000) size reductions.
                for attempt in range(16):
                    self.progress.reserve_actions(1)
                    check_run()
                    call = {'id': 'source-recovery-' + uuid.uuid4().hex, 'type': 'function',
                            'function': {'name': 'read_file', 'arguments': json.dumps(arguments)}}
                    message = {'role': 'assistant', 'content': '', 'tool_calls': [call]}
                    self.store.add_message(self.chat_id, 'assistant',
                        'Continuing source reading automatically.', payload={
                            'message': message, 'application_generated': 'source_read_recovery',
                            'request_completed': False, 'continuation': self.run_record()})
                    self.changed.emit()
                    self.status.emit('Reading next source page automatically…')
                    # Signals may stop the job or save steering. Pair the call
                    # before propagating that interruption.
                    try:
                        check_run()
                    except (RunHalted, Cancelled) as error:
                        self.save_tool_result(call, arguments, json.dumps({
                            'error': 'Not executed: source reading was stopped.',
                            'executed': False, 'code': getattr(error, 'reason', 'cancelled')}))
                        raise
                    result = executor.execute('read_file', arguments)
                    ident = self.save_tool_result(call, arguments, result)
                    outcome = json.loads(result)
                    check_run()
                    if 'denied' in outcome:
                        raise RunHalted('approval_denied', 'File reading was denied. Automatic reading stopped.')
                    if outcome.get('code') == 'invalid_pagination' and not pagination_retry_used:
                        # A source may shrink below the cursor since its last
                        # observation. Read the new snapshot from a valid start.
                        arguments = outcome['retry_read_file']
                        pagination_retry_used = True
                        continue
                    if 'error' in outcome:
                        self.progress.observe('read_file', arguments, outcome, ident)
                        return True
                    planned = request_exposure(packed_messages(), check_run())
                    if not any(ident in item.get('source_result_ids', []) for item in planned) and retrieval_budget:
                        # Prioritize the explicitly requested source over
                        # optional automatic excerpts when context is tight.
                        retrieval_budget = 0
                        planned = request_exposure(packed_messages(), check_run())
                    if any(ident in item.get('source_result_ids', []) for item in planned):
                        # A reread can add exposure without adding retrieval.
                        # Account for it after the next completed request, never
                        # halt before that request gets to see the viable page.
                        recovery_observation = (arguments, outcome, ident)
                        return True
                    if arguments['max_chars'] == 1:
                        raise ContextOverflowError('A source page cannot fit beside required instructions and tools, even at one character. Source coverage remains incomplete.')
                    arguments = reading.smaller_page(arguments)
                raise RunHalted('no_progress', 'Source paging could not produce a deliverable page within bounded recovery attempts.')

            batch_retry_used = False
            while True:
                # This is a new bounded segment, not an enlarged round loop.
                # Same worker/token own cancellation and approvals throughout.
                for round_index in range(10):
                    current = check_run()
                    prior_exposure = source_signature(current, 'exposed_ranges')
                    messages = packed_messages()
                    exposure = request_exposure(messages, current)
                    if recovery_observation is not None:
                        arguments, _, ident = recovery_observation
                        if not any(ident in item.get('source_result_ids', []) for item in exposure):
                            # A segment checkpoint can add overhead after the
                            # previous preflight. Refit before spending a model
                            # request on a preview with no source exposure.
                            recover_source(path=arguments['path'])
                            current = check_run()
                            messages = packed_messages()
                            exposure = request_exposure(messages, current)
                    self.progress.reserve_request()
                    rounds = round_index + 1
                    draft, reasoning = '', ''
                    request_record = self.run_record()
                    stream_payload = {'continuation': request_record, 'source_exposure_pending': exposure,
                                      'run_configuration': self.run_configuration, 'context': dict(context_receipt)}
                    message_id = self.store.add_message(self.chat_id, 'assistant', '', status='streaming',
                                                        payload=stream_payload)
                    self.changed.emit()
                    last_save = 0.0

                    def progress():
                        nonlocal last_save
                        if time.monotonic() - last_save > 0.12:
                            save_partial()
                            self.changed.emit()
                            last_save = time.monotonic()

                    def delta(text):
                        nonlocal draft
                        draft += text
                        progress()

                    def thinking_delta(text):
                        nonlocal reasoning
                        reasoning += text
                        progress()

                    self.status.emit('Thinking locally…' if self.thinking else 'Replying locally…')
                    callbacks = {'on_reasoning': thinking_delta} if accepts_reasoning else {}
                    reply = dict(self.engine.complete(messages, tools or None, self.cancel_event,
                                                      delta, self.thinking, **callbacks))
                    returned_reasoning = reply.pop('reasoning_content', None) or reply.get('reasoning')
                    reply.pop('reasoning', None)
                    if isinstance(returned_reasoning, str) and returned_reasoning:
                        reasoning = returned_reasoning
                    draft = reply.get('content') or draft
                    calls = reply.get('tool_calls') or []
                    response_id = message_id
                    data = {'message': reply, 'pause_context_closed': False,
                            'continuation': request_record, 'source_exposure': exposure,
                            'request_completed': True, 'run_configuration': self.run_configuration,
                            'context': dict(context_receipt)}
                    if reasoning:
                        data['reasoning'] = reasoning
                    self.store.update_message(message_id, draft, payload=data)
                    message_id = None
                    exposure_changed = prior_exposure != source_signature(self.store.messages(self.chat_id), 'exposed_ranges')
                    if exposure_changed:
                        self.progress.observe_source_exposure()
                    observation_halt = None
                    if recovery_observation is not None:
                        arguments, outcome, ident = recovery_observation
                        recovery_observation = None
                        try:
                            self.progress.observe('read_file', arguments, outcome, ident, progress=exposure_changed)
                        except RunHalted as error:
                            # Classify this response and pair all its proposed
                            # calls before propagating a deferred read blocker.
                            observation_halt = error
                    self.changed.emit()
                    if not calls:
                        current = self.store.messages(self.chat_id)
                        coverage = evidence_state(current, origin)
                        source_work = any(row['id'] >= origin and row['role'] == 'tool'
                            and row_payload(row).get('message', {}).get('name') in ('read_file', 'search_project')
                            for row in current)
                        if source_work:
                            data['coverage'] = coverage
                        if source_work and coverage['incomplete']:
                            data['task_outcome'] = 'source_incomplete'
                            self.store.update_message(response_id, draft, 'incomplete', payload=data)
                            self.store.add_message(self.chat_id, 'notice',
                                'Provisional response: source coverage is incomplete. '
                                'Any exhaustive-reading claim in the model response is unverified.\n'
                                + evidence_summary(coverage, max_files=3),
                                payload={'coverage': coverage, 'task_outcome': 'source_incomplete'})
                            self.changed.emit()
                            if observation_halt is not None:
                                raise observation_halt
                            if recover_source():
                                continue
                            self.progress.observe('provisional_response', {},
                                {'error': 'The model ended without resolving incomplete source coverage; no new action or evidence.'}, response_id)
                            continue
                        if observation_halt is not None:
                            self.store.update_message(response_id, draft, 'incomplete', payload=data)
                            raise observation_halt
                        check_run()
                        data.update(pause_context_closed=True, task_outcome='response_unverified',
                                    terminal_input_cursor=input_cursor)
                        self.store.update_message(response_id, draft, payload=data)
                        self.changed.emit()
                        self.status.emit('Response saved · task completion is not independently verified')
                        return

                    oversized = len(calls) > 8
                    halted = observation_halt
                    recovery_pending = []
                    recovery_paths = set()
                    try:
                        self.progress.reserve_actions(len(calls))
                    except RunHalted as error:
                        halted = error
                    for call in calls:
                        if halted is None:
                            try:
                                check_run()
                            except RunHalted as error:
                                halted = error
                            except Cancelled:
                                halted = RunHalted('cancelled', 'Stopped before this action.')
                        function = call.get('function', {})
                        name = function.get('name', '')
                        arguments = function.get('arguments', '{}')
                        try:
                            args = json.loads(arguments) if isinstance(arguments, str) else arguments
                        except (json.JSONDecodeError, TypeError):
                            args = None
                        effect_args, identity_error = args, None
                        if isinstance(args, dict) and not halted and not self.cancel_event.is_set():
                            try:
                                effect_args = executor.execution_arguments(name, args)
                            except (ValueError, OSError, RuntimeError) as error:
                                identity_error = str(error)
                        prior = source_signature(self.store.messages(self.chat_id)) if name == 'read_file' else None
                        if halted or self.cancel_event.is_set():
                            result = json.dumps({'error': 'Not executed: this run was stopped before this action.',
                                'executed': False, 'code': halted.reason if halted else 'cancelled'})
                        elif oversized:
                            result = json.dumps({'error': 'Batch exceeds 8 actions. No action in this batch ran. '
                                'Request one tool call at a time; inspect already saved evidence before repeating any action.',
                                'code': 'tool_batch_limit', 'executed': False})
                        elif not any(definition['function']['name'] == name for definition in tools):
                            result = json.dumps({'denied': 'This tool is disabled. Do not retry or bypass.'})
                        elif identity_error is not None:
                            result = json.dumps({'error': identity_error, 'executed': False})
                        elif isinstance(effect_args, dict) and self.progress.duplicate_effect(name, effect_args) is not None:
                            result = json.dumps({'error': 'Repeated action was not executed. Read its saved result instead; no new source change justifies replay.',
                                'executed': False, 'code': 'duplicate_action',
                                'result_id': self.progress.duplicate_effect(name, effect_args)})
                        else:
                            self.status.emit('Requested: ' + name)
                            self._approval_halted = None
                            # Use the same normalized execution parameters as
                            # the replay check; retain rationale/unknown keys so
                            # approval text and normal validation remain exact.
                            execution_args = {**args, **effect_args} if isinstance(args, dict) else args
                            result = executor.execute(name, execution_args)
                            if self._approval_halted is not None:
                                halted = self._approval_halted
                                result = json.dumps({'error': halted.detail,
                                    'executed': False, 'code': halted.reason})
                        ident = self.save_tool_result(call, args, result)
                        outcome = json.loads(result)
                        if name == 'run_command' and outcome.get('executed') is True:
                            # Record the parameters the tool actually used even
                            # if a cwd alias changed between checking and launch.
                            effect_args = {key: outcome[key] for key in ('command', 'cwd', 'timeout')}
                        if halted or self.cancel_event.is_set() or oversized:
                            continue
                        if 'denied' in outcome:
                            halted = RunHalted('approval_denied', 'Approval was denied or the tool is disabled. No further action will run; do not bypass this decision.')
                        elif (interrupted_outcome(outcome) or
                              name in ('write_file', 'edit_file', 'run_command', 'remember') and
                              'error' in outcome and outcome.get('executed') is not False):
                            halted = RunHalted('action_blocker', 'An action failed or was interrupted with effects that need review. Its saved result is evidence, not permission to retry.')
                        else:
                            try:
                                progress = (prior != source_signature(self.store.messages(self.chat_id))) if name == 'read_file' else None
                                # Repair invalid paging and repeated source pages
                                # before a third identical attempt can halt the
                                # run. Only application-validated paging errors
                                # are retried; ordinary I/O failures stay bounded.
                                recovery = None
                                if name == 'read_file' and isinstance(args, dict):
                                    if outcome.get('code') == 'invalid_pagination':
                                        recovery = {'path': args.get('path'), 'retry': outcome.get('retry_read_file')}
                                    elif 'error' not in outcome and progress is False:
                                        # A model-chosen smaller page may still
                                        # provide new exposure on the next call.
                                        # Intervene for already exposed text or
                                        # an exact repeated, unexposed page.
                                        current = self.store.messages(self.chat_id)
                                        state = evidence_state(current, origin)
                                        descriptors = source_evidence(name, outcome)
                                        for descriptor in descriptors:
                                            file = next((file for file in state['files'] if
                                                file['path'] == descriptor['path']), None)
                                            repeated_page = any(row['role'] == 'tool' and origin <= row['id'] < ident
                                                and descriptor in row_payload(row).get('source_evidence', [])
                                                for row in current)
                                            if repeated_page or file and all(any(left <= start and end <= right
                                                    for left, right in file['exposed_ranges'])
                                                    for start, end in descriptor['ranges']):
                                                recovery = {'path': outcome.get('path')}
                                if recovery is not None:
                                    recovery_path = recovery['path']
                                    if recovery_path not in recovery_paths:
                                        recovery_paths.add(recovery_path)
                                        recovery_pending.append((recovery, name, effect_args, outcome, ident, progress))
                                else:
                                    self.progress.observe(name, effect_args if isinstance(effect_args, dict) else {}, outcome, ident, progress=progress)
                            except RunHalted as error:
                                halted = error
                    check_run()
                    if halted:
                        raise halted
                    for recovery, name, arguments, outcome, ident, progress in recovery_pending:
                        if not recover_source(**recovery):
                            self.progress.observe(name, arguments, outcome, ident, progress=progress)
                    if oversized:
                        if batch_retry_used:
                            raise RunHalted('tool_batch_limit', 'Paused after two oversized action batches; neither batch was executed.')
                        batch_retry_used = True
                        self.status.emit('Oversized batch saved without execution; requesting one smaller step.')
                check_run()
                self.progress.next_segment()
                self.pause('action_round_limit',
                    f'Continuing automatically into segment {self.progress.segments}; the preceding ten rounds are saved.',
                    rounds, automatic=True)
        except ContextOverflowError as error:
            if message_id is not None:
                save_partial('error')
            self.pause('context_limit', str(error), rounds)
        except RunHalted as error:
            if message_id is not None:
                save_partial('interrupted')
            self.pause(error.reason, error.detail, rounds)
        except Exception as error:
            cancelled = self.cancel_event.is_set() or isinstance(error, Cancelled)
            if cancelled and self.stop_reason == 'time_budget':
                if message_id is not None:
                    save_partial('interrupted')
                self.pause('time_budget', f'Run reached its {self.limits.max_seconds:g}-second wall limit, including approval wait.', rounds)
            else:
                state = 'interrupted' if cancelled else 'error'
                if message_id is not None:
                    save_partial(state)
                if cancelled:
                    rows = self.store.messages(self.chat_id)
                    cursor = next((row['id'] for row in reversed(rows) if row['role'] == 'user'), None)
                    admission = {'terminal_input_cursor': cursor} if cursor is not None else {}
                    self.store.add_message(self.chat_id, 'notice', 'Stopped. Your conversation is saved.', state,
                        payload={'pause_context_closed': True, 'continuation': self.run_record(), **admission})
                    self.changed.emit()
                    self.status.emit('Stopped')
                else:
                    self.pause('runtime_error', str(error), rounds)
                    self.status.emit('Could not finish · see saved blocker')
        finally:
            with self._stop_lock:
                self._finished = True
            if timer:
                timer.cancel()
                timer.join()
            # Qt remains responsive while this worker owns engine teardown.
            # Its finished signal must never hand a stale cancel to a new run.
            if self._cancel_thread is not None:
                self._cancel_thread.join()
