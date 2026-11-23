"""Minimal LLM clients: Ollama (local), any OpenAI-compatible endpoint, and record/replay.

LLMs are optional everywhere in OntoMem. They are used for:
  * extraction (``LLMExtractor``) and SHACL repair,
  * the S2 / S2b baselines (Mem0-style and ontology-constrained key assignment),
  * optional natural-language phrasing of replies.

``RecordingLLM`` saves every request/response to a JSONL cassette and ``ReplayLLM``
plays it back, so LLM experiments are reproducible and tests run without a model.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


class LLMError(RuntimeError):
    pass


class LLMClient(ABC):
    name = "llm"
    calls = 0
    prompt_tokens = 0
    completion_tokens = 0
    seconds = 0.0

    @abstractmethod
    def _chat(self, messages: list[dict[str, str]], schema: dict | None, temperature: float) -> tuple[str, dict]:
        """Return (content, usage)."""

    def chat(self, messages: list[dict[str, str]], schema: dict | None = None, temperature: float = 0.0) -> str:
        t0 = time.perf_counter()
        content, usage = self._chat(messages, schema, temperature)
        self.seconds += time.perf_counter() - t0
        self.calls += 1
        self.prompt_tokens += int(usage.get("prompt_tokens", 0))
        self.completion_tokens += int(usage.get("completion_tokens", 0))
        return content

    def chat_json(self, messages: list[dict[str, str]], schema: dict | None = None, temperature: float = 0.0) -> Any:
        text = self.chat(messages, schema, temperature)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            start, end = text.find("{"), text.rfind("}")
            if start >= 0 and end > start:
                try:
                    return json.loads(text[start : end + 1])
                except json.JSONDecodeError:
                    pass
            raise LLMError(f"model did not return JSON: {text[:200]!r}") from None

    def usage(self) -> dict[str, Any]:
        return {
            "model": self.name,
            "calls": self.calls,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "seconds": round(self.seconds, 2),
        }


class OllamaLLM(LLMClient):
    """Ollama's /api/chat with JSON-schema structured output (Ollama >= 0.5)."""

    def __init__(self, model: str = "qwen2.5:7b-instruct", host: str | None = None, timeout: float = 120) -> None:
        self.model = model
        self.name = f"ollama:{model}"
        self.host = (host or os.environ.get("OLLAMA_HOST", "http://localhost:11434")).rstrip("/")
        self.timeout = timeout

    def _chat(self, messages, schema, temperature):
        import httpx

        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": temperature, "seed": 7},
        }
        if schema is not None:
            body["format"] = schema
        try:
            r = httpx.post(f"{self.host}/api/chat", json=body, timeout=self.timeout)
            r.raise_for_status()
        except Exception as e:  # pragma: no cover - needs a running server
            raise LLMError(f"Ollama request failed ({self.host}): {e}") from e
        data = r.json()
        usage = {"prompt_tokens": data.get("prompt_eval_count", 0), "completion_tokens": data.get("eval_count", 0)}
        return data["message"]["content"], usage


class OpenAICompatibleLLM(LLMClient):
    """Any server that speaks the OpenAI chat-completions API (vLLM, LM Studio, llama.cpp, ...)."""

    def __init__(self, model: str, base_url: str, api_key: str | None = None, timeout: float = 120) -> None:
        self.model = model
        self.name = f"openai-compat:{model}"
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "not-needed")
        self.timeout = timeout

    def _chat(self, messages, schema, temperature):
        import httpx

        body: dict[str, Any] = {"model": self.model, "messages": messages, "temperature": temperature}
        if schema is not None:
            body["response_format"] = {"type": "json_schema", "json_schema": {"name": "out", "schema": schema}}
        try:
            r = httpx.post(
                f"{self.base_url}/chat/completions",
                json=body,
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=self.timeout,
            )
            r.raise_for_status()
        except Exception as e:  # pragma: no cover
            raise LLMError(f"request failed ({self.base_url}): {e}") from e
        data = r.json()
        return data["choices"][0]["message"]["content"], data.get("usage", {})


def _key(messages: list[dict[str, str]], schema: dict | None) -> str:
    blob = json.dumps({"m": messages, "s": schema}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:24]


class RecordingLLM(LLMClient):
    """Wraps a real client and appends every exchange to a JSONL cassette."""

    def __init__(self, inner: LLMClient, cassette: str | Path) -> None:
        self.inner = inner
        self.name = inner.name
        self.path = Path(cassette)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _chat(self, messages, schema, temperature):
        content, usage = self.inner._chat(messages, schema, temperature)
        with self.path.open("a") as f:
            f.write(json.dumps({"key": _key(messages, schema), "content": content, "usage": usage}) + "\n")
        return content, usage


class ReplayLLM(LLMClient):
    """Plays back a cassette. Unknown requests raise, so a replay can never silently invent output."""

    def __init__(self, cassette: str | Path | None = None, responses: dict[str, str] | None = None, name: str = "replay") -> None:
        self.name = name
        self.table: dict[str, tuple[str, dict]] = {}
        if cassette and Path(cassette).exists():
            for line in Path(cassette).read_text().splitlines():
                rec = json.loads(line)
                self.table[rec["key"]] = (rec["content"], rec.get("usage", {}))
        for k, v in (responses or {}).items():
            self.table[k] = (v, {})

    def _chat(self, messages, schema, temperature):
        k = _key(messages, schema)
        if k not in self.table:
            raise LLMError(f"no recorded response for request {k}")
        return self.table[k]


class ScriptedLLM(LLMClient):
    """Test double: a function from the last user message to a JSON-serialisable answer."""

    def __init__(self, fn, name: str = "scripted") -> None:
        self.fn = fn
        self.name = name

    def _chat(self, messages, schema, temperature):
        out = self.fn(messages[-1]["content"], messages)
        return (out if isinstance(out, str) else json.dumps(out)), {"prompt_tokens": 0, "completion_tokens": 0}


def from_env() -> LLMClient | None:
    """Build a client from ONTOMEM_LLM, e.g. ``ollama:qwen2.5:7b-instruct`` or
    ``openai:http://localhost:8000/v1:my-model``. Returns None when unset."""
    spec = os.environ.get("ONTOMEM_LLM", "").strip()
    if not spec:
        return None
    if spec.startswith("ollama:"):
        return OllamaLLM(spec.removeprefix("ollama:"))
    if spec.startswith("openai:"):
        rest = spec.removeprefix("openai:")
        base, _, model = rest.rpartition(":")
        return OpenAICompatibleLLM(model=model, base_url=base)
    raise ValueError(f"unknown ONTOMEM_LLM spec {spec!r}")
