"""E2E: ask the OpenStack Assistant (goose) pod a question.

Requires a cluster where the assistant is deployed, e.g. with
`make openstack_assistant` (install_yamls): an OpenStackAssistant CR
reconciled by the openstack-operator into a pod labeled
service=openstackassistant. The test execs into that pod and runs goose in
headless mode:

    goose run -t "<question>" --output-format json --no-session

goose prints one JSON object to stdout (its JsonOutput):

    {"messages": [...], "metadata": {"status": "completed", ...}}

where messages are goose Message objects. The test asserts the run
completed and produced non-empty assistant text; it does not match the
answer's content, which depends on the LLM backend and on the tools the
model chooses to call. --no-session keeps the PVC-backed session database
clean. The exec-launched goose inherits the image's
GOOSE_ADDITIONAL_CONFIG_FILES environment variable, so the rendered
config (lightspeed provider, MCP servers) applies.

The test skips when the assistant pod is not deployed, keeping bare
lightspeed-tests runs green in environments without it.
"""

import json
import time

import pytest

from rhos_lightspeed_tests.cluster import (
    AssistantPodNotAvailable,
    exec_in_pod,
    find_assistant_pod,
)


@pytest.fixture
def goose_pod(config):
    """The Running and ready assistant pod; skip when it is not deployed."""
    settings = config["goose_pod"]
    try:
        return find_assistant_pod(
            namespace=settings["namespace"],
            pod_label=settings["pod_label"],
            timeout=settings["pod_timeout"],
        )
    except AssistantPodNotAvailable as exc:
        pytest.skip(
            f"OpenStack Assistant '{settings['assistant_name']}' is not "
            f"available: {exc}"
        )


def _assistant_text(payload: dict) -> str:
    """Join the assistant's text items from a goose run JSON payload.

    Tolerates interleaved tool-request and tool-response items in the
    message content.
    """
    parts = []
    for message in payload.get("messages", []):
        if message.get("role") != "assistant":
            continue
        for item in message.get("content", []):
            if item.get("type") == "text" and item.get("text"):
                parts.append(item["text"])
    return "".join(parts).strip()


def _parse_output_json(stdout: str) -> dict:
    """Parse goose run's JSON output, tolerating stray non-JSON lines."""
    start = stdout.find("{")
    if start < 0:
        raise ValueError(f"no JSON object in goose run stdout:\n{stdout}")
    payload, _ = json.JSONDecoder().raw_decode(stdout[start:])
    return payload


def test_goose_run_answers_question(goose_pod, config):
    """goose run -t in the assistant pod answers with non-empty text."""
    settings = config["goose_pod"]
    command = [
        "goose",
        "run",
        "-t",
        settings["question"],
        "--output-format",
        "json",
        "--no-session",
    ]

    started = time.monotonic()
    exit_code, stdout, stderr = exec_in_pod(
        pod_name=goose_pod.metadata.name,
        namespace=settings["namespace"],
        command=command,
        timeout=settings["timeout"],
    )
    elapsed = time.monotonic() - started
    # The elapsed time reaches the JUnit report through junit_logging.
    print(f"goose run finished in {elapsed:.1f}s with exit code {exit_code}")

    context = (
        f"goose run took {elapsed:.1f}s\nexit code: {exit_code}\n"
        f"stdout:\n{stdout}\nstderr:\n{stderr}"
    )
    assert exit_code == 0, context

    try:
        payload = _parse_output_json(stdout)
    except ValueError as exc:
        pytest.fail(f"goose run stdout is not valid JSON: {exc}\n{context}")

    status = payload.get("metadata", {}).get("status")
    assert status == "completed", f"goose run status {status!r}\n{context}"

    text = _assistant_text(payload)
    assert text, f"goose run returned no assistant text\n{context}"
