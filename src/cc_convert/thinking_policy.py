# SPDX-License-Identifier: Apache-2.0
"""How much previous-turn reasoning to send back to the upstream.

Providers disagree, and some of them fail hard when the guess is wrong:

* Legacy DeepSeek R1 rejects ``reasoning_content`` in the request entirely.
* DeepSeek V4 / Kimi K2.x / GLM-5 require it on the assistant tool-call turn,
  otherwise the API returns 400 ("reasoning_content ... must be passed back").
* Anthropic requires thinking blocks (with a valid signature) on the final
  assistant message of a tool-use turn; earlier turns are optional and are
  stripped server-side anyway.
* OpenAI reasoning items are only needed inside the current tool loop.

Keeping reasoning for the **last assistant message only** ("turn") satisfies
the strict providers without resending the whole conversation's reasoning,
which is the behaviour of most gateways.

Modes:
    upstream  - built-in rules, plus learning from reasoning-related 400s
    rules     - built-in rules only
    turn      - last assistant message only
    keep      - every assistant message
    drop      - never send reasoning back
"""

from typing import Literal

ThinkingHistoryMode = Literal["upstream", "rules", "turn", "keep", "drop"]
EffectivePolicy = Literal["turn", "keep", "drop"]

MODES: tuple[str, ...] = ("upstream", "rules", "turn", "keep", "drop")
DEFAULT_MODE: ThinkingHistoryMode = "upstream"

#: (substring matched against the resolved model name, policy, note)
RULES: tuple[tuple[str, EffectivePolicy, str], ...] = (
    (
        "deepseek-r1",
        "drop",
        "R1 协议不接受回传 reasoning_content，回传会 400",
    ),
    (
        "deepseek-reasoner",
        "drop",
        "旧版 reasoner 同 R1：上下文不应包含上一轮推理",
    ),
    (
        "deepseek-v4",
        "turn",
        "V4 thinking 在工具调用轮要求回传 reasoning_content",
    ),
    ("deepseek", "turn", "新版本 thinking 模式需要工具调用轮回传"),
    ("kimi", "turn", "K2.x 工具循环缺少 reasoning_content 会 400"),
    ("moonshot", "turn", "Kimi 系列工具循环需要推理内容"),
    ("glm", "turn", "GLM-5 工具循环要求保留推理"),
    ("qwen", "turn", "Qwen 建议保留同一轮工具链的推理"),
    ("qwq", "drop", "QwQ 类推理模型不应回传历史推理"),
    ("claude", "turn", "工具轮需带签名回传，跨轮可省略"),
    ("gpt-5", "turn", "OpenAI 推理项仅在当前工具循环内需要"),
    ("o1", "turn", "OpenAI 推理项仅在当前工具循环内需要"),
    ("o3", "turn", "OpenAI 推理项仅在当前工具循环内需要"),
    ("o4", "turn", "OpenAI 推理项仅在当前工具循环内需要"),
    ("gemini", "turn", "Gemini 的 thought signature 仅在函数调用轮需要"),
)

DEFAULT_RULE_POLICY: EffectivePolicy = "turn"

#: Set by :func:`note_upstream_rejection` when an upstream tells us, via a 400,
#: that our reasoning shape was wrong. Keyed by resolved model name.
_LEARNED: dict[str, EffectivePolicy] = {}

_REQUIRED_HINTS = (
    "must be passed back",
    "must be passed",
    "reasoning_content is missing",
    "missing reasoning",
)
_FORBIDDEN_HINTS = (
    "not allowed",
    "should not",
    "invalid",
    "unexpected",
    "unsupported",
)


def rule_for_model(model: str | None) -> EffectivePolicy:
    """Return the built-in policy for *model* (case-insensitive substring match)."""
    normalized = (model or "").lower()
    for needle, policy, _note in RULES:
        if needle in normalized:
            return policy
    return DEFAULT_RULE_POLICY


def resolve_policy(mode: str | None, model: str | None) -> EffectivePolicy:
    """Resolve a configured mode into an effective policy."""
    mode = mode or DEFAULT_MODE
    if mode in ("keep", "drop", "turn"):
        return mode  # type: ignore[return-value]

    if mode == "upstream":
        learned = _LEARNED.get((model or "").lower())
        if learned:
            return learned
    return rule_for_model(model)


def note_upstream_rejection(model: str | None, status: int, body: str) -> EffectivePolicy | None:
    """Learn from a 400 that rejected our reasoning shape.

    Only called in ``upstream`` mode. Returns the learned policy, or ``None``
    when the error is unrelated to reasoning.
    """
    if status != 400 or not body:
        return None

    lowered = body.lower()
    if "reasoning" not in lowered and "thinking" not in lowered:
        return None

    if any(hint in lowered for hint in _REQUIRED_HINTS):
        policy: EffectivePolicy = "keep"
    elif any(hint in lowered for hint in _FORBIDDEN_HINTS):
        policy = "drop"
    else:
        return None

    _LEARNED[(model or "").lower()] = policy
    return policy


def learned_policy(model: str | None) -> EffectivePolicy | None:
    """Return the learned policy for *model*, if any (for tests/introspection)."""
    return _LEARNED.get((model or "").lower())


def reset_learned() -> None:
    """Clear learned overrides (tests)."""
    _LEARNED.clear()


#: Shown in the UI's info panel. Summarised from each vendor's documentation.
PROVIDER_NOTES: tuple[dict[str, str], ...] = (
    {
        "provider": "Anthropic Claude",
        "policy": "turn",
        "summary": "工具调用轮必须原样回传 thinking（含签名）；跨用户轮次可省略，服务端会自动剥离早期 thinking。",
        "source": "https://platform.claude.com/docs/en/build-with-claude/thinking-tool-workflows",
    },
    {
        "provider": "DeepSeek R1 / 旧版 reasoner",
        "policy": "drop",
        "summary": "上一轮的 reasoning_content 不应拼进下一轮上下文；回传会触发 400。",
        "source": "https://api-docs.deepseek.com/guides/reasoning_model",
    },
    {
        "provider": "DeepSeek V4 / 新版 thinking",
        "policy": "turn",
        "summary": "工具调用轮必须回传 reasoning_content，否则报“must be passed back”。",
        "source": "https://github.com/modelscope/evalscope/pull/1396",
    },
    {
        "provider": "Kimi K2.x / Moonshot",
        "policy": "turn",
        "summary": "工具循环缺少 reasoning_content 会 400；K2.7 要求真实文本，早期版本接受空串。",
        "source": "https://github.com/tbosancheros39/opencode-thinking-fix",
    },
    {
        "provider": "GLM-5 / 智谱",
        "policy": "turn",
        "summary": "工具循环要求保留推理；GLM 另有 thinking.clear_thinking 控制历史留存。",
        "source": "https://docs.infini-ai.com/gen-studio/api/text-generation/tutorial-reasoning/",
    },
    {
        "provider": "OpenAI GPT-5 / o 系列",
        "policy": "turn",
        "summary": "Responses API 的 reasoning 项仅在当前工具循环内需要，跨轮次无需回传。",
        "source": "https://platform.openai.com/docs/guides/reasoning",
    },
    {
        "provider": "Google Gemini",
        "policy": "turn",
        "summary": "3.x 的函数调用轮要求回传 thought signature，缺失会 400；2.5 会降级。",
        "source": "https://ai.google.dev/gemini-api/docs/generate-content/thought-signatures",
    },
)
