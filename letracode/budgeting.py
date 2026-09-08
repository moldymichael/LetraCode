"""Request accounting shared by packing and the inference boundary."""
from dataclasses import dataclass
import json


@dataclass(frozen=True)
class RequestUsage:
    prompt_tokens: int
    reply_tokens: int
    safety_tokens: int
    method: str

    @property
    def total_tokens(self):
        return self.prompt_tokens + self.reply_tokens + self.safety_tokens


def fallback_usage(messages, tools, max_tokens, thinking=False):
    """Conservative estimate, not a claim about an unavailable tokenizer.

    Count every serialized UTF-8 byte as a token, plus a template allowance.
    This intentionally leaves less usable context than normal tokenization.
    Arbitrary model templates can still exceed the allowance; the server's
    context rejection remains a bounded, recoverable failure.
    """
    body = {'messages': messages, 'tools': tools or [],
            'chat_template_kwargs': {'enable_thinking': bool(thinking)},
            'parallel_tool_calls': False}
    size = len(json.dumps(body, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8'))
    return RequestUsage(size + 24 * len(messages), max_tokens, 512,
                        'conservative UTF-8 estimate; runtime template/tokenizer unavailable')
