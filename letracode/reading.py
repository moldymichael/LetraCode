"""Choose bounded recovery reads from application coverage, never model claims."""
from pathlib import Path
import json
import re


def _user_reading_policy(rows, origin, paths):
    """Bind explicit full-reading directions to observed, named files only.

    This is deliberately not a relevance/understanding classifier. A question
    about a 'whole story' does not request exhaustive reading. Only user rows
    can add these requirements; source passages cannot. Later explicit user
    directions can narrow scope, unlike a later model-chosen passage read.
    """
    policy = {}
    for row in rows:
        if row['id'] < origin or row['role'] != 'user' or row['status'] != 'complete':
            continue
        text = row['content'].casefold().replace('\\', '/')
        # Do this before sentence splitting: otherwise a second sentence inside
        # a quotation could look like a new user directive. Quoted filenames
        # remain usable arguments to an actual surrounding read instruction.
        text = re.sub(r'```[\s\S]*?(?:```|\Z)|~~~[\s\S]*?(?:~~~|\Z)', ' ', text)
        text = re.sub(r'(?m)^[ \t]*>[^\n]*', ' ', text)
        names = {value.casefold().replace('\\', '/') for path in paths for value in (path, Path(path).name)}
        for pattern in (r'"([^"]*)"', r'“([^”]*)”', r'`([^`]*)`', r"(?<!\w)'([^']*)'"):
            text = re.sub(pattern, lambda match: match.group(0) if match.group(1).strip() in names else ' ', text)
        conditional = re.search(r'\bif\s+you\s+read\s+(?:any\s+)?files?\b', text)
        boundary = (r'(?<=[.!?;])\s+|\n\s*\n|\bbut\s+|'
                    r'(?:,\s*|\band\s+)(?=(?:only\s+)?(?:read|search|use)\b|'
                    r"(?:do not|don't|don’t|make sure)\b)")
        for clause in re.split(boundary, text):
            clause = clause.strip(' ,')
            # Questions about or quotations of reading instructions are not
            # direct instructions. Leave ambiguous prose to the model's scoped
            # tool choice rather than broadening application recovery.
            directive = re.match(r'(?:(?:please|can you|could you|would you|you should|'
                r'i want you to|make sure (?:that )?you)\s+)?'
                r"(?:(?:do not|don't|don’t|never|avoid|stop|no need to)\s+)?read(?:ing)?\b", clause)
            if not directive:
                continue
            negative = re.search(r"\b(?:do not|don't|don’t|never|avoid|stop|no need to)\b.{0,40}\bread(?:ing)?\b", clause)
            negative = negative or re.search(r'\bnot\s+(?:the\s+)?(?:whole|entire|complete|full)\b', clause)
            whole = re.search(r'\b(?:whole|entire|complete(?:ly)?|in full|every line|beginning to end)\b', clause)
            all_files = re.search(r'\b(?:all|every|each|any)\b.{0,30}\b(?:files?|sources?|chapters?)\b', clause)
            if not (whole or all_files or negative):
                continue
            named = []
            groups = {}
            for path in paths:
                groups.setdefault(Path(path).name.casefold(), []).append(path)
            for name, candidates in groups.items():
                exact = [path for path in candidates if re.search(
                    r'(?<![\w/])' + re.escape(path.casefold().replace('\\', '/'))
                    + r'''(?=$|[\s`"',;)]|[.!?](?:\s|$))''', clause)]
                if exact:
                    named.extend(exact)
                elif len(candidates) == 1 and re.search(r'(?<![\w/])' + re.escape(name) + r'(?![\w.])', clause):
                    named.extend(candidates)
            targets = named or (paths if all_files or conditional or re.search(r'\bstop reading\b', clause) else [])
            for path in targets:
                policy[path] = not bool(negative)
    return policy


def required_source_paths(rows, origin, state=None):
    """Whole-file obligations are direct read intent, not incidental search hits.

    Default direct reads preserve the historical whole-file contract. Passage
    reads cannot cancel an earlier obligation. Application recovery cannot add
    obligations of its own, including when reopening an older saved run.
    """
    generated = set()
    for row in rows:
        if row['id'] < origin or row['role'] != 'assistant':
            continue
        data = json.loads(row.get('payload') or '{}')
        if data.get('application_generated') == 'source_read_recovery':
            generated.update(call.get('id') for call in data.get('message', {}).get('tool_calls', []))
    required = set()
    for row in rows:
        if row['id'] < origin or row['role'] != 'tool' or row['status'] != 'complete':
            continue
        data = json.loads(row.get('payload') or '{}')
        message = data.get('message', {})
        arguments = data.get('arguments')
        if (message.get('name') != 'read_file' or message.get('tool_call_id') in generated
                or isinstance(arguments, dict) and arguments.get('scope') == 'passage'):
            continue
        # These descriptors have already been checked by evidence_state. Failed
        # reads remain ledger limitations without inventing a recoverable path.
        required.update(item['path'] for item in data.get('source_evidence', []))
        result = json.loads(message.get('content', '{}'))
        if isinstance(result, dict) and result.get('code') == 'invalid_pagination':
            # The source was found but its cursor was invalid. Preserve that
            # direct whole-file intent through the application's repaired read.
            retry = result.get('retry_read_file', {})
            if isinstance(retry, dict) and isinstance(retry.get('path'), str):
                required.add(retry['path'])
    if state is not None:
        for path, whole in _user_reading_policy(rows, origin, [file['path'] for file in state['files']]).items():
            if whole:
                required.add(path)
            else:
                required.discard(path)
    return required


class SourceReadRecovery:
    def __init__(self):
        self.page_sizes = {}

    def next_read(self, state, path=None, *, required_paths=None):
        if path is not None:
            path = str(Path(path).expanduser().resolve())
        for file in state['files']:
            if required_paths is not None and file['path'] not in required_paths:
                continue
            if path is not None and path != file['path']:
                continue
            if file['complete_supported_text']:
                continue
            gaps = file['missing_ranges']
            if gaps:
                start, end = gaps[0]
            elif not file['length_known'] or not file['exposure_observed']:
                start, end = 0, 4000
            else:
                # Extracted EOF with source_truncated is a limitation, not a
                # cursor to poll forever. Coverage must remain incomplete.
                continue
            maximum = min(self.page_sizes.get(file['path'], 4000), end - start)
            return {'path': file['path'], 'offset': start, 'max_chars': maximum}
        return None

    def smaller_page(self, arguments):
        maximum = max(1, arguments['max_chars'] // 2)
        self.page_sizes[arguments['path']] = maximum
        return {**arguments, 'max_chars': maximum}
