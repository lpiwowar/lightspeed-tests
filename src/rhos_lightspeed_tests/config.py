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

    prometheus = config.setdefault("prometheus", {})
    prometheus["url"] = os.environ.get(
        "RHOS_LIGHTSPEED_PROMETHEUS_URL", prometheus.get("url", "")
    )
    prometheus["ca_cert_path"] = os.environ.get(
        "RHOS_LIGHTSPEED_PROMETHEUS_CA_CERT_PATH",
        prometheus.get("ca_cert_path", ""),
    )
    prometheus["api_timeout"] = float(
        os.environ.get(
            "RHOS_LIGHTSPEED_PROMETHEUS_API_TIMEOUT",
            prometheus.get("api_timeout", 10),
        )
    )
    prometheus["scrape_timeout"] = float(
        os.environ.get(
            "RHOS_LIGHTSPEED_PROMETHEUS_SCRAPE_TIMEOUT",
            prometheus.get("scrape_timeout", 120),
        )
    )

    return config
