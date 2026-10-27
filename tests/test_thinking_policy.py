# SPDX-License-Identifier: Apache-2.0
"""Tests for how much previous-turn reasoning is sent back to the upstream."""

import pytest

from cc_convert import thinking_policy as tp
from cc_convert.converter import convert_anthropic_to_openai


@pytest.fixture(autouse=True)
def _clear_learned():
    tp.reset_learned()
    yield
    tp.reset_learned()


class TestRules:
    @pytest.mark.parametrize(
        "model,expected",
        [
            ("deepseek-r1", "drop"),
            ("deepseek-reasoner", "drop"),
            ("deepseek-v4.1-flash", "turn"),
            ("deepseek-chat", "turn"),
            ("kimi-k2.5", "turn"),
            ("moonshot-v1", "turn"),
            ("glm-5.2", "turn"),
            ("qwen3-max", "turn"),
            ("qwq-32b", "drop"),
            ("claude-opus-5", "turn"),
            ("gpt-5.6-sol", "turn"),
            ("gemini-3-pro", "turn"),
            ("some-unknown-model", "turn"),
        ],
    )
    def test_rule_for_model(self, model, expected):
        assert tp.rule_for_model(model) == expected

    def test_rule_is_case_insensitive(self):
        assert tp.rule_for_model("DeepSeek-R1") == "drop"


class TestResolvePolicy:
    @pytest.mark.parametrize("mode", ["keep", "drop", "turn"])
    def test_manual_modes_win(self, mode):
        assert tp.resolve_policy(mode, "deepseek-r1") == mode

    def test_rules_mode_ignores_learned(self):
        tp.note_upstream_rejection("m", 400, "reasoning_content must be passed back")
        assert tp.resolve_policy("rules", "m") == "turn"

    def test_upstream_mode_uses_learned_override(self):
        tp.note_upstream_rejection("m", 400, "reasoning_content must be passed back")
        assert tp.resolve_policy("upstream", "m") == "keep"

    def test_upstream_mode_falls_back_to_rules(self):
        assert tp.resolve_policy("upstream", "deepseek-r1") == "drop"


class TestLearning:
    def test_missing_reasoning_learns_keep(self):
        policy = tp.note_upstream_rejection(
            "kimi-k2", 400, "thinking is enabled but reasoning_content is missing"
        )
        assert policy == "keep"

    def test_forbidden_reasoning_learns_drop(self):
        policy = tp.note_upstream_rejection(
            "r1", 400, "reasoning_content is not allowed in this request"
        )
        assert policy == "drop"

    def test_unrelated_error_is_ignored(self):
        assert tp.note_upstream_rejection("m", 400, "invalid model") is None
        assert tp.note_upstream_rejection("m", 500, "reasoning failed") is None
        assert tp.learned_policy("m") is None


def _conversation():
    return [
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": "first-thought"},
                {"type": "text", "text": "a"},
            ],
        },
        {"role": "user", "content": "again"},
        {
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": "second-thought"},
                {"type": "text", "text": "b"},
            ],
        },
        {"role": "user", "content": "final"},
    ]


def _replayed(params):
    return [
        m.get("reasoning_content")
        for m in params["messages"]
        if m.get("reasoning_content")
    ]


class TestConverterHistory:
    def _convert(self, mode, model="some-model"):
        return convert_anthropic_to_openai(
            {"model": model, "max_tokens": 16, "messages": _conversation()},
            thinking_history=mode,
        )

    def test_turn_keeps_only_last_assistant_thinking(self):
        assert _replayed(self._convert("turn")) == ["second-thought"]

    def test_keep_replays_everything(self):
        assert _replayed(self._convert("keep")) == [
            "first-thought",
            "second-thought",
        ]

    def test_drop_replays_nothing(self):
        assert _replayed(self._convert("drop")) == []

    def test_rules_drop_for_r1(self):
        assert _replayed(self._convert("rules", model="deepseek-r1")) == []

    def test_rules_turn_for_v4(self):
        assert _replayed(self._convert("rules", model="deepseek-v4.1")) == [
            "second-thought"
        ]


class TestApiValidation:
    def test_rejects_unknown_mode(self):
        from cc_convert.web.routes import _validate_instance_body

        errors = _validate_instance_body(
            {"thinking_history": "bogus"}, require_key=False
        )
        assert any("thinking_history" in e for e in errors)

    @pytest.mark.parametrize("mode", list(tp.MODES))
    def test_accepts_known_modes(self, mode):
        from cc_convert.web.routes import _validate_instance_body

        assert _validate_instance_body(
            {"thinking_history": mode}, require_key=False
        ) == []


def test_worker_settings_carry_the_mode():
    from cc_convert.profile_store import (
        InstanceProfile,
        ManagerConfig,
        ProfileStore,
        build_worker_settings,
    )

    store = ProfileStore(manager=ManagerConfig(port=18400), instances=[])
    inst = InstanceProfile(
        name="x",
        port=18401,
        upstream_base_url="https://api.example.com/v1",
        thinking_history="drop",
    )

    assert build_worker_settings(store, inst)["thinking_history"] == "drop"
