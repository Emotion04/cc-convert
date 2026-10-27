# SPDX-License-Identifier: Apache-2.0
"""Tests for mapping upstream prompt-cache usage into Anthropic usage fields.

OpenAI-compatible providers report cache hits in different places, and their
token accounting differs from Anthropic's:

* OpenAI / vLLM / SGLang — ``prompt_tokens_details.cached_tokens``
* DeepSeek               — ``prompt_cache_hit_tokens``
* some vLLM builds       — ``cache_read_input_tokens``

OpenAI counts cached tokens *inside* ``prompt_tokens``; Anthropic reports
``input_tokens``, ``cache_read_input_tokens`` and
``cache_creation_input_tokens`` as separate, additive fields.
"""

from types import SimpleNamespace

from cc_convert.streaming.handler import _extract_cache_tokens
from cc_convert.utils.tokens import (
    anthropic_input_tokens,
    extract_cache_creation_tokens,
    extract_cache_read_tokens,
)


class TestExtractCacheRead:
    def test_openai_style(self):
        usage = {"prompt_tokens": 1000, "prompt_tokens_details": {"cached_tokens": 768}}
        assert extract_cache_read_tokens(usage) == 768

    def test_deepseek_style(self):
        usage = {"prompt_tokens": 1000, "prompt_cache_hit_tokens": 896}
        assert extract_cache_read_tokens(usage) == 896

    def test_vllm_direct_field(self):
        assert extract_cache_read_tokens({"cache_read_input_tokens": 512}) == 512

    def test_sdk_usage_object(self):
        usage = SimpleNamespace(
            prompt_tokens=1000,
            prompt_tokens_details=SimpleNamespace(cached_tokens=640),
        )
        assert extract_cache_read_tokens(usage) == 640

    def test_no_cache(self):
        assert extract_cache_read_tokens({"prompt_tokens": 10}) is None
        assert extract_cache_read_tokens({"prompt_tokens_details": {"cached_tokens": 0}}) is None
        assert extract_cache_read_tokens(None) is None


class TestCacheCreation:
    def test_present(self):
        assert extract_cache_creation_tokens({"cache_creation_input_tokens": 128}) == 128

    def test_absent_or_zero(self):
        assert extract_cache_creation_tokens({"cached": 0}) is None
        assert extract_cache_creation_tokens(None) is None


class TestAnthropicInputTokens:
    def test_subtracts_cached_tokens(self):
        """Anthropic input_tokens must exclude cached tokens."""
        usage = {"prompt_tokens": 1000, "prompt_cache_hit_tokens": 800}
        assert anthropic_input_tokens(usage) == 200

    def test_subtracts_both_read_and_creation(self):
        usage = {
            "prompt_tokens": 1000,
            "prompt_tokens_details": {"cached_tokens": 600},
            "cache_creation_input_tokens": 100,
        }
        assert anthropic_input_tokens(usage) == 300

    def test_without_cache_returns_prompt_tokens(self):
        assert anthropic_input_tokens({"prompt_tokens": 500}) == 500

    def test_clamps_at_zero(self):
        """A provider reporting more cache than prompt tokens must not go negative."""
        usage = {"prompt_tokens": 100, "prompt_cache_hit_tokens": 500}
        assert anthropic_input_tokens(usage) == 0

    def test_falls_back_when_prompt_tokens_missing(self):
        assert anthropic_input_tokens({}, fallback=42) == 42
        assert anthropic_input_tokens(None, fallback=42) == 42


class TestStreamingHelper:
    def test_uses_deepseek_field(self):
        usage = {"prompt_tokens": 1000, "prompt_cache_hit_tokens": 896}
        assert _extract_cache_tokens(usage) == (896, None)

    def test_uses_openai_details(self):
        usage = {"prompt_tokens": 1000, "prompt_tokens_details": {"cached_tokens": 768}}
        assert _extract_cache_tokens(usage) == (768, None)

    def test_non_dict_usage(self):
        assert _extract_cache_tokens(None) == (None, None)
