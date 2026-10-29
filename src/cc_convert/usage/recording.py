# SPDX-License-Identifier: Apache-2.0
"""Normalize the three upstream usage shapes into one accounting record.

Three wire shapes reach this proxy, and they disagree about what
``input`` means:

- **Anthropic** (``/v1/messages`` upstreams): ``input_tokens`` *excludes*
  cached tokens; cache reads/writes are separate fields.
- **OpenAI chat** (``/v1/chat/completions``): ``prompt_tokens`` *includes*
  cached tokens; cache reads hide in ``prompt_tokens_details.cached_tokens``.
- **OpenAI Responses** (``/v1/responses``): ``input_tokens`` *includes*
  cached tokens (details in ``input_tokens_details``).

Everything here is a pure function: no IO, no clock, no settings. Callers
hand in whatever ``usage`` dict the upstream produced and get a
:class:`NormalizedUsage` back — or ``None`` when nothing usable was
reported, which the ledger turns into an ``unreported`` count instead of a
silent zero (see docs/用量统计设计.md §2).
"""

from dataclasses import dataclass

# (min_prompt_tokens, label), ascending. A prompt belongs to the last tier
# whose minimum it has reached, so 31999 → "0-32k", 32000 → "32-128k" and
# 200000 → "200-400k" — matching how vendors count tier boundaries
# ("达到 min_prompt 后").
PROMPT_CLASS_BOUNDS: tuple[tuple[int, str], ...] = (
    (0, "0-32k"),
    (32_000, "32-128k"),
    (128_000, "128-200k"),
    (200_000, "200-400k"),
    (400_000, "400k-1M"),
    (1_000_000, ">=1M"),
)


def prompt_class(total_prompt_tokens: int) -> str:
    """Classify the prompt size (input + cache read + cache write) into a bucket."""
    label = PROMPT_CLASS_BOUNDS[0][1]
    for min_prompt, name in PROMPT_CLASS_BOUNDS:
        if total_prompt_tokens >= min_prompt:
            label = name
    return label


@dataclass
class NormalizedUsage:
    """One upstream usage report in unified form."""

    input_tokens: int = 0  # excludes cache reads and writes
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cache_read: int = 0
    cache_write: int = 0  # 5-minute TTL (or unknown TTL)
    cache_write_1h: int = 0  # 1-hour TTL, only when the upstream splits it
    # Where the TTL classification came from: "response" when the upstream
    # split 5m/1h itself, "config" when we assumed the instance default.
    ttl_source: str = "config"

    @property
    def total_prompt(self) -> int:
        return self.input_tokens + self.cache_read + self.cache_write + self.cache_write_1h


def _as_int(value) -> int:
    """Coerce a JSON number to int, treating None/bool/str as 0."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return int(value)


def normalize_usage(usage) -> NormalizedUsage | None:
    """Normalize any upstream usage shape. ``None`` when nothing usable.

    A dict that parses but carries all-zero counts still counts as
    *reported* — the upstream answered, it just had nothing to report.
    """
    if not isinstance(usage, dict) or not usage:
        return None
    if "prompt_tokens" in usage or "completion_tokens" in usage:
        return _from_chat(usage)
    if "input_tokens" in usage:
        return _from_input_tokens(usage)
    return None


def _split_cache_write_1h(usage: dict, result: NormalizedUsage) -> None:
    """Fill in the 5m/1h split from an Anthropic-style ``cache_creation`` block.

    Only Anthropic-native upstreams (and relays that mirror them) provide
    this; when absent the whole cache_write stays 5m/unknown with
    ``ttl_source="config"``.
    """
    creation = usage.get("cache_creation")
    if not isinstance(creation, dict):
        return
    write_5m = _as_int(creation.get("ephemeral_5m_input_tokens"))
    write_1h = _as_int(creation.get("ephemeral_1h_input_tokens"))
    if write_5m or write_1h:
        result.cache_write = write_5m
        result.cache_write_1h = write_1h
        result.ttl_source = "response"


def _from_chat(usage: dict) -> NormalizedUsage | None:
    """OpenAI chat-completions shape: ``prompt_tokens`` includes cache reads."""
    details = usage.get("prompt_tokens_details") or {}
    completion_details = usage.get("completion_tokens_details") or {}

    cache_read = _as_int(details.get("cached_tokens")) or _as_int(
        usage.get("cache_read_input_tokens")
    )
    cache_write = _as_int(usage.get("cache_creation_input_tokens"))
    output = _as_int(usage.get("completion_tokens"))
    reasoning = _as_int(completion_details.get("reasoning_tokens"))

    if "prompt_tokens" in usage:
        prompt = _as_int(usage.get("prompt_tokens"))
        input_tokens = max(prompt - cache_read - cache_write, 0)
    else:
        # Anthropic-style relay that skipped prompt_tokens: input_tokens
        # already excludes cache, so it must not be subtracted again.
        input_tokens = _as_int(usage.get("input_tokens"))

    if not any((input_tokens, output, cache_read, cache_write)):
        return None

    result = NormalizedUsage(
        input_tokens=input_tokens,
        output_tokens=output,
        reasoning_tokens=reasoning,
        cache_read=cache_read,
        cache_write=cache_write,
    )
    _split_cache_write_1h(usage, result)
    return result


def _from_input_tokens(usage: dict) -> NormalizedUsage | None:
    """Anthropic or Responses shape — both key on ``input_tokens``.

    The two disagree on whether cache is included, so the shape is told
    apart by its fields: Anthropic carries ``cache_read_input_tokens`` /
    ``cache_creation_input_tokens``, Responses carries
    ``input_tokens_details`` / ``output_tokens_details``.
    """
    anthropic_style = any(
        key in usage
        for key in ("cache_read_input_tokens", "cache_creation_input_tokens", "cache_creation")
    )
    details = usage.get("input_tokens_details") or {}
    output_details = usage.get("output_tokens_details") or {}

    cache_read = _as_int(usage.get("cache_read_input_tokens")) or _as_int(
        details.get("cached_tokens")
    )
    cache_write = _as_int(usage.get("cache_creation_input_tokens")) or _as_int(
        usage.get("cache_write_tokens") or details.get("cache_write_tokens")
    )
    output = _as_int(usage.get("output_tokens"))
    reasoning = _as_int(output_details.get("reasoning_tokens"))

    input_total = _as_int(usage.get("input_tokens"))
    if anthropic_style:
        input_tokens = input_total  # already excludes cache
    else:
        input_tokens = max(input_total - cache_read - cache_write, 0)

    if not any((input_total, output, cache_read, cache_write)):
        return None

    result = NormalizedUsage(
        input_tokens=input_tokens,
        output_tokens=output,
        reasoning_tokens=reasoning,
        cache_read=cache_read,
        cache_write=cache_write,
    )
    _split_cache_write_1h(usage, result)
    return result
