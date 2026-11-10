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

    openshift = config["openshift"]
    openshift["service_account_token_path"] = os.environ.get(
        "RHOS_LIGHTSPEED_OPENSHIFT_SERVICE_ACCOUNT_TOKEN_PATH",
        openshift.get("service_account_token_path", ""),
    )
    openshift["api_server_ca_cert_path"] = os.environ.get(
        "RHOS_LIGHTSPEED_OPENSHIFT_API_SERVER_CA_CERT_PATH",
        openshift.get("api_server_ca_cert_path", ""),
    )
    openshift["kubeconfig_path"] = os.environ.get(
        "RHOS_LIGHTSPEED_OPENSHIFT_KUBECONFIG_PATH",
        openshift.get("kubeconfig_path"),
    )
    openshift["timeout"] = int(
        os.environ.get(
            "RHOS_LIGHTSPEED_OPENSHIFT_TIMEOUT",
            openshift.get("timeout", 300),
        )
    )

    assistant_pod = config["openstack_assistant_pod"]
    assistant_pod["namespace"] = os.environ.get(
        "RHOS_LIGHTSPEED_OPENSTACK_ASSISTANT_POD_NAMESPACE",
        assistant_pod.get("namespace", ""),
    )
    assistant_pod["name"] = os.environ.get(
        "RHOS_LIGHTSPEED_OPENSTACK_ASSISTANT_POD_NAME",
        assistant_pod.get("name", ""),
    )

    return config
