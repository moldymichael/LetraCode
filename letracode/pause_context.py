"""Versioned references to authoritative chat rows, never an execution queue."""
from .engine import ContextOverflowError
import json

RECENT_CONTEXT_ROWS = 12


def payload(row):
    try:
        value = json.loads(row.get('payload') or '{}')
    except (TypeError, ValueError):
        raise ContextOverflowError(f"Pause context is malformed at saved message {row.get('id')}.")
    if not isinstance(value, dict):
        raise ContextOverflowError(f"Pause context is malformed at saved message {row.get('id')}.")
    return value


def active_checkpoint(rows):
    for row in reversed(rows):
        data = payload(row)
        if data.get('pause_context_closed'):
            return None
        if row['role'] == 'notice' and 'checkpoint' in data:
            checkpoint = data['checkpoint']
            if not isinstance(checkpoint, dict) or not checkpoint:
                raise ContextOverflowError(f"Pause context is malformed at saved message {row.get('id')}.")
            return checkpoint
    return None


def _ident(value):
    return type(value) is int and value > 0


def resolve_intent(rows, chat_id=None, *, starting_task=False, pinned_intent=None):
    """Keep every user steering row since the original anchor.

    Ordinary history yields to the request budget. A new task checkpoint freezes
    a bounded recent starting span; active checkpoints retain their saved span
    and all user steering. Legacy checkpoints keep their original reference
    policy. Callers supply only this chat's rows; foreign references are never
    fetched, and historical content never authorizes replay.

    A running worker may supply the validated intent frozen at admission. Until
    a checkpoint exists, this pins its original anchor and starting references
    while allowing newly saved user steering to extend the through cursor.
    """
    scoped = {row.get('chat_id') for row in rows if row.get('chat_id') is not None}
    if len(scoped) > 1 or (chat_id is not None and scoped and scoped != {chat_id}):
        raise ContextOverflowError('Pause context contains rows from another chat.')
    chat_id = chat_id or next(iter(scoped), None)
    users = [row for row in rows if row['role'] == 'user' and row['status'] == 'complete']
    checkpoint = active_checkpoint(rows)
    saved_checkpoint = checkpoint
    if checkpoint is None and pinned_intent is not None:
        if not isinstance(pinned_intent, dict):
            raise ContextOverflowError('Pinned task context is malformed.')
        checkpoint = {'user_message_id': pinned_intent.get('origin_user_message_id'),
                      'last_user_message_id': pinned_intent.get('through_user_message_id'),
                      'pause_context': pinned_intent}
    if not users:
        if checkpoint is not None:
            raise ContextOverflowError('Original intent is missing or unavailable in this chat. Start a new chat with the full request.')
        return None, [], None
    latest = users[-1]
    by_id = {row['id']: row for row in rows}
    references = None
    reference_policy = 'recent_v1' if starting_task else 'optional_v1'
    if checkpoint is not None:
        reference_policy = 'all_v1'
        context = checkpoint.get('pause_context')
        if context is not None:
            if (not isinstance(context, dict) or type(context.get('version')) is not int
                    or context['version'] != 1 or context.get('chat_id') != chat_id):
                raise ContextOverflowError('Pause context has an unsupported version or belongs to another chat.')
            anchor_id = context.get('origin_user_message_id')
            reference_policy = context.get('reference_policy', 'all_v1')
            if reference_policy not in ('all_v1', 'recent_v1'):
                raise ContextOverflowError('Pause context has an unsupported referenced context policy.')
            references = context.get('referenced_context_ids')
            if (not isinstance(references, list) or len(references) > 128
                    or any(not _ident(ident) for ident in references)):
                raise ContextOverflowError('Pause context has malformed referenced context IDs.')
            through = context.get('through_user_message_id')
            if (not _ident(through) or through not in by_id or by_id[through]['role'] != 'user'
                    or not _ident(anchor_id) or not anchor_id <= through <= latest['id']
                    or type(checkpoint.get('last_user_message_id')) is not int
                    or checkpoint['last_user_message_id'] != through):
                raise ContextOverflowError('Pause context has a missing or malformed last-user cursor.')
        else:
            anchor_id = checkpoint.get('user_message_id')
            if 'last_user_message_id' in checkpoint:
                last = checkpoint['last_user_message_id']
                if (not _ident(last) or not _ident(anchor_id) or last not in by_id
                        or by_id[last]['role'] != 'user' or by_id[last]['status'] != 'complete'
                        or not anchor_id <= last <= latest['id']):
                    raise ContextOverflowError('Pause context has a malformed last-user cursor.')
        anchor = by_id.get(anchor_id) if _ident(anchor_id) else None
        if anchor is None or anchor['role'] != 'user' or anchor['status'] != 'complete' or anchor_id > latest['id']:
            raise ContextOverflowError('Original intent is missing or unavailable in this chat. Restore its saved user message or start a new chat with the full request.')
        if saved_checkpoint is None and pinned_intent is not None and any(
                row['id'] > anchor_id and payload(row).get('pause_context_closed') for row in rows):
            raise ContextOverflowError('Pinned task context was closed; it cannot restore an ended task.')
    else:
        anchor = latest
        anchor_id = anchor['id']
    expected_references = [row['id'] for row in rows if row['id'] < anchor_id
                          and row['role'] in ('user', 'assistant', 'tool') and row['status'] == 'complete']
    if reference_policy == 'recent_v1':
        ended_after = next((row['id'] for row in reversed(rows) if row['id'] < anchor_id
                            and row['role'] == 'notice'
                            and payload(row).get('task_outcome') == 'ended_by_user'
                            and payload(row).get('pause_context_closed') is True), 0)
        expected_references = [ident for ident in expected_references if ident > ended_after][-RECENT_CONTEXT_ROWS:]
    elif reference_policy == 'optional_v1':
        expected_references = []
    if references is None:
        references = expected_references
        if len(references) > 128:
            raise ContextOverflowError('Required referenced context has more than 128 saved messages. Start a new chat with the complete selected context.')
    referenced = []
    for ident in references:
        row = by_id.get(ident)
        if (row is None or ident >= anchor_id or row['role'] not in ('user', 'assistant', 'tool')
                or row['status'] != 'complete'):
            raise ContextOverflowError(f'Required referenced context is missing or unavailable at saved message {ident}. Restore it or start a new chat with the complete context.')
        referenced.append(row)
    if references != expected_references:
        raise ContextOverflowError('Pause context has incomplete, duplicated or reordered referenced context. Restore the original checkpoint or start a new chat with the full request.')
    context = {'version': 1, 'chat_id': chat_id, 'origin_user_message_id': anchor_id,
               'through_user_message_id': latest['id'], 'referenced_context_ids': references,
               'intent_preview': anchor['content'][:240]}
    if reference_policy != 'all_v1':
        context['reference_policy'] = reference_policy
    required = referenced + [row for row in users if row['id'] >= anchor_id]
    return context, required, saved_checkpoint
