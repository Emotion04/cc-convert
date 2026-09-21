# SPDX-License-Identifier: Apache-2.0
"""Smoke test: every module in the package must be importable.

This is the cheapest guard against the class of defect that motivated it: a
module-level ``NameError`` from a function defined above its own import.
``router.py`` had exactly that and it broke collection of 15 test files, yet a
single ``import cc_convert.router`` would have caught it in about a second.
"""

import importlib
import pkgutil

import cc_convert


def _all_module_names():
    return sorted(
        module.name
        for module in pkgutil.walk_packages(
            cc_convert.__path__, prefix=cc_convert.__name__ + "."
        )
    )


def test_package_walk_finds_modules():
    """Guard against ``walk_packages`` silently returning nothing."""
    names = _all_module_names()
    assert len(names) >= 25, f"Only found {len(names)} submodules of cc_convert"


def test_every_module_imports():
    """Import each submodule, reporting every failure rather than just the first."""
    failures = []
    for name in _all_module_names():
        try:
            importlib.import_module(name)
        except Exception as exc:
            failures.append(f"{name}: {type(exc).__name__}: {exc}")

    assert not failures, "Modules failed to import:\n" + "\n".join(failures)


def test_console_entrypoint_is_importable():
    """The target of the ``ccconvert`` console script must import cleanly."""
    module = importlib.import_module("cc_convert.main")
    assert callable(module.main)
