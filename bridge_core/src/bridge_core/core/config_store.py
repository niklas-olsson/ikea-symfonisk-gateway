"""Configuration store with SQLite persistence."""

import json
import sqlite3
from pathlib import Path
from typing import Any


class ConfigStore:
    """Persistent configuration store using SQLite."""

    def __init__(self, db_path: str | Path = "config.db"):
        self.db_path = Path(db_path)
        self._init_db()

    def _init_db(self) -> None:
        """Initialize the SQLite database."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT)")
            conn.commit()

    def get(self, key: str, default: Any = None) -> Any:
        """Get a configuration value."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT value FROM config WHERE key = ?", (key,))
            row = cursor.fetchone()
            if row:
                return json.loads(row[0])
            return default

    def set(self, key: str, value: Any) -> None:
        """Set a configuration value."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)",
                (key, json.dumps(value)),
            )
            conn.commit()

    def delete(self, key: str) -> None:
        """Delete a configuration value."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM config WHERE key = ?", (key,))
            conn.commit()

    def list_all(self) -> dict[str, Any]:
        """List all configuration values."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT key, value FROM config")
            return {row[0]: json.loads(row[1]) for row in cursor.fetchall()}

    def load_from_file(self, path: str | Path) -> None:
        """Load configuration from a file."""
        path = Path(path)
        if not path.exists():
            return
        with open(path) as f:
            config = json.load(f)
            for key, value in config.items():
                self.set(key, value)

    def save_to_file(self, path: str | Path) -> None:
        """Save current configuration to a file."""
        config = self.list_all()
        with open(path, "w") as f:
            json.dump(config, f, indent=4)
