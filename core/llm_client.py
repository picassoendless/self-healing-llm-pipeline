"""
LLM Client — pluggable backend abstraction.

Supported backends:
    • openai  — OpenAI GPT-4 (via openai SDK)
    • claude  — Anthropic Claude (via anthropic SDK)

Usage:
    client = LLMClient(backend=LLMBackend.OPENAI, model="gpt-4")
    response = client.complete(system="You are helpful.", user="Hello!")
"""

from __future__ import annotations

import os
import logging
from enum import Enum
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger("llm_client")


class LLMBackend(str, Enum):
    OPENAI = "openai"
    CLAUDE = "claude"


@dataclass
class LLMRequest:
    user: str
    system: Optional[str] = None
    temperature: float = 0.0
    max_tokens: int = 1024


@dataclass
class LLMResponse:
    text: str
    model: str
    backend: str
    prompt_tokens: int = 0
    completion_tokens: int = 0


class LLMClient:
    """Unified interface for OpenAI and Claude backends."""

    def __init__(self, backend: LLMBackend, model: str, **kwargs):
        self.backend = backend
        self.model = model
        self._client = self._build_client()

    def complete(self, request: LLMRequest) -> LLMResponse:
        dispatch = {
            LLMBackend.OPENAI: self._complete_openai,
            LLMBackend.CLAUDE: self._complete_claude,
        }
        return dispatch[self.backend](request)

    def chat(self, system: str, user: str) -> str:
        return self.complete(LLMRequest(user=user, system=system)).text

    def _build_client(self):
        if self.backend == LLMBackend.OPENAI:
            try:
                import openai
                return openai.OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
            except ImportError:
                raise ImportError("pip install openai")

        elif self.backend == LLMBackend.CLAUDE:
            try:
                import anthropic
                return anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
            except ImportError:
                raise ImportError("pip install anthropic")

        raise ValueError(f"Unsupported backend: {self.backend}")

    def _complete_openai(self, req: LLMRequest) -> LLMResponse:
        messages = []
        if req.system:
            messages.append({"role": "system", "content": req.system})
        messages.append({"role": "user", "content": req.user})

        response = self._client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=req.temperature,
            max_tokens=req.max_tokens,
        )
        choice = response.choices[0]
        return LLMResponse(
            text=choice.message.content,
            model=response.model,
            backend="openai",
            prompt_tokens=response.usage.prompt_tokens,
            completion_tokens=response.usage.completion_tokens,
        )

    def _complete_claude(self, req: LLMRequest) -> LLMResponse:
        messages = [{"role": "user", "content": req.user}]
        kwargs = dict(model=self.model, max_tokens=req.max_tokens, messages=messages)
        if req.system:
            kwargs["system"] = req.system

        response = self._client.messages.create(**kwargs)
        return LLMResponse(
            text=response.content[0].text,
            model=response.model,
            backend="claude",
            prompt_tokens=response.usage.input_tokens,
            completion_tokens=response.usage.output_tokens,
        )
