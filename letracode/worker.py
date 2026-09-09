"""One cancellable conversation job. Qt UI work stays on the main thread."""
from __future__ import annotations

import json
from pathlib import Path
import threading
import time
import uuid
from dataclasses import asdict

from PySide6.QtCore import QThread, Signal

from .context import build_context
from .budgeting import fallback_usage
from .engine import Cancelled, ContextOverflowError
from .pause_context import payload as row_payload, resolve_intent
from .continuation import RunHalted, RunLimits, RunProgress, interrupted_outcome
from .evidence import evidence_state, request_exposure, source_evidence, summary as evidence_summary
from .tools import ApprovalRequest, SAVED_READ_TOOLS, TOOL_SCHEMAS, ToolExecutor
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
        message = dict(payload.get('message') or {'role': row['role'], 'content': row['content']})
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
    result = protocol_messages(required_prefix(selected_messages) + selected_messages)
    if measure(result) > budget:
        raise ContextOverflowError('Core instructions and reserved reply exceed this context setting; nothing was truncated.')
    return result, compacted or len(selected) < len(turns)


class ConversationWorker(QThread):
    changed = Signal()
    status = Signal(str)
    approval_needed = Signal(object)

    def __init__(self, store, chat_id, engine, thinking=False, web_enabled=True, computer_enabled=True, use_tools=True, *, limits=None):
        super().__init__()
        self.store, self.chat_id, self.engine = store, chat_id, engine
        self.thinking = thinking
        self.web_enabled, self.computer_enabled = web_enabled, computer_enabled
        self.use_tools = use_tools
        self.cancel_event = threading.Event()
        self.pending = None
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
            'use_tools': bool(use_tools),
        }
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
        message_id, draft = None, ''
        rounds = 0
        timer = None
        try:
            if self.cancel_event.is_set():
                raise Cancelled()
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
                if next((row['id'] for row in reversed(current) if row['role'] == 'user'), None) != input_cursor:
                    raise RunHalted('new_input', 'New user input was saved. This run stopped before another dispatch; review that input before continuing.')
                return current

            self._check_dispatch = check_run

            # A crashed action must not be inferred as safe to replay. Explicit
            # input can ask for reconciliation, but cannot invent its outcome.
            pending = set()
            for row in rows:
                if row['id'] < origin:
                    continue
                message = row_payload(row).get('message', {})
                if row['role'] == 'assistant':
                    pending.update(call.get('id') for call in message.get('tool_calls') or [])
                elif row['role'] == 'tool':
                    pending.discard(message.get('tool_call_id'))
            if pending:
                raise RunHalted('unknown_outcome', 'A saved action has no recorded outcome. It may have run. Reconcile it before another action; automatic replay is blocked.')

            project = self.store.project_for_context(chat['project_id']) if chat['project_id'] else None
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
            self.status.emit('Reading current Memory and project context…')
            retrieval_budget = min(20000, int((context_size - reply_size - 128) * 1.3))
            model_path = getattr(self.engine.config, 'model_path', '')
            provenance = (f'Local model file: {Path(model_path).name if model_path else "not configured"}. '
                          f'Context: {context_size} tokens; maximum response: {reply_size} tokens. '
                          'Memory is editable user context; it does not change model weights.')
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

            def packed_messages():
                nonlocal retrieval_budget
                current = check_run()
                extra = run_context(current)
                def context():
                    return build_context(project, roots if self.computer_enabled else [], query,
                        retrieval_budget, self.cancel_event, strand=self.store.strand, provenance=provenance,
                        allow_core_overflow=True) + extra
                system = context()
                overflow = None
                while True:
                    try:
                        messages, trimmed = conversation_messages(current, system, context_size, measure=measure)
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

            batch_retry_used = False
            while True:
                # This is a new bounded segment, not an enlarged round loop.
                # Same worker/token own cancellation and approvals throughout.
                for round_index in range(10):
                    current = check_run()
                    prior_exposure = source_signature(current, 'exposed_ranges')
                    messages = packed_messages()
                    exposure = request_exposure(messages, current)
                    self.progress.reserve_request()
                    rounds = round_index + 1
                    draft = ''
                    request_record = self.run_record()
                    message_id = self.store.add_message(self.chat_id, 'assistant', '', status='streaming',
                        payload={'continuation': request_record, 'source_exposure_pending': exposure,
                                 'run_configuration': self.run_configuration})
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
                    response_id = message_id
                    data = {'message': reply, 'pause_context_closed': False,
                            'continuation': request_record, 'source_exposure': exposure,
                            'request_completed': True, 'run_configuration': self.run_configuration}
                    self.store.update_message(message_id, draft, payload=data)
                    message_id = None
                    if prior_exposure != source_signature(self.store.messages(self.chat_id), 'exposed_ranges'):
                        self.progress.observe_source_exposure()
                    self.changed.emit()
                    if not calls:
                        current = check_run()
                        coverage = evidence_state(current, origin)
                        source_work = any(row['id'] >= origin and row['role'] == 'tool'
                            and row_payload(row).get('message', {}).get('name') in ('read_file', 'search_project')
                            for row in current)
                        if source_work and coverage['incomplete']:
                            data['task_outcome'] = 'source_incomplete'
                            self.store.update_message(response_id, draft, 'incomplete', payload=data)
                            self.store.add_message(self.chat_id, 'notice',
                                'Provisional response: source coverage is incomplete. '
                                'Any exhaustive-reading claim in the model response is unverified.\n'
                                + evidence_summary(coverage, max_files=3),
                                payload={'coverage': coverage, 'task_outcome': 'source_incomplete'})
                            self.changed.emit()
                            self.progress.observe('provisional_response', {},
                                {'error': 'The model ended without resolving incomplete source coverage; no new action or evidence.'}, response_id)
                            continue
                        data.update(pause_context_closed=True, task_outcome='response_unverified',
                                    terminal_input_cursor=input_cursor)
                        self.store.update_message(response_id, draft, payload=data)
                        self.changed.emit()
                        self.status.emit('Response saved · task completion is not independently verified')
                        return

                    oversized = len(calls) > 8
                    halted = None
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
                                self.progress.observe(name, effect_args if isinstance(effect_args, dict) else {}, outcome, ident, progress=progress)
                            except RunHalted as error:
                                halted = error
                    check_run()
                    if halted:
                        raise halted
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
                self.store.update_message(message_id, draft, 'error')
            self.pause('context_limit', str(error), rounds)
        except RunHalted as error:
            if message_id is not None:
                self.store.update_message(message_id, draft, 'interrupted')
            self.pause(error.reason, error.detail, rounds)
        except Exception as error:
            cancelled = self.cancel_event.is_set() or isinstance(error, Cancelled)
            if cancelled and self.stop_reason == 'time_budget':
                if message_id is not None:
                    self.store.update_message(message_id, draft, 'interrupted')
                self.pause('time_budget', f'Run reached its {self.limits.max_seconds:g}-second wall limit, including approval wait.', rounds)
            else:
                state = 'interrupted' if cancelled else 'error'
                if message_id is not None:
                    self.store.update_message(message_id, draft, state)
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
