# SPDX-License-Identifier: Apache-2.0
"""
Multi-instance dashboard routes for the CC Convert manager.
"""

import os
import sys
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from cc_convert import __version__
from cc_convert.profile_store import (
    InstanceProfile,
    ProfileStore,
    check_upstream_base_url,
    load_profile_store,
    save_profile_store,
    mask_api_key,
    suggest_port,
)

web_router = APIRouter()

def _templates_directory() -> Path:
    """Locate the Jinja templates.

    In a frozen build ``__file__`` points inside the bundle and the templates
    are unpacked under ``sys._MEIPASS``; in a source checkout they sit next to
    this module.
    """
    bundled = getattr(sys, "_MEIPASS", None)
    if bundled:
        return Path(bundled) / "cc_convert" / "web" / "templates"
    return Path(__file__).parent / "templates"


templates_dir = _templates_directory()


def _csrf_context(request: Request) -> dict:
    """Context processor to inject CSRF token from request state into templates."""
    return {
        "csrf_token": getattr(request.state, "csrf_token", ""),
    }


templates = Jinja2Templates(
    directory=str(templates_dir),
    context_processors=[_csrf_context],
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_store(request: Request) -> ProfileStore:
    return request.app.state.store


def _get_supervisor(request: Request):
    return request.app.state.supervisor


def _instance_to_response(inst: InstanceProfile, status: dict | None = None) -> dict:
    """Serialize an instance for API responses (key always masked)."""
    d = inst.model_dump()  # excludes upstream_api_key per model_dump override
    d["key_configured"] = bool(inst.upstream_api_key)
    d["key_last4"] = inst.upstream_api_key[-4:] if inst.upstream_api_key and len(inst.upstream_api_key) > 4 else None
    if status:
        d["status"] = status.get("status", "stopped")
        d["instance_pid"] = status.get("pid")
        d["instance_started_at"] = status.get("started_at")
        d["instance_uptime"] = status.get("uptime")
    else:
        d["status"] = "unknown"
        d["instance_pid"] = None
        d["instance_started_at"] = None
        d["instance_uptime"] = None

    # Effective reasoning policy. A persisted correction may have overridden
    # the user's choice; expose that so the UI can mention it once instead of
    # alerting on every reply.
    from cc_convert.thinking_policy import (
        MANUAL_MODES,
        PolicyContext,
        load_correction,
        rule_for_model,
    )

    correction = None
    if not inst.thinking_history_force_manual:
        correction = load_correction(
            PolicyContext(
                instance_id=inst.id,
                base_url=inst.upstream_base_url,
                model=inst.target_model,
                upstream_format=inst.upstream_format,
            )
        )

    if correction:
        d["thinking_history_effective"] = correction["policy"]
        d["thinking_history_corrected"] = True
        d["thinking_history_correction_reason"] = correction.get("reason")
    else:
        d["thinking_history_effective"] = (
            inst.thinking_history
            if inst.thinking_history in MANUAL_MODES
            else rule_for_model(inst.target_model)
        )
        d["thinking_history_corrected"] = False
        d["thinking_history_correction_reason"] = None

    return d


def _normalize_instance_fields(data: dict) -> None:
    """Trim stray whitespace from user-pasted instance fields.

    Values copied from a browser or chat window often carry leading/trailing
    spaces. A URL with a leading space fails the ``http://`` check outright,
    and a key with a trailing space is sent inside the Authorization header,
    where the upstream rejects it. Trimming only the ends cannot change a
    legitimate URL or API key.
    """
    for field in ("upstream_base_url", "upstream_api_key"):
        value = data.get(field)
        if isinstance(value, str):
            data[field] = value.strip()


def _validate_instance_body(data: dict, require_key: bool = True) -> list[str]:
    """Validate instance fields and return a list of error messages (empty = valid)."""
    errors = []
    if "name" in data:
        if not isinstance(data["name"], str) or len(data["name"]) == 0 or len(data["name"]) > 64:
            errors.append("name must be between 1 and 64 characters")
    if "port" in data:
        p = data["port"]
        if not isinstance(p, int) or p < 1 or p > 65535:
            errors.append("port must be between 1 and 65535")
    if "upstream_base_url" in data:
        u = data["upstream_base_url"]
        if not isinstance(u, str) or not (u.startswith("http://") or u.startswith("https://")):
            errors.append("upstream_base_url must start with http:// or https://")
    if require_key and ("upstream_api_key" not in data or not data.get("upstream_api_key")):
        errors.append("upstream_api_key is required")
    if "thinking_history" in data:
        from cc_convert.thinking_policy import MODES

        if data["thinking_history"] not in MODES:
            errors.append(
                "thinking_history must be one of: " + ", ".join(MODES)
            )
    for flag in ("thinking_history_auto_correct", "thinking_history_force_manual"):
        if flag in data and not isinstance(data[flag], bool):
            errors.append(f"{flag} must be a boolean")
    # target_model is optional — empty means passthrough
    return errors


# ---------------------------------------------------------------------------
# Page routes
# ---------------------------------------------------------------------------

@web_router.get("/", response_class=HTMLResponse)
async def dashboard_page(request: Request):
    """Dashboard page showing instance cards."""
    return templates.TemplateResponse(request, "dashboard.html")


@web_router.get("/config", response_class=HTMLResponse)
async def config_page(request: Request):
    """Instance create/edit form.  Query param ?id=<id> for edit."""
    from cc_convert.thinking_policy import MODES, PROVIDER_NOTES

    return templates.TemplateResponse(
        request,
        "config.html",
        {"thinking_modes": MODES, "provider_notes": PROVIDER_NOTES},
    )


@web_router.get("/logs", response_class=HTMLResponse)
async def logs_page(request: Request):
    """Log viewer with instance selector."""
    return templates.TemplateResponse(request, "logs.html")


@web_router.get("/ports", response_class=HTMLResponse)
async def ports_page(request: Request):
    """Port health management page."""
    return templates.TemplateResponse(request, "ports.html")


@web_router.get("/api-test", response_class=HTMLResponse)
async def api_test_page(request: Request):
    """API tester with instance selector."""
    return templates.TemplateResponse(request, "api_test.html")


@web_router.get("/docs", response_class=HTMLResponse)
async def docs_page(request: Request):
    """Custom API documentation page (standalone layout, not base.html)."""
    return templates.TemplateResponse(request, "docs.html")


# ---------------------------------------------------------------------------
# Instance CRUD
# ---------------------------------------------------------------------------

@web_router.get("/api/instances")
async def list_instances(request: Request):
    """List all instances with masked keys and supervisor status."""
    store = _get_store(request)
    supervisor = _get_supervisor(request)
    results = []
    for inst in store.instances:
        st = await supervisor.get_instance_status(inst.id)
        results.append(_instance_to_response(inst, st))
    return results


@web_router.post("/api/instances")
async def create_instance(request: Request):
    """Create a new instance."""
    store = _get_store(request)
    supervisor = _get_supervisor(request)
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(400, detail="Invalid JSON body")

    _normalize_instance_fields(data)

    errors = _validate_instance_body(data, require_key=True)
    if errors:
        raise HTTPException(422, detail="; ".join(errors))

    port = data.get("port", 0)
    if not port or port == 0:
        port = suggest_port(store)

    instance = InstanceProfile(
        name=data["name"],
        port=port,
        upstream_base_url=data["upstream_base_url"],
        upstream_api_key=data["upstream_api_key"],
        target_model=data.get("target_model", ""),
        client_model=data.get("client_model", ""),
        client_sonnet_model=data.get("client_sonnet_model", ""),
        client_opus_model=data.get("client_opus_model", ""),
        client_haiku_model=data.get("client_haiku_model", ""),
        thinking_history=data.get("thinking_history", "upstream"),
        thinking_history_auto_correct=bool(
            data.get("thinking_history_auto_correct", True)
        ),
        thinking_history_force_manual=bool(
            data.get("thinking_history_force_manual", False)
        ),
        auto_start=bool(data.get("auto_start", True)),
    )

    store.instances.append(instance)
    # Persist first: the reachability check is advisory and must never prevent
    # the user from saving a configuration they are still fixing.
    save_profile_store(store)

    warnings = await check_upstream_base_url(
        instance.upstream_base_url, instance.upstream_api_key
    )

    auto = data.get("auto_start", True)
    status = None
    if auto:
        ok = await supervisor.start_instance(instance)
        status = {"status": "running" if ok else "error"}

    return {**_instance_to_response(instance, status), "warnings": warnings}


@web_router.get("/api/instances/{instance_id}")
async def get_instance(request: Request, instance_id: str):
    """Get a single instance with masked key and supervisor status."""
    store = _get_store(request)
    supervisor = _get_supervisor(request)
    for inst in store.instances:
        if inst.id == instance_id:
            st = await supervisor.get_instance_status(instance_id)
            return _instance_to_response(inst, st)
    raise HTTPException(404, detail="Instance not found")


@web_router.patch("/api/instances/{instance_id}")
async def update_instance(request: Request, instance_id: str):
    """Update an instance.  Partial update with special key handling."""
    store = _get_store(request)
    supervisor = _get_supervisor(request)

    try:
        data = await request.json()
    except Exception:
        raise HTTPException(400, detail="Invalid JSON body")

    # The upstream fields may be stripped or rewritten below; remember whether
    # the user actually touched them so we only probe when it is meaningful.
    touches_upstream = any(
        key in data
        for key in ("upstream_base_url", "upstream_api_key", "clear_upstream_api_key")
    )

    # Find the instance
    instance = None
    idx = -1
    for i, inst in enumerate(store.instances):
        if inst.id == instance_id:
            instance = inst
            idx = i
            break
    if instance is None:
        raise HTTPException(404, detail="Instance not found")

    # Special handling for upstream_api_key
    if "upstream_api_key" in data:
        val = data["upstream_api_key"]
        if val is None or (isinstance(val, str) and val.startswith("***")):
            # Safety: reject mask-prefixed values (someone sent masked key back)
            if "clear_upstream_api_key" in data and data.get("clear_upstream_api_key"):
                data["upstream_api_key"] = ""
            else:
                # Keep existing key, remove from patch
                data.pop("upstream_api_key")
        elif val == "":
            # Explicit empty -> clear
            data["upstream_api_key"] = ""
    elif data.get("clear_upstream_api_key"):
        data["upstream_api_key"] = ""

    # Remove meta fields from patch
    data.pop("clear_upstream_api_key", None)
    data.pop("key_configured", None)
    data.pop("key_last4", None)
    data.pop("status", None)
    data.pop("instance_pid", None)
    data.pop("instance_started_at", None)
    data.pop("instance_uptime", None)
    data.pop("id", None)

    if not data:
        st = await supervisor.get_instance_status(instance_id)
        return _instance_to_response(instance, st)

    # Validate remaining fields
    _normalize_instance_fields(data)
    errors = _validate_instance_body(data, require_key=False)
    if errors:
        raise HTTPException(422, detail="; ".join(errors))

    # Use supervisor.update_instance for lifecycle-aware update
    result = await supervisor.update_instance(instance_id, data)
    if not result.get("success"):
        raise HTTPException(500, detail=result.get("error", "Update failed"))

    # Re-read from store (supervisor.update_instance mutates in-place)
    for inst in store.instances:
        if inst.id == instance_id:
            st = await supervisor.get_instance_status(instance_id)
            warnings = []
            if touches_upstream:
                # Advisory only — the update above has already been applied.
                warnings = await check_upstream_base_url(
                    inst.upstream_base_url, inst.upstream_api_key
                )
            return {**_instance_to_response(inst, st), "warnings": warnings}

    raise HTTPException(404, detail="Instance not found after update")


@web_router.delete("/api/instances/{instance_id}")
async def delete_instance(request: Request, instance_id: str):
    """Delete an instance.  Only allowed if stopped."""
    store = _get_store(request)
    supervisor = _get_supervisor(request)

    for i, inst in enumerate(store.instances):
        if inst.id == instance_id:
            st = await supervisor.get_instance_status(instance_id)
            if st.get("status") == "running":
                raise HTTPException(409, detail="Cannot delete running instance. Stop it first.")
            store.instances.pop(i)
            save_profile_store(store)
            return {"success": True}

    raise HTTPException(404, detail="Instance not found")


# ---------------------------------------------------------------------------
# Instance lifecycle
# ---------------------------------------------------------------------------

@web_router.post("/api/instances/{instance_id}/start")
async def start_instance(request: Request, instance_id: str):
    """Start an instance."""
    store = _get_store(request)
    supervisor = _get_supervisor(request)
    for inst in store.instances:
        if inst.id == instance_id:
            ok = await supervisor.start_instance(inst)
            if ok:
                return {"success": True}
            raise HTTPException(500, detail="Failed to start instance")
    raise HTTPException(404, detail="Instance not found")


@web_router.post("/api/instances/{instance_id}/stop")
async def stop_instance(request: Request, instance_id: str):
    """Stop an instance."""
    supervisor = _get_supervisor(request)
    ok = await supervisor.stop_instance(instance_id)
    if ok:
        return {"success": True}
    raise HTTPException(500, detail="Failed to stop instance")


@web_router.post("/api/instances/{instance_id}/restart")
async def restart_instance(request: Request, instance_id: str):
    """Restart an instance."""
    supervisor = _get_supervisor(request)
    ok = await supervisor.restart_instance(instance_id)
    if ok:
        return {"success": True}
    raise HTTPException(500, detail="Failed to restart instance")


# ---------------------------------------------------------------------------
# Instance monitoring
# ---------------------------------------------------------------------------

@web_router.get("/api/instances/{instance_id}/status")
async def instance_status(request: Request, instance_id: str):
    """Get live instance status from supervisor."""
    supervisor = _get_supervisor(request)
    st = await supervisor.get_instance_status(instance_id)
    return st


@web_router.get("/api/instances/{instance_id}/stats")
async def instance_stats(request: Request, instance_id: str):
    """Aggregate stats for an instance."""
    supervisor = _get_supervisor(request)
    st = await supervisor.get_instance_status(instance_id)
    total_requests = st.get("total_requests", 0)
    total_errors = st.get("total_errors", 0)
    error_rate = round(total_errors / total_requests * 100, 1) if total_requests > 0 else 0.0
    return {
        "today_requests": total_requests,
        "today_errors": total_errors,
        "error_rate": error_rate,
        "request_change": 0,
    }


@web_router.get("/api/instances/{instance_id}/logs")
async def instance_logs(
    request: Request,
    instance_id: str,
    type: str = "server",
    lines: int = 100,
):
    """Get logs for a specific instance."""
    supervisor = _get_supervisor(request)
    return await supervisor.get_instance_logs(instance_id, lines=lines, log_type=type)


# ---------------------------------------------------------------------------
# Port suggestion & health management
# ---------------------------------------------------------------------------

@web_router.post("/api/ports/suggest")
async def port_suggest(request: Request):
    """Suggest a free port."""
    store = _get_store(request)
    port = suggest_port(store)
    return {"port": port}


@web_router.post("/api/detect-format")
async def detect_format(request: Request):
    """Probe the given upstream URL and return the detected API format."""
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(400, detail="Invalid JSON body")
    base_url = (data.get("url") or "").strip()
    api_key = (data.get("key") or "").strip()
    if not base_url:
        raise HTTPException(400, detail="url is required")
    from cc_convert.profile_store import detect_upstream_format as _detect
    fmt = await _detect(base_url, api_key)
    return {"format": fmt}


@web_router.post("/api/probe-thinking")
async def probe_thinking(request: Request):
    """Probe the upstream and recommend a reasoning-history mode.

    Uses at most two requests with max_tokens=1. Advisory only — the user
    still chooses and saves.
    """
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(400, detail="Invalid JSON body")

    url = (data.get("url") or "").strip()
    key = (data.get("key") or "").strip()
    model = (data.get("model") or "").strip()
    instance_id = data.get("instance_id")

    if instance_id and (not key or key.startswith("***")):
        store = _get_store(request)
        for inst in store.instances:
            if inst.id == instance_id:
                key = inst.upstream_api_key
                url = url or inst.upstream_base_url
                model = model or inst.target_model
                break

    if not url:
        raise HTTPException(400, detail="url is required")
    if not model:
        raise HTTPException(400, detail="请先填写目标模型，再执行探测")

    from cc_convert.thinking_policy import probe_upstream

    return await probe_upstream(url, key, model)


@web_router.get("/api/ports/overview")
async def ports_overview(request: Request):
    """Health summary for the manager port and every instance port."""
    supervisor = _get_supervisor(request)
    return supervisor.get_port_overview()


@web_router.post("/api/ports/{instance_id}/check")
async def port_check(request: Request, instance_id: str):
    """Check a single instance's port + upstream health."""
    supervisor = _get_supervisor(request)
    result = supervisor.check_instance_port(instance_id)
    if "error" in result:
        raise HTTPException(404, detail=result["error"])
    return result


@web_router.post("/api/ports/{instance_id}/rebind")
async def port_rebind(request: Request, instance_id: str):
    """Rebind an instance to a new free port (restart if running)."""
    supervisor = _get_supervisor(request)
    result = await supervisor.rebind_instance_port(instance_id)
    if not result.get("success"):
        raise HTTPException(500, detail=result.get("error", "Rebind failed"))
    return result


# ---------------------------------------------------------------------------
# Manager status
# ---------------------------------------------------------------------------

@web_router.get("/api/manager")
async def manager_status(request: Request):
    """Get manager dashboard status."""
    store = _get_store(request)
    supervisor = _get_supervisor(request)
    running_ids = supervisor.get_running_instances()
    return {
        "version": __version__,
        "pid": os.getpid(),
        "port": store.manager.port,
        "host": store.manager.host,
        "uptime": time.time() - request.app.state.started_at if hasattr(request.app.state, "started_at") else 0,
        "instance_count": len(store.instances),
        "running_count": len(running_ids),
    }


# ---------------------------------------------------------------------------
# Global settings
# ---------------------------------------------------------------------------

_GLOBAL_FIELDS = {
    "log_level": "global_log_level",
    "timeout": "global_timeout",
    "cors_origins": "global_cors_origins",
    "cors_credentials": "global_cors_credentials",
    "cors_methods": "global_cors_methods",
    "cors_headers": "global_cors_headers",
    "tavily_api_key": "global_tavily_api_key",
    "tavily_timeout": "global_tavily_timeout",
    "tavily_max_results": "global_tavily_max_results",
    "tongxiao_api_key": "global_tongxiao_api_key",
    "tongxiao_timeout": "global_tongxiao_timeout",
    "tongxiao_max_results": "global_tongxiao_max_results",
    "websearch_max_uses": "global_websearch_max_uses",
    "websearch_provider": "global_websearch_provider",
}


@web_router.get("/api/global")
async def get_global_settings(request: Request):
    """Read global settings from store."""
    store = _get_store(request)
    result = {}
    for short, attr in _GLOBAL_FIELDS.items():
        val = getattr(store, attr, None)
        # Mask sensitive keys
        if short in ("tavily_api_key", "tongxiao_api_key") and val:
            result[short] = mask_api_key(val)
        else:
            result[short] = val
    return result


@web_router.patch("/api/global")
async def patch_global_settings(request: Request):
    """Update global settings in store."""
    store = _get_store(request)
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(400, detail="Invalid JSON body")

    for short, attr in _GLOBAL_FIELDS.items():
        if short in data:
            val = data[short]
            # Reject masked sentinels for key fields
            if short in ("tavily_api_key", "tongxiao_api_key") and isinstance(val, str) and val.startswith("***"):
                continue
            setattr(store, attr, val)

    save_profile_store(store)
    return await get_global_settings(request)


# ---------------------------------------------------------------------------
# CSRF validation middleware (registered in create_manager_app)
# ---------------------------------------------------------------------------

CSRF_SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}


async def csrf_middleware(request: Request, call_next):
    """Validate X-CCCONVERT-CSRF header on mutating requests."""
    if request.method in CSRF_SAFE_METHODS:
        return await call_next(request)

    token = getattr(request.app.state, "csrf_token", None)
    if token is None:
        # CSRF not configured -- allow pass-through for backwards compat
        return await call_next(request)

    header = request.headers.get("X-CCCONVERT-CSRF", "")
    if header != token:
        return JSONResponse(
            status_code=403,
            content={"detail": "Invalid or missing CSRF token"},
        )

    return await call_next(request)
