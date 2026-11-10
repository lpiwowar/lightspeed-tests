"""Unit tests for the ACP client."""

from collections.abc import Iterator
from typing import Any

import pytest
from acp.schema import AgentMessageChunk, SessionNotification, TextContentBlock

from rhos_lightspeed_tests.lib.acp import AcpClient, AcpError, AcpMethod, ACPTransport


class ScriptedTransport(ACPTransport):
    """In-memory transport returning a predetermined sequence of messages."""

    def __init__(self, messages: Iterator[dict[str, Any]]) -> None:
        self.messages = iter(messages)
        self.sent: list[dict[str, Any]] = []
        self.closed = False

    def send(self, message: dict[str, Any]) -> None:
        self.sent.append(message)

    def receive(self, timeout: float) -> dict[str, Any] | None:
        return next(self.messages, None)

    def close(self) -> None:
        self.closed = True


def _agent_text(session_id: str, text: str) -> dict[str, Any]:
    """Build an ACP session-update notification carrying an assistant chunk."""
    notification = SessionNotification(
        session_id=session_id,
        update=AgentMessageChunk(content=TextContentBlock(text=text)),
    )
    return {
        "jsonrpc": "2.0",
        "method": AcpMethod.SESSION_UPDATE,
        "params": notification.model_dump(by_alias=True, exclude_none=True),
    }


def test_client_negotiates_creates_session_and_collects_prompt_chunks() -> None:
    transport = ScriptedTransport(
        iter(
            [
                {"id": 1, "result": {"protocolVersion": 1}},
                {"id": 2, "result": {"sessionId": "session-1"}},
                _agent_text("session-1", "Hello, "),
                _agent_text("session-1", "OpenStack!"),
                {"id": 3, "result": {"stopReason": "end_turn"}},
            ]
        )
    )
    client = AcpClient(transport)

    client.initialize()
    session_id = client.new_session(cwd="/workspace")
    response = client.prompt(session_id, "Say hello")

    assert session_id == "session-1"
    assert response == "Hello, OpenStack!"
    assert [message["method"] for message in transport.sent] == [
        AcpMethod.INITIALIZE,
        AcpMethod.SESSION_NEW,
        AcpMethod.SESSION_PROMPT,
    ]
    assert transport.sent[1]["params"] == {"cwd": "/workspace", "mcpServers": []}
    assert transport.sent[2]["params"]["prompt"] == [
        {"type": "text", "text": "Say hello"}
    ]


def test_client_denies_permission_requests_while_waiting_for_response() -> None:
    transport = ScriptedTransport(
        iter(
            [
                {
                    "jsonrpc": "2.0",
                    "id": 42,
                    "method": AcpMethod.REQUEST_PERMISSION,
                    "params": {},
                },
                {"id": 1, "result": {"protocolVersion": 1}},
            ]
        )
    )

    AcpClient(transport).initialize()

    assert transport.sent[1] == {
        "jsonrpc": "2.0",
        "id": 42,
        "result": {"outcome": {"outcome": "cancelled"}},
    }


@pytest.mark.parametrize(
    ("result", "match"),
    [
        ({"protocolVersion": 2}, "unsupported ACP version"),
        ({"protocolVersion": 1}, None),
    ],
)
def test_initialize_rejects_an_unsupported_protocol_version(
    result: dict[str, int], match: str | None
) -> None:
    transport = ScriptedTransport(iter([{"id": 1, "result": result}]))
    client = AcpClient(transport)

    if match:
        with pytest.raises(AcpError, match=match):
            client.initialize()
    else:
        client.initialize()
