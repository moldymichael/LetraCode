"""An explicit approval boundary around model-requested computer actions."""
from __future__ import annotations

import difflib
import hashlib
import json
import os
import selectors
import signal
import stat
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from .context import ProjectFiles, read_text, readable_without_approval, sensitive
from .web import fetch_public, search_results, search_url, validate_url


@dataclass(frozen=True)
class ApprovalRequest:
    title: str
    details: str
    kind: str
    warning: str = ''


class Denied(Exception):
    pass


def schema(name, description, properties, required):
    return {'type':'function','function':{'name':name,'description':description,'parameters':{'type':'object','properties':properties,'required':required,'additionalProperties':False}}}


STRING = {'type':'string'}
MEMORY_SCOPE = {'type':'string','enum':['global','project','learning']}
SAVED_READ_TOOLS = ('read_memory', 'read_tool_result', 'list_tool_results')
TOOL_SCHEMAS = [
    schema('read_memory','Read ordinary Strand memory for this chat scope. Pages are partial; follow next_offset. Project means the active project, never another project.', {'scope':MEMORY_SCOPE,'offset':{'type':'integer'},'max_chars':{'type':'integer'}}, ['scope']),
    schema('remember','Save a user-requested memory or proposed learning update. Use project for story/project facts, global for shared preferences, learning for correctable programming evidence. Shows destination and text for review unless learning has an explicit grant. No source/identity writes or training.', {'scope':MEMORY_SCOPE,'text':STRING}, ['scope','text']),
    schema('read_tool_result','Retrieve a saved tool result from this chat, without running the action again. Use result_id from its compacted receipt or list_tool_results and follow next_offset.', {'result_id':{'type':'integer'},'offset':{'type':'integer'},'max_chars':{'type':'integer'}}, ['result_id']),
    schema('list_tool_results','Discover saved tool results from this chat, including earlier paused or compacted turns, without rerunning actions. Metadata is partial; use read_tool_result for full saved output. Start after_id=0. For each next page keep through_id and set after_id=next_after_id. limit is 1–20, default 10.', {'after_id':{'type':'integer'},'through_id':{'type':'integer'},'limit':{'type':'integer'}}, []),
    schema('list_files','List a local folder (no recursive enumeration). Outside project links requires approval.', {'path':STRING}, ['path']),
    schema('read_file','Read a UTF-8, Markdown, source, PDF or DOCX file with numbered lines. Use start_line and max_lines for long files.', {'path':STRING,'start_line':{'type':'integer'},'max_lines':{'type':'integer'}}, ['path']),
    schema('search_project','Search linked project text files for evidence. Returns diverse passages with paths and line numbers.', {'query':STRING}, ['query']),
    schema('write_file','Create or replace a UTF-8 text file. User must approve the exact diff; old contents are backed up.', {'path':STRING,'content':STRING}, ['path','content']),
    schema('run_command','Ask user to approve a shell command. Runs unsandboxed with their account; timeout and output cap apply. Never bypass a denied action.', {'command':STRING,'cwd':STRING,'timeout':{'type':'integer'},'reason':STRING}, ['command','cwd']),
    schema('web_search','Search the public internet. User approves the exact query. Do not send private project text or secrets.', {'query':STRING}, ['query']),
    schema('fetch_url','Retrieve a public HTTP/HTTPS text page for research. User approves the full URL; cite the returned URL.', {'url':STRING}, ['url']),
]


class ToolExecutor:
    def __init__(self, roots, data_dir, approve, cancel, web_enabled=True, computer_enabled=True, *, store=None, chat_id=None):
        self.store, self.chat_id = store, chat_id
        self.roots = list(roots)
        self.data_dir = Path(data_dir)
        self.approve = approve
        self.cancel = cancel
        self.web_enabled, self.computer_enabled = web_enabled, computer_enabled

    def _ask(self, request):
        if self.cancel.is_set() or not self.approve(request) or self.cancel.is_set():
            raise Denied('Action denied by user or cancelled. Do not retry or bypass it.')

    def _str(self, args, key, limit=4096):
        value = args.get(key)
        if not isinstance(value, str) or not value.strip() and key != 'content' or len(value) > limit or '\x00' in value:
            raise ValueError(f'{key} must be valid text, at most {limit:,} characters.')
        return value

    def _path(self, args, key='path'):
        raw = self._str(args, key)
        path = Path(raw).expanduser()
        if not path.is_absolute():
            raise ValueError('Use an absolute local path.')
        return path

    def _read_permission(self, path):
        if not readable_without_approval(path, self.roots):
            self._ask(ApprovalRequest('Read outside linked project files?', f'Path: {path}\nResolved path: {path.resolve()}\n\nContents will be available to the local model.', 'read', 'This file or folder is outside the normal project scope, or has a sensitive/hidden path.'))

    def execute(self, name, args):
        try:
            if self.cancel.is_set():
                raise Denied('Cancelled')
            if not isinstance(args, dict):
                raise ValueError('Tool arguments must be an object.')
            known = next((s['function'] for s in TOOL_SCHEMAS if s['function']['name'] == name), None)
            if not known:
                raise ValueError('Unknown tool.')
            if not set(args) <= set(known['parameters']['properties']):
                raise ValueError('Unknown argument.')
            if name in ('web_search','fetch_url'):
                if not self.web_enabled:
                    raise Denied('Internet access is turned off.')
            elif name not in SAVED_READ_TOOLS and not self.computer_enabled:
                raise Denied('Computer tools are turned off.')
            result = getattr(self, '_' + name)(args)
            return json.dumps(result, ensure_ascii=False)
        except Denied as error:
            return json.dumps({'denied':str(error)})
        except Exception as error:
            return json.dumps({'error':str(error)[:2500]})

    def _memory_scope(self, args):
        if self.store is None or not self.chat_id:
            raise ValueError('Memory requires an active saved chat.')
        chat = self.store.chat(self.chat_id)
        if not chat:
            raise ValueError('This chat no longer exists.')
        scope = self._str(args, 'scope', 20)
        if scope not in ('global', 'project', 'learning'):
            raise ValueError('Memory scope must be global, project or learning. Identity is user-editable only.')
        project_id = chat['project_id'] if scope == 'project' else None
        if scope == 'project' and not project_id:
            raise ValueError('Project memory requires a project chat. Choose a project or explicitly use global scope.')
        return scope, project_id

    def _read_memory(self, args):
        scope, project_id = self._memory_scope(args)
        return self.store.strand.read_page(scope, project_id=project_id,
            offset=args.get('offset', 0), max_chars=args.get('max_chars', 4000))

    def _remember(self, args):
        scope, project_id = self._memory_scope(args)
        text = self._str(args, 'text', 8000)
        snapshot = self.store.strand.snapshot(scope, project_id)
        grant = scope == 'learning' and self.store.setting('strand_learning_grant', False) is True
        if not grant:
            diff = '\n'.join(difflib.unified_diff(snapshot['text'].splitlines(),
                (snapshot['text'].rstrip() + '\n\n' + text).splitlines(),
                fromfile=str(snapshot['path']), tofile=str(snapshot['path']), lineterm=''))
            self._ask(ApprovalRequest('Remember this in Strand?',
                f"Scope: {scope}\nPath: {snapshot['path']}\n\nText to save:\n{text}\n\nAppend preview (the saved entry also records its ID, date and origin):\n{diff}",
                'memory', 'Save only if the text and scope are right. You can inspect the ordinary file and Undo this save.'))
        if self.cancel.is_set():
            raise Denied('Cancelled before saving memory.')
        return self.store.strand.remember(scope, text, project_id=project_id,
            origin=f'chat:{self.chat_id}; ' + ('learning grant' if grant else 'user approved'),
            expected_sha256=snapshot['sha256'])

    def _read_tool_result(self, args):
        if self.store is None or not self.chat_id:
            raise ValueError('Saved result retrieval requires an active chat.')
        return self.store.tool_result_page(self.chat_id, args.get('result_id'),
            offset=args.get('offset', 0), max_chars=args.get('max_chars', 4000))

    def _list_tool_results(self, args):
        if self.store is None or not self.chat_id:
            raise ValueError('Saved result retrieval requires an active chat.')
        after_id, limit = args.get('after_id', 0), args.get('limit', 10)
        if type(after_id) is not int or after_id < 0 or type(limit) is not int or not 1 <= limit <= 20:
            raise ValueError('after_id must be nonnegative and limit must be 1–20.')
        through_id = args.get('through_id')
        if 'through_id' in args and (type(through_id) is not int or through_id < 0):
            raise ValueError('through_id must be a nonnegative integer from the first page.')
        if through_id is None:
            through_id = self.store.rows(
                "SELECT COALESCE(MAX(id), 0) AS id FROM messages WHERE chat_id=? AND role='tool'",
                (self.chat_id,))[0]['id']
        # The worker saves these pages too. Freeze the upper bound so a reader
        # cannot chase newly appended catalog pages forever.
        rows = self.store.rows(
            "SELECT * FROM messages WHERE chat_id=? AND role='tool' AND id>? AND id<=? ORDER BY id LIMIT ?",
            (self.chat_id, after_id, through_id, limit + 1))
        results = []
        for row in rows[:limit]:
            message = json.loads(row['payload']).get('message', {})
            content = message.get('content', row['content'])
            if not isinstance(content, str):
                content = json.dumps(content, ensure_ascii=False)
            item = {'result_id': row['id'], 'created': row['created'], 'status': row['status']}

            def bounded(value):
                if not isinstance(value, (str, int, float, bool, type(None))):
                    value = json.dumps(value, ensure_ascii=False)
                if isinstance(value, str) and len(value) > 240:
                    item['metadata_truncated'] = True
                    return value[:240]
                return value

            item.update(name=bounded(message.get('name', '')),
                        tool_call_id=bounded(message.get('tool_call_id', '')),
                        total_chars=len(content), preview=bounded(content))
            try:
                outcome = json.loads(content)
            except (TypeError, json.JSONDecodeError):
                outcome = None
            if isinstance(outcome, dict):
                item['outcome'] = {key: bounded(outcome[key]) for key in (
                    'denied', 'error', 'executed', 'exit_code', 'timed_out', 'cancelled',
                    'output_limit_reached', 'code') if key in outcome}
                item['source'] = {key: bounded(outcome[key]) for key in (
                    'path', 'url', 'query', 'result_id') if key in outcome}
            results.append(item)
        return {'results': results, 'through_id': through_id,
                'next_after_id': results[-1]['result_id'] if len(rows) > limit else None}

    def _list_files(self, args):
        path = self._path(args)
        self._read_permission(path)
        result = []
        for entry in sorted(path.iterdir(), key=lambda p: p.name.casefold()):
            if len(result) >= 300:
                break
            if sensitive(entry):
                continue
            result.append({'path':str(entry),'kind':'symlink' if entry.is_symlink() else 'folder' if entry.is_dir() else 'file'})
        return {'entries':result, 'limit':300, 'hidden_files_omitted':True}

    def _read_file(self, args):
        path = self._path(args)
        self._read_permission(path)
        start, maximum = args.get('start_line',1), args.get('max_lines',180)
        if type(start) is not int or type(maximum) is not int or start < 1 or not 1 <= maximum <= 400:
            raise ValueError('start_line must be positive and max_lines must be 1–400.')
        lines = read_text(path).splitlines()
        end = min(len(lines), start + maximum - 1)
        text = '\n'.join(f'{i+1}: {lines[i]}' for i in range(start-1, end))
        return {'path':str(path.resolve()),'start_line':start,'total_lines':len(lines),'text':text[:16000],'truncated':end<len(lines) or len(text)>16000}

    def _search_project(self, args):
        query = self._str(args, 'query', 500)
        hits = ProjectFiles(self.roots).search(query, limit=6, cancel=self.cancel)
        return {'results':hits, 'scope':'Linked files; bounded text search, not an exhaustive analysis.'}

    def _write_file(self, args):
        original = self._path(args)
        content = self._str(args, 'content', 200000)
        if original.is_symlink():
            raise ValueError('Writing through a symlink is blocked; select the real path explicitly.')
        path = original.resolve()
        if not path.parent.is_dir():
            raise ValueError('Parent folder does not exist. Create it yourself or approve a separate command.')
        before = None
        mode = 0o600
        if path.exists():
            if not path.is_file() or path.stat().st_nlink > 1:
                raise ValueError('Can only replace ordinary files with a single hard link.')
            before = read_text(path)
            mode = stat.S_IMODE(path.stat().st_mode)
        old_bytes = path.read_bytes() if before is not None else None
        digest = hashlib.sha256(old_bytes).digest() if old_bytes is not None else None
        diff = '\n'.join(difflib.unified_diff((before or '').splitlines(), content.splitlines(), fromfile=str(path), tofile=str(path), lineterm=''))
        if before == content:
            return {'path':str(path),'unchanged':True}
        self._ask(ApprovalRequest('Approve this file edit?', f'Path: {path}\n\n{diff}', 'write', 'The complete diff is shown below. Existing contents will be backed up locally.'))
        if original.is_symlink() or original.resolve() != path:
            raise ValueError('Path changed while awaiting approval. No edit was made.')
        current = path.read_bytes() if path.exists() else None
        if (hashlib.sha256(current).digest() if current is not None else None) != digest:
            raise ValueError('File changed while awaiting approval. No edit was made; read the latest version first.')
        backup = None
        if old_bytes is not None:
            backup_dir = self.data_dir / 'file-backups'
            backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            backup = backup_dir / (uuid.uuid4().hex + '-' + path.name)
            fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd,'wb') as file:
                file.write(old_bytes); file.flush(); os.fsync(file.fileno())
        fd, temporary = tempfile.mkstemp(prefix='.letracode-', dir=path.parent)
        try:
            with os.fdopen(fd,'wb') as file:
                file.write(content.encode('utf-8')); file.flush(); os.fsync(file.fileno())
            os.chmod(temporary, mode)
            if self.cancel.is_set():
                raise Denied('Cancelled before saving.')
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)
        return {'path':str(path),'written_characters':len(content),'backup':str(backup) if backup else None}

    def _run_command(self, args):
        command = self._str(args, 'command', 12000)
        cwd = self._path(args, 'cwd').resolve(strict=True)
        if not cwd.is_dir():
            raise ValueError('Working directory is not a folder.')
        timeout = args.get('timeout',60)
        if type(timeout) is not int or not 1 <= timeout <= 300:
            raise ValueError('Timeout must be between 1 and 300 seconds.')
        reason = args.get('reason','')
        if not isinstance(reason, str):
            raise ValueError('Reason must be text.')
        self._ask(ApprovalRequest('Run this terminal command?', f'Working directory: {cwd}\nTime limit: {timeout} seconds\nPurpose: {reason[:1000]}\n\n{command}', 'command', 'Runs outside a sandbox with your user account. It can modify or delete files and send data over the network. Approve only a command you understand.'))
        env = {key:os.environ[key] for key in ('PATH','HOME','USER','LOGNAME','LANG','LC_ALL','TERM','TMPDIR','XDG_RUNTIME_DIR') if key in os.environ}
        env.update({'GIT_TERMINAL_PROMPT':'0','PAGER':'cat'})
        proc = subprocess.Popen(['/bin/bash','--noprofile','--norc','-c',command], cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
        output, size = [], 0
        timed_out, capped = False, False
        deadline = time.monotonic() + timeout
        selector = selectors.DefaultSelector()
        selector.register(proc.stdout, selectors.EVENT_READ)
        try:
            while selector.get_map():
                if self.cancel.is_set() or time.monotonic() > deadline:
                    timed_out = time.monotonic() > deadline
                    break
                for key, _ in selector.select(0.1):
                    chunk = os.read(key.fileobj.fileno(), 8192)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    output.append(chunk); size += len(chunk)
                    if size >= 64000:
                        capped = True
                        break
                if capped:
                    break
        finally:
            selector.close()
            # Kill the whole session even if the shell exited leaving children alive.
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                proc.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                pass
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait(timeout=2)
            proc.stdout.close()
        return {'output':b''.join(output).decode('utf-8',errors='replace')[:64000], 'exit_code':proc.returncode,'timed_out':timed_out,'cancelled':self.cancel.is_set(),'output_limit_reached':capped}

    def _web_approval(self, url, query=None):
        validate_url(url)
        self._ask(ApprovalRequest('Search the internet?' if query else 'Open this webpage?', (f'Search query: {query}\n\n' if query else '') + f'URL sent to website:\n{url}', 'web', 'Only this URL/query is sent. The website sees your IP address. Check that it contains no private information.'))
        return True

    def _web_search(self, args):
        query = self._str(args, 'query', 500)
        url = search_url(query)
        self._web_approval(url, query)
        page = fetch_public(url, self.cancel, self._web_approval)
        results = search_results(page['raw'])
        if not results:
            raise ValueError('Search returned no readable results. The search provider may be blocking automated access. Try a direct documentation URL or research in your browser and link a saved page.')
        return {'query':query, 'results':results,'source':page['url']}

    def _fetch_url(self, args):
        url = self._str(args, 'url')
        self._web_approval(url)
        page = fetch_public(url, self.cancel, self._web_approval)
        return {'url':page['url'],'text':page['text'][:20000],'truncated':len(page['text'])>20000}
