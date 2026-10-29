# SPDX-License-Identifier: Apache-2.0
"""
Main entry point for the cc-convert proxy server.
"""

import argparse
import asyncio
import logging
import os
import secrets
import sys
import warnings
from contextlib import asynccontextmanager
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from cc_convert import __version__
from cc_convert.config import Settings, get_config_file, get_settings
from cc_convert.launcher import INTERNAL_ARGS as _INTERNAL_ARGS
from cc_convert.protocol import AnthropicError, AnthropicErrorResponse
from cc_convert.router import build_upstream_timeout, openai_router, router
from cc_convert.runtime_paths import get_log_dir
from cc_convert.runtime_state import InstanceRuntimeState
from cc_convert.web import web_router


def get_default_log_dir() -> str:
    """Get default log directory.

    Delegates to :func:`cc_convert.runtime_paths.get_log_dir`, so the directory
    sits under the runtime directory and follows ``CCCONVERT_RUNTIME_DIR``.

    Returns:
        Path to log directory
    """
    return str(get_log_dir())


def _close_logger_handlers(logger: logging.Logger) -> None:
    """Remove and close every handler attached to ``logger``.

    Handlers must be *closed*, not merely dropped: a file handler whose
    reference is discarded keeps its file open until garbage collection,
    which on Windows blocks deleting or rotating the log directory.
    """
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            # Closing must never prevent the application from starting.
            pass


def setup_logging(log_level: str, log_dir: str | None = None) -> None:
    """Setup logging with daily rotation, keeping only today's logs.

    Args:
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR)
        log_dir: Directory for log files (platform-specific default when None)
    """
    # Default log directory based on platform
    if log_dir is None:
        log_dir = get_default_log_dir()

    # Expand user directory if specified
    log_dir = os.path.expanduser(log_dir)

    # Create log directory if it doesn't exist
    Path(log_dir).mkdir(parents=True, exist_ok=True)

    log_file = os.path.join(log_dir, "server.log")
    api_log_file = os.path.join(log_dir, "api.log")

    # Create formatter
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    # Setup root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, log_level.upper()))

    # Clear existing handlers, closing them so their files are released.
    _close_logger_handlers(root_logger)

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # File handler with daily rotation
    # backupCount=1 means keep today's log + 1 backup (2 days total)
    # when='midnight' rotates at midnight
    file_handler = TimedRotatingFileHandler(
        log_file,
        when='midnight',
        interval=1,
        backupCount=1,  # Keep 2 days of logs
        encoding='utf-8'
    )
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    # API log handler - dedicated file for API request/response logging
    api_handler = TimedRotatingFileHandler(
        api_log_file,
        when='midnight',
        interval=1,
        backupCount=1,  # Keep 2 days of logs
        encoding='utf-8'
    )
    api_handler.setFormatter(logging.Formatter("%(message)s"))
    api_handler.setLevel(getattr(logging, log_level.upper()))

    # Create dedicated API logger.
    # Must be cleared first: addHandler appends, so repeated setup_logging
    # calls would otherwise stack a new api.log handler each time and
    # duplicate every api log line (and leak a file handle per call).
    api_logger = logging.getLogger("api")
    api_logger.setLevel(getattr(logging, log_level.upper()))
    _close_logger_handlers(api_logger)
    api_logger.addHandler(api_handler)
    api_logger.propagate = False  # Don't propagate to root logger

    logging.info(f"Logging configured. Log file: {log_file}, API log: {api_log_file}")


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create and configure the FastAPI application.

    Thin wrapper around create_proxy_app for backward compatibility.
    """
    if settings is None:
        settings = get_settings()

    # Configure logging with daily rotation
    # Use platform-specific default if log_dir is not set
    log_dir = settings.log_dir if settings.log_dir else None
    setup_logging(settings.log_level, log_dir)

    return create_proxy_app(settings, instance_id="worker", internal_token="")


def create_proxy_app(settings: Settings, instance_id: str, internal_token: str, revision: int = 0) -> FastAPI:
    """Create an CC Convert Proxy Worker app instance.

    Args:
        settings: Application settings
        instance_id: Unique identifier for this worker instance
        internal_token: Shared secret for inter-process /_ccconvert/status auth
        revision: Store revision this worker was launched for (used by
            supervisor health-check to confirm the worker matches the
            latest persisted config)

    Returns:
        Configured FastAPI application for the proxy worker
    """
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # One connection pool per worker for its whole lifetime: TCP and TLS
        # setup to the upstream is reused instead of being paid on every
        # request. Also bounds the number of open sockets to this pool.
        app.state.http_client = httpx.AsyncClient(
            timeout=build_upstream_timeout(settings)
        )
        try:
            yield
        finally:
            await app.state.http_client.aclose()

    app = FastAPI(
        title="CC Convert Proxy Worker",
        description="A proxy server that converts Anthropic Messages API to OpenAI API",
        version=__version__,
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    # Store settings and instance metadata in app state
    app.state.settings = settings
    app.state.instance_id = instance_id
    app.state.internal_token = internal_token

    # Initialize per-instance runtime state for statistics
    app.state.runtime_state = InstanceRuntimeState(instance_id=instance_id, revision=revision)

    # Add CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=settings.cors_credentials,
        allow_methods=settings.cors_methods,
        allow_headers=settings.cors_headers,
    )

    # Stats recording middleware — counts API requests and errors via runtime_state
    @app.middleware("http")
    async def stats_middleware(request: Request, call_next):
        """Record API request statistics for worker monitoring."""
        path = request.url.path
        # Only count actual API calls, not web UI or docs
        if not path.startswith("/v1/") and path not in ("/health", "/count_tokens"):
            return await call_next(request)

        response = await call_next(request)
        request.app.state.runtime_state.record_request(is_error=response.status_code >= 400)
        return response

    # Proxy routers (no web_router — dashboard not on workers)
    app.include_router(router)
    app.include_router(openai_router)

    # Internal status endpoint — used by supervisor for health monitoring
    @app.get("/_ccconvert/status")
    async def ccconvert_status(request: Request):
        """Return runtime state of this worker instance.

        Protected by internal_token: requires Authorization: Bearer <token>.
        """
        auth = request.headers.get("Authorization", "")
        if auth != f"Bearer {internal_token}":
            return JSONResponse(
                status_code=401,
                content={"error": "Unauthorized"},
            )

        state = request.app.state.runtime_state
        return {
            "instance_id": state.instance_id,
            "revision": state.revision,
            "started_at": state.started_at,
            "uptime": state.uptime,
            "total_requests": state.total_requests,
            "total_errors": state.total_errors,
            "error_rate": state.error_rate,
            "status": state.status,
        }

    # Exception handlers — keep existing Anthropic-style error responses
    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        """Convert HTTP exceptions to Anthropic error format."""
        # Check if detail is already an AnthropicErrorResponse dict
        if isinstance(exc.detail, dict) and "error" in exc.detail:
            return JSONResponse(
                status_code=exc.status_code,
                content=exc.detail,
            )

        error_response = AnthropicErrorResponse(
            error=AnthropicError(
                type="api_error",
                message=str(exc.detail),
            )
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=error_response.model_dump(),
        )

    @app.exception_handler(Exception)
    async def general_exception_handler(request: Request, exc: Exception):
        """Handle unexpected exceptions."""
        logging.exception("Unhandled exception")
        error_response = AnthropicErrorResponse(
            error=AnthropicError(
                type="internal_error",
                message=str(exc),
            )
        )
        return JSONResponse(
            status_code=500,
            content=error_response.model_dump(),
        )

    return app


def create_manager_app(store, supervisor) -> FastAPI:
    """Create the CC Convert Manager app with dashboard and instance management.

    Args:
        store: ProfileStore instance for reading/writing instance profiles
        supervisor: InstanceSupervisor instance for managing subprocess lifecycle

    Returns:
        Configured FastAPI application for the manager dashboard
    """
    import contextlib
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Runs inside the uvicorn event loop: initialise the shared HTTP client
        # here so no other event loop is used.
        sup = app.state.supervisor
        await sup.ensure_client()

        # Auto-start instances in the background. Waiting for them here blocks
        # uvicorn from listening until every instance has been tried, which is
        # tens of seconds when several upstreams are unreachable — the dashboard
        # would look "started but unreachable".
        startup_task = asyncio.create_task(sup.startup_all())
        try:
            yield
        finally:
            if not startup_task.done():
                startup_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await startup_task
            await sup.shutdown_all()

    # docs_url/redoc_url=None: the manager serves a custom docs page at /docs
    # (see web/routes.py docs_page) instead of the default Swagger UI.
    app = FastAPI(
        title="CC Convert Manager",
        version=__version__,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
    )

    app.state.store = store
    app.state.supervisor = supervisor

    # Generate per-startup CSRF token
    app.state.csrf_token = secrets.token_urlsafe(32)

    # Web dashboard router only (no proxy traffic in manager)
    app.include_router(web_router)

    # Host header validation middleware
    @app.middleware("http")
    async def host_validation_middleware(request: Request, call_next):
        """Reject requests with non-localhost Host headers.

        The manager is a local-only admin interface; external access is
        handled by the reverse proxy (not the manager itself).
        """
        host = request.headers.get("host", "")
        # Allow empty (no host header), localhost, 127.0.0.1, and IPv6 ::1
        if host and not host.startswith(("localhost", "127.0.0.1", "::1", "[::1]")):
            return JSONResponse(
                status_code=403,
                content={"detail": "Invalid Host header: manager is local-only"},
            )
        return await call_next(request)

    # CSRF middleware
    @app.middleware("http")
    async def csrf_middleware(request: Request, call_next):
        """Protect mutating endpoints with CSRF token validation.

        Token is injected into templates via request.state for GET requests,
        and validated via the X-CCCONVERT-CSRF header on POST/PUT/PATCH/DELETE.
        """
        # Validate CSRF token on state-changing methods
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            header_token = request.headers.get("X-CCCONVERT-CSRF", "")
            if header_token != app.state.csrf_token:
                return JSONResponse(
                    status_code=403,
                    content={"detail": "Invalid or missing CSRF token"},
                )

        # Inject token into request state for template context processor
        request.state.csrf_token = app.state.csrf_token
        return await call_next(request)

    # Exception handlers
    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        """Convert HTTP exceptions to JSON for the dashboard."""
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": str(exc.detail)},
        )

    @app.exception_handler(Exception)
    async def general_exception_handler(request: Request, exc: Exception):
        """Handle unexpected exceptions in the manager app."""
        logging.exception("Unhandled exception in manager app")
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error"},
        )

    return app


def run_foreground(settings: Settings) -> None:
    """Run server in foreground mode (blocking).

    Deprecated: Use create_proxy_app() with uvicorn directly instead.
    """
    warnings.warn(
        "run_foreground() is deprecated, use create_proxy_app() with uvicorn directly.",
        FutureWarning,
        stacklevel=2,
    )

    # Validate required settings
    if not settings.openai_api_key:
        config_file = get_config_file()
        print(
            f"Error: openai_api_key is required.\n"
            f"Please edit the configuration file:\n"
            f"  {config_file}\n"
            f"\nSet your OpenAI API key:\n"
            f'  openai_api_key = "your-api-key"',
            file=sys.stderr,
        )
        sys.exit(1)

    # Create app
    app = create_app(settings)

    # Run server
    print(f"Starting server on {settings.host}:{settings.port}")
    print(f"Proxying to: {settings.openai_base_url}")
    print("Press Ctrl+C to stop\n")

    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
        timeout_keep_alive=300,  # Keep connections alive for 5 minutes
    )


def _print_instance_status(store) -> None:
    """Print one line per configured instance with its running state."""
    if not store.instances:
        return

    from cc_convert import daemon
    from cc_convert.runtime_paths import get_instance_pid_file

    for inst in store.instances:
        pid_file = get_instance_pid_file(inst.id)
        if pid_file.exists():
            try:
                pid = int(pid_file.read_text().strip())
                running = daemon._is_process_running(pid)
            except (ValueError, OSError):
                running = False
        else:
            running = daemon._is_port_in_use(inst.port)
        state = "running" if running else "stopped"
        print(f"  {inst.name}: {state} (port {inst.port})")


def main() -> None:
    """Main entry point with subcommand support."""
    # Frozen builds have no .py files to execute, so they re-enter the runner
    # entry points through these internal arguments (see launcher.py). Handled
    # before argparse so they never show up in --help.
    if len(sys.argv) > 1:
        internal = sys.argv[1]
        if internal in _INTERNAL_ARGS.values():
            sys.argv = [sys.argv[0]]
            if internal == _INTERNAL_ARGS["daemon"]:
                from cc_convert.daemon_runner import run_manager

                run_manager()
            else:
                from cc_convert.worker_runner import run_worker

                run_worker()
            return

    # Create main parser
    parser = argparse.ArgumentParser(
        prog="ccconvert",
        description="CC Convert Manager - multi-instance proxy dashboard",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  ccconvert start              # Start the manager dashboard in background
  ccconvert stop               # Stop the manager
  ccconvert restart            # Restart the manager
  ccconvert status             # Check manager and instance status
  ccconvert logs               # View manager logs
  ccconvert logs -f            # Follow manager logs
  ccconvert open               # Open dashboard in browser
        """.strip(),
    )

    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    # Create subparsers for commands
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # start command
    start_parser = subparsers.add_parser("start", help="Start the manager dashboard in background")
    start_parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Manager port (default: from ProfileStore config)",
    )

    # stop command
    stop_parser = subparsers.add_parser("stop", help="Stop the manager")
    stop_parser.add_argument(
        "-f", "--force",
        action="store_true",
        help="Force kill the manager",
    )

    # restart command
    restart_parser = subparsers.add_parser("restart", help="Restart the manager")
    restart_parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Manager port override (default: from ProfileStore config)",
    )

    # status command
    status_parser = subparsers.add_parser("status", help="Check manager and instance status")

    # logs command
    logs_parser = subparsers.add_parser("logs", help="View manager logs")
    logs_parser.add_argument(
        "-f", "--follow",
        action="store_true",
        help="Follow log output (like tail -f)",
    )
    logs_parser.add_argument(
        "-n", "--lines",
        type=int,
        default=50,
        help="Number of lines to show (default: 50)",
    )

    # open command
    open_parser = subparsers.add_parser("open", help="Open dashboard in default browser")
    open_parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Manager port (default: from ProfileStore config)",
    )

    args = parser.parse_args()

    # Import daemon module only when needed
    from cc_convert import daemon

    # Handle subcommands
    if args.command == "start":
        from cc_convert.profile_store import load_profile_store

        store = load_profile_store()
        port = args.port or store.manager.port

        # `start` is idempotent: "ensure a manager is running". When one is
        # already up, the desired state is already satisfied, so this is a
        # success. It used to return False and exit 1, which made the launch
        # scripts report failure on a perfectly healthy setup.
        already_running, pid, config = daemon.get_status()
        if already_running:
            from cc_convert.process_info import process_name

            actual_port = (config or {}).get("port", port)
            # Shows which build owns the manager: ccconvert.exe (packaged) or
            # python.exe (source checkout).
            origin = process_name(pid) or "another process"
            print(f"Manager already running (PID {pid}, {origin}), nothing to do")
            print(f"Dashboard: http://127.0.0.1:{actual_port}")
            _print_instance_status(store)
            print()
            print(
                "An existing manager is already serving requests. "
                "Run 'ccconvert restart' to replace it with this one."
            )
            sys.exit(0)

        success = daemon.start_daemon(port=port)
        if success:
            # start_daemon moves to a different port when the configured one is
            # already taken, so read back where it actually landed. Printing the
            # port we asked for could send the user to a *different* manager.
            _, _, actual_config = daemon.get_status()
            actual_port = (actual_config or {}).get("port", port)
            print(f"Dashboard: http://127.0.0.1:{actual_port}")
            _print_instance_status(store)
        sys.exit(0 if success else 1)

    elif args.command == "stop":
        success = daemon.stop_daemon(force=args.force)
        if success:
            print("Manager stopped")
        sys.exit(0 if success else 1)

    elif args.command == "restart":
        success = daemon.restart_daemon(port=args.port)
        if success:
            from cc_convert.profile_store import load_profile_store
            store = load_profile_store()
            _, _, actual_config = daemon.get_status()
            port = (actual_config or {}).get("port", store.manager.port)
            print(f"Dashboard: http://127.0.0.1:{port}")
            _print_instance_status(store)
        sys.exit(0 if success else 1)

    elif args.command == "status":
        running, pid, config = daemon.get_status()
        if running:
            port = config.get("port", "?") if config else "?"
            print(f"Manager is running (PID: {pid})")
            print(f"Dashboard: http://127.0.0.1:{port}")
        else:
            print("Manager is not running")

        # List instances from ProfileStore if available
        try:
            from cc_convert.profile_store import load_profile_store
            store = load_profile_store()
            if store.instances:
                print()
                print("Instances:")
                _print_instance_status(store)
        except Exception:
            pass

        sys.exit(0)

    elif args.command == "logs":
        success = daemon.show_logs(follow=args.follow, lines=args.lines)
        sys.exit(0 if success else 1)

    elif args.command == "open":
        import webbrowser

        from cc_convert.profile_store import load_profile_store
        store = load_profile_store()
        port = args.port or store.manager.port
        url = f"http://127.0.0.1:{port}"
        print(f"Opening {url} in default browser...")
        webbrowser.open(url)
        sys.exit(0)

    else:
        # No command - show help instead of running in foreground
        parser.print_help()
        print()
        print("Use 'ccconvert start' to start the manager dashboard.")
        sys.exit(0)


if __name__ == "__main__":
    main()
