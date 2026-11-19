# SPDX-License-Identifier: Apache-2.0
"""Claude Code settings-fragment generation and file binding."""

import json

import pytest

from cc_convert import claude_config, claude_settings_file


class FakeInstance:
    """Minimal stand-in for InstanceProfile's settings-binding fields."""

    def __init__(self, **kw):
        self.port = kw.get("port", 18402)
        self.target_model = kw.get("target_model", "")
        self.client_model = kw.get("client_model", "")
        self.client_fable_model = kw.get("client_fable_model", "")
        self.client_opus_model = kw.get("client_opus_model", "")
        self.client_sonnet_model = kw.get("client_sonnet_model", "")
        self.client_haiku_model = kw.get("client_haiku_model", "")
        self.context_window = kw.get("context_window", 0)
        self.compact_window = kw.get("compact_window", 0)
        self.prompt_cache_ttl = kw.get("prompt_cache_ttl", "")
        self.subagent_cache_ttl = kw.get("subagent_cache_ttl", "")
        self.upstream_api_key = kw.get("upstream_api_key", "sk-upstream")


class TestCompactWindow:
    def test_derives_three_percent_below(self):
        assert claude_config.default_compact_window(1_000_000) == 970_000
        assert claude_config.default_compact_window(200_000) == 194_000

    def test_zero_stays_zero(self):
        assert claude_config.default_compact_window(0) == 0


class TestRender:
    def test_minimal_document(self):
        doc = claude_config.render(
            claude_config.ClaudeConfigSpec(
                base_url="http://127.0.0.1:18402", model="m-1"
            )
        )
        assert doc == {
            "env": {
                "ANTHROPIC_BASE_URL": "http://127.0.0.1:18402",
                "ANTHROPIC_AUTH_TOKEN": "local",
                "ANTHROPIC_MODEL": "m-1",
            }
        }

    def test_optional_fields_are_omitted_when_unset(self):
        doc = claude_config.render(
            claude_config.ClaudeConfigSpec(base_url="http://127.0.0.1:1", model="m")
        )
        env = doc["env"]
        assert "CLAUDE_CODE_MAX_CONTEXT_TOKENS" not in env
        assert "CLAUDE_CODE_AUTO_COMPACT_WINDOW" not in env
        assert "promptCacheTtl" not in doc
        assert "subagentPromptCacheTtl" not in doc

    def test_full_document(self):
        inst = FakeInstance(
            target_model="deepseek-v4.1-flash-1017[1m]",
            context_window=1_000_000,
            prompt_cache_ttl="1h",
            subagent_cache_ttl="5m",
        )
        doc = claude_config.render_for_instance(inst)
        env = doc["env"]
        assert env["ANTHROPIC_MODEL"] == "deepseek-v4.1-flash-1017[1m]"
        assert env["CLAUDE_CODE_MAX_CONTEXT_TOKENS"] == "1000000"
        assert env["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] == "970000"
        assert doc["promptCacheTtl"] == "1h"
        assert doc["subagentPromptCacheTtl"] == "5m"

    def test_tier_overrides_only_when_set(self):
        inst = FakeInstance(target_model="base", client_opus_model="opus-x")
        env = claude_config.render_for_instance(inst)["env"]
        assert env["ANTHROPIC_DEFAULT_OPUS_MODEL"] == "opus-x"
        # Unset tiers inherit ANTHROPIC_MODEL, so they stay unwritten.
        assert "ANTHROPIC_DEFAULT_SONNET_MODEL" not in env

    def test_client_model_beats_target_model(self):
        inst = FakeInstance(target_model="upstream-name", client_model="claude-name")
        assert claude_config.render_for_instance(inst)["env"]["ANTHROPIC_MODEL"] == "claude-name"

    def test_upstream_key_written_when_requested(self):
        # The proxy never checks the token; writing the real key makes the
        # generated file self-describing, which is the default in the UI.
        inst = FakeInstance()
        assert claude_config.render_for_instance(inst, upstream_key=True)["env"][
            "ANTHROPIC_AUTH_TOKEN"
        ] == "sk-upstream"
        # The placeholder remains available for setups that must not persist
        # the key to disk.
        assert claude_config.render_for_instance(inst)["env"]["ANTHROPIC_AUTH_TOKEN"] == "local"

    def test_explicit_compact_window_wins(self):
        inst = FakeInstance(context_window=200_000, compact_window=150_000)
        env = claude_config.render_for_instance(inst)["env"]
        assert env["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] == "150000"


class TestSettingsFile:
    @pytest.fixture
    def claude_dir(self, monkeypatch, tmp_path):
        directory = tmp_path / "claude"
        directory.mkdir()
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(directory))
        return directory

    def test_bare_filename_resolves_inside_claude_dir(self, claude_dir):
        assert claude_settings_file.resolve_path("x.json") == claude_dir / "x.json"

    def test_absolute_path_is_kept(self, tmp_path):
        target = tmp_path / "elsewhere" / "y.json"
        assert claude_settings_file.resolve_path(str(target)) == target

    def test_listing_reports_base_url_and_main_settings(self, claude_dir):
        (claude_dir / "settings.json").write_text(
            json.dumps({"env": {"ANTHROPIC_BASE_URL": "https://upstream"}}), encoding="utf-8"
        )
        (claude_dir / "broken.json").write_text("not json", encoding="utf-8")
        items = {i["name"]: i for i in claude_settings_file.list_settings_files()}
        assert items["settings.json"]["is_main_settings"] is True
        assert items["settings.json"]["base_url"] == "https://upstream"
        # A malformed file is still listed so the user can find and fix it.
        assert items["broken.json"]["has_env"] is False

    def test_write_fragment_merges_instead_of_replacing(self, claude_dir):
        path = claude_dir / "keep.json"
        path.write_text(
            json.dumps({"env": {"KEEP_ME": "1", "ANTHROPIC_MODEL": "old"}, "permissions": {"x": 1}}),
            encoding="utf-8",
        )
        claude_settings_file.write_fragment(
            path,
            {"env": {"ANTHROPIC_MODEL": "new", "ANTHROPIC_BASE_URL": "http://127.0.0.1:1"}},
        )
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["env"]["KEEP_ME"] == "1"
        assert data["env"]["ANTHROPIC_MODEL"] == "new"
        assert data["permissions"] == {"x": 1}

    def test_remove_managed_keys_leaves_other_content(self, claude_dir):
        path = claude_dir / "unbind.json"
        path.write_text(
            json.dumps({"env": {"ANTHROPIC_MODEL": "m", "MINE": "keep"}}), encoding="utf-8"
        )
        claude_settings_file.remove_managed_keys(path, {"ANTHROPIC_MODEL"})
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["env"] == {"MINE": "keep"}

    def test_remove_managed_keys_drops_empty_env(self, claude_dir):
        path = claude_dir / "empty.json"
        path.write_text(json.dumps({"env": {"ANTHROPIC_MODEL": "m"}}), encoding="utf-8")
        claude_settings_file.remove_managed_keys(path, {"ANTHROPIC_MODEL"})
        assert "env" not in json.loads(path.read_text(encoding="utf-8"))
