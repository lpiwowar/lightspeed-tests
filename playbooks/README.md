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
* `lightspeed_model`: (String) Goose model to use for Goose API tests. Default value: empty.
* `openshift_service_account_token_path`: (String) Path to the service-account token used for OpenShift pod/exec calls. Default value: `/var/run/secrets/kubernetes.io/serviceaccount/token`
* `openshift_api_server_ca_cert_path`: (String) Path to the cluster CA certificate used for OpenShift pod/exec calls. Default value: `/var/run/secrets/kubernetes.io/serviceaccount/ca.crt`
* `openshift_kubeconfig_path`: (String) Optional kubeconfig path for OpenShift pod/exec calls outside the cluster. Default value: empty.
* `openshift_timeout`: (Integer) Timeout in seconds for OpenShift API and pod/exec calls. Default value: `300`
* `openstack_assistant_pod_namespace`: (String) Namespace containing the OpenStack Assistant pod. Default value: `openstack`
* `openstack_assistant_pod_name`: (String) Name of the OpenStack Assistant pod. Default value: `openstack-assistant`
* `test_question`: (String) Sample question for testing. Default value: `What is OpenStack?`
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
    # OpenStack Assistant pod-exec test settings
    openshift_service_account_token_path: "/var/run/secrets/kubernetes.io/serviceaccount/token"
    openshift_api_server_ca_cert_path: "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
    # Set when running outside the cluster; otherwise leave empty.
    openshift_kubeconfig_path: ""
    openshift_timeout: 300
    openstack_assistant_pod_namespace: "openstack"
    openstack_assistant_pod_name: "openstack-assistant"
    test_question: "What is OpenStack?"
    # Pytest options
    pytest_args: "-v --tb=short --color=yes"
    # JUnit XML output path (test-operator writable location)
    junit_xml_path: "/var/lib/AnsibleTests/external_files/test-results.xml"
```
Run `oc apply -f lightspeed-ansible-test-cr.yaml` to run the test suite.
