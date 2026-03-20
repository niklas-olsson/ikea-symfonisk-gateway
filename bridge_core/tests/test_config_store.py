"""Tests for the ConfigStore component."""

import json
from pathlib import Path

import pytest
from bridge_core.core.config_store import ConfigStore


@pytest.fixture
def config_store(tmp_path: Path) -> ConfigStore:
    """Provide a ConfigStore instance using a temporary database file."""
    db_path = tmp_path / "test_config.db"
    return ConfigStore(db_path)


def test_config_store_get_set_delete(config_store: ConfigStore) -> None:
    """Test basic CRUD operations on the ConfigStore."""
    config_store.set("key1", "value1")
    config_store.set("key2", {"a": 1, "b": 2})

    assert config_store.get("key1") == "value1"
    assert config_store.get("key2") == {"a": 1, "b": 2}
    assert config_store.get("non_existent", "default") == "default"

    config_store.delete("key1")
    assert config_store.get("key1") is None


def test_config_store_list_all(config_store: ConfigStore) -> None:
    """Test listing all configuration entries."""
    data = {"key1": "val1", "key2": 100}
    for k, v in data.items():
        config_store.set(k, v)

    assert config_store.list_all() == data


def test_config_store_persistence(tmp_path: Path) -> None:
    """Test that configuration data persists across ConfigStore instances."""
    db_path = tmp_path / "persistent_config.db"

    # Set data in the first store
    store1 = ConfigStore(db_path)
    store1.set("persist_key", "persist_val")

    # Access data from a second store pointing to the same file
    store2 = ConfigStore(db_path)
    assert store2.get("persist_key") == "persist_val"


def test_config_store_file_ops(config_store: ConfigStore, tmp_path: Path) -> None:
    """Test loading and saving configuration from/to a file."""
    # Test saving to file
    config_store.set("key_f", "val_f")
    export_path = tmp_path / "config_export.json"
    config_store.save_to_file(export_path)

    with open(export_path) as f:
        data = json.load(f)
        assert data == {"key_f": "val_f"}

    # Test loading from file
    import_path = tmp_path / "config_import.json"
    import_data = {"imported_key": "imported_val"}
    with open(import_path, "w") as f:
        json.dump(import_data, f)

    config_store.load_from_file(import_path)
    assert config_store.get("imported_key") == "imported_val"
    assert config_store.get("key_f") == "val_f"
