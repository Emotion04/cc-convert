# SPDX-License-Identifier: Apache-2.0
"""Diagnostics appended to upstream error messages.

The upstream's own error body is passed through unchanged; anything cc-convert
adds is prefixed with ``[cc-convert]`` so the reader can tell the two apart.
This exists because an OpenAI-compatible provider returning a bare 404 (for
example when the base URL is missing ``/v1``) is otherwise indistinguishable
from "the model does not exist".
"""

DIAG_PREFIX = "[cc-convert]"


def upstream_diagnostic(method: str, url: str, status: int) -> str:
    """Return a clearly-labelled diagnostic block for a non-2xx upstream reply."""
    hint = ""
    if status == 404 and "/v1" not in url:
        hint = (
            ' - if the provider uses the common "/v1" prefix, '
            "the configured base URL may be missing it"
        )
    return f"\n\n{DIAG_PREFIX} upstream: {method.upper()} {url} -> {status}{hint}"
