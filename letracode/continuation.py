"""Pure run-wide limits and observed-progress accounting.

This controller grants no permissions and never dispatches or retries actions.
Callers reserve budgets before dispatch and save outcomes before observing them.
"""
from __future__ import annotations

from collections import deque
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import math
import time


class RunHalted(RuntimeError):
    def __init__(self, reason: str, detail: str):
        self.reason, self.detail = reason, detail
        super().__init__(detail)


@dataclass(frozen=True)
class RunLimits:
    max_segments: int = 12
    max_requests: int = 120
    max_actions: int = 240
    max_seconds: float = 3600.0
    max_stalls: int = 3

    def __post_init__(self):
        for field in ('max_segments', 'max_requests', 'max_actions', 'max_stalls'):
            if type(getattr(self, field)) is not int or getattr(self, field) < 1:
                raise ValueError(f'{field} must be a positive integer')
        if (type(self.max_seconds) not in (int, float) or not math.isfinite(self.max_seconds)
                or self.max_seconds <= 0):
            raise ValueError('max_seconds must be finite and positive')


_READ_TOOLS = frozenset(('read_file', 'read_memory', 'read_tool_result', 'list_tool_results',
                         'list_files', 'search_project', 'web_search', 'fetch_url'))
_WRITE_TOOLS = frozenset(('write_file', 'edit_file', 'remember'))
_EFFECT_TOOLS = _WRITE_TOOLS | {'run_command'}
_VOLATILE = frozenset(('id', 'result_id', 'receipt_id', 'call_id', 'tool_call_id',
                        'created', 'updated', 'date', 'timestamp', 'started_at', 'completed_at',
                        'duration', 'duration_seconds', 'elapsed_seconds'))
_SAVED_CURSORS = frozenset(('after_id', 'through_id', 'next_after_id'))
_PAGE_ARGUMENTS = frozenset(('offset', 'next_offset', 'start_line', 'end_line', 'max_lines', 'max_chars'))


def _without_metadata(value, omitted):
    if isinstance(value, dict):
        return {key: _without_metadata(item, omitted) for key, item in value.items() if key not in omitted}
    if isinstance(value, list):
        return [_without_metadata(item, omitted) for item in value]
    return value


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _effect_identity(name, args):
    if name == 'run_command':
        # The tool adapter supplies the resolved execution cwd. The optional
        # approval rationale is descriptive, never part of the shell operation.
        return _canonical({'command': args.get('command'), 'cwd': args.get('cwd'),
                           'timeout': args.get('timeout', 60)})
    return _canonical(args)


def _fingerprint(name, args, result, empty_page):
    omitted = _VOLATILE
    argument_omitted = {'tool_call_id', 'call_id'}
    if name in ('read_tool_result', 'list_tool_results'):
        omitted |= _SAVED_CURSORS
        argument_omitted |= {'result_id'} | _SAVED_CURSORS
    if name == 'read_tool_result' and isinstance(result, dict):
        result = dict(result)
        # A saved outcome's hash/size can change solely because a newly saved
        # row ID or timestamp appears inside its JSON content. Compare evidence.
        result.pop('sha256', None)
        result.pop('total_chars', None)
        if isinstance(result.get('content'), str):
            try:
                result['content'] = json.loads(result['content'])
            except (ValueError, RecursionError):
                pass  # A partial text page remains ordinary evidence text.
    if name == 'list_tool_results' and isinstance(result, dict):
        result = _without_metadata(result, omitted | {'preview', 'total_chars', 'metadata_truncated'})
        if isinstance(result.get('results'), list):
            # Appending duplicate catalog rows does not create new evidence.
            result['results'] = sorted({_canonical(row) for row in result['results']})
    if empty_page:
        argument_omitted |= _PAGE_ARGUMENTS
        omitted |= _PAGE_ARGUMENTS
    return hashlib.sha256(_canonical([
        name, _without_metadata(args, argument_omitted), _without_metadata(result, omitted),
    ]).encode('utf-8')).hexdigest()


def _result(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, RecursionError):
            return {'error': 'Tool outcome was not valid JSON'}
    if not isinstance(value, (dict, list)):
        return {'error': 'Tool outcome had no structured result'}
    return value


def _successful(name, result):
    if isinstance(result, dict):
        if 'error' in result or 'denied' in result or result.get('timed_out') or result.get('cancelled'):
            return False
    if name == 'run_command':
        return (isinstance(result, dict) and result.get('executed') is True
                and type(result.get('exit_code')) is int and result['exit_code'] == 0
                and not result.get('output_limit_reached'))
    if name == 'remember':
        return isinstance(result, dict) and result.get('status') == 'saved'
    if name in ('write_file', 'edit_file'):
        return (isinstance(result, dict) and
                (result.get('unchanged') is True or type(result.get('written_characters')) is int))
    return True


class RunProgress:
    def __init__(self, limits: RunLimits | None = None, clock=time.monotonic):
        self.limits = limits if limits is not None else RunLimits()
        if not isinstance(self.limits, RunLimits):
            raise ValueError('limits must be RunLimits')
        self._clock, self._started = clock, clock()
        self.requests, self.actions, self.segments, self.stalls = 0, 0, 1, 0
        self._halted = None
        self._seen = set()
        self._source_hashes = {}
        self._generation = 0
        self._effects = {}
        self._result_ids = deque(maxlen=16)
        self._outcomes = deque(maxlen=8)

    def _halt(self, reason, detail):
        self._halted = self._halted or RunHalted(reason, detail)
        raise self._halted

    def check_time(self):
        if self._halted is not None:
            raise self._halted
        if self._clock() - self._started >= self.limits.max_seconds:
            self._halt('time_budget', f'Run reached its {self.limits.max_seconds:g}-second time limit.')

    def reserve_request(self):
        self.check_time()
        if self.requests >= self.limits.max_requests:
            self._halt('request_budget', f'Run reached its {self.limits.max_requests}-request limit.')
        self.requests += 1

    def reserve_actions(self, count):
        if type(count) is not int or count < 0:
            raise ValueError('Action count must be a nonnegative integer')
        self.check_time()
        if self.actions + count > self.limits.max_actions:
            self._halt('action_budget', f'Run cannot dispatch {count} actions within its {self.limits.max_actions}-action limit.')
        self.actions += count

    def next_segment(self):
        self.check_time()
        if self.segments >= self.limits.max_segments:
            self._halt('segment_budget', f'Run reached its {self.limits.max_segments}-segment limit.')
        self.segments += 1

    def duplicate_effect(self, name, args):
        """Return an earlier saved outcome ID; callers must not replay it.

        An executed command is remembered even when it failed or was interrupted.
        A changed known source or successful write permits a new verification
        command. This is never authorization to execute that command.
        """
        if name not in _EFFECT_TOOLS:
            return None
        found = self._effects.get((name, _effect_identity(name, args)))
        return found[1] if found is not None and found[0] in (None, self._generation) else None

    def observe_source_exposure(self):
        """New application-verified exposure breaks a stall streak, not replay protection.

        The worker compares completed-request range unions before calling this.
        Merely exposing an already retrieved source never changes the source
        generation and cannot authorize another command or edit.
        """
        self.stalls = 0

    def _source_change(self, name, args, result, successful):
        if (not successful or not isinstance(result, dict)
                or name not in _WRITE_TOOLS | {'read_file', 'read_memory'}):
            return False
        path = result.get('path', args.get('path'))
        source_hash = result.get('source_sha256') or result.get('sha256') or result.get('after_sha256')
        changed = name in _WRITE_TOOLS and result.get('unchanged') is not True
        if isinstance(path, str) and isinstance(source_hash, str) and source_hash:
            previous = self._source_hashes.get(path)
            changed = changed or previous is not None and previous != source_hash
            self._source_hashes[path] = source_hash
        if changed and name in ('write_file', 'edit_file', 'read_file'):
            self._generation += 1
        return changed

    def observe(self, name, args, result, result_id, progress=None):
        """Record a saved outcome, returning whether it advances this run.

        Repetitions and failures share a consecutive stall budget. `progress`
        may report newly covered source ranges; explicit False overrides source
        page novelty, while None infers novelty. It cannot turn failure or empty
        EOF pages into success. Result row IDs never establish progress.
        """
        if self._halted is not None:
            raise self._halted
        if not isinstance(name, str) or not isinstance(args, dict) or progress is not None and type(progress) is not bool:
            raise ValueError('Observation requires a tool name, argument object and optional boolean progress')
        result = _result(result)
        successful = _successful(name, result)
        changed = self._source_change(name, args, result, successful)
        source_page = name in ('read_file', 'read_memory', 'read_tool_result')
        empty_page = source_page and isinstance(result, dict) and result.get(
            'content' if name == 'read_tool_result' else 'text') == ''
        fingerprint = _fingerprint(name, args, result, empty_page)
        unchanged = isinstance(result, dict) and result.get('unchanged') is True
        novel = fingerprint not in self._seen
        if source_page and progress is False:
            novel = False
        progressed = successful and not unchanged and (progress is True or changed or novel)
        if empty_page:
            # The absence of all content is evidence once. An empty cursor past
            # a nonempty source supplies no new range, regardless of its offset.
            progressed = (successful and type(result.get('total_chars')) is int and result['total_chars'] == 0
                          and (changed or fingerprint not in self._seen))
        self._seen.add(fingerprint)
        self.stalls = 0 if progressed else self.stalls + 1
        if result_id is not None:
            self._result_ids.append(result_id)
        if name not in _READ_TOOLS:
            summary = {}
            if isinstance(result, dict):
                for key in ('executed', 'exit_code', 'timed_out', 'cancelled', 'output_limit_reached',
                            'path', 'sha256', 'after_sha256', 'unchanged', 'status', 'error', 'denied'):
                    if key in result:
                        value = result[key]
                        summary[key] = (value if type(value) in (int, float, bool, type(None))
                                        else (value if isinstance(value, str) else _canonical(value))[:400])
                if isinstance(result.get('output'), str):
                    summary['output_tail'] = result['output'][-600:]
                if name == 'run_command':
                    for key in ('command', 'cwd'):
                        value = result.get(key, args.get(key))
                        if isinstance(value, str):
                            summary[key] = value[:400]
            self._outcomes.append({'name': name, 'result_id': result_id, 'successful': successful,
                                   'outcome': summary})
        completed = (name in _WRITE_TOOLS and successful or name == 'run_command'
                     and isinstance(result, dict) and result.get('executed') is True)
        if completed:
            interrupted = name == 'run_command' and (result.get('timed_out') or result.get('cancelled')
                                                     or type(result.get('exit_code')) is not int)
            # Later source changes cannot resolve unknown interrupted effects.
            self._effects[(name, _effect_identity(name, args))] = (None if interrupted else self._generation, result_id)
        # Keep the just-saved outcome even if the dispatch consumed the final
        # time allowance. The caller can checkpoint the terminal snapshot.
        self.check_time()
        if self.stalls >= self.limits.max_stalls:
            kind = 'failed or repeated outcomes' if not successful else 'repeated outcomes without new evidence'
            self._halt('no_progress', f'Run stopped after {self.stalls} consecutive {kind}.')
        return progressed

    def snapshot(self):
        return {'segments': self.segments, 'requests': self.requests, 'actions': self.actions,
                'stalls': self.stalls, 'elapsed_seconds': max(0.0, self._clock() - self._started),
                'halted': self._halted.reason if self._halted is not None else None,
                'last_result_ids': deepcopy(list(self._result_ids)),
                'last_outcomes': deepcopy(list(self._outcomes))}
