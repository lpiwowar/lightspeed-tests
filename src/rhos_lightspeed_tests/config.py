"""Configuration management for RHOS Lightspeed tests."""

import os
from pathlib import Path

import yaml

DEFAULT_CONFIG_PATH = Path(__file__).with_name("default.yaml")


def _env_int(name: str, default: int) -> int:
    """Read an integer setting from the environment, or use the default."""
    value = os.environ.get(name)
    if not value:
        return int(default)
    return int(value)


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

    goose_pod = config.setdefault("goose_pod", {})
    goose_pod["namespace"] = os.environ.get(
        "RHOS_LIGHTSPEED_ASSISTANT_NAMESPACE",
        goose_pod.get("namespace", "openstack"),
    )
    goose_pod["assistant_name"] = os.environ.get(
        "RHOS_LIGHTSPEED_ASSISTANT_NAME",
        goose_pod.get("assistant_name", "openstack-assistant"),
    )
    goose_pod["pod_label"] = goose_pod.get("pod_label", "openstackassistant")
    goose_pod["question"] = os.environ.get(
        "RHOS_LIGHTSPEED_ASSISTANT_QUESTION",
        goose_pod.get("question", "What is nova in OpenStack?"),
    )
    goose_pod["pod_timeout"] = _env_int(
        "RHOS_LIGHTSPEED_ASSISTANT_POD_TIMEOUT",
        goose_pod.get("pod_timeout", 120),
    )
    goose_pod["timeout"] = _env_int(
        "RHOS_LIGHTSPEED_ASSISTANT_TIMEOUT",
        goose_pod.get("timeout", 300),
    )

    return config
