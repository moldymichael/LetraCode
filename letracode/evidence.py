"""Bounded ordinary-source evidence derived from saved application observations.

Ranges count Python characters and are half-open. This ledger is not an inventory,
an understanding score, or a task-completion oracle. System excerpts are untracked.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re

MAX_FILES = 128
MAX_VERSIONS = 128
MAX_RANGES = 4096
SOURCE_TOOLS = ('read_file', 'search_project')


def _int(value, label, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError(f'Invalid source evidence {label}: expected an integer >= {minimum}.')
    return value


def _hash(value):
    if not isinstance(value, str) or re.fullmatch(r'[a-f0-9]{64}', value) is None:
        raise ValueError('Invalid source evidence SHA-256 version.')
    return value


def _ranges(value, total):
    if not isinstance(value, list) or len(value) > MAX_RANGES:
        raise ValueError('Source evidence exceeds the 4096-range limit or has invalid ranges.')
    result = []
    for pair in value:
        if not isinstance(pair, list) or len(pair) != 2:
            raise ValueError('Invalid source evidence range.')
        start, end = (_int(pair[0], 'range start'), _int(pair[1], 'range end'))
        if not start <= end <= total:
            raise ValueError('Source evidence range falls outside supported text.')
        if start != end:
            result.append([start, end])
    return result


def _merge(ranges):
    merged = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    if len(merged) > MAX_RANGES:
        raise ValueError('Source evidence exceeds the 4096-range limit.')
    return merged


def _missing(ranges, total):
    result, cursor = [], 0
    for start, end in ranges:
        if cursor < start:
            result.append([cursor, start])
        cursor = end
    if cursor < total:
        result.append([cursor, total])
    return result


def _source(result, name):
    if not isinstance(result, dict):
        raise ValueError('Invalid source evidence result object.')
    path, text = result.get('path'), result.get('text')
    if (not isinstance(path, str) or len(path) > 4096 or '\x00' in path
            or not Path(path).is_absolute() or '..' in Path(path).parts):
        raise ValueError('Invalid source evidence absolute path.')
    if not isinstance(text, str) or len(text) > 16000:
        raise ValueError('Invalid or oversized source evidence text page.')
    total = _int(result.get('total_chars'), 'total_chars')
    editable, truncated = result.get('editable'), result.get('source_truncated')
    if type(editable) is not bool or type(truncated) is not bool:
        raise ValueError('Source evidence editability and truncation must be booleans.')
    if editable:
        source_hash, extractor, scope = _hash(result.get('sha256')), 'utf8-v1', 'UTF-8 source text'
    else:
        extraction = result.get('extraction')
        if not isinstance(extraction, dict) or result.get('sha256') is not None:
            raise ValueError('Read-only source evidence requires binary and extraction provenance.')
        extractor = extraction.get('version')
        if not isinstance(extractor, str) or not extractor or len(extractor) > 160:
            raise ValueError('Invalid source evidence extractor version.')
        source_hash = _hash(result.get('source_sha256'))
        scope = extraction.get('coverage', 'Supported extracted document text only')
        if not isinstance(scope, str) or len(scope) > 1000:
            raise ValueError('Invalid source extraction coverage description.')
    coverage = result.get('coverage')
    if coverage is not None:
        if not isinstance(coverage, dict) or type(coverage.get('version')) is not int or coverage['version'] != 1:
            raise ValueError('Invalid source coverage metadata version.')
        representation = coverage.get('representation')
        if representation not in ('raw-characters', 'numbered-lines'):
            raise ValueError('Invalid source evidence representation.')
        ranges = _ranges(coverage.get('ranges'), total)
        if sum(end - start for start, end in ranges) > len(text) + 2:
            raise ValueError('Source coverage exceeds the returned text representation.')
    else:
        representation, ranges = 'raw-characters', None
    if representation == 'raw-characters' or name == 'search_project':
        offset = _int(result.get('offset'), 'offset')
        if offset + len(text) > total and text:
            raise ValueError('Source page exceeds its supported text length.')
        actual = [[offset, offset + len(text)]] if text else []
        if ranges is not None and ranges != actual:
            raise ValueError('Source coverage does not match the returned character page.')
        ranges = actual
    return {'version': 1, 'kind': 'source', 'path': path, 'source_sha256': source_hash,
            'extractor_version': extractor, 'extraction_coverage': scope,
            'total_chars': total, 'ranges': ranges, 'source_truncated': truncated,
            'representation': representation}


def source_evidence(name, result_dict):
    """Describe only successful application source results, never model text."""
    if name not in SOURCE_TOOLS:
        return []
    if not isinstance(result_dict, dict):
        raise ValueError('Invalid source evidence result object.')
    if 'denied' in result_dict or 'error' in result_dict:
        return []
    results = result_dict.get('results') if name == 'search_project' else [result_dict]
    if not isinstance(results, list) or len(results) > MAX_FILES:
        raise ValueError('Source evidence exceeds the 128-file limit or has invalid results.')
    return [_source(result, name) for result in results]


def _payload(row):
    try:
        data = json.loads(row.get('payload') or '{}')
    except (TypeError, ValueError) as error:
        raise ValueError(f"Malformed evidence payload at saved row {row.get('id')}.") from error
    if not isinstance(data, dict):
        raise ValueError(f"Malformed evidence payload at saved row {row.get('id')}.")
    return data


def _body(row):
    message = _payload(row).get('message', {})
    if not isinstance(message, dict):
        raise ValueError(f"Malformed saved tool message at row {row.get('id')}.")
    return message


def _scoped(rows):
    chats = {row.get('chat_id') for row in rows if row.get('chat_id') is not None}
    if len(chats) > 1:
        raise ValueError('Source evidence cannot combine rows from different chats.')
    by_id = {}
    for row in rows:
        ident = _int(row.get('id'), 'saved row ID', 1)
        if ident in by_id:
            raise ValueError('Source evidence contains duplicate saved row IDs.')
        by_id[ident] = row
    return by_id


def _saved_sources(row):
    data = _payload(row)
    if 'source_evidence' not in data:
        return []
    if row['role'] != 'tool' or row['status'] != 'complete':
        raise ValueError('Source evidence must belong to a completed tool result.')
    descriptors = data['source_evidence']
    if not isinstance(descriptors, list) or len(descriptors) > MAX_FILES:
        raise ValueError('Invalid saved source evidence list (128-file limit).')
    message = _body(row)
    try:
        expected = source_evidence(message.get('name'), json.loads(message.get('content', '')))
    except (TypeError, ValueError) as error:
        raise ValueError(f"Invalid source evidence at saved result {row['id']}: {error}") from error
    if descriptors != expected:
        raise ValueError(f"Source evidence does not match saved result {row['id']}.")
    return descriptors


def _saved_page(row, by_id):
    try:
        page = json.loads(_body(row).get('content', ''))
    except (TypeError, ValueError) as error:
        raise ValueError('Malformed saved-result recovery page.') from error
    if not isinstance(page, dict) or 'error' in page or 'denied' in page:
        return None
    original_id = _int(page.get('result_id'), 'source result ID', 1)
    original = by_id.get(original_id)
    if original is None or original['role'] != 'tool' or original['status'] != 'complete' or original_id >= row['id']:
        raise ValueError('Saved-result recovery refers to unavailable earlier evidence in this chat.')
    if not _saved_sources(original):
        return None
    raw = _body(original).get('content')
    offset, total = _int(page.get('offset'), 'saved page offset'), _int(page.get('total_chars'), 'saved result total')
    content = page.get('content')
    expected_hash = hashlib.sha256(raw.encode('utf-8')).hexdigest()
    if (not isinstance(content, str) or total != len(raw) or page.get('sha256') != expected_hash
            or content != raw[offset:offset + len(content)]):
        raise ValueError('Saved-result page content/hash does not match its saved source version.')
    end = min(offset + len(content), total)
    ranges = [[offset, end]] if content else []
    return {'version': 1, 'kind': 'saved_result_page', 'source_result_id': original_id,
            'result_row_id': row['id'], 'result_sha256': expected_hash,
            'total_chars': total, 'ranges': ranges}


def request_exposure(messages, rows):
    """Describe exact full tool bodies in the submitted request; caller records success.

    Partial previews/system excerpts are deliberately uncredited. Recovery-page
    proofs address saved JSON; evidence_state promotes source coverage only after
    all of that JSON has been exposed across successful completed requests.
    """
    by_id = _scoped(rows)
    visible = {(message.get('tool_call_id'), message.get('name'), message.get('content'))
               for message in messages if message.get('role') == 'tool'
               and isinstance(message.get('content'), str)}
    result = []
    for row in rows:
        if row['role'] != 'tool' or row['status'] != 'complete':
            continue
        message = _body(row)
        if (message.get('tool_call_id'), message.get('name'), message.get('content')) not in visible:
            continue
        for descriptor in _saved_sources(row):
            result.append({**descriptor, 'source_result_ids': [row['id']]})
        if message.get('name') == 'read_tool_result':
            proof = _saved_page(row, by_id)
            if proof is not None:
                result.append(proof)
    if len(result) > MAX_RANGES:
        raise ValueError('Request evidence exceeds the 4096-observation limit.')
    return result


def _key(descriptor):
    return descriptor['path'], descriptor['source_sha256'], descriptor['extractor_version']


def _requested_path(data):
    arguments = data.get('arguments')
    path = arguments.get('path') if isinstance(arguments, dict) else None
    if (isinstance(path, str) and path and '\x00' not in path
            and Path(path).expanduser().is_absolute()):
        return os.path.normpath(str(Path(path).expanduser()))
    return None


def _written_source(row, versions):
    """A successful approved source write observes a new version, not a read."""
    message = _body(row)
    if message.get('name') not in ('write_file', 'edit_file'):
        return None
    try:
        result = json.loads(message.get('content', ''))
    except (TypeError, ValueError) as error:
        raise ValueError('Malformed saved source write result.') from error
    if not isinstance(result, dict) or 'error' in result or 'denied' in result:
        return None
    path = result.get('path')
    if (not isinstance(path, str) or len(path) > 4096 or '\x00' in path
            or not Path(path).is_absolute() or '..' in Path(path).parts):
        raise ValueError('Invalid path in saved source write result.')
    arguments = _payload(row).get('arguments')
    if arguments is not None and (not isinstance(arguments, dict)
            or not isinstance(arguments.get('path'), str)
            or Path(arguments['path']).expanduser() != Path(path)):
        raise ValueError('Source write result path differs from its saved arguments.')
    sha = _hash(result.get('sha256'))
    existing = versions.get((path, sha, 'utf8-v1'))
    unchanged = result.get('unchanged', False)
    if type(unchanged) is not bool:
        raise ValueError('Invalid unchanged flag in saved source write result.')
    length_known = not unchanged or (existing is not None and existing['length_known'])
    total = existing['total_chars'] if unchanged and existing else (
        0 if unchanged else _int(result.get('written_characters'), 'written_characters'))
    return {'version': 1, 'kind': 'source', 'path': path, 'source_sha256': sha,
            'extractor_version': 'utf8-v1', 'extraction_coverage': 'UTF-8 source text',
            'total_chars': total, 'ranges': [], 'source_truncated': False,
            'representation': 'write-observation', 'length_known': length_known}


def evidence_state(rows, origin_user_id):
    """Rebuild observed ordinary-read coverage; never infer whole-work coverage."""
    by_id = _scoped(rows)
    anchor = by_id.get(_int(origin_user_id, 'origin user ID', 1))
    if anchor is None or anchor['role'] != 'user' or anchor['status'] != 'complete':
        raise ValueError('Source coverage requires the original complete user row in this chat.')
    rows = sorted((row for row in rows if row['id'] >= origin_user_id), key=lambda row: row['id'])
    versions, latest, saved, recovery = {}, {}, {}, {}
    issues = ['System retrieval excerpts and whole-work inventory are untracked; this is ordinary-read coverage only.',
              'Coverage describes observed snapshots, not a fresh verification of current files on disk.']
    untracked = False
    unresolved_reads = set()

    def add(descriptor, ident, exposed=False, write=False):
        key = _key(descriptor)
        if key not in versions:
            if len(versions) >= MAX_VERSIONS:
                raise ValueError('Source evidence exceeds the 128-version limit.')
            versions[key] = {key: value for key, value in descriptor.items() if key not in ('ranges', 'kind', 'version')}
            versions[key].update(retrieved_ranges=[], exposed_ranges=[], source_result_ids=[],
                                 write_result_ids=[], exposure_observed=False,
                                 length_known=descriptor.get('length_known', True))
        entry = versions[key]
        if not entry['length_known'] and descriptor.get('length_known', True):
            entry.update(total_chars=descriptor['total_chars'], length_known=True)
        if (entry['total_chars'] != descriptor['total_chars'] or
                entry['source_truncated'] != descriptor['source_truncated'] or
                entry['extraction_coverage'] != descriptor['extraction_coverage']):
            raise ValueError('Inconsistent source text length/coverage for the same source version.')
        field = 'exposed_ranges' if exposed else 'retrieved_ranges'
        entry[field] = _merge(entry[field] + descriptor['ranges'])
        ids_field = 'write_result_ids' if write else 'source_result_ids'
        entry[ids_field] = sorted(set(entry[ids_field] + [ident]))
        if len(entry[ids_field]) > MAX_RANGES:
            raise ValueError('Source evidence exceeds the 4096-result-ID limit.')
        if not write:
            entry['representation'] = descriptor['representation']
        if exposed:
            entry['exposure_observed'] = True
        else:
            latest[descriptor['path']] = key
        if len(latest) > MAX_FILES:
            raise ValueError('Source evidence exceeds the 128-file limit.')

    for row in rows:
        if row['role'] != 'tool' or row['status'] != 'complete':
            continue
        descriptors = _saved_sources(row)
        saved[row['id']] = descriptors
        mutation = _written_source(row, versions)
        if mutation is not None:
            add(mutation, row['id'], write=True)
        if _body(row).get('name') == 'run_command':
            note = 'Commands may change files without an observed source hash; command output is not source-read coverage.'
            if note not in issues:
                issues.append(note)
        if not descriptors and _body(row).get('name') in SOURCE_TOOLS:
            data = _payload(row)
            path = _requested_path(data)
            # Modern unsuccessful reads have a validated empty sidecar and
            # saved request arguments. Keep their history, but allow a later
            # supported read of that path to resolve the failed attempt.
            # Normalize lexically: rebuilding history must not follow today's
            # symlinks and reinterpret the source identity of old evidence.
            if ('source_evidence' in data and _body(row).get('name') == 'read_file'
                    and path is not None):
                unresolved_reads.add(path)
            else:
                untracked = True
            issues.append(f"Saved source result {row['id']} is untracked or unsuccessful; no exposure is inferred.")
        for descriptor in descriptors:
            add(descriptor, row['id'])
            if _body(row).get('name') == 'read_file':
                unresolved_reads.discard(os.path.normpath(descriptor['path']))
                unresolved_reads.discard(_requested_path(_payload(row)))
    for row in rows:
        if row['role'] != 'assistant' or row['status'] != 'complete':
            continue
        observations = _payload(row).get('source_exposure', [])
        if not isinstance(observations, list) or len(observations) > MAX_RANGES:
            raise ValueError('Invalid saved request exposure list (4096-observation limit).')
        for observation in observations:
            if not isinstance(observation, dict) or type(observation.get('version')) is not int or observation['version'] != 1:
                raise ValueError('Invalid saved source exposure metadata version.')
            if observation.get('kind') == 'saved_result_page':
                page_id = _int(observation.get('result_row_id'), 'recovery page row ID', 1)
                page_row = by_id.get(page_id)
                if (page_row is None or page_id >= row['id'] or page_row['status'] != 'complete'
                        or page_row['role'] != 'tool' or _body(page_row).get('name') != 'read_tool_result'
                        or _saved_page(page_row, by_id) != observation):
                    raise ValueError('Saved source recovery proof does not match its completed result.')
                source_id = observation['source_result_id']
                if source_id < origin_user_id:
                    continue
                key = (source_id, observation['result_sha256'])
                recovery[key] = _merge(recovery.get(key, []) + observation['ranges'])
                if not _missing(recovery[key], observation['total_chars']):
                    for descriptor in saved.get(source_id, []):
                        add(descriptor, source_id, exposed=True)
            elif observation.get('kind') == 'source':
                identifiers = observation.get('source_result_ids')
                if not isinstance(identifiers, list) or not identifiers or len(identifiers) > MAX_FILES:
                    raise ValueError('Source exposure requires bounded saved result IDs.')
                descriptor = {key: value for key, value in observation.items() if key != 'source_result_ids'}
                for ident in identifiers:
                    _int(ident, 'source result ID', 1)
                    if ident >= row['id'] or ident not in by_id:
                        raise ValueError('Source exposure refers to unavailable earlier evidence.')
                    if by_id[ident]['role'] != 'tool' or by_id[ident]['status'] != 'complete':
                        raise ValueError('Source exposure must reference a completed tool result.')
                    original = saved[ident] if ident in saved else _saved_sources(by_id[ident])
                    if descriptor not in original:
                        raise ValueError('Source exposure differs from its saved source result.')
                    if ident >= origin_user_id:
                        add(descriptor, ident, exposed=True)
            else:
                raise ValueError('Unknown saved source exposure kind.')
    range_count = sum(len(entry[field]) for entry in versions.values() for field in ('retrieved_ranges', 'exposed_ranges'))
    if range_count + sum(map(len, recovery.values())) > MAX_RANGES:
        raise ValueError('Source evidence exceeds the 4096-range limit.')
    files = []
    for path, current in sorted(latest.items()):
        history = []
        for key, entry in versions.items():
            if key[0] != path:
                continue
            missing = _missing(entry['exposed_ranges'], entry['total_chars'])
            history.append({**entry, 'missing_ranges': missing,
                            'complete_supported_text': entry['length_known'] and entry['exposure_observed'] and not missing and not entry['source_truncated']})
        file = next(entry for entry in history if _key(entry) == current)
        files.append({**file, 'versions': history})
        if len(history) > 1:
            issues.append(f'Source version changed: {path}; earlier coverage was not merged into the current version.')
        if file['source_truncated']:
            issues.append(f'Extraction limit leaves unsupported source text: {path}.')
    if not files:
        issues.append('No tracked ordinary source files are available for coverage verification.')
    return {'version': 1, 'files': files,
            'incomplete': untracked or bool(unresolved_reads) or not files or any(not file['complete_supported_text'] for file in files),
            'issues': issues, 'whole_work_verified': False, 'current_disk_verified': False,
            'scope': 'Observed ordinary source results only; system excerpts and inventory are untracked.'}


def summary(state, max_files=8):
    """Small coverage guidance with source IDs; never an understanding claim."""
    _int(max_files, 'summary file limit')
    if max_files > MAX_FILES:
        raise ValueError('Source summary exceeds the 128-file limit.')
    files = state['files']
    complete = sum(file['complete_supported_text'] for file in files)
    text = (f'Ordinary read coverage: {complete}/{len(files)} observed files have complete supported-text exposure. '
            'This does not verify whole-work scope, understanding, or task completion. '
            'System excerpts are untracked. Ranges are half-open Unicode character offsets.\n')
    shown = 0
    for file in files[:max_files]:
        gaps = ', '.join(f'[{start},{end})' for start, end in file['missing_ranges'][:3]) or (
            'none' if file['exposure_observed'] else 'no verified exposure yet')
        if not file['length_known']:
            gaps = 'unknown until source is read'
        if len(file['missing_ranges']) > 3:
            gaps += f" (+{len(file['missing_ranges']) - 3} more gaps)"
        path = json.dumps(file['path'], ensure_ascii=False)
        if len(path) > 190:
            path = path[:187] + '...'
        line = (f"{path}; version {file['source_sha256'][:12]}/{file['extractor_version'][:60]}; "
                f"missing {gaps}; saved source result IDs {file['source_result_ids'][-6:]}"
                + ('; extraction truncated' if file['source_truncated'] else '') + '\n')
        if len(text) + len(line) > 2300:
            break
        text += line
        shown += 1
    if shown < len(files):
        text += f'{len(files) - shown} files omitted from this summary; full ranges remain in the ledger.\n'
    if state['incomplete']:
        text += 'Coverage remains incomplete or untracked. Recover saved results or read missing source pages; do not claim complete inspection.'
    return text[:2500]
