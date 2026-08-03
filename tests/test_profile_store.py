"""Tests for the profile_store module."""

from pathlib import Path

import pytest
import tomli_w

from cc_convert.profile_store import (
    InstanceProfile,
    ManagerConfig,
    ProfileStore,
    build_worker_settings,
    load_profile_store,
    mask_api_key,
    migrate_v1_then_load,
    save_profile_store,
    suggest_port,
)


class TestProfileStore:
    """Tests for ProfileStore, InstanceProfile, and related functions."""

    def test_create_instance_minimal(self):
        """InstanceProfile with minimum required fields sets all correctly."""
        inst = InstanceProfile(
            name="test",
            port=18001,
            upstream_base_url="https://api.test.com/v1",
            upstream_api_key="sk-test1234",
            target_model="gpt-4",
        )
        assert inst.name == "test"
        assert inst.port == 18001
        assert inst.upstream_base_url == "https://api.test.com/v1"
        assert inst.upstream_api_key == "sk-test1234"
        assert inst.target_model == "gpt-4"
        assert inst.auto_start is True
        assert isinstance(inst.id, str) and inst.id.startswith("inst_")

    def test_instance_key_excluded_from_dump(self):
        """model_dump() excludes upstream_api_key; live object retains it."""
        inst = InstanceProfile(
            name="test",
            port=18001,
            upstream_base_url="https://api.test.com/v1",
            upstream_api_key="sk-test1234",
            target_model="gpt-4",
        )
        dumped = inst.model_dump()
        assert "upstream_api_key" not in dumped
        # _instance_to_response (in routes.py) derives key_configured
        # from the live object, not from model_dump()
        assert inst.upstream_api_key == "sk-test1234"
        assert bool(inst.upstream_api_key) is True

    def test_mask_api_key(self):
        """mask_api_key returns masked string or None."""
        assert mask_api_key("sk-abc1234") == "***1234"
        assert mask_api_key("ab") == "***"
        assert mask_api_key(None) is None

    def test_suggest_port_avoids_manager_and_instance_ports(self, monkeypatch):
        """suggest_port excludes both manager and instance ports."""
        monkeypatch.setattr(
            "cc_convert.profile_store._find_free_port",
            lambda start=18000, end=29999, exclude=None: 18500,
        )
        store = ProfileStore(
            manager=ManagerConfig(port=18000),
            instances=[
                InstanceProfile(
                    name="test",
                    port=18001,
                    upstream_base_url="https://api.test.com/v1",
                    upstream_api_key="sk-test1234",
                    target_model="gpt-4",
                )
            ],
        )
        port = suggest_port(store)
        assert port not in {18000, 18001}
        assert port == 18500

    def test_build_worker_settings(self):
        """build_worker_settings merges global defaults with instance fields."""
        store = ProfileStore(
            manager=ManagerConfig(port=18000),
            global_timeout=60,
            instances=[
                InstanceProfile(
                    name="test",
                    port=18001,
                    upstream_base_url="https://api.test.com/v1",
                    upstream_api_key="sk-test1234",
                    target_model="gpt-4",
                )
            ],
        )
        inst = store.instances[0]
        settings = build_worker_settings(store, inst)
        assert settings["openai_base_url"] == "https://api.test.com/v1"
        assert settings["port"] == 18001
        assert settings["default_model"] == "gpt-4"
        assert settings["model_mapping"] == []
        assert settings["host"] == "127.0.0.1"
        assert settings["request_timeout"] == 60
        assert settings["openai_api_key"] == "sk-test1234"
        assert settings["log_level"] == "INFO"

    def test_store_save_load_roundtrip(self, tmp_path):
        """save_profile_store / load_profile_store roundtrip preserves all fields."""
        config_path = tmp_path / "config.toml"
        original = ProfileStore(
            manager=ManagerConfig(port=18000),
            instances=[
                InstanceProfile(
                    name="test-instance",
                    port=18001,
                    upstream_base_url="https://api.test.com/v1",
                    upstream_api_key="sk-test1234",
                    target_model="gpt-4",
                )
            ],
            global_timeout=120,
        )
        rev_before = original.revision
        save_profile_store(original, config_path)
        assert config_path.exists()

        loaded = load_profile_store(config_path)
        assert loaded.manager.port == 18000
        assert len(loaded.instances) == 1
        inst = loaded.instances[0]
        assert inst.name == "test-instance"
        assert inst.port == 18001
        assert inst.upstream_base_url == "https://api.test.com/v1"
        assert inst.upstream_api_key == "sk-test1234"
        assert inst.target_model == "gpt-4"
        assert loaded.revision == rev_before + 1

    def test_v1_migration(self, tmp_path, monkeypatch):
        """migrate_v1_then_load converts flat v1 TOML to v2 ProfileStore."""
        ports = iter([18500, 18501])
        monkeypatch.setattr(
            "cc_convert.profile_store._find_free_port",
            lambda start=18000, end=29999, exclude=None: next(ports),
        )

        config_path = tmp_path / "config.toml"
        old_config = {
            "openai_api_key": "sk-old-key",
            "openai_base_url": "https://api.openai.com/v1",
            "default_model": "gpt-4",
            "port": 8080,
            "request_timeout": 60,
        }
        with open(config_path, "wb") as f:
            tomli_w.dump(old_config, f)

        store = migrate_v1_then_load(config_path)

        # .v1.bak backup was created
        bak_path = config_path.with_suffix(".toml.v1.bak")
        assert bak_path.exists()

        assert store.schema_version == 2
        assert len(store.instances) == 1
        assert store.instances[0].target_model == "gpt-4"
        assert store.manager.port != 8080

    def test_key_patch_rejects_masked(self):
        """Masked API keys (starting with ***) are not written to the store."""
        inst = InstanceProfile(
            name="test",
            port=18001,
            upstream_base_url="https://api.test.com/v1",
            upstream_api_key="sk-real-secret",
            target_model="gpt-4",
        )

        # Simulate the routes.py guard (lines 211-220): a key starting with
        # "***" is a masked value that must not replace the real key.
        def _reject_masked(instance: InstanceProfile, new_key: str) -> bool:
            if new_key is not None and isinstance(new_key, str) and new_key.startswith("***"):
                return False  # rejected — routes.py pops it from the patch data
            instance.upstream_api_key = new_key
            return True

        # Masked value is rejected
        assert _reject_masked(inst, "***1234") is False
        assert inst.upstream_api_key == "sk-real-secret"
        assert not inst.upstream_api_key.startswith("***")

        # Valid value is accepted
        assert _reject_masked(inst, "sk-new-key") is True
        assert inst.upstream_api_key == "sk-new-key"

        # Empty string clears the key
        assert _reject_masked(inst, "") is True
        assert inst.upstream_api_key == ""
