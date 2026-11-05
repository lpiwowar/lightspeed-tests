# Lightspeed Tests Playbooks

Ansible playbooks to run the test suite.

## `run_lightspeed_tests.yaml`

Executes the RHOS Lightspeed test suite using `lightspeed-tests`. The playbook:

1. Installs the [uv](https://astral.sh/uv) package manager if not already available
2. Installs Python 3.12 using uv
3. Verifies the test suite directory exists (cloned by test-operator)
4. Reads the Kubernetes service account token for authentication
5. Installs Python dependencies using uv
6. Runs `lightspeed-tests` with configurable pytest arguments
7. Generates JUnit XML test results for reporting

Test results are stored in the location specified by `junit_xml_path`, which the test-operator can consume for reporting.

### Parameters

All parameters can be overridden via the `AnsibleTest` custom resource when running through test-operator.

* `lightspeed_url`: (String) URL of the Lightspeed service to test against. Default value: `https://lightspeed-app-server.openstack-lightspeed.svc.cluster.local:8443`
* `lightspeed_timeout`: (Integer) Timeout in seconds for Lightspeed API requests. Default value: `30`
* `test_question`: (String) Sample question for testing. Default value: `What is OpenStack?`
* `assistant_namespace`: (String) Namespace where the OpenStack Assistant (goose pod) is deployed. The e2e goose pod test skips when the assistant is not deployed there. Default value: `openstack`
* `assistant_name`: (String) Name of the OpenStackAssistant custom resource. Default value: `openstack-assistant`
* `assistant_question`: (String) Question passed to `goose run -t` in the assistant pod. Default value: `What is nova in OpenStack?`
* `assistant_pod_timeout`: (Integer) Seconds to wait for the assistant pod to be Running and ready. Default value: `120`
* `assistant_timeout`: (Integer) Seconds for the `goose run` exec in the assistant pod; LLM round trips and tool use can be slow. Default value: `300`
* `pytest_args`: (String) Additional arguments to pass to pytest. Default value: `-v --tb=short --color=yes`
* `junit_xml_path`: (String) Path where JUnit XML test results will be written. The test-operator provides writable storage at this location. Default value: `/var/lib/AnsibleTests/external_files/test-results.xml`
* `test_suite_path`: (String) Path to the test suite directory. The test-operator clones git repositories to this location. Default value: `{{ ansible_env.HOME }}/ansible`

### Usage

This playbook is designed to be executed by the test-operator via an `AnsibleTest` custom resource.

1\. Create a file `lightspeed-auth-resources.yaml` with the following content:

```yaml
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: lightspeed-test-role
rules:
- nonResourceURLs: ["/ls-access"]
  verbs: ["get"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: lightspeed-test-role-binding
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: lightspeed-test-role
subjects:
- kind: ServiceAccount
  name: default
  namespace: openstack-lightspeed
```

And run `oc apply -f lightspeed-auth-resources.yaml`. This will grant Lightspeed service access  to `default` service account used by the test-operator.

When the OpenStack Assistant (goose pod) e2e test should also run, the
AnsibleTest pod's service account additionally needs, in the assistant's
namespace, permission to find the assistant pod and exec into it. Create a
file `lightspeed-goose-pod-resources.yaml` (adjust the subject to the
service account the AnsibleTest pod actually uses; see the README section
"RBAC for the test-operator run"):

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
  name: default
  namespace: openstack-lightspeed
```

Without this role the goose pod e2e test skips (it cannot look for the
assistant pod); the rest of the suite is unaffected. Deploy the assistant
with `make openstack_assistant` (install_yamls) before running the suite
when the test should execute.

2\. Create a file `lightspeed-dummy-resources.yaml` with the following content:

```yaml
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: test-operator-dummy-config
  namespace: openstack-lightspeed
  labels:
    app: lightspeed-tests
    persistent: "true"
    managed-by: test-operator
data:
  clouds.yaml: |
    # Dummy file - not used by lightspeed tests
    # Required by test-operator but lightspeed tests don't need OpenStack
---
apiVersion: v1
kind: Secret
metadata:
  name: test-operator-dummy-secret
  namespace: openstack-lightspeed
  labels:
    app: lightspeed-tests
    persistent: "true"
    managed-by: test-operator
type: Opaque
stringData:
  secure.yaml: |
    # Dummy file - not used by lightspeed tests
    # Required by test-operator but lightspeed tests don't need OpenStack
---
apiVersion: v1
kind: Secret
metadata:
  name: dataplane-ansible-ssh-private-key-secret
  namespace: openstack-lightspeed
  labels:
    app: lightspeed-tests
    persistent: "true"
    managed-by: test-operator
type: Opaque
stringData:
  ssh-privatekey: |
    # Dummy SSH key - not used by lightspeed tests
```

Run `oc apply -f lightspeed-dummy-resources.yaml`. This will create empty configs that are expected by the test-operator but are not actually used for anything. Without the dummy resources, the test operator will not run the tests.

3\. Create an `AnsibleTest` CR file `lightspeed-ansible-test-cr.yaml` with the following content:

```yaml
---
apiVersion: test.openstack.org/v1beta1
kind: AnsibleTest
metadata:
  name: lightspeed-tests
  namespace: openstack-lightspeed
  labels:
    app.kubernetes.io/name: test-operator
    app.kubernetes.io/managed-by: kustomize
spec:
  openStackConfigMap: test-operator-dummy-config
  openStackConfigSecret: test-operator-dummy-secret
  ansibleGitRepo: https://github.com/openstack-k8s-operators/lightspeed-tests
  ansibleGitBranch: main
  ansiblePlaybookPath: playbooks/run_lightspeed_tests.yaml

  # Ansible inventory - run tests on localhost
  ansibleInventory: |
    localhost ansible_connection=local ansible_python_interpreter=python3

  debug: true
  containerImage: quay.io/podified-antelope-centos9/openstack-ansible-tests:current-podified

  # Storage class for any PVCs (test results, logs)
  # if running in CRC, use crc-csi-hostpath-provisioner
  # if running in OpenShift cluster, use: local-storage
  storageClass: crc-csi-hostpath-provisioner

  # Enable privileged mode to mount service account token
  privileged: true
  # Resource limits for the test pod
  resources:
    limits:
      cpu: 2000m
      memory: 2Gi
    requests:
      cpu: 1000m
      memory: 1Gi

  # Variables passed to Ansible playbook
  ansibleVarFiles: |
    ---
    # Lightspeed service URL (defaults to in-cluster service)
    lightspeed_url: "https://lightspeed-app-server.openstack-lightspeed.svc.cluster.local:8443"
    lightspeed_timeout: 30
    # Same value as the Goose container's LIGHTSPEED_MODEL
    lightspeed_model: "<your configured model>"
    test_question: "What is OpenStack?"
    # Pytest options
    pytest_args: "-v --tb=short --color=yes"
    # JUnit XML output path (test-operator writable location)
    junit_xml_path: "/var/lib/AnsibleTests/external_files/test-results.xml"
```
Run `oc apply -f lightspeed-ansible-test-cr.yaml` to run the test suite.
