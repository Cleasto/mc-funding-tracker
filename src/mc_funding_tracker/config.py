"""Configuration management for mc-funding-tracker."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict

import yaml

DEFAULT_CONFIG = {
    "anthropic_api_key": "",
    "sec_contact_email": "",
    "claude_model": "claude-sonnet-5",
}

CONFIG_DIR = Path.home() / ".config" / "mc-funding-tracker"
CONFIG_FILE = CONFIG_DIR / "config.yaml"
LOG_FILE = CONFIG_DIR / "tracker.log"
DB_PATH = CONFIG_DIR / "tracker.db"


def load_config() -> Dict[str, Any]:
    """Load configuration from file, merging with defaults."""
    config = DEFAULT_CONFIG.copy()

    if CONFIG_FILE.exists():
        with open(CONFIG_FILE) as f:
            user_config = yaml.safe_load(f) or {}
            config.update(user_config)

    if not config["anthropic_api_key"]:
        config["anthropic_api_key"] = os.environ.get("ANTHROPIC_API_KEY", "")

    return config


def save_config(config: Dict[str, Any]) -> None:
    """Save configuration to file."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    save_config = config.copy()
    if os.environ.get("ANTHROPIC_API_KEY"):
        save_config["anthropic_api_key"] = ""

    with open(CONFIG_FILE, "w") as f:
        yaml.dump(save_config, f, default_flow_style=False)
