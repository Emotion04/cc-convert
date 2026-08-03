# SPDX-License-Identifier: Apache-2.0
"""
Per-instance runtime state for the CC Convert proxy worker.
"""

import time
from dataclasses import dataclass, field
from datetime import date
from threading import Lock
from typing import Literal


@dataclass
class InstanceRuntimeState:
    """Tracks runtime metrics for a single worker instance."""

    instance_id: str
    revision: int = 0
    started_at: float = field(default_factory=time.time)
    total_requests: int = 0
    total_errors: int = 0
    status: Literal["running", "stopped", "error"] = "running"

    # Internal day-tracking for auto-reset
    _current_date: str = field(default_factory=lambda: date.today().isoformat())
    _lock: Lock = field(default_factory=Lock)

    @property
    def uptime(self) -> float:
        return time.time() - self.started_at

    @property
    def error_rate(self) -> float:
        if self.total_requests > 0:
            return round(self.total_errors / self.total_requests * 100, 1)
        return 0.0

    def record_request(self, is_error: bool = False) -> None:
        """Record a request (and optionally an error), resetting counters if the day changed."""
        with self._lock:
            today = date.today().isoformat()
            if self._current_date != today:
                self._current_date = today
                self.total_requests = 0
                self.total_errors = 0
            self.total_requests += 1
            if is_error:
                self.total_errors += 1

    def reset_daily(self) -> None:
        """Reset daily counters."""
        with self._lock:
            self.total_requests = 0
            self.total_errors = 0
            self._current_date = date.today().isoformat()

    def to_dict(self) -> dict:
        """Return a snapshot of the current state as a plain dict."""
        with self._lock:
            return {
                "instance_id": self.instance_id,
                "revision": self.revision,
                "started_at": self.started_at,
                "uptime": self.uptime,
                "total_requests": self.total_requests,
                "total_errors": self.total_errors,
                "error_rate": self.error_rate,
                "status": self.status,
            }
