"""Shared pytest fixtures."""

import pytest

from rhos_lightspeed_tests.client import RHOSLightspeedClient
from rhos_lightspeed_tests.config import load_config


@pytest.fixture
def config():
    return load_config()


@pytest.fixture
def client(config):
    ls = config["rhos_lightspeed"]
    return RHOSLightspeedClient(
        base_url=ls["base_url"],
        api_prefix=ls["api_prefix"],
        token=ls["token"],
        timeout=ls["timeout"],
        verify_tls=ls["verify_tls"],
    )
