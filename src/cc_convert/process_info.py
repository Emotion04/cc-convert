# SPDX-License-Identifier: Apache-2.0
"""Best-effort process introspection for user-facing messages."""

import platform
import subprocess


def process_name(pid: int) -> str:
    """Return a short process name for *pid*, or "" when it cannot be read.

    Used to tell the user *which* build already owns the manager — a frozen
    ``ccconvert.exe`` versus a source ``python.exe`` — when the other one is
    started.
    """
    try:
        if platform.system() == "Windows":
            # tasklist rather than wmic: Windows 11 24H2+ no longer ships wmic.
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                errors="replace",
                timeout=5,
            )
            for line in result.stdout.splitlines():
                line = line.strip()
                # Rows look like: "python.exe","12345","Console","1","50,000 K".
                # With no match tasklist prints a localised sentence instead.
                if line.startswith('"'):
                    return line.split('","')[0].strip('"')
        else:
            result = subprocess.run(
                ["ps", "-p", str(pid), "-o", "comm="],
                capture_output=True,
                text=True,
                errors="replace",
                timeout=5,
            )
            if result.stdout.strip():
                return result.stdout.strip().split()[0]
    except (subprocess.SubprocessError, OSError):
        pass
    return ""
