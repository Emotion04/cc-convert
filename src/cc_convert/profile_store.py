# SPDX-License-Identifier: Apache-2.0
"""Profile store for managing multiple instance profiles with global settings."""

import os
import shutil
import socket
import sys
import tempfile
from pathlib import Path
from typing import Literal, Optional
from uuid import uuid4

import tomli_w
from pydantic import BaseModel, Field, field_validator

from cc_convert.config import load_config_from_file
from cc_convert.runtime_paths import get_config_dir, get_config_file

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib


def _find_free_port(start: int = 18401, end: int = 29999, exclude: Optional[set[int]] = None) -> int:
    """Find a free TCP port by scanning sequentially from *start*.

    Sequential allocation gives predictable, easy-to-remember port numbers
    (18401, 18402, …) instead of random slots.
    """
    exclude = exclude or set()
    for port in range(start, end + 1):
        if port in exclude:
            continue
        # No SO_REUSEADDR: on Windows it permits double-binding and would
        # report an occupied port as free.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"No free port found in range [{start}, {end}]")


def suggest_port(store: "ProfileStore") -> int:
    """Suggest a free port avoiding the manager port and all instance ports."""
    exclude = {store.manager.port}
    exclude.update(inst.port for inst in store.instances)
    return _find_free_port(exclude=exclude)


def mask_api_key(key: Optional[str]) -> Optional[str]:
    """Mask an API key, showing only the last 4 characters."""
    if key and len(key) > 4:
        return "***" + key[-4:]
    elif key:
        return "***"
    return None


def _get_config_dir() -> Path:
    """Return the config directory.

    Delegates to :func:`cc_convert.runtime_paths.get_config_dir` so the
    documented ``CCCONVERT_CONFIG_DIR`` override applies to the profile store
    too. This previously consulted its own variable named
    ``"CC Convert_CONFIG_DIR"`` — with a space, leftover from an earlier
    rename — which nothing ever set, so the override was silently ignored
    and the config file could not be relocated.
    """
    return get_config_dir()


def _get_config_path() -> Path:
    """Return the default config file path (``<config_dir>/config.toml``)."""
    return get_config_file()


class InstanceProfile(BaseModel):
    """A single proxy instance configuration.

    The ``upstream_api_key`` field is write-only: it is stripped from
    ``model_dump()`` / ``model_dump_json()`` output to prevent accidental
    exposure through API responses.
    """

    id: str = Field(default_factory=lambda: "inst_" + uuid4().hex[:12])
    name: str
    port: int
    upstream_base_url: str
    upstream_api_key: str = Field(default="")
    target_model: str = Field(default="", description="Target model name. Leave empty to passthrough Claude Code's model name unchanged.")
    client_model: str = Field(default="", description="ANTHROPIC_MODEL override for Claude Code (optional)")
    client_haiku_model: str = Field(default="", description="ANTHROPIC_DEFAULT_HAIKU_MODEL override for Claude Code (optional)")
    client_opus_model: str = Field(default="", description="ANTHROPIC_DEFAULT_OPUS_MODEL override for Claude Code (optional)")
    client_sonnet_model: str = Field(default="", description="ANTHROPIC_DEFAULT_SONNET_MODEL override for Claude Code (optional)")
    upstream_format: Literal["chat_completions", "responses", "auto"] = Field(
        default="auto",
        description="Upstream API format: chat_completions, responses, or auto-detect"
    )
    auto_start: bool = True

    # -- validators --------------------------------------------------------

    @field_validator("name")
    @classmethod
    def _validate_name_length(cls, v: str) -> str:
        if not v or len(v) > 64:
            raise ValueError("name must be between 1 and 64 characters")
        return v

    @field_validator("port")
    @classmethod
    def _validate_port_range(cls, v: int) -> int:
        if v < 1 or v > 65535:
            raise ValueError("port must be between 1 and 65535")
        return v

    @field_validator("upstream_base_url")
    @classmethod
    def _validate_and_strip_url(cls, v: str) -> str:
        # Pasted values often carry leading/trailing whitespace, which would
        # otherwise fail the scheme check for no good reason.
        v = v.strip().rstrip("/")
        if not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError("upstream_base_url must start with http:// or https://")
        return v

    @field_validator("upstream_api_key")
    @classmethod
    def _strip_api_key(cls, v: str) -> str:
        # A trailing space would end up inside the Authorization header.
        return v.strip()

    # -- serialisation overrides -------------------------------------------

    def model_dump(self, *args, **kwargs):
        kwargs.setdefault("exclude", set())
        if isinstance(kwargs["exclude"], set):
            kwargs["exclude"] = kwargs["exclude"] | {"upstream_api_key"}
        elif isinstance(kwargs["exclude"], dict):
            kwargs["exclude"]["upstream_api_key"] = True
        return super().model_dump(*args, **kwargs)

    def model_dump_json(self, *args, **kwargs):
        kwargs.setdefault("exclude", set())
        if isinstance(kwargs["exclude"], set):
            kwargs["exclude"] = kwargs["exclude"] | {"upstream_api_key"}
        elif isinstance(kwargs["exclude"], dict):
            kwargs["exclude"]["upstream_api_key"] = True
        return super().model_dump_json(*args, **kwargs)


class ManagerConfig(BaseModel):
    """Management dashboard configuration."""

    port: int
    host: str = "127.0.0.1"


class ProfileStore(BaseModel):
    """Top-level profile store containing all instances and global settings."""

    schema_version: int = 2
    revision: int = 0
    manager: ManagerConfig
    instances: list[InstanceProfile] = []
    global_timeout: float = 300.0
    global_log_level: str = "INFO"
    global_cors_origins: list[str] = ["*"]
    global_cors_credentials: bool = True
    global_cors_methods: list[str] = ["*"]
    global_cors_headers: list[str] = ["*"]
    global_tavily_api_key: str = ""
    global_tavily_timeout: float = 30.0
    global_tavily_max_results: int = 5
    global_tongxiao_api_key: str = ""
    global_tongxiao_timeout: float = 30.0
    global_tongxiao_max_results: int = 5
    global_websearch_max_uses: int = 5
    global_websearch_provider: Literal["tavily", "tongxiao", "both"] = "tavily"


def load_profile_store(config_path: Optional[Path] = None) -> ProfileStore:
    """Load ProfileStore from TOML config. Migrates v1 if needed."""
    if config_path is None:
        config_path = _get_config_path()
    config_path = Path(config_path)

    if not config_path.exists():
        return migrate_v1_then_load(config_path)

    with open(config_path, "rb") as f:
        data = tomllib.load(f)

    if "schema_version" not in data:
        return migrate_v1_then_load(config_path)

    return ProfileStore(**data)


def save_profile_store(store: ProfileStore, config_path: Optional[Path] = None) -> None:
    """Atomically save ProfileStore to TOML. Revision auto-incremented."""
    if config_path is None:
        config_path = _get_config_path()
    config_path = Path(config_path)

    store.revision += 1

    # ``upstream_api_key`` is excluded by the ``model_dump`` override on
    # ``InstanceProfile``, so we manually re-inject the live values.
    data = store.model_dump(mode="python")
    for i, inst in enumerate(store.instances):
        data["instances"][i]["upstream_api_key"] = inst.upstream_api_key

    config_dir = config_path.parent
    config_dir.mkdir(parents=True, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(suffix=".tmp", prefix="config_", dir=config_dir)
    try:
        with os.fdopen(fd, "wb") as f:
            tomli_w.dump(data, f)
        os.replace(tmp_path, config_path)
        if sys.platform != "win32":
            config_dir.chmod(0o700)
            config_path.chmod(0o600)
    except BaseException:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def migrate_v1_then_load(config_path: Path) -> ProfileStore:
    """Migrate flat v1 config to v2 ProfileStore. Backs up original as .v1.bak."""
    config_dir = config_path.parent
    config_dir.mkdir(parents=True, exist_ok=True)

    # Read v1 TOML from config_path; fall back to the default location only
    # when this *is* the default location. Falling back unconditionally meant
    # that a caller passing an explicit, not-yet-existing path would silently
    # migrate whatever the ambient default file happened to contain.
    if config_path.exists():
        with open(config_path, "rb") as f:
            raw = tomllib.load(f)
    elif config_path == get_config_file():
        raw = load_config_from_file()
    else:
        raw = {}

    original_exists = bool(raw)
    bak_path: Optional[Path] = None

    if original_exists and config_path.exists():
        bak_path = config_path.with_suffix(".toml.v1.bak")
        shutil.copy2(config_path, bak_path)

    # Parse v1 flat fields
    v1 = {
        "openai_api_key": raw.get("openai_api_key", ""),
        "openai_base_url": raw.get("openai_base_url", "https://api.openai.com/v1"),
        "default_model": raw.get("default_model", ""),
        "host": raw.get("host", "0.0.0.0"),
        "port": raw.get("port", 8080),
        "request_timeout": raw.get("request_timeout", 300.0),
        "log_level": raw.get("log_level", "INFO"),
        "cors_origins": raw.get("cors_origins", ["*"]),
        "cors_credentials": raw.get("cors_credentials", True),
        "cors_methods": raw.get("cors_methods", ["*"]),
        "cors_headers": raw.get("cors_headers", ["*"]),
        "tavily_api_key": raw.get("tavily_api_key", ""),
        "tavily_timeout": raw.get("tavily_timeout", 30.0),
        "tavily_max_results": raw.get("tavily_max_results", 5),
        "tongxiao_api_key": raw.get("tongxiao_api_key", ""),
        "tongxiao_timeout": raw.get("tongxiao_timeout", 30.0),
        "tongxiao_max_results": raw.get("tongxiao_max_results", 5),
        "websearch_max_uses": raw.get("websearch_max_uses", 5),
        "websearch_provider": raw.get("websearch_provider", "tavily"),
        "model_mapping": raw.get("model_mapping", []),
    }

    # Determine target_model: from default_model, or if all model_mapping
    # rules map to the same 'to', use that.
    target_model = v1["default_model"]
    if not target_model and v1["model_mapping"]:
        targets = {r.get("to", "") for r in v1["model_mapping"]}
        if len(targets) == 1:
            target_model = targets.pop()

    # Sequential ports: manager = 18400, instance = next free after that
    manager_port = _find_free_port(18400)
    instance_port = _find_free_port(18401, exclude={manager_port})

    try:
        instance = InstanceProfile(
            name="default",
            port=instance_port,
            upstream_base_url=v1["openai_base_url"],
            upstream_api_key=v1["openai_api_key"],
            target_model=target_model or "claude-sonnet-4-20250514",
        )
        manager = ManagerConfig(port=manager_port, host=v1["host"])
        store = ProfileStore(
            schema_version=2,
            revision=0,
            manager=manager,
            instances=[instance],
            global_timeout=v1["request_timeout"],
            global_log_level=v1["log_level"],
            global_cors_origins=v1["cors_origins"],
            global_cors_credentials=v1["cors_credentials"],
            global_cors_methods=v1["cors_methods"],
            global_cors_headers=v1["cors_headers"],
            global_tavily_api_key=v1["tavily_api_key"],
            global_tavily_timeout=v1["tavily_timeout"],
            global_tavily_max_results=v1["tavily_max_results"],
            global_tongxiao_api_key=v1["tongxiao_api_key"],
            global_tongxiao_timeout=v1["tongxiao_timeout"],
            global_tongxiao_max_results=v1["tongxiao_max_results"],
            global_websearch_max_uses=v1["websearch_max_uses"],
            global_websearch_provider=v1["websearch_provider"],
        )
        save_profile_store(store, config_path)
        return store
    except BaseException:
        if original_exists and bak_path is not None:
            shutil.copy2(bak_path, config_path)
        raise


def build_worker_settings(store: ProfileStore, instance: InstanceProfile) -> dict:
    """Merge global defaults and instance fields into a Settings-compatible dict.

    The returned dict can be unpacked as ``Settings(**kwargs)``.  Instance
    ``port`` becomes ``Settings.port``, ``target_model`` becomes
    ``Settings.default_model``, ``upstream_base_url`` becomes
    ``openai_base_url``, and ``upstream_api_key`` becomes ``openai_api_key``.
    ``model_mapping`` is intentionally left empty (model resolution is handled
    at the management level).
    """
    # Workers always bind to localhost; the instance port is the proxy port.
    return {
        "openai_api_key": instance.upstream_api_key,
        "openai_base_url": instance.upstream_base_url,
        "host": "127.0.0.1",
        "port": instance.port,
        "default_model": instance.target_model,
        "request_timeout": store.global_timeout,
        "log_level": store.global_log_level,
        "cors_origins": store.global_cors_origins,
        "cors_credentials": store.global_cors_credentials,
        "cors_methods": store.global_cors_methods,
        "cors_headers": store.global_cors_headers,
        "tavily_api_key": store.global_tavily_api_key,
        "tavily_timeout": store.global_tavily_timeout,
        "tavily_max_results": store.global_tavily_max_results,
        "tongxiao_api_key": store.global_tongxiao_api_key,
        "tongxiao_timeout": store.global_tongxiao_timeout,
        "tongxiao_max_results": store.global_tongxiao_max_results,
        "websearch_max_uses": store.global_websearch_max_uses,
        "websearch_provider": store.global_websearch_provider,
        "model_mapping": [],
        "upstream_format": instance.upstream_format,
    }


async def detect_upstream_format(base_url: str, api_key: str = "", timeout: float = 8.0) -> str:
    """Probe the upstream to determine which API format it supports.

    Returns one of ``"chat_completions"`` or ``"responses"``.
    """
    import httpx

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    base = base_url.rstrip("/")
    probe_body = {"model": "ping", "max_tokens": 1, "messages": [{"role": "user", "content": "p"}]}

    # 1. Try chat/completions (most common)
    try:
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as c:
            r = await c.post(f"{base}/chat/completions", headers=headers, json=probe_body)
            if r.status_code in (200, 400, 401, 403, 404, 422):
                # 200→works, 400/401/403/404/422→endpoint exists, just our probe is bad
                return "chat_completions"
            # Non-JSON response likely means the path doesn't exist (e.g. HTML 404 page)
            if r.status_code == 404 and "text/html" in (r.headers.get("content-type") or ""):
                pass  # fall through to next probe
            else:
                return "chat_completions"  # endpoint exists
    except (httpx.ConnectError, httpx.TimeoutException, httpx.RemoteProtocolError, httpx.ConnectTimeout):
        pass

    # 2. Try responses
    try:
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as c:
            r2 = await c.post(
                f"{base}/responses",
                headers=headers,
                json={"model": "ping", "input": "p"},
            )
            if r2.status_code in (200, 400, 401, 403, 404, 422):
                return "responses"
    except (httpx.ConnectError, httpx.TimeoutException, httpx.RemoteProtocolError, httpx.ConnectTimeout):
        pass

    # 3. Default to chat_completions (most universal)
    return "chat_completions"
