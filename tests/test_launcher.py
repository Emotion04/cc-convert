# SPDX-License-Identifier: Apache-2.0
"""Tests for frozen-build support.

A packaged build has no ``.py`` files on disk, so the daemon and worker runners
are re-entered through internal arguments on the bundled executable. Source
checkouts must keep starting the actual runner files — the guard is
``sys.frozen``, so both modes coexist.
"""

import sys
from pathlib import Path

from cc_convert import launcher


class TestRunnerCommand:
    def test_source_mode_runs_runner_file(self):
        cmd = launcher.runner_command("daemon")

        assert cmd[0] == sys.executable
        assert cmd[1].endswith("daemon_runner.py")
        assert Path(cmd[1]).exists()

    def test_frozen_mode_re_executes_binary(self, monkeypatch):
        monkeypatch.setattr(sys, "frozen", True, raising=False)

        assert launcher.runner_command("daemon") == [sys.executable, "__daemon"]
        assert launcher.runner_command("worker") == [sys.executable, "__worker"]

    def test_is_frozen_defaults_false(self):
        assert launcher.is_frozen() is False


class TestInternalDispatch:
    def test_daemon_argument_runs_manager(self, monkeypatch):
        import cc_convert.daemon_runner as daemon_runner
        from cc_convert.main import main

        called = []
        monkeypatch.setattr(daemon_runner, "run_manager", lambda: called.append("daemon"))
        monkeypatch.setattr(sys, "argv", ["ccconvert", "__daemon"])

        main()

        assert called == ["daemon"]
        assert sys.argv == ["ccconvert"]

    def test_worker_argument_runs_worker(self, monkeypatch):
        import cc_convert.worker_runner as worker_runner
        from cc_convert.main import main

        called = []
        monkeypatch.setattr(worker_runner, "run_worker", lambda: called.append("worker"))
        monkeypatch.setattr(sys, "argv", ["ccconvert", "__worker"])

        main()

        assert called == ["worker"]


class TestTemplatesDirectory:
    def test_source_mode_uses_package_directory(self):
        from cc_convert.web.routes import _templates_directory

        directory = _templates_directory()

        assert directory.name == "templates"
        assert (directory / "dashboard.html").exists()

    def test_frozen_mode_uses_bundle_root(self, monkeypatch, tmp_path):
        from cc_convert.web import routes

        bundled = tmp_path / "cc_convert" / "web" / "templates"
        bundled.mkdir(parents=True)
        monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)

        assert routes._templates_directory() == bundled
