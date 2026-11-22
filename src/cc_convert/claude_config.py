# SPDX-License-Identifier: Apache-2.0
"""Generate minimal Claude Code settings files from proxy instances.

Why minimal: ``claude --settings <file>`` *merges* the given file over the
user's main ``settings.json``. Writing a full settings document would freeze
every unrelated preference (permissions, plugins, status line) at the moment
of generation, and later edits to the main file would stop applying. So we
write only what differs for this proxy instance.

Resulting shape::

    {
      "env": {
        "ANTHROPIC_BASE_URL": "http://127.0.0.1:18402",
        "ANTHROPIC_AUTH_TOKEN": "local",
        "ANTHROPIC_MODEL": "...",
        "ANTHROPIC_DEFAULT_{FABLE,OPUS,SONNET,HAIKU}_MODEL": "...",   # optional
        "CLAUDE_CODE_MAX_CONTEXT_TOKENS": "1000000",                  # optional
        "CLAUDE_CODE_AUTO_COMPACT_WINDOW": "970000"                   # optional
      },
      "promptCacheTtl": "1h",            # optional, Claude models only
      "subagentPromptCacheTtl": "5m"     # optional
    }

The auth token is a placeholder because cc-convert's proxy does not
authenticate incoming requests — the value never leaves the machine. Callers
that point BASE_URL straight at an upstream can ask for the real key instead.
"""

from dataclasses import dataclass, field
from typing import Any

#: Placeholder token used when the settings file talks to the local proxy.
LOCAL_TOKEN = "local"

#: Fraction of the context window used as the auto-compact threshold.
#: Compacting slightly before the window fills keeps a request from being
#: rejected mid-turn; 3% is the smallest gap that leaves room for one reply.
COMPACT_RATIO = 0.97

#: Aliases Claude Code resolves per model tier. Empty values are omitted so
#: the tier inherits ANTHROPIC_MODEL.
TIER_KEYS: tuple[tuple[str, str], ...] = (
    ("ANTHROPIC_DEFAULT_FABLE_MODEL", "client_fable_model"),
    ("ANTHROPIC_DEFAULT_OPUS_MODEL", "client_opus_model"),
    ("ANTHROPIC_DEFAULT_SONNET_MODEL", "client_sonnet_model"),
    ("ANTHROPIC_DEFAULT_HAIKU_MODEL", "client_haiku_model"),
)


def default_compact_window(context_window: int) -> int:
    """Auto-compact threshold for a given context window (0 when unset)."""
    if context_window <= 0:
        return 0
    return int(context_window * COMPACT_RATIO)


@dataclass
class ClaudeConfigSpec:
    """Everything needed to render a settings fragment."""

    base_url: str
    auth_token: str = LOCAL_TOKEN
    model: str = ""
    #: Tier → model name overrides (already resolved; empty means inherit).
    tier_models: dict[str, str] = field(default_factory=dict)
    context_window: int = 0
    compact_window: int = 0
    prompt_cache_ttl: str = ""
    subagent_cache_ttl: str = ""


def build_spec(instance: Any, *, auth_token: str = LOCAL_TOKEN) -> ClaudeConfigSpec:
    """Build a spec from an :class:`InstanceProfile`.

    ``base_url`` points at the proxy's local port, which is what makes the
    instance usable from Claude Code.
    """
    tiers = {
        key: (getattr(instance, attr, "") or "").strip()
        for key, attr in TIER_KEYS
    }
    model = (getattr(instance, "client_model", "") or "").strip()
    if not model:
        # ANTHROPIC_MODEL is the base every tier falls back to, so derive it
        # from the proxy's target model when the user did not set one.
        model = (getattr(instance, "target_model", "") or "").strip()

    context_window = int(getattr(instance, "context_window", 0) or 0)
    compact_window = int(getattr(instance, "compact_window", 0) or 0)
    if context_window and not compact_window:
        compact_window = default_compact_window(context_window)

    return ClaudeConfigSpec(
        base_url=f"http://127.0.0.1:{getattr(instance, 'port', 0)}",
        auth_token=auth_token,
        model=model,
        tier_models={k: v for k, v in tiers.items() if v},
        context_window=context_window,
        compact_window=compact_window,
        prompt_cache_ttl=(getattr(instance, "prompt_cache_ttl", "") or ""),
        subagent_cache_ttl=(getattr(instance, "subagent_cache_ttl", "") or ""),
    )


def render(spec: ClaudeConfigSpec) -> dict[str, Any]:
    """Render the settings fragment. Omitted keys fall back to the main file."""
    env: dict[str, str] = {
        "ANTHROPIC_BASE_URL": spec.base_url,
        "ANTHROPIC_AUTH_TOKEN": spec.auth_token,
    }
    # Claude Code resolves each tier independently; an unwritten tier falls
    # back to the *main* settings.json, whose model usually belongs to a
    # different provider. The user's rule: tiers default to ANTHROPIC_MODEL,
    # so all five keys are always written together once a model is known.
    if spec.model:
        env["ANTHROPIC_MODEL"] = spec.model
        for key, _attr in TIER_KEYS:
            env[key] = spec.tier_models.get(key) or spec.model
    if spec.context_window > 0:
        env["CLAUDE_CODE_MAX_CONTEXT_TOKENS"] = str(spec.context_window)
    if spec.compact_window > 0:
        env["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] = str(spec.compact_window)

    doc: dict[str, Any] = {"env": env}
    # Both TTL switches only affect Anthropic models; non-Claude upstreams
    # ignore them, which is why they default to "omitted" rather than "5m".
    if spec.prompt_cache_ttl:
        doc["promptCacheTtl"] = spec.prompt_cache_ttl
    if spec.subagent_cache_ttl:
        doc["subagentPromptCacheTtl"] = spec.subagent_cache_ttl
    return doc


def render_for_instance(instance: Any, *, upstream_key: bool = False) -> dict[str, Any]:
    """Convenience wrapper used by the API layer."""
    token = (
        (getattr(instance, "upstream_api_key", "") or LOCAL_TOKEN)
        if upstream_key
        else LOCAL_TOKEN
    )
    return render(build_spec(instance, auth_token=token))
