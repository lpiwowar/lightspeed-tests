"""ACP client and OpenShift pod-exec transport for Goose."""

import itertools
import json
import logging
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import AbstractContextManager
from typing import Any, Protocol

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
from pydantic import BaseModel

logger = logging.getLogger(__name__)


class ACPError(RuntimeError):
    """An ACP request failed or did not receive a valid response."""


# ---------------------------------------------------------------------------
# ACP Transport layer
# ---------------------------------------------------------------------------


class ACPTransport(Protocol):
    """Minimal structural interface for sending and receiving ACP JSON-RPC dicts."""

    def send(self, message: dict[str, Any]) -> None: ...
    def receive(self) -> dict[str, Any] | None: ...
    def close(self) -> None: ...


class PodExecTransport(AbstractContextManager["PodExecTransport"]):
    """ACP transport over a bidirectional OpenShift pod-exec WebSocket."""

    def __init__(
        self,
        pod_name: str,
        namespace: str,
        command: Sequence[str],
        openshift_config: Mapping[str, str],
        container: str | None = None,
        timeout: float = 300.0,
    ) -> None:
        self._timeout = timeout
        self._stdout_buffer = ""
        self._stream = stream(
            PodExecTransport._create_core_api(openshift_config).connect_get_namespaced_pod_exec,
            pod_name,
            namespace,
            command=list(command),
            container=container,
            stdin=True,
            stdout=True,
            stderr=True,
            tty=False,
            _preload_content=False,
        )

    def send(self, message: dict[str, Any]) -> None:
        self._stream.write_stdin(f"{json.dumps(message)}\n")

    def receive(self) -> dict[str, Any] | None:
        deadline = time.monotonic() + self._timeout
        while True:
            while "\n" not in self._stdout_buffer:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not self._stream.is_open():
                    return None

                self._stream.update(timeout=min(1.0, remaining))
                if self._stream.peek_stdout():
                    self._stdout_buffer += self._stream.read_stdout()
                if self._stream.peek_stderr():
                    logger.debug("Goose stderr: %s", self._stream.read_stderr())

            line, self._stdout_buffer = self._stdout_buffer.split("\n", 1)
            if line := line.strip():
                try:
                    return json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ACPError(f"Goose emitted invalid ACP JSON: {line!r}") from exc

    def close(self) -> None:
        if getattr(self, "_stream", None) is not None:
            self._stream.close()

    def __exit__(self, *_: object) -> None:
        self.close()

    @staticmethod
    def _create_core_api(openshift_config: Mapping[str, str]) -> client.CoreV1Api:
        cfg = client.Configuration()
        try:
            InClusterConfigLoader(
                token_filename=openshift_config["service_account_token_path"],
                cert_filename=openshift_config["api_server_ca_cert_path"],
            ).load_and_set(cfg)
        except ConfigException:
            load_kube_config(
                config_file=openshift_config.get("kubeconfig_path"),
                client_configuration=cfg,
            )
        return client.CoreV1Api(client.ApiClient(cfg))


# ---------------------------------------------------------------------------
# ACP Client layer
# ---------------------------------------------------------------------------


class ACPClient:
    """Client for communicating with an ACP-compatible agent (such as Goose).

    Manages JSON-RPC request IDs, session state, and streamed assistant responses
    over a provided :class:`ACPTransport`.

    Example:
        >>> client = ACPClient()
        >>> with PodExecTransport(pod, namespace, ["goose", "acp"], openshift_cfg) as transport:
        ...     client.initialize(transport)
        ...     client.new_session(transport)
        ...     reply = client.prompt(transport, "List open Neutron networks")
    """

    PROTOCOL_VERSION = 1

    def __init__(self) -> None:
        self._request_ids: Iterator[int] = itertools.count(1)
        self._session_id: str | None = None

    def initialize(self, transport: ACPTransport) -> None:
        """Negotiate ACP version with the agent."""
        req = InitializeRequest(
            protocol_version=self.PROTOCOL_VERSION,
            client_capabilities=ClientCapabilities(),
        )
        resp = InitializeResponse.model_validate(
            self._rpc(transport, "initialize", req)
        )
        if resp.protocol_version != self.PROTOCOL_VERSION:
            raise ACPError(f"Unsupported ACP version: {resp.protocol_version}")

    def new_session(self, transport: ACPTransport, cwd: str = "/") -> str:
        """Create an ACP session, store its ID on the client, and return it."""
        req = NewSessionRequest(cwd=cwd, mcp_servers=[])
        resp = NewSessionResponse.model_validate(
            self._rpc(transport, "session/new", req)
        )
        if not resp.session_id:
            raise ACPError("ACP session/new returned an empty session ID")
        self._session_id = resp.session_id
        return resp.session_id

    def prompt(self, transport: ACPTransport, text: str) -> str:
        """Send a text prompt in the active session and return the assistant response."""
        if self._session_id is None:
            raise ACPError("No active ACP session; call new_session() before prompt()")
        return "".join(self._stream_prompt(transport, self._session_id, text))

    def _stream_prompt(
        self,
        transport: ACPTransport,
        session_id: str,
        text: str,
    ) -> Iterator[str]:
        """Send a text prompt and yield streamed assistant text chunks as they arrive."""
        chunks: list[str] = []
        req = PromptRequest(
            session_id=session_id,
            prompt=[TextContentBlock(text=text)],
        )

        result = self._rpc(transport, "session/prompt", req, chunks=chunks)
        yield from chunks

        resp = PromptResponse.model_validate(result)
        if resp.stop_reason != "end_turn":
            raise ACPError(f"ACP prompt stopped unexpectedly: {resp.stop_reason}")

    def _rpc(
        self,
        transport: ACPTransport,
        method: str,
        params: BaseModel,
        chunks: list[str] | None = None,
    ) -> dict[str, Any]:
        request_id = next(self._request_ids)
        transport.send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params.model_dump(by_alias=True, exclude_none=True),
            }
        )
        return self._await_response(transport, request_id, method, chunks)

    def _await_response(
        self,
        transport: ACPTransport,
        request_id: int,
        method: str,
        chunks: list[str] | None = None,
    ) -> dict[str, Any]:
        while (message := transport.receive()) is not None:
            if message.get("id") == request_id:
                if "error" in message:
                    raise ACPError(f"ACP {method} failed: {message['error']}")
                return message.get("result", {})

            self._handle_notification(transport, message, chunks)

        raise ACPError(f"Timed out or stream closed waiting for ACP {method} response")

    def _handle_notification(
        self,
        transport: ACPTransport,
        message: dict[str, Any],
        chunks: list[str] | None,
    ) -> None:
        method = message.get("method")
        params = message.get("params", {})

        if method == "session/update" and chunks is not None:
            update = SessionNotification.model_validate(params).update
            if isinstance(update, ContentChunk) and isinstance(
                update.content, TextContentBlock
            ):
                chunks.append(update.content.text)

        elif method == "session/request_permission" and "id" in message:
            outcome = RequestPermissionResponse(outcome=DeniedOutcome()).model_dump(
                by_alias=True, exclude_none=True
            )
            transport.send({"jsonrpc": "2.0", "id": message["id"], "result": outcome})
