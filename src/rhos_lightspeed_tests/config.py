"""Configuration management for RHOS Lightspeed tests."""

import os
from pathlib import Path

import yaml

DEFAULT_CONFIG_PATH = Path(__file__).with_name("default.yaml")


def load_config(config_path: str | None = None) -> dict:
    path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
    with open(path) as f:
        config = yaml.safe_load(f)

    config["rhos_lightspeed"]["base_url"] = os.environ.get(
        "RHOS_LIGHTSPEED_URL",
        config["rhos_lightspeed"]["base_url"],
    )

    config["rhos_lightspeed"]["token"] = os.environ.get(
        "RHOS_LIGHTSPEED_TOKEN",
        "",
    )

    goose = config.setdefault("goose", {})
    goose["model"] = os.environ.get("RHOS_LIGHTSPEED_MODEL", goose.get("model", ""))

    return config
