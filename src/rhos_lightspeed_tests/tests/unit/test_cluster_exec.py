"""Unit tests for the exec read loop in cluster.exec_in_pod.

Fakes kubernetes' WSClient websocket (channel buffers, update, close and
the returncode property) to verify output collection, deadline handling
and exit-code extraction without a cluster.
"""

import json

import pytest

from rhos_lightspeed_tests import cluster
from rhos_lightspeed_tests.cluster import AssistantPodNotReady, PodExecTimeout
from rhos_lightspeed_tests.cluster import exec_in_pod, find_assistant_pod


class FakeWSClient:
    """Mimics the WSClient methods exec_in_pod relies on.

    Each update() call processes the next event: ("out"|"err", text)
    appends to a channel buffer, ("status", dict) fills the status
    channel, and ("close",) ends the connection.
    """

    def __init__(self, events):
        self._events = list(events)
        self._connected = True
        self._buffers = {}

    def is_open(self):
        return self._connected

    def update(self, timeout=None):
        if not self._connected or not self._events:
            return
        kind, data = self._events.pop(0)
        if kind == "close":
            self._connected = False
        else:
            self._buffers[kind] = self._buffers.get(kind, "") + data

    def _peek(self, channel):
        return self._buffers.get(channel, "")

    def _read(self, channel):
        data = self._buffers.pop(channel, "")
        return data

    def peek_stdout(self, timeout=0):
        return self._peek("out")

    def read_stdout(self, timeout=None):
        return self._read("out")

    def peek_stderr(self, timeout=0):
        return self._peek("err")

    def read_stderr(self, timeout=None):
        return self._read("err")

    @property
    def returncode(self):
        # Same logic as WSClient.returncode: the status channel carries a
        # metav1.Status object; Success means 0, otherwise the exit code
        # is buried in details.causes.
        err = json.loads(self._read("status"))
        if err["status"] == "Success":
            return 0
        return int(err["details"]["causes"][0]["message"])

    def close(self, **kwargs):
        self._connected = False


def _exec(events, monkeypatch, timeout=30):
    """Run exec_in_pod against a FakeWSClient fed with the events."""
    fake = FakeWSClient(events)
    monkeypatch.setattr(cluster, "load_kube_config", lambda: None)
    monkeypatch.setattr(cluster, "stream", lambda *args, **kwargs: fake)
    return exec_in_pod(
        pod_name="openstack-assistant-abcde",
        namespace="openstack",
        command=["goose", "run"],
        timeout=timeout,
    )


class TestExecInPod:
    def test_collects_output_and_success_exit_code(self, monkeypatch):
        exit_code, stdout, stderr = _exec(
            [
                ("out", '{"messages": ['),
                ("out", ']}'),
                ("err", "INFO goose :: config loaded"),
                ("status", json.dumps({"metadata": {}, "status": "Success"})),
                ("close", None),
            ],
            monkeypatch,
        )

        assert exit_code == 0
        assert stdout == '{"messages": []}'
        assert stderr == "INFO goose :: config loaded"

    def test_reports_nonzero_exit_code(self, monkeypatch):
        status = {
            "metadata": {},
            "status": "Failure",
            "reason": "NonZeroExitCode",
            "details": {"causes": [{"reason": "ExitCode", "message": "2"}]},
        }

        exit_code, stdout, stderr = _exec(
            [
                ("err", "goose: error"),
                ("status", json.dumps(status)),
                ("close", None),
            ],
            monkeypatch,
        )

        assert exit_code == 2
        assert stdout == ""
        assert stderr == "goose: error"

    def test_reports_none_without_status(self, monkeypatch):
        exit_code, stdout, stderr = _exec([("out", "orphan"), ("close", None)], monkeypatch)

        assert exit_code is None
        assert stdout == "orphan"

    def test_timeout_raises_with_partial_output(self, monkeypatch):
        with pytest.raises(PodExecTimeout, match="partial stdout"):
            _exec([("out", "still running")], monkeypatch, timeout=0.05)


class TestFindAssistantPod:
    def test_not_ready_pod_fails_after_timeout(self, monkeypatch):
        class Pod:
            class metadata:
                name = "openstack-assistant-abcde"

            class status:
                phase = "Pending"
                conditions = []

        class CoreV1Api:
            def list_namespaced_pod(self, namespace, label_selector):
                class Listing:
                    items = [Pod()]

                return Listing()

        monkeypatch.setattr(cluster, "load_kube_config", lambda: None)
        monkeypatch.setattr(cluster.client, "CoreV1Api", CoreV1Api)

        with pytest.raises(AssistantPodNotReady, match="Pending"):
            find_assistant_pod(
                namespace="openstack",
                pod_label="openstackassistant",
                timeout=0,
            )
