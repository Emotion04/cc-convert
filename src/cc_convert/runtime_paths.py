# SPDX-License-Identifier: Apache-2.0
"""Platform-specific runtime path resolution for cc-convert."""

import os
from pathlib import Path


def get_config_dir() -> Path:
    """Get platform-specific config directory.

    Respects the ``CCCONVERT_CONFIG_DIR`` environment variable.
    Defaults to ~/.ccconvert.

    Returns:
        Path to the config directory
    """
    override = os.environ.get("CCCONVERT_CONFIG_DIR")
    if override:
        return Path(override)
    return Path.home() / ".ccconvert"


def get_config_file() -> Path:
    """Get config file path.

    Returns:
        Path to the config file (~/.ccconvert/config.toml)
    """
    return get_config_dir() / "config.toml"


def get_runtime_dir() -> Path:
    """Get platform-specific runtime data directory.

    Respects the ``CCCONVERT_RUNTIME_DIR`` environment variable.
    Windows: %LOCALAPPDATA%\\ccconvert
    POSIX:   ~/.local/share/ccconvert

    Returns:
        Path to the runtime data directory
    """
    override = os.environ.get("CCCONVERT_RUNTIME_DIR")
    if override:
        return Path(override)
    if os.name == "nt":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            return Path(local_app_data) / "ccconvert"
        return Path.home() / "AppData" / "Local" / "ccconvert"
    return Path.home() / ".local" / "share" / "ccconvert"


def get_log_dir() -> Path:
    """Get the directory holding ``server.log`` and ``api.log``.

    Lives under the runtime directory so that ``CCCONVERT_RUNTIME_DIR``
    relocates logs together with the rest of the runtime state. It previously
    used a separately-computed path built from ``cc-convert`` (with a hyphen)
    that honoured no override at all — so a relocated runtime still wrote its
    logs to the real user directory, and the test suite leaked log output
    outside its sandbox.
    """
    return get_runtime_dir() / "logs"


def get_manager_state_file() -> Path:
    """Get path to the manager state JSON file.

    Returns:
        Path to manager.json under the runtime directory
    """
    return get_runtime_dir() / "manager.json"


def get_instance_dir(instance_id: str) -> Path:
    """Get per-instance data directory.

    Args:
        instance_id: Unique identifier for the instance

    Returns:
        Path to the instance directory
    """
    return get_runtime_dir() / "instances" / instance_id


def get_instance_log_dir(instance_id: str) -> Path:
    """Get per-instance log directory.

    Args:
        instance_id: Unique identifier for the instance

    Returns:
        Path to the instance log directory
    """
    return get_instance_dir(instance_id) / "logs"


def get_instance_pid_file(instance_id: str) -> Path:
    """Get per-instance PID file path.

    Args:
        instance_id: Unique identifier for the instance

    Returns:
        Path to the instance PID file
    """
    return get_instance_dir(instance_id) / "ccconvert.pid"


def get_instance_meta_file(instance_id: str) -> Path:
    """Get per-instance metadata file path.

    Args:
        instance_id: Unique identifier for the instance

    Returns:
        Path to the instance metadata JSON file
    """
    return get_instance_dir(instance_id) / "ccconvert.json"


def get_manager_log_dir() -> Path:
    """Get manager log directory.

    Returns:
        Path to manager-logs under the runtime directory
    """
    return get_runtime_dir() / "manager-logs"


def ensure_dirs() -> None:
    """Create config and runtime directories with appropriate permissions.

    On Unix-like systems, directories are created with 0o700 permissions.
    On Windows, default permissions are used.
    """
    config_dir = get_config_dir()
    config_dir.mkdir(parents=True, exist_ok=True)

    runtime_dir = get_runtime_dir()
    runtime_dir.mkdir(parents=True, exist_ok=True)

    get_manager_log_dir().mkdir(parents=True, exist_ok=True)

    if os.name != "nt":
        config_dir.chmod(0o700)
        runtime_dir.chmod(0o700)
        get_manager_log_dir().chmod(0o700)
