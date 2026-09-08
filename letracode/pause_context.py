"""Versioned references to authoritative chat rows, never an execution queue."""
from .engine import ContextOverflowError
import json


def payload(row):
    try:
        value = json.loads(row.get('payload') or '{}')
    except (TypeError, ValueError):
        raise ContextOverflowError(f"Pause context is malformed at saved message {row.get('id')}.")
    if not isinstance(value, dict):
        raise ContextOverflowError(f"Pause context is malformed at saved message {row.get('id')}.")
    return value


def active_checkpoint(rows):
    checkpoint = None
    for row in rows:
        data = payload(row)
        if data.get('pause_context_closed'):
            checkpoint = None
        if row['role'] == 'notice' and 'checkpoint' in data:
            checkpoint = data['checkpoint']
            if not isinstance(checkpoint, dict) or not checkpoint:
                raise ContextOverflowError(f"Pause context is malformed at saved message {row.get('id')}.")
    return checkpoint


def _ident(value):
    return type(value) is int and value > 0


def resolve_intent(rows, chat_id=None):
    """Keep every user steering row since the original anchor.

    The preceding completed conversation is conservatively retained as starting
    context (including plans separated by intervening exchanges). No language
    classifier is used. Its content remains historical evidence, not permission. Callers
    must supply only this chat's rows; foreign references are never fetched.
    """
    scoped = {row.get('chat_id') for row in rows if row.get('chat_id') is not None}
    if len(scoped) > 1 or (chat_id is not None and scoped and scoped != {chat_id}):
        raise ContextOverflowError('Pause context contains rows from another chat.')
    chat_id = chat_id or next(iter(scoped), None)
    users = [row for row in rows if row['role'] == 'user' and row['status'] == 'complete']
    checkpoint = active_checkpoint(rows)
    if not users:
        if checkpoint is not None:
            raise ContextOverflowError('Original intent is missing or unavailable in this chat. Start a new chat with the full request.')
        return None, [], None
    latest = users[-1]
    by_id = {row['id']: row for row in rows}
    references = None
    if checkpoint is not None:
        context = checkpoint.get('pause_context')
        if context is not None:
            if (not isinstance(context, dict) or type(context.get('version')) is not int
                    or context['version'] != 1 or context.get('chat_id') != chat_id):
                raise ContextOverflowError('Pause context has an unsupported version or belongs to another chat.')
            anchor_id = context.get('origin_user_message_id')
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
    else:
        anchor = latest
        anchor_id = anchor['id']
    expected_references = [row['id'] for row in rows if row['id'] < anchor_id
                          and row['role'] in ('user', 'assistant', 'tool') and row['status'] == 'complete']
    if references is None:
        # No reference picker exists. Guessing the nearest answer can silently
        # replace an earlier selected plan with "Yes, ready." Keep the complete
        # starting span or visibly require a self-contained new chat.
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
    required = referenced + [row for row in users if row['id'] >= anchor_id]
    return context, required, checkpoint
