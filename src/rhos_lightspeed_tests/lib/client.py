"""HTTP client for RHOS Lightspeed API."""

import requests


class RHOSLightspeedClient:
    def __init__(
        self,
        base_url: str,
        api_prefix: str = "/v1",
        token: str = "",
        timeout: int = 30,
        verify_tls: bool = False,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.api_prefix = api_prefix
        self.verify_tls = verify_tls
        self.session = requests.Session()
        self.session.verify = verify_tls
        self.session.headers["Authorization"] = f"Bearer {token}"

    def query(self, question: str) -> requests.Response:
        return self.session.post(
            f"{self.base_url}{self.api_prefix}/query",
            json={"query": question},
            timeout=self.timeout,
        )

    def responses(
        self,
        payload: dict,
        headers: dict[str, str] | None = None,
    ) -> requests.Response:
        """Send a Responses API request; callers must close streamed responses."""
        return self.session.post(
            f"{self.base_url}{self.api_prefix}/responses",
            json=payload,
            headers=headers,
            stream=payload.get("stream", False),
            timeout=self.timeout,
        )

    def metrics(self) -> requests.Response:
        return self.session.get(
            f"{self.base_url}/metrics",
            timeout=self.timeout,
        )

    def health(self) -> requests.Response:
        return self.session.get(
            f"{self.base_url}/health",
            timeout=self.timeout,
        )
