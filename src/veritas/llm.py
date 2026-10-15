"""Provider-specific code lives here only. The pipeline sees `LLM.complete(system, user) -> str`."""
from __future__ import annotations

import os
import subprocess
import tempfile
import time
from typing import Protocol


class LLM(Protocol):
    name: str

    def complete(self, system: str, user: str) -> str: ...


class LLMError(Exception):
    pass


class AnthropicLLM:
    """Direct Messages API call. No tools are passed, so the model cannot act on injected text."""

    def __init__(self, model: str, max_tokens: int = 16000):
        key = os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise LLMError("ANTHROPIC_API_KEY is not set")
        import anthropic

        self._client = anthropic.Anthropic(api_key=key, timeout=180.0, max_retries=4)
        self.usage = {"input_tokens": 0, "output_tokens": 0, "calls": 0}
        self.model, self.max_tokens, self.name = model, max_tokens, f"anthropic:{model}"

    def complete(self, system: str, user: str) -> str:
        import anthropic

        try:
            r = self._client.messages.create(
                model=self.model, max_tokens=self.max_tokens,
                system=system, messages=[{"role": "user", "content": user}],
            )
        except anthropic.APIError as e:
            raise LLMError(f"Anthropic API error: {e}") from e
        self.usage["input_tokens"] += r.usage.input_tokens
        self.usage["output_tokens"] += r.usage.output_tokens
        self.usage["calls"] += 1
        return "".join(b.text for b in r.content if b.type == "text")


class ClaudeCLILLM:
    """Uses a logged-in `claude` CLI as a text-only model: all tools disabled, empty temp cwd."""

    def __init__(self, model: str, timeout: int = 300):
        self.model, self.timeout, self.name = model, timeout, f"claude-cli:{model}"

    def complete(self, system: str, user: str) -> str:
        with tempfile.TemporaryDirectory() as cwd:
            try:
                r = subprocess.run(
                    ["claude", "-p", "--tools", "", "--model", self.model, "--system-prompt", system,
                     "--no-session-persistence", "--disable-slash-commands", "--setting-sources", ""],
                    input=user, capture_output=True, text=True, timeout=self.timeout, cwd=cwd,
                )
            except (subprocess.TimeoutExpired, FileNotFoundError) as e:
                raise LLMError(f"claude CLI failed: {e}") from e
        if r.returncode != 0 or not r.stdout.strip():
            raise LLMError(f"claude CLI exit {r.returncode}: {r.stderr.strip()[:300]}")
        return r.stdout


class ScriptedLLM:
    """Offline test double: returns canned responses in order. Never used for live runs."""

    name = "scripted-mock"

    def __init__(self, responses: list[str]):
        self.responses, self.calls = list(responses), []

    def complete(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        if not self.responses:
            raise LLMError("ScriptedLLM exhausted")
        return self.responses.pop(0)


def load_dotenv(path: str = ".env") -> None:
    if not os.path.exists(path):
        return
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            if v.strip():
                os.environ.setdefault(k.strip(), v.strip())


def make_llm() -> LLM:
    load_dotenv()
    provider = os.environ.get("SRV_PROVIDER", "claude-cli")
    model = os.environ.get("SRV_MODEL", "sonnet")
    if provider == "anthropic":
        return AnthropicLLM(model)
    if provider == "claude-cli":
        return ClaudeCLILLM(model)
    raise LLMError(f"Unknown SRV_PROVIDER {provider!r}")


def timed(fn, *a):
    t = time.time()
    out = fn(*a)
    return out, time.time() - t
