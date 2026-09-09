"""Separate a model's leading think block without interpreting ordinary prose."""


class ThinkSplitter:
    """Handle tags fragmented across stream events; only the leading block counts."""

    def __init__(self, on_content, on_reasoning):
        self.on_content = on_content
        self.on_reasoning = on_reasoning
        self.state = 'prefix'
        self.pending = ''

    def feed(self, text):
        if not text:
            return
        if self.state == 'answer':
            self.on_content(text)
            return
        self.pending += text
        if self.state == 'prefix':
            stripped = self.pending.lstrip()
            if len(stripped) < len('<think>') and '<think>'.startswith(stripped):
                return
            if stripped.startswith('<think>'):
                self.pending = stripped[len('<think>'):]
                self.state = 'thinking'
            else:
                self.state = 'answer'
                self._flush(self.on_content)
                return
        closing = '</think>'
        index = self.pending.find(closing)
        if index >= 0:
            if index:
                self.on_reasoning(self.pending[:index])
            tail = self.pending[index + len(closing):]
            self.pending = ''
            self.state = 'answer'
            if tail:
                self.on_content(tail)
            return
        # Keep only the suffix that could become the closing delimiter.
        keep = next((size for size in range(min(len(closing) - 1, len(self.pending)), 0, -1)
                     if closing.startswith(self.pending[-size:])), 0)
        ready = self.pending[:-keep] if keep else self.pending
        self.pending = self.pending[-keep:] if keep else ''
        if ready:
            self.on_reasoning(ready)

    def _flush(self, callback):
        pending, self.pending = self.pending, ''
        if pending:
            callback(pending)

    def finish(self):
        self._flush(self.on_reasoning if self.state == 'thinking' else self.on_content)
