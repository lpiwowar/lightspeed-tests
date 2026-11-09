"""Agent Client Protocol (ACP) transport and synchronous JSON-RPC 2.0 ACP client.

Provides :class:`PodExecTransport` for exchanging newline-delimited JSON messages
with an agent running inside an OpenShift/Kubernetes pod over a bidirectional
``exec`` WebSocket, and :class:`ACPClient` for managing protocol initialization,
sessions, and streamed prompt responses.
"""

import itertools
import json
import logging
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager
from typing import Any, Protocol

from acp.schema import (
    AgentMessageChunk,
    ClientCapabilities,
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
    """ACP transport over a bidirectional OpenShift pod-exec WebSocket.

    Spawns a command inside an OpenShift/Kubernetes pod container and exchanges
    newline-delimited JSON (NDJSON) messages over the process's ``stdin`` and
    ``stdout`` streams. Inbound WebSocket frames are buffered internally so
    arbitrary stream chunks are reassembled into complete JSON lines, while
    ``stderr`` output is routed to the debug logger.

    Intended to be used as a context manager to ensure the underlying WebSocket
    stream is cleanly closed on exit:

        >>>  with PodExecTransport(
        ...     pod_name="agent-pod",
        ...     namespace="my-namespace",
        ...     command=["goose", "acp"],
        ...     config=config,
        ... ) as transport:
        ...     transport.send({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
        ...     response = transport.receive()

    Args:
        pod_name: Name of the target OpenShift pod.
        namespace: Namespace containing the target pod.
        command: Executable and arguments (``argv``) to run inside the container.
        config: Parsed global configuration for lightspeed-tests. Must contain
            the keys ``service_account_token_path``, ``api_server_ca_cert_path``
            (optionally ``kubeconfig_path``).
        container: Name of the target container within the pod. Defaults to the
            pod's first/only container when ``None``.
        timeout: Maximum seconds ``receive()`` will wait for a complete JSON
            message before returning ``None``. Defaults to ``300.0``.
    """

    def __init__(
        self,
        pod_name: str,
        namespace: str,
        command: Sequence[str],
        config: Mapping[str, str],
        container: str | None = None,
        timeout: float = 300.0,
    ) -> None:
        self._timeout = timeout
        self._stdout_buffer = ""
        self._stream = stream(
            PodExecTransport._create_core_api(config).connect_get_namespaced_pod_exec,
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
                    logger.debug("Stderr: %s", self._stream.read_stderr())

            line, self._stdout_buffer = self._stdout_buffer.split("\n", 1)
            if line := line.strip():
                try:
                    return json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ACPError(f"Invalid JSON: {line!r}") from exc

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
    """ACP (Agent Client Protocol) client for communicating with compatible agents.

    Implements a synchronous JSON-RPC 2.0 ACP client over a bidirectional
    :class:`ACPTransport`, handling protocol initialization, session lifecycle,
    request/response ID correlation, and reassembly of streamed
    ``session/update`` text chunks. Incoming agent-initiated permission
    requests (``session/request_permission``) are at the moment automatically logged
    and DENIED.

    Note:
        This client is **not thread-safe** and is intended for sequential,
        single-session use on a dedicated transport. Concurrent calls from
        multiple threads or coroutines on the same instance (or shared
        transport) will race on incoming stream reads and overwrite the active
        ``session_id``. Instantiate a separate transport and :class:`ACPClient`
        per parallel worker.

    Example:
        >>> with PodExecTransport(
        ...     pod_name="agent-pod",
        ...     namespace="my-namespace",
        ...     command=["goose", "acp"],
        ...     config=config,
        ... ) as transport:
        ...     client = ACPClient(transport)
        ...     client.initialize()
        ...     client.new_session()
        ...     reply = client.prompt("List open Neutron networks")

    Args:
        transport: Bidirectional transport used to send and receive JSON-RPC
            messages.
    """

    PROTOCOL_VERSION = 1

    def __init__(self, transport: ACPTransport) -> None:
        self._transport = transport
        self._request_ids: Iterator[int] = itertools.count(1)
        self._session_id: str | None = None

    def initialize(self) -> None:
        """Negotiate ACP version with the agent."""
        req = InitializeRequest(
            protocol_version=self.PROTOCOL_VERSION,
            client_capabilities=ClientCapabilities(),
        )
        resp = InitializeResponse.model_validate(self._rpc_request("initialize", req))
        if resp.protocol_version != self.PROTOCOL_VERSION:
            raise ACPError(f"Unsupported ACP version: {resp.protocol_version}")

    def new_session(self, cwd: str = "/") -> str:
        """Create an ACP session, store its ID on the client, and return it."""
        req = NewSessionRequest(cwd=cwd, mcp_servers=[])
        resp = NewSessionResponse.model_validate(self._rpc_request("session/new", req))
        if not resp.session_id:
            raise ACPError("ACP session/new returned an empty session ID")
        self._session_id = resp.session_id
        return resp.session_id

    def prompt(self, text: str) -> str:
        """Send a text prompt in the active session and return the assistant response."""
        if self._session_id is None:
            raise ACPError("No active ACP session; call new_session() before prompt()")

        chunks: list[str] = []

        def collect_chunk(message: dict[str, Any]) -> None:
            if (chunk := self._extract_text_chunk(message)) is not None:
                chunks.append(chunk)

        req = PromptRequest(
            session_id=self._session_id,
            prompt=[TextContentBlock(text=text)],
        )
        result = self._rpc_request("session/prompt", req, on_notification=collect_chunk)

        resp = PromptResponse.model_validate(result)
        if resp.stop_reason != "end_turn":
            raise ACPError(f"ACP prompt stopped unexpectedly: {resp.stop_reason}")

        return "".join(chunks)

    def _rpc_request(
        self,
        method: str,
        params: BaseModel,
        on_notification: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        """Send a JSON-RPC request and block until its matching response arrives."""
        request_id = next(self._request_ids)
        self._transport.send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params.model_dump(by_alias=True, exclude_none=True),
            }
        )
        return self._await_response(request_id, method, on_notification)

    def _rpc_response(self, request_id: int | str, result: BaseModel) -> None:
        """Send a JSON-RPC response replying to an agent-initiated request."""
        self._transport.send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": result.model_dump(by_alias=True, exclude_none=True),
            }
        )

    def _await_response(
        self,
        request_id: int,
        method: str,
        on_notification: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        """Read incoming messages until the response for ``request_id`` arrives."""
        while (message := self._transport.receive()) is not None:
            if (
                message.get("method") == "session/request_permission"
                and "id" in message
            ):
                logger.warning("Denying ACP permission request")
                self._rpc_response(
                    message["id"],
                    RequestPermissionResponse(outcome=DeniedOutcome()),
                )
            elif message.get("id") == request_id:
                if "error" in message:
                    raise ACPError(f"ACP {method} failed: {message['error']}")
                return message.get("result", {})
            elif on_notification is not None:
                on_notification(message)

        raise ACPError(f"Timed out or stream closed waiting for ACP {method} response")

    @staticmethod
    def _extract_text_chunk(message: dict[str, Any]) -> str | None:
        """Return assistant text from a ``session/update`` notification, if present."""
        if message.get("method") != "session/update":
            return None
        update = SessionNotification.model_validate(message.get("params", {})).update
        if isinstance(update, AgentMessageChunk) and isinstance(
            update.content, TextContentBlock
        ):
            return update.content.text
        return None
