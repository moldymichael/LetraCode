"""Choose bounded recovery reads from application coverage, never model claims."""
from pathlib import Path


class SourceReadRecovery:
    def __init__(self):
        self.page_sizes = {}

    def next_read(self, state, path=None):
        if path is not None:
            path = str(Path(path).expanduser().resolve())
        for file in state['files']:
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
