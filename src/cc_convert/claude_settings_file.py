# SPDX-License-Identifier: Apache-2.0
"""Claude Code settings-file integration for the dashboard.

Three operations the UI needs:

- list every ``*.json`` under the user's ``.claude`` directory and mark the
  ones already bound to an instance;
- bind an instance to a file (creating it from the instance, or adopting an
  existing file);
- write the generated fragment back to disk whenever the instance changes.

Claude Code honours ``CLAUDE_CONFIG_DIR``, so we must too; ``~/.claude`` is
only the fallback.
"""

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def claude_config_dir() -> Path:
    """Directory Claude Code reads settings from (mirrors its own rule)."""
    override = os.environ.get("CLAUDE_CONFIG_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".claude"


def resolve_path(raw: str) -> Path:
    """Resolve a stored setting path to an absolute Path.

    Absolute paths are used as-is; a bare filename is understood as
    "inside .claude", which is where users keep per-instance fragments and
    what the UI offers as the default.
    """
    path = Path(raw).expanduser()
    if path.is_absolute():
        return path
    # Keep a relative value inside the Claude directory instead of resolving
    # it against the manager's working directory.
    return claude_config_dir() / path.name


def list_settings_files() -> list[dict[str, Any]]:
    """All JSON files in the Claude directory, with light metadata."""
    directory = claude_config_dir()
    if not directory.is_dir():
        return []
    items: list[dict[str, Any]] = []
    for entry in sorted(directory.glob("*.json")):
        try:
            stat = entry.stat()
        except OSError:
            continue
        has_env = False
        base_url = ""
        try:
            data = json.loads(entry.read_text(encoding="utf-8"))
            env = data.get("env") if isinstance(data, dict) else None
            if isinstance(env, dict):
                has_env = True
                base_url = str(env.get("ANTHROPIC_BASE_URL") or "")
        except (OSError, ValueError):
            # A broken file still deserves to be listed so the user can see
            # and fix it instead of wondering where it went.
            pass
        items.append(
            {
                "path": str(entry),
                "name": entry.name,
                "size": stat.st_size,
                "modified": int(stat.st_mtime),
                "has_env": has_env,
                "base_url": base_url,
                "is_main_settings": entry.name == "settings.json",
            }
        )
    return items


def write_fragment(path: Path, document: dict[str, Any]) -> None:
    """Write a settings fragment atomically, merging into an existing file.

    Merging matters: the user may have added keys we do not manage, and
    dropping them silently would be data loss. Only ``env`` entries and the
    two TTL keys are owned by cc-convert.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    existing: dict[str, Any] = {}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                existing = loaded
        except (OSError, ValueError):
            logger.warning("Existing settings file is not valid JSON, rewriting: %s", path)

    merged = dict(existing)
    current_env = existing.get("env")
    env = dict(current_env) if isinstance(current_env, dict) else {}
    env.update(document.get("env") or {})
    merged["env"] = env
    for key in ("promptCacheTtl", "subagentPromptCacheTtl"):
        if key in document:
            merged[key] = document[key]

    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(merged, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def remove_managed_keys(path: Path, keys: set[str]) -> None:
    """Strip our env keys from a file we are no longer bound to.

    Only keys cc-convert owns are removed; anything else the user put in the
    file stays untouched.
    """
    if not path.is_file():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if not isinstance(data, dict):
        return
    env = data.get("env")
    if isinstance(env, dict):
        for key in keys:
            env.pop(key, None)
        if not env:
            data.pop("env", None)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)
