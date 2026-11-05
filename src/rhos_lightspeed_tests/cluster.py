"""Kubernetes cluster access for the OpenStack Assistant e2e test.

The tests normally run inside the AnsibleTest pod, where the in-cluster
service-account credentials apply; a local kubeconfig is used as a fallback
so the suite also runs from a workstation with cluster access (KUBECONFIG).
"""

import time

from kubernetes import client, config
from kubernetes.client.rest import ApiException
from kubernetes.stream import stream
from kubernetes.stream.ws_client import WSClient


class AssistantPodNotAvailable(RuntimeError):
    """The assistant pod cannot be discovered in the target namespace.

    Raised when no cluster credentials are usable, when looking for the
    pod is not allowed, or when no pod matches the assistant's labels —
    in short, whenever the environment has no discoverable assistant.
    """


class AssistantPodNotReady(RuntimeError):
    """The assistant pod exists but did not become Running and ready."""


class PodExecTimeout(RuntimeError):
    """Command execution in the assistant pod exceeded the deadline."""


def load_kube_config() -> None:
    """Configure the kubernetes client.

    Prefers the in-cluster service account; falls back to the local
    kubeconfig. Raises AssistantPodNotAvailable when neither is usable.
    """
    try:
        config.load_incluster_config()
    except config.ConfigException:
        try:
            config.load_kube_config()
        except config.ConfigException as exc:
            raise AssistantPodNotAvailable(
                "No usable cluster credentials: no in-cluster service-account "
                "token and no valid kubeconfig. Run the tests inside the "
                "AnsibleTest pod, or set KUBECONFIG for a local run."
            ) from exc


def find_assistant_pod(
    namespace: str,
    pod_label: str,
    timeout: float = 120.0,
    poll_interval: float = 2.0,
) -> client.V1Pod:
    """Return a Running and ready assistant pod, waiting up to timeout.

    Selects pods with the service=<pod_label> label, which the
    openstack-operator sets on the pod it reconciles from the
    OpenStackAssistant CR.

    Raises AssistantPodNotAvailable when the assistant cannot be
    discovered (not deployed, or the lookup is not possible), and
    AssistantPodNotReady when a matching pod exists but never becomes
    Running and ready.
    """
    load_kube_config()
    core = client.CoreV1Api()
    selector = f"service={pod_label}"
    deadline = time.monotonic() + timeout
    seen_pods: set[str] = set()
    reason = f"no pod matches label selector '{selector}' in namespace '{namespace}'"

    while True:
        try:
            pods = core.list_namespaced_pod(
                namespace=namespace, label_selector=selector
            ).items
        except ApiException as exc:
            if exc.status in (403, 404):
                raise AssistantPodNotAvailable(
                    f"Cannot look for the assistant pod in namespace "
                    f"'{namespace}' ({exc.status}: {exc.reason}). Deploy the "
                    f"assistant with 'make openstack_assistant' (install_yamls) "
                    f"and grant the test pod's service account 'get' and 'list' "
                    f"on pods there."
                ) from exc
            raise
        if pods:
            ready = [pod for pod in pods if _is_ready(pod)]
            if ready:
                return ready[0]
            seen_pods.update(_pod_name(pod) for pod in pods)
            reason = "assistant pod not ready: " + ", ".join(
                f"{_pod_name(pod)} (phase {_pod_phase(pod)})" for pod in pods
            )
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            if seen_pods:
                raise AssistantPodNotReady(f"{reason} after waiting {timeout:.0f}s")
            raise AssistantPodNotAvailable(
                f"{reason} after waiting {timeout:.0f}s; deploy the assistant "
                f"with 'make openstack_assistant' (install_yamls)"
            )
        time.sleep(min(poll_interval, remaining))


def _pod_name(pod: client.V1Pod) -> str:
    """The pod's name; listed pods always carry metadata and a name."""
    metadata = pod.metadata
    return metadata.name if metadata and metadata.name else "<unnamed>"


def _pod_phase(pod: client.V1Pod) -> str:
    """The pod's phase, e.g. Running, Pending, or Unknown."""
    status = pod.status
    return status.phase if status and status.phase else "Unknown"


def _is_ready(pod: client.V1Pod) -> bool:
    """True when the pod is Running with a Ready condition."""
    status = pod.status
    if status is None or status.phase != "Running":
        return False
    conditions = status.conditions or []
    return any(
        condition.type == "Ready" and condition.status == "True"
        for condition in conditions
    )


def exec_in_pod(
    pod_name: str,
    namespace: str,
    command: list[str],
    timeout: float,
) -> tuple[int | None, str, str]:
    """Run command in the pod and return (exit code, stdout, stderr).

    The exit code is None when Kubernetes reports no status for the
    command. Raises PodExecTimeout when the deadline passes before the
    command finishes; the message carries the output gathered so far.
    """
    load_kube_config()
    core = client.CoreV1Api()
    response = stream(
        core.connect_get_namespaced_pod_exec,
        pod_name,
        namespace,
        command=command,
        stdin=False,
        stdout=True,
        stderr=True,
        tty=False,
        _preload_content=False,
    )

    stdout: list[str] = []
    stderr: list[str] = []

    def drain() -> None:
        if response.peek_stdout():
            stdout.append(response.read_stdout())
        if response.peek_stderr():
            stderr.append(response.read_stderr())

    deadline = time.monotonic() + timeout
    try:
        while response.is_open():
            response.update(timeout=1)
            drain()
            if time.monotonic() >= deadline:
                raise PodExecTimeout(
                    f"command {command} in pod {namespace}/{pod_name} did not "
                    f"finish within {timeout:.0f}s\n"
                    f"partial stdout:\n{''.join(stdout)}\n"
                    f"partial stderr:\n{''.join(stderr)}"
                )
        drain()
    finally:
        response.close()

    return _exit_code(response), "".join(stdout), "".join(stderr)


def _exit_code(response: WSClient) -> int | None:
    """Best-effort exit code from the exec status channel."""
    try:
        return response.returncode
    except (KeyError, TypeError, ValueError, IndexError):
        return None
