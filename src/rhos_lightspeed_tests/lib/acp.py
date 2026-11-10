"""ACP client for interacting with the OpenStack Assistant (Goose)."""

import abc
import json
import time
from collections.abc import Mapping, Sequence
from enum import StrEnum
from types import TracebackType
from typing import Any, Self

from acp.schema import (
    ClientCapabilities,
    ContentChunk,
    DeniedOutcome,
    InitializeRequest,
    InitializeResponse,
    NewSessionRequest,
    NewSessionResponse,
    PromptRequest,
    PromptResponse,
    RequestPermissionResponse,
    SessionNotification,
    TextContentBlock,
)
from kubernetes import client
from kubernetes.config.config_exception import ConfigException
from kubernetes.config.incluster_config import InClusterConfigLoader
from kubernetes.config.kube_config import load_kube_config
from kubernetes.stream import stream


class AcpError(RuntimeError):
    """An ACP request failed or did not receive a valid response."""


class AcpMethod(StrEnum):
    """Supported ACP JSON-RPC method names."""

    INITIALIZE = "initialize"
    SESSION_NEW = "session/new"
    SESSION_PROMPT = "session/prompt"
    SESSION_UPDATE = "session/update"
    REQUEST_PERMISSION = "session/request_permission"


class ACPTransport(abc.ABC):
    """Abstract communication layer for ACP."""

    @abc.abstractmethod
    def send(self, message: dict[str, Any]) -> None:
        """Send a single ACP/JSON-RPC message dictionary."""
        ...

    @abc.abstractmethod
    def receive(self, timeout: float) -> dict[str, Any] | None:
        """Wait up to *timeout* seconds and return the next decoded JSON-RPC message,
        or None if no complete message arrived before timeout.
        """
        ...

    @abc.abstractmethod
    def close(self) -> None:
        """Close the underlying connection or session."""
        ...

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.close()


class PodExecTransport(ACPTransport):
    """ACP transport over a bidirectional OpenShift pod-exec WebSocket."""

    def __init__(
        self,
        pod_name: str,
        namespace: str,
        command: Sequence[str],
        openshift_config: Mapping[str, str],
        container: str | None = None,
    ) -> None:
        self._stdout_buffer = ""

        kwargs: dict[str, Any] = {
            "command": list(command),
            "stdin": True,
            "stdout": True,
            "stderr": True,
            "tty": False,
        }
        if container is not None:
            kwargs["container"] = container

        self._response = stream(
            self._create_core_api(openshift_config).connect_get_namespaced_pod_exec,
            pod_name,
            namespace,
            _preload_content=False,
            **kwargs,
        )

    def send(self, message: dict[str, Any]) -> None:
        payload = json.dumps(message) + "\n"
        self._response.write_stdin(payload)

    def receive(self, timeout: float) -> dict[str, Any] | None:
        deadline = time.monotonic() + timeout

        while "\n" not in self._stdout_buffer:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None

            chunk_timeout = min(1.0, remaining)
            if self._response.is_open():
                self._response.update(timeout=chunk_timeout)

            if self._response.peek_stdout():
                self._stdout_buffer += self._response.read_stdout()
            else:
                return None

        line, self._stdout_buffer = self._stdout_buffer.split("\n", 1)
        line = line.strip()
        if not line:
            return None

        try:
            return json.loads(line)
        except json.JSONDecodeError as exc:
            raise AcpError(f"Goose emitted invalid ACP JSON: {line!r}") from exc

    def close(self) -> None:
        if hasattr(self, "_response") and self._response is not None:
            self._response.close()

    @staticmethod
    def _create_core_api(openshift_config: Mapping[str, str]) -> client.CoreV1Api:
        """Create a Core API client from in-cluster credentials or kubeconfig."""
        configuration = client.Configuration()
        try:
            InClusterConfigLoader(
                token_filename=openshift_config["service_account_token_path"],
                cert_filename=openshift_config["service_account_ca_cert_path"],
            ).load_and_set(configuration)
        except ConfigException:
            load_kube_config(
                config_file=openshift_config.get("kubeconfig_path"),
                client_configuration=configuration,
            )
        return client.CoreV1Api(client.ApiClient(configuration))


class AcpClient:
    """Minimal ACP client"""

    ACP_PROTOCOL_VERSION = 1

    def __init__(self, transport: ACPTransport, timeout: float = 300.0) -> None:
        self._transport = transport
        self._timeout = timeout
        self._next_request_id = 1
        self._response_chunks: list[str] = []

    def initialize(self) -> None:
        """Negotiate ACP version with the agent."""
        req = InitializeRequest(
            protocol_version=AcpClient.ACP_PROTOCOL_VERSION,
            client_capabilities=ClientCapabilities(),
        )
        raw_result = self._request(
            method=AcpMethod.INITIALIZE,
            params=req.model_dump(by_alias=True, exclude_none=True),
        )

        resp = InitializeResponse.model_validate(raw_result)
        if resp.protocol_version != AcpClient.ACP_PROTOCOL_VERSION:
            raise AcpError(
                f"Goose negotiated unsupported ACP version: {resp.protocol_version}"
            )

    def new_session(self, cwd: str = "/") -> str:
        """Create an ACP session and return its ID."""
        req = NewSessionRequest(cwd=cwd, mcp_servers=[])
        raw_result = self._request(
            method=AcpMethod.SESSION_NEW,
            params=req.model_dump(by_alias=True, exclude_none=True),
        )

        resp = NewSessionResponse.model_validate(raw_result)
        if not resp.session_id:
            raise AcpError(f"ACP session/new returned empty session ID: {raw_result}")
        return resp.session_id

    def prompt(self, session_id: str, text: str) -> str:
        """Send a text prompt and return its streamed assistant response."""
        self._response_chunks = []
        req = PromptRequest(
            session_id=session_id,
            prompt=[TextContentBlock(text=text)],
        )
        raw_result = self._request(
            method=AcpMethod.SESSION_PROMPT,
            params=req.model_dump(by_alias=True, exclude_none=True),
        )
        response = PromptResponse.model_validate(raw_result)
        if response.stop_reason != "end_turn":
            raise AcpError(
                f"ACP session/prompt stopped unexpectedly: {response.stop_reason}"
            )
        return "".join(self._response_chunks)

    def close(self) -> None:
        """Close the underlying transport."""
        self._transport.close()

    def _request(self, method: AcpMethod, params: dict[str, Any]) -> dict[str, Any]:
        """Send an ACP request and return its result, processing intervening messages."""
        request_id = self._next_request_id
        self._next_request_id += 1

        self._transport.send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": str(method),
                "params": params,
            }
        )

        deadline = time.monotonic() + self._timeout
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            message = self._transport.receive(timeout=min(1.0, remaining))
            if message is None:
                continue

            if message.get("id") == request_id:
                if "error" in message:
                    raise AcpError(f"ACP {method} failed: {message['error']}")
                return message.get("result", {})

            self._handle_query_message(message)

        raise AcpError(f"Timed out waiting for ACP {method} response")

    def _handle_query_message(self, message: dict[str, Any]) -> None:
        """Process an incoming ACP message while awaiting a query response."""
        method = message.get("method")
        params = message.get("params", {})

        match method:
            # Collect streamed assistant updates while a request is in progress.
            case AcpMethod.SESSION_UPDATE:
                notification = SessionNotification.model_validate(params)
                update = notification.update
                if isinstance(update, ContentChunk) and isinstance(
                    update.content, TextContentBlock
                ):
                    self._response_chunks.append(update.content.text)
                return

            # For now, deny every direct tool request.
            case AcpMethod.REQUEST_PERMISSION if "id" in message:
                response = RequestPermissionResponse(
                    outcome=DeniedOutcome()
                ).model_dump(by_alias=True, exclude_none=True)
                self._transport.send(
                    {
                        "jsonrpc": "2.0",
                        "id": message["id"],
                        "result": response,
                    }
                )
