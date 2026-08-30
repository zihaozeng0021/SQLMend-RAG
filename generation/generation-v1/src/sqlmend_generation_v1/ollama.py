"""Minimal stdlib HTTP client for the pinned local Ollama model."""

from __future__ import annotations

from dataclasses import dataclass
import json
import time
from typing import Any, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .io import canonical_json, sha256_bytes, sha256_json


class OllamaError(RuntimeError):
    """Base class for retryable Ollama transport or protocol errors."""


class OllamaHTTPError(OllamaError):
    pass


class OllamaProtocolError(OllamaError):
    pass


class ModelIdentityError(OllamaError):
    pass


@dataclass(frozen=True, slots=True)
class OllamaIdentity:
    model_tag: str
    model_digest: str
    ollama_version: str


@dataclass(frozen=True, slots=True)
class OllamaResponse:
    content: str
    raw_response_sha256: str
    request_sha256: str
    wall_ms: float
    ollama_total_ms: float
    load_ms: float
    prompt_eval_ms: float
    eval_ms: float
    prompt_tokens: int
    completion_tokens: int


def build_chat_payload(
    *,
    model_tag: str,
    messages: Sequence[Mapping[str, str]],
    output_schema: Mapping[str, Any],
    think: bool | str,
    options: Mapping[str, int | float],
) -> dict[str, Any]:
    return {
        "model": model_tag,
        "messages": [dict(message) for message in messages],
        "stream": False,
        "format": dict(output_schema),
        "think": think,
        "options": dict(options),
    }


def _duration_ms(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return max(0.0, float(value) / 1_000_000.0)


def _token_count(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return max(0, int(value))


class OllamaClient:
    def __init__(self, base_url: str, *, timeout_seconds: float = 300.0):
        parsed = urlparse(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Ollama base_url must be an absolute HTTP(S) URL")
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = float(timeout_seconds)

    def _request_json(
        self,
        method: str,
        path: str,
        payload: Mapping[str, Any] | None = None,
    ) -> tuple[dict[str, Any], bytes]:
        data = None if payload is None else canonical_json(payload).encode("utf-8")
        request = Request(
            self.base_url + path,
            data=data,
            method=method,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read()
        except HTTPError as exc:
            try:
                detail = exc.read().decode("utf-8", errors="replace")
            except OSError:
                detail = ""
            raise OllamaHTTPError(f"Ollama HTTP {exc.code}: {detail[:500]}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise OllamaHTTPError(f"Ollama request failed: {exc}") from exc
        try:
            value = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise OllamaProtocolError("Ollama returned non-JSON data") from exc
        if not isinstance(value, dict):
            raise OllamaProtocolError("Ollama response is not a JSON object")
        if value.get("error"):
            raise OllamaProtocolError(f"Ollama error: {value['error']}")
        return value, raw

    def preflight(self, expected_tag: str, expected_digest: str) -> OllamaIdentity:
        version_response, _ = self._request_json("GET", "/api/version")
        version = version_response.get("version")
        if not isinstance(version, str) or not version:
            raise OllamaProtocolError("Ollama /api/version omitted version")
        tags_response, _ = self._request_json("GET", "/api/tags")
        models = tags_response.get("models")
        if not isinstance(models, list):
            raise OllamaProtocolError("Ollama /api/tags omitted models")
        matching = [
            model
            for model in models
            if isinstance(model, dict)
            and (model.get("name") == expected_tag or model.get("model") == expected_tag)
        ]
        if len(matching) != 1:
            raise ModelIdentityError(
                f"Expected exactly one installed Ollama model named {expected_tag!r}"
            )
        digest = matching[0].get("digest")
        if digest != expected_digest:
            raise ModelIdentityError(
                f"Ollama model digest mismatch for {expected_tag}: {digest!r}"
            )
        return OllamaIdentity(expected_tag, expected_digest, version)

    def chat(
        self,
        *,
        messages: Sequence[Mapping[str, str]],
        output_schema: Mapping[str, Any],
        model_tag: str,
        think: bool | str,
        options: Mapping[str, int | float],
    ) -> OllamaResponse:
        payload = build_chat_payload(
            model_tag=model_tag,
            messages=messages,
            output_schema=output_schema,
            think=think,
            options=options,
        )
        started = time.perf_counter()
        response, raw = self._request_json("POST", "/api/chat", payload)
        wall_ms = (time.perf_counter() - started) * 1000.0
        response_model = response.get("model")
        if response_model != model_tag:
            raise OllamaProtocolError(
                f"Ollama response model differs: {response_model!r} != {model_tag!r}"
            )
        message = response.get("message")
        if not isinstance(message, dict) or not isinstance(message.get("content"), str):
            raise OllamaProtocolError("Ollama response omitted message.content")
        if response.get("done") is not True:
            raise OllamaProtocolError("Ollama non-streaming response is not complete")
        return OllamaResponse(
            content=message["content"],
            raw_response_sha256=sha256_bytes(raw),
            request_sha256=sha256_json(payload),
            wall_ms=wall_ms,
            ollama_total_ms=_duration_ms(response.get("total_duration")),
            load_ms=_duration_ms(response.get("load_duration")),
            prompt_eval_ms=_duration_ms(response.get("prompt_eval_duration")),
            eval_ms=_duration_ms(response.get("eval_duration")),
            prompt_tokens=_token_count(response.get("prompt_eval_count")),
            completion_tokens=_token_count(response.get("eval_count")),
        )
