"""Unit tests for the ACP client."""

from collections.abc import Iterator
from typing import Any

import pytest
from acp.schema import AgentMessageChunk, SessionNotification, TextContentBlock

from rhos_lightspeed_tests.lib import acp
from rhos_lightspeed_tests.lib.acp import ACPClient, ACPError, PodExecTransport


class ScriptedTransport:
    """In-memory transport that replays ACP messages and records requests."""

    def __init__(self, messages: Iterator[dict[str, Any] | None]) -> None:
        self._messages = messages
        self.sent: list[dict[str, Any]] = []

    def send(self, message: dict[str, Any]) -> None:
        self.sent.append(message)

    def receive(self) -> dict[str, Any] | None:
        return next(self._messages, None)

    def close(self) -> None:
        pass


class FakeWebSocket:
    """Small in-memory stand-in for the Kubernetes exec WebSocket."""

    def __init__(self, stdout: list[str] | None = None, open_: bool = True) -> None:
        self.stdout = stdout or []
        self.stderr: list[str] = []
        self.open = open_
        self.stdin: list[str] = []
        self.update_timeouts: list[float] = []
        self.closed = False

    def write_stdin(self, data: str) -> None:
        self.stdin.append(data)

    def is_open(self) -> bool:
        return self.open

    def update(self, timeout: float) -> None:
        self.update_timeouts.append(timeout)

    def peek_stdout(self) -> bool:
        return bool(self.stdout)

    def read_stdout(self) -> str:
        return self.stdout.pop(0)

    def peek_stderr(self) -> bool:
        return bool(self.stderr)

    def read_stderr(self) -> str:
        return self.stderr.pop(0)

    def close(self) -> None:
        self.closed = True


def _pod_transport(monkeypatch: pytest.MonkeyPatch, websocket: FakeWebSocket) -> PodExecTransport:
    class FakeCoreApi:
        def connect_get_namespaced_pod_exec(self) -> None:
            pass

    monkeypatch.setattr(
        PodExecTransport,
        "_create_core_api",
        staticmethod(lambda _: FakeCoreApi()),
    )
    monkeypatch.setattr(acp, "stream", lambda *_args, **_kwargs: websocket)
    return PodExecTransport(
        "assistant-0",
        "openstack",
        ["goose", "acp"],
        {"kubeconfig_path": "/unused"},
        timeout=1,
    )


def _text_update(session_id: str, text: str) -> dict[str, Any]:
    notification = SessionNotification(
        session_id=session_id,
        update=AgentMessageChunk(content=TextContentBlock(text=text)),
    )
    return {
        "jsonrpc": "2.0",
        "method": "session/update",
        "params": notification.model_dump(by_alias=True, exclude_none=True),
    }


class TestACPClient:
    def test_initializes_creates_session_and_collects_prompt_chunks(self) -> None:
        transport = ScriptedTransport(
            iter(
                [
                    {"id": 1, "result": {"protocolVersion": 1}},
                    {"id": 2, "result": {"sessionId": "session-1"}},
                    _text_update("session-1", "Hello, "),
                    _text_update("session-1", "OpenStack!"),
                    {"id": 3, "result": {"stopReason": "end_turn"}},
                ]
            )
        )
        client = ACPClient()

        client.initialize(transport)
        assert client.new_session(transport, cwd="/workspace") == "session-1"
        assert client.prompt(transport, "Say hello") == "Hello, OpenStack!"

        assert [message["method"] for message in transport.sent] == [
            "initialize",
            "session/new",
            "session/prompt",
        ]
        assert transport.sent[0]["params"]["protocolVersion"] == 1
        assert transport.sent[1]["params"] == {"cwd": "/workspace", "mcpServers": []}
        assert transport.sent[2]["params"] == {
            "sessionId": "session-1",
            "prompt": [{"type": "text", "text": "Say hello"}],
        }

    def test_denies_permission_request_while_waiting_for_response(self) -> None:
        transport = ScriptedTransport(
            iter(
                [
                    {
                        "jsonrpc": "2.0",
                        "id": 42,
                        "method": "session/request_permission",
                        "params": {},
                    },
                    {"id": 1, "result": {"protocolVersion": 1}},
                ]
            )
        )

        ACPClient().initialize(transport)

        assert transport.sent[1] == {
            "jsonrpc": "2.0",
            "id": 42,
            "result": {"outcome": {"outcome": "cancelled"}},
        }

    @pytest.mark.parametrize(
        ("result", "match"),
        [
            ({"protocolVersion": 2}, "Unsupported ACP version: 2"),
            ({"protocolVersion": 1}, None),
        ],
    )
    def test_initialize_only_accepts_the_supported_protocol_version(
        self, result: dict[str, int], match: str | None
    ) -> None:
        transport = ScriptedTransport(iter([{"id": 1, "result": result}]))

        if match:
            with pytest.raises(ACPError, match=match):
                ACPClient().initialize(transport)
        else:
            ACPClient().initialize(transport)

    def test_prompt_requires_a_session(self) -> None:
        with pytest.raises(ACPError, match="No active ACP session"):
            ACPClient().prompt(ScriptedTransport(iter(())), "Hello")

    @pytest.mark.parametrize(
        "response",
        [
            {"id": 1, "error": {"code": -32000, "message": "broken"}},
            None,
        ],
    )
    def test_initialize_reports_rpc_errors_and_closed_streams(
        self, response: dict[str, Any] | None
    ) -> None:
        transport = ScriptedTransport(iter([response]))

        with pytest.raises(ACPError):
            ACPClient().initialize(transport)


class TestPodExecTransport:
    def test_serializes_requests_and_reassembles_stdout(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        websocket = FakeWebSocket(['{"id": 1, ', '"result": {"ok": true}}\n'])
        transport = _pod_transport(monkeypatch, websocket)

        transport.send({"id": 1, "method": "initialize"})

        assert transport.receive() == {"id": 1, "result": {"ok": True}}
        assert websocket.stdin == ['{"id": 1, "method": "initialize"}\n']
        assert len(websocket.update_timeouts) == 2

    def test_opens_a_bidirectional_pod_exec(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class FakeCoreApi:
            def connect_get_namespaced_pod_exec(self) -> None:
                pass

        websocket = FakeWebSocket()
        captured: dict[str, Any] = {}

        def fake_stream(target: Any, *args: Any, **kwargs: Any) -> FakeWebSocket:
            captured.update(target=target, args=args, kwargs=kwargs)
            return websocket

        monkeypatch.setattr(
            PodExecTransport,
            "_create_core_api",
            staticmethod(lambda _: FakeCoreApi()),
        )
        monkeypatch.setattr(acp, "stream", fake_stream)

        PodExecTransport(
            "assistant-0",
            "openstack",
            ["goose", "acp"],
            {"kubeconfig_path": "/unused"},
            container="goose",
        )

        assert captured["args"] == ("assistant-0", "openstack")
        assert captured["kwargs"] == {
            "command": ["goose", "acp"],
            "container": "goose",
            "stdin": True,
            "stdout": True,
            "stderr": True,
            "tty": False,
            "_preload_content": False,
        }

    def test_rejects_invalid_json(self, monkeypatch: pytest.MonkeyPatch) -> None:
        transport = _pod_transport(monkeypatch, FakeWebSocket(["not json\n"]))

        with pytest.raises(ACPError, match="invalid ACP JSON"):
            transport.receive()

    def test_returns_none_for_a_closed_stream_and_closes_on_exit(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        websocket = FakeWebSocket(open_=False)
        transport = _pod_transport(monkeypatch, websocket)

        assert transport.receive() is None
        with transport:
            pass

        assert websocket.closed
