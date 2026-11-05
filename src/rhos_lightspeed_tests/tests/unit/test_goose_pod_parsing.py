"""Unit tests for the goose run JSON output parsing.

The e2e test parses the JSON object that `goose run --output-format json
--no-session` prints to stdout (goose's JsonOutput): a list of Message
objects under "messages", each with a role and a list of typed content
items, plus run metadata with a "completed"/"error" status.
"""

import json

import pytest

from rhos_lightspeed_tests.tests.e2e.test_goose_pod import (
    _assistant_text,
    _parse_output_json,
)

# A realistic completed run: the user asks, the assistant calls a tool
# before answering, and the final answer spans two text items.
COMPLETED_RUN = {
    "messages": [
        {
            "role": "user",
            "content": [{"type": "text", "text": "What is nova in OpenStack?"}],
        },
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Let me look at the cluster."},
                {
                    "type": "toolRequest",
                    "id": "toolu_01",
                    "tool_call": {
                        "type": "function",
                        "name": "openstack__list_services",
                        "arguments": {},
                    },
                },
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "toolResponse",
                    "id": "toolu_01",
                    "content": [{"type": "text", "text": "nova, keystone, glance"}],
                }
            ],
        },
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": " Nova is the OpenStack "},
                {"type": "text", "text": "compute service. "},
            ],
        },
    ],
    "metadata": {
        "status": "completed",
        "total_tokens": 4096,
        "input_tokens": 800,
        "output_tokens": 3296,
    },
}

# A run that failed after a partial assistant turn: the metadata reports
# an error while the messages still parse.
ERROR_RUN = {
    "messages": [
        {
            "role": "user",
            "content": [{"type": "text", "text": "What is nova in OpenStack?"}],
        },
        {
            "role": "assistant",
            "content": [{"type": "text", "text": "Let me check "}],
        },
    ],
    "metadata": {"status": "error"},
}


class TestAssistantText:
    def test_joins_assistant_text_across_messages(self):
        """Text from all assistant messages is joined, in order, stripped."""
        text = _assistant_text(COMPLETED_RUN)

        assert text == (
            "Let me look at the cluster. Nova is the OpenStack compute service."
        )

    def test_ignores_tool_and_user_items(self):
        """Tool requests, tool responses and user text are not included."""
        text = _assistant_text(COMPLETED_RUN)

        assert "nova, keystone, glance" not in text
        assert "What is nova" not in text
        assert "toolRequest" not in text

    def test_empty_without_assistant_text(self):
        """A conversation of user and tool messages yields no text."""
        payload = {
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": "hello"}]},
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "toolRequest",
                            "id": "toolu_02",
                            "tool_call": {"type": "function", "name": "x"},
                        }
                    ],
                },
            ]
        }

        assert _assistant_text(payload) == ""

    def test_empty_payload(self):
        """Missing or malformed fields do not raise."""
        assert _assistant_text({}) == ""
        assert _assistant_text({"messages": []}) == ""
        assert _assistant_text({"messages": [{"role": "assistant"}]}) == ""

    def test_reads_error_run(self):
        """An error-status payload still parses; the e2e test checks status."""
        assert _assistant_text(ERROR_RUN) == "Let me check"


class TestParseOutputJson:
    def test_parses_plain_output(self):
        assert _parse_output_json(json.dumps(COMPLETED_RUN)) == COMPLETED_RUN

    def test_tolerates_stray_non_json_lines(self):
        """Leading and trailing non-JSON lines are ignored."""
        raw = (
            "INFO goose :: loading config\n"
            f"{json.dumps(COMPLETED_RUN)}\n"
            "INFO goose :: done\n"
        )

        assert _parse_output_json(raw) == COMPLETED_RUN

    def test_tolerates_trailing_junk_after_object(self):
        raw = f"{json.dumps(ERROR_RUN)}not-json-trailing"

        assert _parse_output_json(raw) == ERROR_RUN

    def test_rejects_output_without_json(self):
        with pytest.raises(ValueError, match="no JSON object"):
            _parse_output_json("goose: command not found\n")

    def test_rejects_invalid_json(self):
        with pytest.raises(ValueError):
            _parse_output_json('{"messages": [ truncated')
