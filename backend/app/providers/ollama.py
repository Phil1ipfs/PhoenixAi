from __future__ import annotations

import json
from typing import AsyncIterator

import httpx

from .base import ChatChunk, ModelProvider, ProviderError


class OllamaProvider(ModelProvider):
    def __init__(self, base_url: str = "http://127.0.0.1:11434", transport: httpx.AsyncBaseTransport | None = None):
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(base_url=self.base_url, transport=transport)

    async def health(self) -> dict:
        try:
            r = await self._client.get("/api/version", timeout=3)
            r.raise_for_status()
            return {"connected": True, "version": r.json().get("version"), "url": self.base_url}
        except Exception as e:  # noqa: BLE001
            return {"connected": False, "error": str(e) or type(e).__name__, "url": self.base_url}

    async def list_models(self) -> list[dict]:
        try:
            r = await self._client.get("/api/tags", timeout=5)
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise ProviderError("unreachable", f"Cannot reach Ollama at {self.base_url}: {e}") from e
        return [
            {
                "name": m["name"],
                "size": m.get("size", 0),
                "parameter_size": m.get("details", {}).get("parameter_size"),
                "quantization": m.get("details", {}).get("quantization_level"),
            }
            for m in r.json().get("models", [])
        ]

    async def loaded_models(self) -> list[dict]:
        try:
            r = await self._client.get("/api/ps", timeout=3)
            r.raise_for_status()
            return [{"name": m["name"], "size": m.get("size", 0)} for m in r.json().get("models", [])]
        except httpx.HTTPError:
            return []

    async def chat_stream(
        self, model: str, messages: list[dict], options: dict, timeout: float
    ) -> AsyncIterator[ChatChunk]:
        options = dict(options)
        keep_alive = options.pop("keep_alive", None)
        payload = {"model": model, "messages": messages, "stream": True, "options": options}
        if keep_alive not in (None, ""):
            payload["keep_alive"] = int(keep_alive) if str(keep_alive).lstrip("-").isdigit() else keep_alive
        # timeout applies to connect and to gaps between chunks (first token can be slow on cold load)
        t = httpx.Timeout(timeout, connect=5)
        try:
            async with self._client.stream("POST", "/api/chat", json=payload, timeout=t) as r:
                if r.status_code != 200:
                    body = (await r.aread()).decode(errors="replace")
                    msg = _extract_error(body) or f"HTTP {r.status_code}"
                    raise ProviderError(_classify(r.status_code, msg), msg)
                async for line in r.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError as e:
                        raise ProviderError("bad_response", f"Invalid stream line: {line[:100]}") from e
                    if "error" in obj:
                        raise ProviderError(_classify(500, obj["error"]), obj["error"])
                    content = obj.get("message", {}).get("content", "")
                    if obj.get("done"):
                        yield ChatChunk(content=content, done=True, metrics=_metrics(obj))
                        return
                    yield ChatChunk(content=content)
        except httpx.TimeoutException as e:
            raise ProviderError("timeout", f"Model produced no output within {timeout:.0f}s") from e
        except httpx.ConnectError as e:
            raise ProviderError("unreachable", f"Cannot reach Ollama at {self.base_url}") from e
        except httpx.HTTPError as e:
            raise ProviderError("unreachable", f"Connection to Ollama failed: {e}") from e

    async def unload(self, model: str) -> None:
        try:
            await self._client.post("/api/generate", json={"model": model, "keep_alive": 0}, timeout=30)
        except httpx.HTTPError as e:
            raise ProviderError("unreachable", f"Cannot reach Ollama at {self.base_url}: {e}") from e

    async def aclose(self) -> None:
        await self._client.aclose()


def _extract_error(body: str) -> str | None:
    try:
        return json.loads(body).get("error")
    except Exception:  # noqa: BLE001
        return body[:200] or None


def _classify(status: int, msg: str) -> str:
    low = msg.lower()
    if status == 404 or "not found" in low:
        return "model_not_found"
    if "memory" in low or "oom" in low:
        return "out_of_memory"
    return "bad_response"


def _metrics(obj: dict) -> dict:
    ev, evd = obj.get("eval_count", 0), obj.get("eval_duration", 0)
    return {
        "prompt_tokens": obj.get("prompt_eval_count", 0),
        "completion_tokens": ev,
        "load_ms": round(obj.get("load_duration", 0) / 1e6),
        "total_ms": round(obj.get("total_duration", 0) / 1e6),
        "tokens_per_sec": round(ev / (evd / 1e9), 2) if evd else None,
    }
