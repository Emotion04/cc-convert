# SPDX-License-Identifier: Apache-2.0
"""How much previous-turn reasoning to send back to the upstream.

Providers disagree, and some fail hard when the guess is wrong:

* Legacy DeepSeek R1 rejects ``reasoning_content`` in the request entirely.
* DeepSeek V4 / Kimi K2.x / GLM-5 require it on every assistant tool-call
  message of the current turn, otherwise the API returns 400.
* Anthropic requires thinking blocks (with a valid signature) on the final
  assistant message of a tool-use turn; earlier turns are optional.
* OpenAI reasoning items are only needed inside the current tool loop.

"turn" therefore means the **current turn**: every assistant message after the
last real user input, including every step of a tool loop. This satisfies the
strict providers without resending the whole conversation's reasoning.

Modes:
    upstream  - built-in rules, plus correction learned from 400 responses
    rules     - built-in rules only
    turn      - the current turn only (whole tool loop)
    keep      - every assistant message
    drop      - never send reasoning back

Corrections are persisted per instance, keyed by a fingerprint of the upstream
configuration. If the user later changes the base URL, target model or upstream
format, the fingerprint no longer matches and the stale correction is ignored.
"""

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

ThinkingHistoryMode = Literal["upstream", "auto", "rules", "turn", "keep", "drop"]
EffectivePolicy = Literal["turn", "keep", "drop"]

MODES: tuple[str, ...] = ("upstream", "auto", "rules", "turn", "keep", "drop")
# "auto" is the default: it needs no probe request and still corrects itself
# from upstream 400s. "upstream" explicitly asks for a real probe at startup.
DEFAULT_MODE: ThinkingHistoryMode = "auto"

MANUAL_MODES = ("turn", "keep", "drop")

#: User-facing labels. The dashboard and the probe API both render these, so a
#: probe never prints a bare internal key ("turn") that the select does not show.
POLICY_LABELS: dict[str, str] = {
    "auto": "智能（auto）",
    "upstream": "遵循上游（upstream）",
    "rules": "严格规则（rules）",
    "turn": "仅当前轮（turn）",
    "keep": "全部回传（keep）",
    "drop": "全部丢弃（drop）",
}


def policy_label(policy: str | None) -> str:
    """Chinese label for a mode key; unknown keys pass through unchanged."""
    if not policy:
        return ""
    return POLICY_LABELS.get(policy, policy)


#: (substring matched against the resolved model name, policy, note)
RULES: tuple[tuple[str, EffectivePolicy, str], ...] = (
    ("deepseek-r1", "drop", "R1 协议不接受回传 reasoning_content，回传会 400"),
    ("deepseek-reasoner", "drop", "旧版 reasoner 同 R1：上下文不应包含上一轮推理"),
    ("deepseek-v4", "turn", "V4 thinking 在工具调用轮要求回传 reasoning_content"),
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


@dataclass
class PolicyContext:
    """Per-worker state used by resolution and correction."""

    instance_id: str = ""
    base_url: str = ""
    model: str = ""
    upstream_format: str = ""
    auto_correct: bool = True
    force_manual: bool = False
    #: Result of the startup probe, only used by the "upstream" mode.
    probe_result: EffectivePolicy | None = None


_CTX = PolicyContext()


def set_probe_result(policy: EffectivePolicy | None) -> None:
    """Record the result of the startup probe (``upstream`` mode only)."""
    _CTX.probe_result = policy

logger = logging.getLogger(__name__)


def configure(
    *,
    instance_id: str = "",
    base_url: str = "",
    model: str = "",
    upstream_format: str = "",
    auto_correct: bool = True,
    force_manual: bool = False,
) -> None:
    """Install the worker's context. Called once at worker startup."""
    global _CTX
    _CTX = PolicyContext(
        instance_id=instance_id,
        base_url=base_url,
        model=model,
        upstream_format=upstream_format,
        auto_correct=auto_correct,
        force_manual=force_manual,
    )


def context() -> PolicyContext:
    return _CTX


def fingerprint(ctx: PolicyContext | None = None) -> str:
    """Stable key for the upstream configuration.

    Deliberately excludes ``upstream_format``: the worker resolves ``"auto"``
    to a concrete format at startup, and a manual probe from the dashboard sees
    the value before that resolution. Keying on URL + target model keeps the
    two sides matching, and still invalidates when either is edited.
    """
    ctx = ctx or _CTX
    raw = f"{(ctx.base_url or '').rstrip('/')}|{ctx.model or ''}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _state_file(ctx: PolicyContext) -> Path | None:
    if not ctx.instance_id:
        return None
    from cc_convert.runtime_paths import get_instance_dir

    return get_instance_dir(ctx.instance_id) / "thinking_learning.json"


def save_correction_for(
    *,
    instance_id: str,
    base_url: str,
    model: str,
    upstream_format: str,
    policy: EffectivePolicy,
    reason: str,
) -> None:
    """Persist a probe result for an instance, from the manager process.

    The worker re-reads this file on every request (``load_correction``), so a
    manual probe from the dashboard takes effect without restarting it.
    """
    ctx = PolicyContext(
        instance_id=instance_id,
        base_url=base_url,
        model=model,
        upstream_format=upstream_format,
    )
    _save_correction(ctx, policy, reason)


def load_correction(ctx: PolicyContext | None = None) -> dict | None:
    """Return the persisted correction for this configuration, if still valid."""
    ctx = ctx or _CTX
    path = _state_file(ctx)
    if path is None or not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if data.get("fingerprint") != fingerprint(ctx):
        return None
    if data.get("policy") not in MANUAL_MODES:
        return None
    return data


def _save_correction(ctx: PolicyContext, policy: EffectivePolicy, reason: str) -> None:
    path = _state_file(ctx)
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "fingerprint": fingerprint(ctx),
                    "policy": policy,
                    "reason": reason,
                    "model": ctx.model,
                    "base_url": ctx.base_url,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except OSError:
        pass


def rule_for_model(model: str | None) -> EffectivePolicy:
    """Return the built-in policy for *model* (case-insensitive substring match)."""
    normalized = (model or "").lower()
    for needle, policy, _note in RULES:
        if needle in normalized:
            return policy
    return DEFAULT_RULE_POLICY


def resolve_policy(mode: str | None, model: str | None = None) -> EffectivePolicy:
    """Resolve the effective policy.

    Precedence: force-manual > learned correction (auto-correct) > manual mode
    > built-in rules.
    """
    ctx = _CTX
    mode = mode or DEFAULT_MODE

    if ctx.force_manual:
        if mode in MANUAL_MODES:
            return mode  # type: ignore[return-value]
        return rule_for_model(model or ctx.model)

    # "rules" is explicitly static: never let a learned correction change it.
    if mode == "rules":
        return rule_for_model(model or ctx.model)

    # "upstream" = the real probe performed at worker startup. A persisted
    # correction wins over it: that is either a manual UI probe or a learned
    # 400, i.e. a fresher observation than the startup one.
    if mode == "upstream":
        if ctx.auto_correct:
            correction = load_correction(ctx)
            if correction:
                return correction["policy"]  # type: ignore[return-value]
        if ctx.probe_result:
            return ctx.probe_result
        return rule_for_model(model or ctx.model)

    # "auto" = researched rules plus corrections learned from upstream 400s.
    if mode == "auto":
        if ctx.auto_correct:
            correction = load_correction(ctx)
            if correction:
                return correction["policy"]  # type: ignore[return-value]
        return rule_for_model(model or ctx.model)

    if mode in MANUAL_MODES:
        if ctx.auto_correct:
            correction = load_correction(ctx)
            if correction:
                return correction["policy"]  # type: ignore[return-value]
        return mode  # type: ignore[return-value]
    return rule_for_model(model or ctx.model)


def note_upstream_rejection(
    status: int, body: str, model: str | None = None
) -> EffectivePolicy | None:
    """Learn from a 400 that rejected our reasoning shape.

    Ignored when auto-correction is off or the user forced manual priority.
    The correction is persisted against the configuration fingerprint, so a
    later change of base URL / model / format invalidates it automatically.
    """
    ctx = _CTX
    if not ctx.auto_correct or ctx.force_manual:
        return None
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

    _save_correction(ctx, policy, body.strip()[:500])
    # Logged once per correction instead of alerting on every reply.
    logger.warning(
        "[cc-convert] upstream rejected the reasoning shape for %s; "
        "switching thinking_history to %s until the upstream config changes. "
        "Upstream said: %s",
        model or ctx.model,
        policy,
        body.strip()[:200],
    )
    return policy


#: Total budget for a probe, shared by both requests. The dashboard shows the
#: same number as a countdown, so the UI and the server cannot disagree. The
#: budget is deliberately generous for the first request: reasoning relays
#: routinely take 10–20 s for a "tiny" request because they inject their own
#: system prompt and gate the answer behind the model's reasoning phase
#: (measured on aiaimax: 3.8–49 s with ``max_tokens=1``).
PROBE_TIMEOUT = 20.0

#: The startup probe runs in the background and must not outlive its usefulness.
STARTUP_PROBE_TIMEOUT = PROBE_TIMEOUT


def _probe_result(policy: str, evidence: str, ok: bool) -> dict:
    return {
        "policy": policy,
        "label": policy_label(policy),
        "evidence": evidence,
        "ok": ok,
    }


async def probe_upstream(
    base_url: str,
    api_key: str = "",
    model: str = "",
    timeout: float = PROBE_TIMEOUT,
    upstream_format: str = "",
) -> dict:
    """Determine the reasoning-replay policy with at most two tiny requests.

    Two signals are used:

    1. Send an assistant message that carries ``reasoning_content``. Classic
       reasoners (R1) reject it — that means the policy must be ``drop``.
    2. Send an assistant tool-call message *without* ``reasoning_content``.
       Providers that require it answer 400 ("must be passed back") — that
       means the policy must be ``turn``.

    ``max_tokens`` is 1 for both probes. ``timeout`` is the budget for the
    **whole** probe, not per request: the dashboard counts down from the same
    number, so a probe can never outlive what the UI promised. The result
    carries ``ok=False`` when the upstream never produced a definite signal
    (unreachable, timed out, endpoint missing, or both probes accepted
    everything). Callers must not persist anything in that case: a failed probe
    says nothing about the upstream and must not overwrite the user's choice.

    ``upstream_format="responses"`` sends the translated Responses body to
    ``/responses``, because that is the endpoint the proxy will actually use —
    probing ``/chat/completions`` there would report a 404 and say nothing.
    """
    import asyncio
    import time

    import httpx

    base = (base_url or "").rstrip("/")
    if not base or not model:
        return _probe_result("turn", "缺少 Base URL 或目标模型，未探测", False)

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    responses_mode = (upstream_format or "") == "responses"
    endpoint = "/responses" if responses_mode else "/chat/completions"

    deadline = time.monotonic() + timeout

    class _BudgetExhausted(Exception):
        pass

    def _remaining() -> float:
        return deadline - time.monotonic()

    def _wire_body(body: dict) -> dict:
        if not responses_mode:
            return body
        from cc_convert.upstream_responses import chat_body_to_responses_body

        return chat_body_to_responses_body(body)

    async def _post(body: dict):
        remaining = _remaining()
        if remaining <= 0.2:
            raise _BudgetExhausted()
        # A dead host should fail fast; a slow model still gets the budget.
        client_timeout = httpx.Timeout(remaining, connect=min(10.0, remaining))
        wire_body = _wire_body(body)

        async def _send():
            async with httpx.AsyncClient(
                timeout=client_timeout, trust_env=False
            ) as client:
                return await client.post(
                    f"{base}{endpoint}", headers=headers, json=wire_body
                )

        return await asyncio.wait_for(_send(), timeout=remaining)

    def _unreachable(exc: BaseException) -> dict:
        timed_out = isinstance(
            exc, (httpx.TimeoutException, asyncio.TimeoutError, _BudgetExhausted)
        )
        if timed_out:
            return _probe_result(
                "turn",
                f"上游 {timeout:g} 秒内没有响应，未改动当前设置",
                False,
            )
        return _probe_result(
            "turn",
            f"无法连接上游（{type(exc).__name__}），未改动当前设置",
            False,
        )

    # Probe 1: does the provider reject reasoning_content outright?
    body_with_reasoning = {
        "model": model,
        "max_tokens": 1,
        "messages": [
            {"role": "user", "content": "x"},
            {
                "role": "assistant",
                "content": "y",
                "reasoning_content": "r",
            },
            {"role": "user", "content": "x"},
        ],
    }
    # Probe 2: does a tool-call message require reasoning_content?
    body_tool_without_reasoning = {
        "model": model,
        "max_tokens": 1,
        "messages": [
            {"role": "user", "content": "x"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_probe",
                        "type": "function",
                        "function": {"name": "noop", "arguments": "{}"},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_probe", "content": "ok"},
        ],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "noop",
                    "description": "",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ],
    }
    # The two probes are independent, so run them at the same time. Doing them
    # one after another doubles the wall time, and with a hard 20 s budget that
    # alone is enough to turn a healthy-but-slow relay into "no response".
    first, second = await asyncio.gather(
        _post(body_with_reasoning),
        _post(body_tool_without_reasoning),
        return_exceptions=True,
    )

    def _status(result: Any) -> int | None:
        return None if isinstance(result, BaseException) else result.status_code

    status_first, status_second = _status(first), _status(second)

    if 401 in (status_first, status_second) or 403 in (
        status_first,
        status_second,
    ):
        code = 401 if 401 in (status_first, status_second) else 403
        failed = first if status_first in (401, 403) else second
        # Surface the upstream's own words: 403 is often not a bad key but an
        # overdue balance, and the snippet says so.
        snippet = " ".join((getattr(failed, "text", "") or "").split())[:160]
        lowered = snippet.lower()
        auth_like = any(
            word in lowered
            for word in ("api key", "apikey", "invalid", "unauthorized", "auth")
        )
        if auth_like or not snippet:
            evidence = f"上游鉴权失败（HTTP {code}），请检查 API Key"
        else:
            # 403 is often not a bad key but an overdue balance / quota.
            evidence = f"上游拒绝请求（HTTP {code}）"
        if snippet:
            evidence += f"：{snippet}"
        return _probe_result("turn", evidence, False)

    # Signal 1: the provider rejects reasoning_content outright.
    if status_first == 400:
        text = (first.text or "").lower()
        if "reasoning" in text or "thinking" in text:
            if any(hint in text for hint in _FORBIDDEN_HINTS) or not any(
                hint in text for hint in _REQUIRED_HINTS
            ):
                return _probe_result(
                    "drop",
                    "上游拒绝回传 reasoning_content → 全部丢弃（drop）",
                    True,
                )

    # Signal 2: the tool loop demands reasoning_content.
    if status_second == 400:
        text = (second.text or "").lower()
        if any(hint in text for hint in _REQUIRED_HINTS):
            return _probe_result(
                "turn",
                "工具调用轮要求回传 reasoning_content → 仅当前轮（turn）",
                True,
            )

    if status_first is None or status_second is None:
        failed = first if status_first is None else second
        return _unreachable(failed)  # type: ignore[arg-type]

    status = f"{status_first}/{status_second}"
    if status_first == 404 and status_second == 404:
        return _probe_result(
            "turn",
            f"上游没有 {endpoint} 接口（HTTP {status}），请检查 Base URL 与上游协议",
            False,
        )
    return _probe_result(
        "turn",
        f"上游未给出明确信号（HTTP {status}），保持当前设置",
        False,
    )


def reset_learned() -> None:
    """Clear the in-process context and any persisted correction (tests)."""
    global _CTX
    ctx = _CTX
    path = _state_file(ctx)
    if path is not None:
        try:
            path.unlink()
        except OSError:
            pass
    _CTX = PolicyContext()


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
