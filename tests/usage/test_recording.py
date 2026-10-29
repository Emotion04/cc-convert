# SPDX-License-Identifier: Apache-2.0
"""Normalization of the three upstream usage shapes (docs/用量统计设计.md §2)."""

import pytest

from cc_convert.usage.recording import NormalizedUsage, normalize_usage, prompt_class


class TestPromptClass:
    @pytest.mark.parametrize(
        ("tokens", "expected"),
        [
            (0, "0-32k"),
            (31_999, "0-32k"),
            (32_000, "32-128k"),
            (127_999, "32-128k"),
            (128_000, "128-200k"),
            (199_999, "128-200k"),
            (200_000, "200-400k"),
            (399_999, "200-400k"),
            (400_000, "400k-1M"),
            (999_999, "400k-1M"),
            (1_000_000, ">=1M"),
        ],
    )
    def test_boundaries(self, tokens, expected):
        assert prompt_class(tokens) == expected


class TestAnthropicShape:
    def test_basic(self):
        usage = normalize_usage(
            {
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_read_input_tokens": 200,
                "cache_creation_input_tokens": 30,
            }
        )
        assert usage == NormalizedUsage(
            input_tokens=100,
            output_tokens=50,
            cache_read=200,
            cache_write=30,
            ttl_source="config",
        )

    def test_ttl_split_from_response(self):
        usage = normalize_usage(
            {
                "input_tokens": 10,
                "output_tokens": 5,
                "cache_creation": {
                    "ephemeral_5m_input_tokens": 40,
                    "ephemeral_1h_input_tokens": 60,
                },
            }
        )
        assert usage.cache_write == 40
        assert usage.cache_write_1h == 60
        assert usage.ttl_source == "response"

    def test_input_excludes_cache(self):
        # Anthropic semantics: input_tokens already excludes cached tokens,
        # so nothing may be subtracted.
        usage = normalize_usage(
            {"input_tokens": 100, "output_tokens": 1, "cache_read_input_tokens": 500}
        )
        assert usage.input_tokens == 100
        assert usage.cache_read == 500


class TestChatShape:
    def test_openai_semantics(self):
        # prompt_tokens INCLUDES cached reads in the OpenAI shape.
        usage = normalize_usage(
            {
                "prompt_tokens": 1000,
                "completion_tokens": 200,
                "prompt_tokens_details": {"cached_tokens": 800},
                "completion_tokens_details": {"reasoning_tokens": 120},
            }
        )
        assert usage.input_tokens == 200
        assert usage.output_tokens == 200
        assert usage.cache_read == 800
        assert usage.reasoning_tokens == 120

    def test_cache_write_subtracted_from_prompt(self):
        usage = normalize_usage(
            {
                "prompt_tokens": 1000,
                "completion_tokens": 10,
                "cache_creation_input_tokens": 300,
                "prompt_tokens_details": {"cached_tokens": 100},
            }
        )
        assert usage.input_tokens == 600
        assert usage.cache_read == 100
        assert usage.cache_write == 300

    def test_vllm_cache_fields(self):
        # vLLM relays expose anthropic-ish fields on the chat shape.
        usage = normalize_usage(
            {
                "prompt_tokens": 500,
                "completion_tokens": 20,
                "cache_read_input_tokens": 400,
            }
        )
        assert usage.cache_read == 400
        assert usage.input_tokens == 100

    def test_anthropic_style_relay_without_prompt_tokens(self):
        # Some relays put anthropic semantics on the chat endpoint: only
        # input_tokens (already excluding cache), no prompt_tokens.
        usage = normalize_usage({"input_tokens": 100, "completion_tokens": 5})
        assert usage.input_tokens == 100
        assert usage.output_tokens == 5


class TestResponsesShape:
    def test_openai_responses_semantics(self):
        # input_tokens INCLUDES cached reads in the Responses shape.
        usage = normalize_usage(
            {
                "input_tokens": 1000,
                "output_tokens": 100,
                "input_tokens_details": {"cached_tokens": 700},
                "output_tokens_details": {"reasoning_tokens": 30},
            }
        )
        assert usage.input_tokens == 300
        assert usage.cache_read == 700
        assert usage.reasoning_tokens == 30

    def test_cache_write_tokens(self):
        usage = normalize_usage(
            {
                "input_tokens": 1000,
                "output_tokens": 10,
                "input_tokens_details": {"cached_tokens": 100},
                "cache_write_tokens": 200,
            }
        )
        assert usage.input_tokens == 700
        assert usage.cache_write == 200


class TestUnusable:
    @pytest.mark.parametrize(
        "payload", [None, {}, "nonsense", {"foo": 1}, {"prompt_tokens": 0, "completion_tokens": 0}]
    )
    def test_returns_none(self, payload):
        assert normalize_usage(payload) is None

    def test_zero_but_reported_still_counts(self):
        # A usage dict that parses with nonzero content is "reported" even
        # when some fields are zero.
        assert normalize_usage({"prompt_tokens": 1, "completion_tokens": 0}) is not None
        assert normalize_usage({"input_tokens": 1, "output_tokens": 0}) is not None

    def test_all_zero_counts_as_unusable(self):
        assert normalize_usage({"input_tokens": 0, "output_tokens": 0}) is None
