"""Deterministic PHOENIX server for UI regression tests: scripted model + mocked web, temp database, port 8001.

Run:  .venv\\Scripts\\python tests\\fake_server.py      (env FAKE_RAM=500 simulates low free memory)
Never used by the real app; it only exists so browser tests can exercise web/citation/offline/memory UI states.
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path
from urllib.parse import unquote_plus

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402
import uvicorn  # noqa: E402

from app import research, sysinfo  # noqa: E402
from app.main import create_app  # noqa: E402
from app.providers import ChatChunk, ModelProvider  # noqa: E402
from test_weather import GEO, forecast_json  # noqa: E402

PAGE = """<html><head><title>Prime Minister of Japan - Encyclopedia</title></head><body>
<p>The prime minister of Japan is the head of government of Japan.</p>
<table><tr><th>Incumbent</th><td>Jane Example</td></tr></table></body></html>"""


class Fake(ModelProvider):
    async def health(self): return {"connected": True, "version": "fake", "url": "fake"}
    async def list_models(self): return [{"name": "qwen2.5-coder:3b", "size": 2 * 2**30, "parameter_size": "3.1B"},
                                          {"name": "qwen2.5-coder:7b", "size": 4 * 2**30, "parameter_size": "7B"}]
    async def loaded_models(self): return [{"name": "qwen2.5-coder:3b", "size": 2 * 2**30}]
    async def aclose(self): pass

    def reply(self, messages) -> str:
        system, last = messages[0]["content"] if messages[0]["role"] == "system" else "", messages[-1]["content"]
        if "You edit Python files" in system:
            return "```python\ndef add(a, b):\n    return a + b\n```"
        if "fabricate-test" in last:
            return "Jane Example holds the office [1], according to [9]."
        if "UNTRUSTED WEB SOURCES" in last:
            return "The incumbent is Jane Example [1]."
        if "could not verify it live" in system:
            return "I could not verify this live; my knowledge may be out of date."
        if "UNTRUSTED PROJECT EXCERPTS" in last:
            return "This file defines `add`, which currently subtracts."
        return "Hello from the fake model.\n\n```python\nprint('hi')\n```"

    async def chat_stream(self, model, messages, options, timeout):
        text = self.reply(messages)
        for i in range(0, len(text), 4):
            await asyncio.sleep(0.03)
            yield ChatChunk(content=text[i:i + 4])
        yield ChatChunk(done=True, metrics={"completion_tokens": len(text) // 4, "tokens_per_sec": 12.3})


def mock_web() -> research.WebClient:
    def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        if "offline-test" in unquote_plus(url):
            raise httpx.ConnectError("simulated outage")
        host, path = req.url.host, req.url.path
        if path == "/robots.txt":
            return httpx.Response(404)
        if host == "geocoding-api.open-meteo.com":     # same real-shaped data as tests/test_weather.py
            return httpx.Response(200, json={"results": GEO.get(req.url.params.get("name", "").lower(), [])})
        if host == "api.open-meteo.com":
            return httpx.Response(200, json=forecast_json(float(req.url.params["latitude"]), float(req.url.params["longitude"])))
        if host == "html.duckduckgo.com":
            link = "https%3A%2F%2Fnews.test%2Fpm"
            return httpx.Response(200, text=f'<a class="result__a" href="//duckduckgo.com/l/?uddg={link}">Prime Minister of Japan</a>'
                                            '<a class="result__snippet" href="x">The prime minister of Japan is the head of government.</a>')
        if host == "en.wikipedia.org":
            return httpx.Response(200, json={"query": {"search": [{"title": "Python", "snippet": "A language"}]}})
        if host == "news.test":
            return httpx.Response(200, text=PAGE, headers={"content-type": "text/html"})
        return httpx.Response(404)

    return research.WebClient(transport=httpx.MockTransport(handler), resolver=lambda h: True)


if __name__ == "__main__":
    if os.environ.get("FAKE_RAM"):
        ram = int(os.environ["FAKE_RAM"])
        sysinfo.memory = lambda: {"total_mb": 12000, "available_mb": ram}
    tmp = tempfile.mkdtemp(prefix="phoenix-fake-")
    app = create_app(Path(tmp) / "fake.db", Fake(), mock_web())
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("FAKE_PORT", "8001")), log_level="warning")
