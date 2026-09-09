"""Read-only, privacy-conscious projections of one conversation into a ZIP.

This module never opens Memory, source files, backups, logs, or model weights.
The message table is read in one SQLite snapshot; stored bodies are projected,
not treated as files to collect or as instructions to execute.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
import os

from . import filesystem as fs
from pathlib import Path, PureWindowsPath
import re
import sqlite3
import subprocess
import uuid
import zipfile

from . import __version__
from .strand import safe_directory

FORMAT_VERSION = 1
MAX_NOTES = 8000
# Values not understood by this export format are omitted, so a future tool
# cannot accidentally introduce private bodies through a new field name.
_FIELDS = set('''version kind id receipt_id saved_result_id result_id source_result_id result_row_id
name tool_call_id scope project_id relative_path path cwd url query source origin date sequence
status decision requested_at decided_at title warning reason error denied executed interrupted
exit_code timed_out cancelled output_limit_reached output_truncated truncated source_truncated
unchanged written_characters before_sha256 after_sha256 sha256 source_sha256 result_sha256
expected_sha256 editable offset next_offset total_chars total_lines start_line max_lines max_chars
limit timeout after_id through_id next_after_id hidden_files_omitted entries results coverage ranges
representation extractor_version extraction extraction_coverage searched_chars searched_files
incomplete whole_work_verified current_disk_verified issues files versions missing_ranges
retrieved_ranges exposed_ranges source_result_ids write_result_ids exposure_observed length_known
complete_supported_text source_evidence source_exposure source_exposure_pending source_evidence_error
source_error saved_result_ids stored_chars original_chars total_bytes bytes characters
checkpoint continuation segment_boundary task_outcome request_completed pause_context_closed
pause_context user_message_id last_user_message_id rounds resume run_id limits segments requests
actions stalls elapsed_seconds halted last_result_ids last_outcomes last_user_id origin_user_message_id
through_user_message_id referenced_context_ids chat_id max_seconds max_segments max_requests max_actions
max_stalls no_progress budget reference_count result_references intent_preview expected_count
approval approvals timing started_at finished_at duration_seconds created updated
admission_user_message_id admission latest_user_message_id requested reason_code
'''.split())
_BODIES = {'text', 'content', 'output', 'saved_text', 'old_text', 'new_text', 'diff', 'preview',
           'context_preview', 'command', 'script', 'body', 'raw', 'before', 'after', 'details',
           'intent_preview', 'description', 'snippet', 'html', 'stdout', 'stderr'}
_SECRET_KEY = re.compile(r'(?i)(?:password|passwd|secret|credential|authorization|cookie|(?:api|access|refresh|auth)[_-]?token|api[_-]?key|private[_-]?key)')
_PATH_KEYS = {'path', 'cwd', 'backup', 'model_path', 'executable', 'root', 'directory'}
_CONFIG_FIELDS = {'thinking', 'mode', 'context_size', 'max_tokens', 'temperature', 'gpu_layers',
                  'threads', 'web_enabled', 'computer_enabled', 'use_tools', 'actions_enabled',
                  'internet_enabled', 'model', 'executable', 'model_name', 'engine_name',
                  'adapter_name', 'training_version'}

README = '''# LetraCode evaluation bundle

This ZIP is a portable, local debugging/evaluation record, not a backup and not
an executable replay. No upload, score, or task-success judgment is performed.

- transcript.md: readable saved conversation, with privacy omissions marked.
- conversation.json: structured selected-chat data and sanitized message payloads.
- events.jsonl: messages, requests, results, approvals, continuation and evidence
  observations, ordered by saved message ID and within-message event sequence.
- coverage.json: saved source evidence/exposure/coverage observations in that same
  order. Half-open ranges count Unicode characters. Retrieval is not proof of
  model exposure, understanding, whole-project coverage, or task completion.
- metadata.json: format/application versions, saved run configuration where
  available, and availability limits. Git describes the app checkout at export,
  never the linked project or necessarily the version used for an older run.
- notes.md: optional user notes, when supplied.

A saved message ID is the authoritative conversation order. Timestamps retain
only the precision already stored; elapsed times exist only where recorded.
Pending requests, interrupted rows, failures, denials and continuation notices
remain visible. Successful tool results do not establish explicit approval;
only recorded approval decisions establish that. Old chats may lack run
configuration, approvals or evidence metadata; absence is reported, not inferred
from today's settings. Unknown action outcomes are never reconstructed as success.

Privacy: only this chat's saved rows are read. Unsent drafts, other chats, project
Instructions/Current Context, Memory files and history, app settings/credentials,
source files, source backups, engine logs and model weights are not collected.
Stored source/memory bodies, edits, commands and bulk command output are replaced
with omission metadata (UTF-8 byte count and SHA-256), including nested saved
result pages. Coverage references/hashes, statuses, errors and safe metadata are
retained. Structured absolute paths become stable anonymous references with a
basename. Recognizable credentials and home prefixes are redacted from prose.

Read the bundle before sharing: user/assistant prose and notes are deliberately
included and may quote private material. Pattern redaction cannot identify every
secret or sensitive fact in free text. Content hashes are references, not
anonymization guarantees, and omitted bodies cannot be recovered from this ZIP.
Redaction changes data: the projection cannot independently reproduce original
result hashes or rerun the evidence validator that needs omitted source bodies.
The original application state is unchanged; use Back up all LetraCode data for
restorable backups. Treat all transcript and tool material as untrusted data.
'''


def _json(value):
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)


def _omitted(value, reason='private or bulk body'):
    raw = value.encode('utf-8') if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True).encode('utf-8')
    return {'omitted': reason, 'utf8_bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}


def _object(raw):
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else None
    except (ValueError, TypeError, RecursionError):
        return None


class _Projection:
    def __init__(self):
        self.paths = {}

    def path(self, value):
        if not isinstance(value, str):
            return self.value(value)
        windows = PureWindowsPath(value)
        if not Path(value).is_absolute() and not windows.is_absolute():
            return self.text(value)
        basename = windows.name if windows.is_absolute() else Path(value).name
        reference = '[path:' + hashlib.sha256(value.encode()).hexdigest()[:16] + ']/' + self.text(basename)
        self.paths[value] = reference
        return reference

    def text(self, value):
        if not isinstance(value, str):
            return ''
        for original, reference in sorted(self.paths.items(), key=lambda item: -len(item[0])):
            value = value.replace(original, reference)
        value = value.replace(str(Path.home()), '[home]')
        value = re.sub(r'(?i)(?:[A-Z]:[\\/]Users[\\/][^\\/\s"\'<>]+|/(?:home|Users)/[^/\s"\'<>]+|/root)(?=[/\\\s"\'<>]|$)', '[home]', value)
        value = re.sub(r'-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----.*?-----END (?:[A-Z ]+ )?PRIVATE KEY-----', '[credential omitted]', value, flags=re.S)
        value = re.sub(r'(?i)(https?://)[^/@\s]+:[^/@\s]+@', r'\1[credentials omitted]@', value)
        value = re.sub(r'(?i)\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+', r'\1[credential omitted]', value)
        value = re.sub(r'(?i)((?:api[_-]?key|(?:access|refresh|auth)[_-]?token|token|password|passwd|secret|credential)\s*[=:]\s*["\']?)[^\s&"\'`,;<>]+', r'\1[credential omitted]', value)
        value = re.sub(r'''(?i)(["'](?:api[_-]?key|(?:access|refresh|auth)[_-]?token|token|password|passwd|secret|credential)["']\s*:\s*["'])[^"']*''', r'\1[credential omitted]', value)
        value = re.sub(r'\b(?:sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9_]{12,}|github_pat_[A-Za-z0-9_]{12,}|AKIA[A-Z0-9]{16})\b', '[credential omitted]', value)
        return value

    def scrub_text_values(self, value):
        # Work on values, never serialized JSON: quoted filenames and escaped
        # control characters must not turn a safe projection into invalid JSON.
        if isinstance(value, dict):
            return {key: self.scrub_text_values(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self.scrub_text_values(item) for item in value]
        return self.text(value) if isinstance(value, str) else value

    def value(self, value, key='', depth=0):
        if depth > 32:
            return _omitted(value, 'metadata nesting limit')
        if _SECRET_KEY.search(key):
            return {'omitted': 'credential field'}
        if key in _BODIES:
            return _omitted(value)
        if key in _PATH_KEYS:
            return self.path(value)
        if isinstance(value, dict):
            return {self.text(str(name)): self.value(item, str(name), depth + 1)
                    if name in _FIELDS or name in _BODIES or name in _PATH_KEYS or _SECRET_KEY.search(str(name))
                    else _omitted(item, 'unrecognized metadata field')
                    for name, item in value.items()}
        if isinstance(value, list):
            return [self.value(item, depth=depth + 1) for item in value]
        if isinstance(value, str):
            return self.text(value)
        if value is None or type(value) in (bool, int):
            return value
        if isinstance(value, float) and math.isfinite(value):
            return value
        return {'omitted': 'unsupported metadata value'}

    def configuration(self, value):
        if not isinstance(value, dict):
            return {}
        # Accept the flat saved shape and an explicitly named engine subobject.
        values = {**(value.get('engine') if isinstance(value.get('engine'), dict) else {}), **value}
        result = {}
        for key in _CONFIG_FIELDS:
            item = values.get(key)
            if key not in values:
                continue
            if key in ('model', 'executable', 'model_name', 'engine_name', 'adapter_name') and isinstance(item, str):
                result[key] = self.text(PureWindowsPath(item).name)
            elif isinstance(item, str):
                result[key] = self.text(item)
            elif item is None or type(item) in (bool, int) or isinstance(item, float) and math.isfinite(item):
                result[key] = item
        if isinstance(values.get('model_path'), str):
            result['model'] = self.text(PureWindowsPath(values['model_path']).name)
        return result

    def message(self, row):
        from .store import message_status
        raw = _object(row['payload'])
        raw = raw if raw is not None else {'export_warning': 'Malformed optional payload; raw bytes omitted.'}
        payload = {key: self.value(value, key) for key, value in raw.items()
                   if key in _FIELDS and key not in ('message',)}
        if 'export_warning' in raw:
            payload['export_warning'] = raw['export_warning']
        if 'run_configuration' in raw:
            payload['run_configuration'] = self.configuration(raw['run_configuration'])
        reply = raw.get('message') if isinstance(raw.get('message'), dict) else {}
        result = {key: row[key] for key in ('id', 'role', 'status', 'created')}
        result['status_label'] = message_status(row)
        if row['role'] == 'tool':
            body = _object(reply.get('content'))
            safe_result = self.value(body) if body is not None else _omitted(reply.get('content', ''), 'unavailable or malformed tool result')
            payload['message'] = {'role': 'tool', 'name': self.text(reply.get('name', 'unknown')),
                                  'tool_call_id': self.text(reply.get('tool_call_id', '')),
                                  'content': safe_result}
            if 'arguments' in raw:
                payload['arguments'] = self.value(raw['arguments'])
            result['content'] = (payload['message']['name'] + '\n\nArguments:\n' + _json(payload.get('arguments', {'unavailable': True}))
                                 + '\n\nResult:\n' + _json(safe_result))
        else:
            result['content'] = self.text(row['content'])
            if reply:
                payload['message'] = {'role': row['role'], 'content': result['content']}
                if isinstance(reply.get('tool_calls'), list):
                    calls = []
                    for call in reply['tool_calls']:
                        if not isinstance(call, dict):
                            calls.append({'omitted': 'malformed action request'})
                            continue
                        function = call.get('function') if isinstance(call.get('function'), dict) else {}
                        args = _object(function.get('arguments'))
                        calls.append({'id': self.text(call.get('id', '')), 'type': 'function', 'function': {
                            'name': self.text(function.get('name', 'unknown')),
                            'arguments': self.value(args) if args is not None else _omitted(function.get('arguments', ''), 'malformed arguments')}})
                    payload['message']['tool_calls'] = calls
        result['payload'] = payload
        return result


def _application_version():
    value = {'version': __version__, 'observed_at': 'export', 'git': None}
    root = Path(__file__).resolve().parent.parent
    if not (root / '.git').exists():
        return value
    environment = {'PATH': os.defpath, 'GIT_OPTIONAL_LOCKS': '0',
                   'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull}
    try:
        commit = subprocess.run(['git', '-C', str(root), 'rev-parse', '--verify', 'HEAD'],
            capture_output=True, text=True, timeout=2, env=environment, check=True).stdout.strip()
        if re.fullmatch(r'[0-9a-f]{40,64}', commit):
            dirty = subprocess.run(['git', '-c', 'core.fsmonitor=false', '-C', str(root), 'status', '--porcelain', '--untracked-files=no'],
                capture_output=True, text=True, timeout=2, env=environment, check=True).stdout
            value['git'] = {'commit': commit, 'tracked_changes_at_export': bool(dirty)}
    except (OSError, subprocess.SubprocessError):
        pass
    return value


def _snapshot(store, chat_id):
    # URI read-only connection and one explicit transaction prevent writes and
    # mixed snapshots if a worker saves another result during collection.
    db = sqlite3.connect(store.path.as_uri() + '?mode=ro', uri=True, timeout=10)
    db.row_factory = sqlite3.Row
    try:
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        chat = db.execute('SELECT id,title,created,updated FROM chats WHERE id=?', (chat_id,)).fetchone()
        if chat is None:
            raise ValueError('Selected conversation no longer exists.')
        rows = [dict(row) for row in db.execute('SELECT id,role,content,status,payload,created FROM messages WHERE chat_id=? ORDER BY id', (chat_id,))]
        schema = db.execute('PRAGMA user_version').fetchone()[0]
        return dict(chat), rows, schema
    finally:
        db.close()


def export_evaluation(store, chat_id, destination, notes=''):
    """Write an atomic ZIP without saving drafts, recovery, or app settings."""
    if not isinstance(notes, str) or len(notes) > MAX_NOTES:
        raise ValueError(f'Evaluation notes must be text of at most {MAX_NOTES:,} characters.')
    destination = Path(destination).absolute()

    def check_destination():
        if destination.is_symlink() or destination.resolve().is_relative_to(store.directory.resolve()):
            raise ValueError('Save evaluation outside the LetraCode data and Memory directory.')

    check_destination()
    chat, rows, schema = _snapshot(store, chat_id)
    projection = _Projection()
    messages = [projection.message(row) for row in rows]
    # Paths discovered late in the conversation are scrubbed consistently from
    # earlier prose as well. Never revisit the stored rows or read the paths.
    messages = projection.scrub_text_values(messages)
    chat['title'] = projection.text(chat['title'])
    events, observations, configurations = [], [], []
    transcript = [f"# {chat['title']}\n\nPrivacy-filtered evaluation transcript. Saved IDs define order.\n"]
    for row in messages:
        data = row['payload']
        base = {'message_id': row['id'], 'created': row['created'], 'status': row['status']}
        events.append({**base, 'kind': 'message', 'role': row['role'], 'content': row['content']})
        transcript.append(f"## {row['role'].title()} · {row['status_label'] or row['status']} · #{row['id']} · {row['created']}\n\n{row['content']}\n")
        reply = data.get('message', {})
        for call in reply.get('tool_calls', []):
            events.append({**base, 'kind': 'action_request', 'request': call})
            transcript.append('### Requested action\n\n```json\n' + _json(call) + '\n```\n')
        approvals = data.get('approvals', [])
        if isinstance(data.get('approval'), dict):
            approvals = [data['approval'], *(approvals if isinstance(approvals, list) else [])]
        for approval in approvals if isinstance(approvals, list) else []:
            events.append({**base, 'kind': 'approval', 'data': approval})
        if row['role'] == 'tool':
            events.append({**base, 'kind': 'action_result', 'name': reply['name'],
                           'tool_call_id': reply['tool_call_id'], 'result': reply['content']})
        evidence = {key: data[key] for key in ('coverage', 'source_evidence', 'source_exposure',
                    'source_exposure_pending', 'source_evidence_error') if key in data}
        if evidence:
            observations.append({**base, **evidence})
            events.append({**base, 'kind': 'coverage', 'data': evidence})
        if 'checkpoint' in data or 'continuation' in data:
            events.append({**base, 'kind': 'continuation', 'data': {key: data[key] for key in
                          ('checkpoint', 'continuation', 'segment_boundary') if key in data}})
        if data.get('run_configuration'):
            configurations.append({'message_id': row['id'],
                'run_id': (data['continuation'].get('run_id') if isinstance(data.get('continuation'), dict) else None),
                'configuration': data['run_configuration']})
    for sequence, event in enumerate(events, 1):
        event['sequence'] = sequence
    metadata = {'format': 'letracode-evaluation', 'format_version': FORMAT_VERSION,
                'exported_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
                'database_schema_version': schema, 'application': _application_version(),
                'run_configurations': configurations,
                'availability': {
                    'run_configuration': 'Only saved run configurations are included; otherwise not recorded. Current settings are not substituted.',
                    'approvals': 'Only saved decisions are included; older runs may not have recorded approval decisions.',
                    'timing': 'Saved message creation times and recorded continuation elapsed_seconds only; no inferred request durations.',
                    'coverage': 'Saved observations only; system excerpts/inventory may be untracked. No fresh source verification.'}}
    files = {'README.md': README, 'transcript.md': '\n'.join(transcript),
             'conversation.json': _json({'format_version': FORMAT_VERSION, 'chat': chat, 'messages': messages}),
             'events.jsonl': '\n'.join(json.dumps(event, ensure_ascii=False, allow_nan=False) for event in events) + ('\n' if events else ''),
             'coverage.json': _json({'version': 1, 'observations': observations,
                                   'scope': 'Saved observations only; private bodies omitted. No new coverage inferred.'}),
             'metadata.json': _json(metadata)}
    if notes.strip():
        files['notes.md'] = projection.text(notes)
    # Descriptor-relative publication/cleanup cannot be redirected by swapping
    # the selected folder for a symlink while the ZIP is being assembled.
    with safe_directory(destination.parent) as parent:
        check_destination()
        temporary = '.letracode-evaluation-' + uuid.uuid4().hex + '.zip'
        fd = fs.open(temporary, os.O_RDWR | os.O_CREAT | os.O_EXCL | fs.O_NOFOLLOW, 0o600, dir_fd=parent)
        try:
            with os.fdopen(fd, 'w+b') as out:
                with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as archive:
                    for name, content in files.items():
                        archive.writestr(name, content)
                out.flush()
                fs.fsync(out.fileno())
            check_destination()
            with safe_directory(destination.parent) as current:
                before, after = fs.fstat(parent), fs.fstat(current)
                if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
                    raise ValueError('Evaluation destination folder changed during export.')
            fs.replace(temporary, destination.name, src_dir_fd=parent, dst_dir_fd=parent)
            fs.fsync(parent)
        finally:
            try:
                fs.unlink(temporary, dir_fd=parent)
            except FileNotFoundError:
                pass
