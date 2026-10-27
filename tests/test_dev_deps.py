# SPDX-License-Identifier: Apache-2.0
"""Guard against the two dev-dependency lists drifting apart again.

``pyproject.toml`` declares dev dependencies in two places:

* ``[dependency-groups] dev`` — what plain ``uv sync`` installs.
* ``[project.optional-dependencies] dev`` — kept so that
  ``pip install -e ".[dev]"`` still works for contributors who do not use uv.

They once drifted: the group held only jinja2/pytest-cov while the extra held
pytest/pytest-asyncio/black/ruff/mypy. Since ``uv sync`` installs the *group*,
a plain sync left ``pytest-asyncio`` missing and made the async test modules
uncollectable. This test is what keeps them in step.
"""

import tomllib
from pathlib import Path

import pytest

PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"

pytestmark = pytest.mark.skipif(
    not PYPROJECT.exists(),
    reason="pyproject.toml not present (not running from a source checkout)",
)


def _package_names(requirements):
    """Bare, normalised package names from requirement strings.

    Handles the ``name>=1.0`` / ``name[extra]`` / ``name; marker`` shapes that
    appear in this file without pulling in a full PEP 508 parser.
    """
    names = set()
    for requirement in requirements:
        name = requirement.split(";")[0]
        for separator in (">=", "==", "~=", "<=", "!=", ">", "<"):
            name = name.split(separator)[0]
        names.add(name.split("[")[0].strip().lower())
    return names


def test_dev_dependency_lists_match():
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))

    group = _package_names(data["dependency-groups"]["dev"])
    extra = _package_names(data["project"]["optional-dependencies"]["dev"])

    assert group == extra, (
        "[dependency-groups] dev and [project.optional-dependencies] dev have "
        "drifted apart.\n"
        f"  only in dependency-groups:      {sorted(group - extra)}\n"
        f"  only in optional-dependencies:  {sorted(extra - group)}\n"
        "A plain `uv sync` installs the dependency-group, so it must stay "
        "complete; the extra exists only for pip users."
    )
