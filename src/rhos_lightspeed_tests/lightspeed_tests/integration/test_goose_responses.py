"""Simulate the Responses API messages sent by the OpenStack Goose image.

These tests send real requests to a deployed Lightspeed service without
launching Goose. They use the shared client's bearer authentication and an
explicit model, structured input messages, streaming, store=false, and
X-LCS-Merge-Server-Tools: true.

The simple-question scenario checks stream completion and usable assistant
text. The client-tool scenario also checks that an offered function definition
is retained in the completed response. Both accept any valid assistant text,
so the deployment's LLM backend can return a canned answer. The client-tool
scenario checks API compatibility; it does not verify tool invocation or
server-tool merging. Inline OKP retrieval does not require a response tool
entry, and Lightspeed can hide server MCP tools from client responses.

The history scenario sends a unique OpenStack project name in the first turn,
replays the user and assistant messages, then asks for that name without
repeating it in the follow-up. This requires generated answer content and is
marked llm_dependent. Select the other two scenarios with:

    lightspeed-tests -k goose -m "not llm_dependent"

This selection still requires a deployed Lightspeed service and a configured
LLM backend, which can be real or mocked. It does not configure the mock.

Protocol references:
https://github.com/openstack-k8s-operators/s2i-openstack-containers/blob/5f1ddc43e54607f1b8e2a5b0101be1109bf11115/containers/goose/goose/openstack-goose-entrypoint.sh
https://github.com/aaif-goose/goose/blob/61830521ad3134fe1ffa32f62d95ede205216bfc/crates/goose-provider-types/src/formats/openai_responses.rs
"""

import json
from collections.abc import Iterator
from uuid import uuid4

import pytest
import requests

GOOSE_HEADERS = {"X-LCS-Merge-Server-Tools": "true"}
QUESTION = "What is OpenStack?"
CLIENT_TOOL = {
    "type": "function",
    "name": "goose_test_calculate_sum",
    "description": "Add two numbers. Only use for arithmetic questions.",
    "parameters": {
        "type": "object",
        "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
        "required": ["a", "b"],
    },
    "strict": False,
}


@pytest.fixture
def goose_payload(config):
    """Use the same explicit model as the Goose container's provider."""
    model = config["goose"]["model"]
    if not model:
        pytest.fail(
            "Set RHOS_LIGHTSPEED_MODEL to the Goose container's LIGHTSPEED_MODEL "
            "value before running the Goose integration tests."
        )
    return {
        "model": model,
        "input": [
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": QUESTION}],
            }
        ],
        "store": False,
        "stream": True,
    }


def _sse_events(response: requests.Response) -> Iterator[dict]:
    """Decode SSE data fields, including multiline events and a final partial frame."""
    response.encoding = "utf-8"
    data_lines = []
    for line in response.iter_lines(decode_unicode=True):
        if line == "":
            if data_lines:
                data = "\n".join(data_lines)
                data_lines = []
                if data == "[DONE]":
                    return
                yield json.loads(data)
        elif line.startswith("data:"):
            data_lines.append(line[5:].removeprefix(" "))

    if data_lines:
        data = "\n".join(data_lines)
        if data != "[DONE]":
            yield json.loads(data)


def _assistant_text(data: dict) -> str:
    """Read the structured assistant output Goose consumes, excluding tool items."""
    return "".join(
        part["text"]
        for item in data["output"]
        if item["type"] == "message" and item["role"] == "assistant"
        for part in item["content"]
        if part["type"] == "output_text"
    ).strip()


def _completed_response(response: requests.Response) -> dict:
    """Require a usable text stream and the response fields consumed by Goose."""
    assert response.status_code == 200, (
        f"Goose request returned HTTP {response.status_code}"
    )
    media_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip()
    assert media_type.lower() == "text/event-stream"

    created_id = None
    completed = None
    deltas = []
    for event in _sse_events(response):
        event_type = event["type"]
        assert event_type not in {"error", "response.failed", "response.incomplete"}, (
            f"Goose response stream reported {event_type}"
        )
        if event_type == "response.created":
            created_id = event["response"]["id"]
        elif event_type == "response.output_text.delta":
            deltas.append(event["delta"])
        elif event_type == "response.completed":
            assert completed is None, "Stream completed more than once"
            completed = event["response"]

        if event_type in {"response.output_item.added", "response.output_item.done"}:
            assert event["item"]["type"] in {"message", "function_call", "reasoning"}, (
                "Server tool output leaked into the Goose stream"
            )

    assert created_id, "Missing response.created event"
    assert completed is not None, "Stream ended without response.completed"
    assert completed["id"] == created_id
    assert completed["object"] == "response"
    assert completed["status"] == "completed"
    assert isinstance(completed["created_at"], int)
    assert isinstance(completed["model"], str) and completed["model"]
    assert all(
        item["type"] in {"message", "function_call", "reasoning"}
        for item in completed["output"]
    ), "Completed response contains output types Goose cannot consume"
    text = _assistant_text(completed)
    assert text, "No assistant output_text in completed response"
    assert deltas and "".join(deltas).strip() == text, (
        "Streamed text does not match the completed assistant answer"
    )
    return completed


def _ask_goose(client, payload: dict) -> dict:
    """Close the HTTP response even when a stream assertion fails."""
    with client.responses(payload, headers=GOOSE_HEADERS) as response:
        return _completed_response(response)


def build_goose_history(first_input: list[dict], first_response: dict) -> list[dict]:
    """Copy the input history and append nonempty assistant text messages."""
    history = list(first_input)

    for item in first_response.get("output", []):
        if item.get("type") == "message" and item.get("role") == "assistant":
            usable_parts = []

            for part in item.get("content", []):
                if part.get("type") == "output_text" and part.get("text"):
                    usable_parts.append(
                        {
                            "type": part["type"],
                            "text": part["text"],
                            "annotations": part.get("annotations", []),
                        }
                    )

            if usable_parts:
                history.append(
                    {"type": "message", "role": "assistant", "content": usable_parts}
                )
    return history


class TestGooseResponses:
    def test_goose_streaming_question_returns_answer(self, client, goose_payload):
        """Require a complete text stream, independent of the answer's wording."""
        _ask_goose(client, goose_payload)

    @pytest.mark.llm_dependent
    def test_goose_follow_up_accepts_message_history(self, client, goose_payload):
        """Recall a unique OpenStack project name from replayed message history."""
        project_name = f"goose-history-{uuid4().hex[:12]}"
        first_payload = {
            **goose_payload,
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": (
                                f"My OpenStack project is named {project_name}. "
                                f"{QUESTION}"
                            ),
                        }
                    ],
                }
            ],
        }
        first_response = _ask_goose(client, first_payload)
        history = build_goose_history(first_payload["input"], first_response)
        history.append(
            {
                "type": "message",
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": (
                            "What is the exact OpenStack project name I gave you "
                            "earlier? Return only the project name."
                        ),
                    }
                ],
            }
        )
        follow_up = _ask_goose(client, {**goose_payload, "input": history})

        assert project_name in _assistant_text(follow_up).lower(), (
            "Follow-up did not recall the OpenStack project name from history"
        )

    def test_goose_question_accepts_client_tools(self, client, goose_payload):
        """Accept and retain a client function tool alongside any valid text answer.

        This checks the API contract rather than the LLM's choice to call a
        tool, so a canned answer from a mocked LLM backend is sufficient.
        """
        data = _ask_goose(client, {**goose_payload, "tools": [CLIENT_TOOL]})

        assert any(
            tool.get("type") == "function" and tool.get("name") == CLIENT_TOOL["name"]
            for tool in data.get("tools") or []
        ), "Goose client tool was lost"
