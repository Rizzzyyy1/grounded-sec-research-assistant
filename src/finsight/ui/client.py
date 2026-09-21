"""Thin HTTP client for the FinSight API used by the Streamlit pages.

The UI never imports the retrieval or generation code: it talks to the same public API any other
client would, so what the UI shows is exactly what the API guarantees.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

DEFAULT_URL = "http://127.0.0.1:8000"


class ApiError(RuntimeError):
    """The API answered with an error status (message is the API's own explanation)."""


class ApiClient:
    def __init__(self, base_url: str | None = None, *, timeout: float = 180.0) -> None:
        self._http = httpx.Client(
            base_url=base_url or os.environ.get("FINSIGHT_API_URL", DEFAULT_URL), timeout=timeout
        )

    def _request(self, method: str, url: str, **kw: Any) -> Any:
        try:
            response = self._http.request(method, url, **kw)
        except httpx.TransportError as exc:
            raise ApiError(f"cannot reach the API at {self._http.base_url}: {exc}") from exc
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail", response.text)
            except ValueError:
                detail = response.text
            raise ApiError(f"{response.status_code}: {detail}")
        return response.json()

    def ready(self) -> dict[str, Any]:
        return self._request("GET", "/readyz")  # type: ignore[no-any-return]

    def query(self, question: str, mode: str = "auto") -> dict[str, Any]:
        return self._request("POST", "/v1/query", json={"question": question, "mode": mode})  # type: ignore[no-any-return]

    def companies(self) -> list[dict[str, Any]]:
        return self._request("GET", "/v1/companies")  # type: ignore[no-any-return]

    def financials(
        self, ticker: str, metrics: list[str], years: str | None = None
    ) -> dict[str, Any]:
        params = {"metrics": ",".join(metrics), **({"years": years} if years else {})}
        return self._request("GET", f"/v1/companies/{ticker}/financials", params=params)  # type: ignore[no-any-return]

    def ratios(self, ticker: str, names: list[str], years: str | None = None) -> dict[str, Any]:
        params = {"names": ",".join(names), **({"years": years} if years else {})}
        return self._request("GET", f"/v1/companies/{ticker}/ratios", params=params)  # type: ignore[no-any-return]
