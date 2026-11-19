# SPDX-License-Identifier: Apache-2.0
"""Path resolution, including the portable ("green build") mode.

Portable mode only applies to frozen builds: a ``data/`` directory beside the
executable redirects both config and runtime state there, so the whole folder
can be moved or carried on a USB stick. Running from source must never pick it
up, otherwise a stray ``data/`` in the checkout would shadow ~/.ccconvert.
"""

import sys

import pytest

from cc_convert import runtime_paths


@pytest.fixture
def frozen_exe(monkeypatch, tmp_path):
    """Pretend the process is a frozen build living in *tmp_path*."""
    exe = tmp_path / "ccconvert.exe"
    exe.write_bytes(b"")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    monkeypatch.delenv("CCCONVERT_CONFIG_DIR", raising=False)
    monkeypatch.delenv("CCCONVERT_RUNTIME_DIR", raising=False)
    return tmp_path


class TestPortableMode:
    def test_no_data_dir_means_normal_locations(self, frozen_exe):
        assert runtime_paths.get_portable_root() is None
        assert runtime_paths.get_config_dir() != frozen_exe / "data" / "config"

    def test_data_dir_redirects_config_and_runtime(self, frozen_exe):
        (frozen_exe / "data").mkdir()
        assert runtime_paths.get_portable_root() == frozen_exe / "data"
        assert runtime_paths.get_config_dir() == frozen_exe / "data" / "config"
        assert runtime_paths.get_runtime_dir() == frozen_exe / "data" / "runtime"
        assert (
            runtime_paths.get_config_file()
            == frozen_exe / "data" / "config" / "config.toml"
        )

    def test_env_override_beats_portable(self, frozen_exe, monkeypatch, tmp_path):
        (frozen_exe / "data").mkdir()
        override = tmp_path / "custom-config"
        monkeypatch.setenv("CCCONVERT_CONFIG_DIR", str(override))
        assert runtime_paths.get_config_dir() == override

    def test_source_run_ignores_data_dir(self, monkeypatch, tmp_path):
        # Not frozen: a data/ directory beside the python executable must not
        # hijack the paths.
        exe = tmp_path / "python.exe"
        exe.write_bytes(b"")
        (tmp_path / "data").mkdir()
        monkeypatch.setattr(sys, "frozen", False, raising=False)
        monkeypatch.setattr(sys, "executable", str(exe))
        monkeypatch.delenv("CCCONVERT_CONFIG_DIR", raising=False)
        assert runtime_paths.get_portable_root() is None
