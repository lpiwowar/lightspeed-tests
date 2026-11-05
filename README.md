# rhos-lightspeed-tests

Test suite for RHOS Lightspeed in RHOSO environments.

## Installation

Python 3.12 is required. Install from a cloned repo:

```bash
uv sync --python 3.12
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

The goose pod e2e test (see below) is configured with:

```bash
export RHOS_LIGHTSPEED_ASSISTANT_NAMESPACE="openstack"
export RHOS_LIGHTSPEED_ASSISTANT_NAME="openstack-assistant"
export RHOS_LIGHTSPEED_ASSISTANT_QUESTION="What is nova in OpenStack?"
export RHOS_LIGHTSPEED_ASSISTANT_POD_TIMEOUT=120  # wait for the pod, seconds
export RHOS_LIGHTSPEED_ASSISTANT_TIMEOUT=300      # goose run exec, seconds
```

All of them have sensible defaults and can usually be left unset.

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

# Skip Goose tests that require a real LLM's answer content
lightspeed-tests -k goose -m "not llm_dependent"

# Verify test discovery without contacting a service
lightspeed-tests --collect-only

# Choose a different JUnit XML report path
lightspeed-tests --junitxml=results.xml
```

Integration tests require a deployed Lightspeed service. Goose tests also
require a configured model.

## OpenStack Assistant (goose pod) e2e test

`tests/e2e/test_goose_pod.py` runs against a cluster where the OpenStack
Assistant is deployed, e.g. with `make openstack_assistant` from an
[install_yamls](https://github.com/openstack-k8s-operators/install_yamls)
checkout (control plane with `openstackclient` and Lightspeed first; the
target enables the OpenStack MCP server automatically). The test execs into
the assistant pod and runs goose in headless mode:

```
goose run -t "<question>" --output-format json --no-session
```

It asserts that the run completes with a non-empty answer. The exec-launched
goose inherits the image's `GOOSE_ADDITIONAL_CONFIG_FILES`, so the rendered
lightspeed provider and MCP servers apply. The elapsed time is printed and
included in the JUnit report (via `junit_logging = system-out`) to help tune
the timeouts.

Run it locally with cluster access (`KUBECONFIG`, or in-cluster credentials
inside the AnsibleTest pod):

```bash
uv run lightspeed-tests src/rhos_lightspeed_tests/tests/e2e/test_goose_pod.py -v
```

The test **skips** when the assistant pod is not deployed — including when
there are no usable cluster credentials or the lookup is not permitted —
so bare runs stay green in environments without the assistant. Check the
skip reason to tell "not deployed" from a missing-permissions skip.

### RBAC for the test-operator run

The AnsibleTest pod's service account needs, in the assistant's namespace
(`openstack` by default), permission to find the pod and exec into it:

```yaml
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: lightspeed-goose-pod-test-role
  namespace: openstack
rules:
- apiGroups: [""]
  resources: ["pods"]
  verbs: ["get", "list"]
- apiGroups: [""]
  resources: ["pods/exec"]
  verbs: ["create"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: lightspeed-goose-pod-test-role-binding
  namespace: openstack
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: Role
  name: lightspeed-goose-pod-test-role
subjects:
- kind: ServiceAccount
  name: <AnsibleTest pod's service account>
  namespace: <AnsibleTest CR's namespace>
```

Confirm the subject with
`oc get pod <ansibletest pod> -o jsonpath='{.spec.serviceAccountName}'`.
Current test-operator versions give each AnsibleTest CR a dedicated
`ansibletest-<CR-name>` service account in the CR's namespace; older
versions used the namespace's `default` service account. Without this
role the e2e test skips with a "Cannot look for the assistant pod" reason
(the pod lookup is forbidden).

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
│   ├── cluster.py                   # Kubernetes access (pod exec for e2e)
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
