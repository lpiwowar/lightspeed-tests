# rhos-lightspeed-tests

Test suite for RHOS Lightspeed in RHOSO environments.

## Installation

Python 3.12 is required. Install from a cloned repo:

```bash
uv sync
source .venv/bin/activate
```

Or install the standalone command directly from GitHub:

```bash
uv tool install --python 3.12 git+https://github.com/openstack-k8s-operators/lightspeed-tests.git
```

The package includes the tests and their default configuration. Once installed,
`lightspeed-tests` works from any directory. `lightspeed-test` is also available
as an alias.

## Configuration

The tests load the default config from `src/rhos_lightspeed_tests/default.yaml`.
Configure the service through environment variables:

```bash
export RHOS_LIGHTSPEED_URL="https://localhost:8443"
export RHOS_LIGHTSPEED_TOKEN="<Lightspeed bearer token>"
export RHOS_LIGHTSPEED_MODEL="<same value as Goose LIGHTSPEED_MODEL>"
```

`RHOS_LIGHTSPEED_URL` and `RHOS_LIGHTSPEED_MODEL` can override the URL and model
from the default config. Use a service origin without `/v1`. With the test-operator
playbook, supply `lightspeed_model`. For Goose API tests, set the model to the Goose
container's `LIGHTSPEED_MODEL` value.

If you run the tests outside of OpenShift, you need to have a running port forward
for the `lightspeed-app-server` service. For example:

```
oc port-forward -n openstack-lightspeed svc/lightspeed-app-server 8443:8443
```

## Running tests

The command accepts pytest options and returns pytest's exit status. In a uv
checkout, prefix the commands below with `uv run`.

```bash
# Run all tests
lightspeed-tests

# Run integration tests only
lightspeed-tests -k integration

# Run one installed test module
lightspeed-tests rhos_lightspeed_tests.tests.integration.test_query_endpoint

# Run one test file from a checkout
lightspeed-tests src/rhos_lightspeed_tests/tests/integration/test_query_endpoint.py

# Run Goose API tests
lightspeed-tests -k goose

# Run tests for OpenStack Assistant (goose). This requires either in-cluster
# service-account credentials or a kubeconfig with pod/exec access. See the
# following environment variables configure the OpenStack Assistant test:
#   * RHOS_LIGHTSPEED_OPENSHIFT_SERVICE_ACCOUNT_TOKEN_PATH: sets the path to
#     the in-cluster service-account token used to authenticate pod/exec calls.
#   * RHOS_LIGHTSPEED_OPENSHIFT_API_SERVER_CA_CERT_PATH: sets the path to
#     the cluster CA certificate used to verify the API server for pod/exec calls.
#   * RHOS_LIGHTSPEED_OPENSHIFT_KUBECONFIG_PATH: sets the kubeconfig used for
#     pod/exec access when the test is run outside the cluster.
#   * RHOS_LIGHTSPEED_OPENSHIFT_TIMEOUT: sets the maximum time, in seconds, the
#     test allows for OpenShift API and pod/exec operations.
#   * RHOS_LIGHTSPEED_OPENSTACK_ASSISTANT_POD_NAMESPACE: sets the namespace of
#     the OpenStack Assistant pod that the test connects to.
#   * RHOS_LIGHTSPEED_OPENSTACK_ASSISTANT_POD_NAME: sets the name of the
#     OpenStack Assistant pod that the test connects to.
lightspeed-tests -m openstack_assistant

# Skip Goose tests that require a real LLM's answer content
lightspeed-tests -k goose -m "not llm_dependent"

# Verify test discovery without contacting a service
lightspeed-tests --collect-only

# Choose a different JUnit XML report path
lightspeed-tests --junitxml=results.xml
```

Integration tests require a deployed Lightspeed service. Goose tests also
require a configured model.

## Adding tests

1. Create test file in appropriate directory:
   - `src/rhos_lightspeed_tests/tests/unit/` - Fast, isolated suite tests
   - `src/rhos_lightspeed_tests/tests/integration/` - Tests requiring deployed service
   - `src/rhos_lightspeed_tests/tests/e2e/` - Full end-to-end scenarios

2. Use the shared fixtures from `conftest.py`:
   ```python
   class TestMyFeature:
       def test_something(self, client, config):
           response = client.query("my question")
           assert response.status_code == 200
   ```

3. Run: `uv run lightspeed-tests -k test_my_feature`

Build a source distribution and wheel with `uv build`.

## Project structure

```
rhos-lightspeed-tests/
├── src/rhos_lightspeed_tests/       # Installed package
│   ├── __init__.py                  # lightspeed-tests entry point
│   ├── client.py                    # API client (query, health)
│   ├── config.py                    # Loads defaults and environment overrides
│   ├── default.yaml                 # Bundled default configuration
│   ├── pytest.ini                   # Bundled pytest settings and default suite
│   └── tests/
│       ├── conftest.py              # Shared fixtures (config, client)
│       ├── unit/
│       ├── integration/
│       └── e2e/
├── pyproject.toml
└── README.md
```
