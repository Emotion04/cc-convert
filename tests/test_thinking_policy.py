# SPDX-License-Identifier: Apache-2.0
"""Tests for how much previous-turn reasoning is sent back to the upstream."""

import pytest

from cc_convert import thinking_policy as tp
from cc_convert.converter import convert_anthropic_to_openai


@pytest.fixture(autouse=True)
def _context():
    tp.configure(
        instance_id="inst_test",
        base_url="https://api.example.com/v1",
        model="m",
        upstream_format="chat_completions",
    )
    tp.reset_learned()
    tp.configure(
        instance_id="inst_test",
        base_url="https://api.example.com/v1",
        model="m",
        upstream_format="chat_completions",
    )
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
    def test_manual_modes_win_without_correction(self, mode):
        assert tp.resolve_policy(mode, "deepseek-r1") == mode

    def test_auto_correct_overrides_manual_mode(self):
        tp.note_upstream_rejection(
            400, "reasoning_content must be passed back", "m"
        )
        # Default is auto_correct=True, so the correction wins over "drop".
        assert tp.resolve_policy("drop", "m") == "keep"

    def test_force_manual_ignores_correction(self):
        tp.configure(
            instance_id="inst_test",
            base_url="https://api.example.com/v1",
            model="m",
            upstream_format="chat_completions",
            force_manual=True,
        )
        tp.note_upstream_rejection(
            400, "reasoning_content must be passed back", "m"
        )
        assert tp.resolve_policy("drop", "m") == "drop"

    def test_auto_correct_disabled_only_records_nothing(self):
        tp.configure(
            instance_id="inst_test",
            base_url="https://api.example.com/v1",
            model="m",
            upstream_format="chat_completions",
            auto_correct=False,
        )
        assert (
            tp.note_upstream_rejection(
                400, "reasoning_content must be passed back", "m"
            )
            is None
        )
        assert tp.resolve_policy("drop", "m") == "drop"

    def test_rules_mode_falls_back_to_rules(self):
        assert tp.resolve_policy("rules", "deepseek-r1") == "drop"
        assert tp.resolve_policy("rules", "deepseek-v4.1") == "turn"


class TestLearning:
    def test_missing_reasoning_learns_keep(self):
        policy = tp.note_upstream_rejection(
            400,
            "thinking is enabled but reasoning_content is missing",
            "kimi-k2",
        )
        assert policy == "keep"
        assert tp.resolve_policy("upstream", "kimi-k2") == "keep"

    def test_forbidden_reasoning_learns_drop(self):
        policy = tp.note_upstream_rejection(
            400, "reasoning_content is not allowed in this request", "r1"
        )
        assert policy == "drop"
        assert tp.resolve_policy("upstream", "r1") == "drop"

    def test_unrelated_error_is_ignored(self):
        assert tp.note_upstream_rejection(400, "invalid model", "m") is None
        assert tp.note_upstream_rejection(500, "reasoning failed", "m") is None

    def test_correction_is_persisted_with_fingerprint(self):
        tp.note_upstream_rejection(
            400, "reasoning_content must be passed back", "m"
        )
        stored = tp.load_correction()
        assert stored is not None
        assert stored["policy"] == "keep"
        assert stored["fingerprint"] == tp.fingerprint()

    def test_changed_upstream_config_invalidates_correction(self):
        tp.note_upstream_rejection(
            400, "reasoning_content must be passed back", "m"
        )
        # The user edits the base URL: same instance, different upstream.
        tp.configure(
            instance_id="inst_test",
            base_url="https://other.example.com/v1",
            model="m",
            upstream_format="chat_completions",
        )
        assert tp.load_correction() is None
        assert tp.resolve_policy("upstream", "deepseek-r1") == "drop"


def _multi_turn():
    """Two finished turns and a new user input — all prior reasoning is old."""
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


def _tool_loop():
    """One user turn with two tool-call steps (tool results are user messages)."""
    return [
        {"role": "user", "content": "do it"},
        {
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": "t1"},
                {
                    "type": "tool_use",
                    "id": "call_1",
                    "name": "bash",
                    "input": {"cmd": "ls"},
                },
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "call_1", "content": "ok"}
            ],
        },
        {
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": "t2"},
                {
                    "type": "tool_use",
                    "id": "call_2",
                    "name": "bash",
                    "input": {"cmd": "pwd"},
                },
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "call_2", "content": "ok"}
            ],
        },
        {
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": "t3"},
                {"type": "text", "text": "done"},
            ],
        },
    ]


def _replayed(params):
    return [
        m.get("reasoning_content")
        for m in params["messages"]
        if m.get("reasoning_content")
    ]


class TestConverterHistory:
    def _convert(self, mode, messages, model="some-model"):
        return convert_anthropic_to_openai(
            {"model": model, "max_tokens": 16, "messages": messages},
            thinking_history=mode,
        )

    def test_turn_drops_previous_conversation_turns(self):
        assert _replayed(self._convert("turn", _multi_turn())) == []

    def test_turn_keeps_every_assistant_in_the_current_tool_loop(self):
        # DeepSeek V4 / Kimi / GLM need reasoning on each tool-call message,
        # not just the last one.
        assert _replayed(self._convert("turn", _tool_loop())) == ["t1", "t2", "t3"]

    def test_keep_replays_everything(self):
        assert _replayed(self._convert("keep", _multi_turn())) == [
            "first-thought",
            "second-thought",
        ]

    def test_drop_replays_nothing(self):
        assert _replayed(self._convert("drop", _tool_loop())) == []

    def test_rules_drop_for_r1(self):
        assert _replayed(self._convert("rules", _multi_turn(), "deepseek-r1")) == []

    def test_rules_turn_for_v4(self):
        assert _replayed(self._convert("rules", _tool_loop(), "deepseek-v4.1")) == [
            "t1",
            "t2",
            "t3",
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


class TestProbe:
    """probe_upstream makes at most two max_tokens=1 requests."""

    @staticmethod
    def _client(responses):
        class Fake:
            def __init__(self):
                self.calls = []

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def post(self, url, headers=None, json=None):
                from types import SimpleNamespace

                self.calls.append(json)
                item = responses.pop(0)
                if isinstance(item, Exception):
                    raise item
                return SimpleNamespace(status_code=item[0], text=item[1])

        return Fake()

    def _run(self, responses, model="m"):
        import asyncio
        from unittest.mock import patch

        client = self._client(responses)
        with patch("httpx.AsyncClient", return_value=client):
            result = asyncio.run(
                tp.probe_upstream("https://api.example.com/v1", "k", model)
            )
        return result, client

    def test_rejects_reasoning_means_drop(self):
        result, _ = self._run([(400, "reasoning_content is not allowed")])
        assert result["policy"] == "drop"

    def test_tool_loop_requires_reasoning_means_turn(self):
        result, client = self._run(
            [(200, "ok"), (400, "reasoning_content must be passed back")]
        )
        assert result["policy"] == "turn"
        assert len(client.calls) == 2

    def test_no_signal_defaults_to_turn(self):
        result, _ = self._run([(200, "ok"), (200, "ok")])
        assert result["policy"] == "turn"

    def test_connection_error_defaults_to_turn(self):
        import httpx

        result, _ = self._run([httpx.ConnectError("")])
        assert result["policy"] == "turn"
        assert "无法连接" in result["evidence"]

    def test_missing_model_skips_probing(self):
        import asyncio
        from unittest.mock import patch

        client = self._client([])
        with patch("httpx.AsyncClient", return_value=client):
            result = asyncio.run(
                tp.probe_upstream("https://api.example.com/v1", "k", "")
            )
        assert result["policy"] == "turn"
        assert client.calls == []
