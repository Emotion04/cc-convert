# SPDX-License-Identifier: Apache-2.0
"""Token-related utility functions."""

import json
import secrets
import string
from typing import Any


def _generate_server_tool_id() -> str:
    """Generate Anthropic-style server tool use ID (srvtoolu_...)."""
    # Generate 24 random alphanumeric characters
    chars = string.ascii_lowercase + string.digits
    random_part = "".join(secrets.choice(chars) for _ in range(24))
    return f"srvtoolu_{random_part}"


def _normalize_usage(usage: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(usage, dict):
        return usage
    allowed_keys = {
        "input_tokens",
        "output_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
        "server_tool_use",
    }
    normalized = {k: v for k, v in usage.items() if k in allowed_keys}
    return normalized or None


def _usage_get(usage: Any, name: str) -> Any:
    """Read a field from a usage dict or an OpenAI SDK usage object."""
    if usage is None:
        return None
    if isinstance(usage, dict):
        return usage.get(name)
    return getattr(usage, name, None)


def extract_cache_read_tokens(usage: Any) -> int | None:
    """Return the number of prompt tokens served from cache.

    OpenAI-compatible providers disagree about where they report this:

    * OpenAI / vLLM / SGLang: ``prompt_tokens_details.cached_tokens``
    * DeepSeek:               ``prompt_cache_hit_tokens``
    * some vLLM builds:       ``cache_read_input_tokens``

    The value is a *subset* of ``prompt_tokens``. Anthropic reports it as a
    separate field, so callers must subtract it — see
    :func:`anthropic_input_tokens`.
    """
    details = _usage_get(usage, "prompt_tokens_details")
    for value in (
        _usage_get(details, "cached_tokens"),
        _usage_get(usage, "prompt_cache_hit_tokens"),
        _usage_get(usage, "cache_read_input_tokens"),
    ):
        if isinstance(value, int) and value > 0:
            return value
    return None


def extract_cache_creation_tokens(usage: Any) -> int | None:
    """Return tokens written to the cache, when the provider reports them."""
    value = _usage_get(usage, "cache_creation_input_tokens")
    return value if isinstance(value, int) and value > 0 else None


def anthropic_input_tokens(usage: Any, fallback: int = 0) -> int:
    """Convert an OpenAI ``prompt_tokens`` into Anthropic ``input_tokens``.

    OpenAI counts cached tokens *inside* ``prompt_tokens``; Anthropic reports
    ``input_tokens``, ``cache_read_input_tokens`` and
    ``cache_creation_input_tokens`` as separate, additive fields. Passing the
    OpenAI number straight through double-counted every cached token.
    """
    prompt = _usage_get(usage, "prompt_tokens")
    if not isinstance(prompt, int):
        prompt = fallback
    cached = (extract_cache_read_tokens(usage) or 0) + (
        extract_cache_creation_tokens(usage) or 0
    )
    return max(0, prompt - cached)


def _count_tokens(text: str) -> int:
    try:
        import tiktoken  # type: ignore[import-not-found]

        encoding = tiktoken.get_encoding("cl100k_base")
        return len(encoding.encode(text))
    except Exception:
        return 0


def _chunk_text(text: str, chunk_size: int = 200) -> list[str]:
    if not text:
        return []
    return [text[i : i + chunk_size] for i in range(0, len(text), chunk_size)]


def _estimate_input_tokens(openai_params: dict[str, Any]) -> int:
    try:
        import tiktoken  # type: ignore[import-not-found]

        encoding = tiktoken.get_encoding("cl100k_base")
        total_tokens = 0

        system = openai_params.get("system")
        if isinstance(system, str):
            total_tokens += len(encoding.encode(system))

        messages = openai_params.get("messages", [])
        if isinstance(messages, list):
            for msg in messages:
                if not isinstance(msg, dict):
                    continue
                content = msg.get("content", "")
                if isinstance(content, str):
                    total_tokens += len(encoding.encode(content))
                elif isinstance(content, list):
                    for block in content:
                        if not isinstance(block, dict):
                            total_tokens += len(encoding.encode(str(block)))
                            continue
                        block_type = block.get("type")
                        if block_type == "text":
                            total_tokens += len(encoding.encode(block.get("text", "")))
                        elif block_type == "image_url":
                            total_tokens += 85

                tool_calls = msg.get("tool_calls")
                if isinstance(tool_calls, list) and tool_calls:
                    total_tokens += len(encoding.encode(json.dumps(tool_calls)))

        tools = openai_params.get("tools")
        if isinstance(tools, list) and tools:
            total_tokens += len(encoding.encode(json.dumps(tools)))

        tool_choice = openai_params.get("tool_choice")
        if tool_choice is not None:
            total_tokens += len(encoding.encode(json.dumps(tool_choice)))

        response_format = openai_params.get("response_format")
        if response_format is not None:
            total_tokens += len(encoding.encode(json.dumps(response_format)))

        return total_tokens
    except Exception:
        return 0
