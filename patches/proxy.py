"""
Patch Proxy Server
==================

A lightweight FastAPI server that sits between Garak and OpenAI,
applying PatchEngine mitigations transparently at the network layer.

Architecture
------------
    Garak ──► localhost:8080/v1/chat/completions
                        │
                   PatchProxy
                        │
              ┌─────────┴──────────┐
              │   sanitize_input() │  ← prompt-level patches
              └─────────┬──────────┘
                        │
              ┌─────────▼──────────┐
              │      OpenAI API    │  ← real model call
              └─────────┬──────────┘
                        │
              ┌─────────▼──────────┐
              │  sanitize_output() │  ← output-level patches
              └─────────┬──────────┘
                        │
                     Garak ← cleaned response

Why this matters
----------------
Garak owns its own OpenAI connection — wrapping the Python client
object does nothing because Garak bypasses it entirely. The only
correct interception point is the network layer. By setting:

    OPENAI_BASE_URL=http://localhost:8080/v1

Garak sends all requests through this proxy, which applies patches
before forwarding upstream. The model sees hardened prompts; Garak
sees filtered responses. Fully model-agnostic — works with any scanner.

Research contribution
---------------------
This is the correct self-healing architecture: patches operate at the
API boundary, not inside the scanner or the model. This means patches
are portable across any tool that speaks the OpenAI chat completions
protocol (Garak, LangChain, custom apps).

Usage
-----
    # Start proxy (blocking — run in separate terminal or as thread)
    python patch_proxy.py --patch-config config/patches_synthesized.yaml --port 8080

    # Point Garak at proxy
    OPENAI_BASE_URL=http://localhost:8080/v1 python -m garak ...
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
import threading
from pathlib import Path
from typing import Optional

import requests
import uvicorn
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse

from patches import PatchEngine, PatchConfig

log = logging.getLogger("patch_proxy")


# ======================================================================
# FastAPI app
# ======================================================================

def create_app(patch_engine: PatchEngine, upstream: str = "https://api.openai.com") -> FastAPI:
    app = FastAPI(title="Self-Healing LLM Patch Proxy")

    @app.get("/health")
    def health():
        return {"status": "ok", "patches_active": True}

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request):
        body = await request.json()

        # ── PROMPT-LEVEL PATCH ──────────────────────────────────────────
        messages = body.get("messages", [])

        # Extract system and user content
        system_content = ""
        user_content = ""
        patched_messages = []

        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role == "system":
                system_content = content
            elif role == "user":
                user_content = content

        # Apply prompt-level patches
        clean_user, hardened_system = patch_engine.sanitize_input(
            user_content, system_content
        )

        # Log prompt-level patch activity clearly
        if clean_user != user_content:
            if clean_user.startswith("[BLOCKED BY GUARDRAIL]"):
                log.info("[PROMPT-LEVEL PATCH] Input BLOCKED by injection guardrail")
            else:
                log.info("[PROMPT-LEVEL PATCH] Input sanitized before forwarding to model")

        if hardened_system != system_content:
            log.info(
                "[PROMPT-LEVEL PATCH] System prompt hardened — added %d chars of security prefix",
                len(hardened_system) - len(system_content),
            )

        # Rebuild messages with patched content
        for msg in messages:
            role = msg.get("role", "")
            if role == "system":
                patched_messages.append({**msg, "content": hardened_system})
            elif role == "user":
                patched_messages.append({**msg, "content": clean_user})
            else:
                patched_messages.append(msg)

        # If input was blocked, return fallback immediately (no upstream call)
        if clean_user.startswith("[BLOCKED BY GUARDRAIL]"):
            return JSONResponse(_make_blocked_response(
                "I'm not able to process that request.",
                body.get("model", "gpt-3.5-turbo"),
            ))

        # ── FORWARD TO UPSTREAM ─────────────────────────────────────────
        patched_body = {**body, "messages": patched_messages}
        api_key = os.environ.get("OPENAI_API_KEY", "")

        upstream_url = f"{upstream}/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        try:
            upstream_resp = requests.post(
                upstream_url,
                json=patched_body,
                headers=headers,
                timeout=60,
            )
            upstream_resp.raise_for_status()
            response_data = upstream_resp.json()
        except requests.RequestException as e:
            log.error("Upstream call failed: %s", e)
            raise HTTPException(status_code=502, detail=f"Upstream error: {e}")

        # ── OUTPUT-LEVEL PATCH ──────────────────────────────────────────
        choices = response_data.get("choices", [])
        for choice in choices:
            raw_text = choice.get("message", {}).get("content", "")
            clean_text = patch_engine.sanitize_output(raw_text)
            if clean_text != raw_text:
                log.info("[OUTPUT-LEVEL PATCH] Response modified — unsafe content replaced")
            choice["message"]["content"] = clean_text

        response_data["choices"] = choices
        return JSONResponse(response_data)

    return app


def _make_blocked_response(fallback_text: str, model: str) -> dict:
    """Construct an OpenAI-format response for blocked requests."""
    return {
        "id": f"blocked-{int(time.time())}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": fallback_text},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


# ======================================================================
# Server lifecycle helpers (used by pipeline.py)
# ======================================================================

class ProxyServer:
    """
    Manages the proxy server lifecycle from within the pipeline.
    Starts in a background thread, shuts down after Stage 3.
    """

    def __init__(
        self,
        patch_config_path: str,
        port: int = 8080,
        host: str = "127.0.0.1",
        upstream: str = "https://api.openai.com",
    ):
        self.port = port
        self.host = host
        self.patch_config_path = patch_config_path
        self.upstream = upstream
        self._server: Optional[uvicorn.Server] = None
        self._thread: Optional[threading.Thread] = None

    def start(self):
        """Load patch config and start proxy in a background thread."""
        from core.llm_client import LLMClient, LLMBackend
        patch_cfg = PatchConfig.from_yaml(self.patch_config_path)

        llm_client = LLMClient(
            backend=LLMBackend(os.environ.get("LLM_BACKEND", "openai")),
            model=os.environ.get("LLM_MODEL", "gpt-3.5-turbo"),
        )
        patch_engine = PatchEngine(patch_cfg, llm_client=llm_client)
        app = create_app(patch_engine, upstream=self.upstream)

        config = uvicorn.Config(
            app,
            host=self.host,
            port=self.port,
            log_level="warning",  # keep proxy logs quiet during Garak scan
        )
        self._server = uvicorn.Server(config)

        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()

        # Wait until the server is actually accepting connections
        self._wait_ready()
        log.info("Patch proxy started on http://%s:%d", self.host, self.port)

    def stop(self):
        if self._server:
            self._server.should_exit = True
            self._thread.join(timeout=5)
            log.info("Patch proxy stopped")

    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}/v1"

    def _wait_ready(self, timeout: float = 10.0):
        import socket
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                with socket.create_connection((self.host, self.port), timeout=0.5):
                    return
            except OSError:
                time.sleep(0.1)
        raise RuntimeError(f"Proxy did not start within {timeout}s")


# ======================================================================
# Standalone entry point
# ======================================================================

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    )

    parser = argparse.ArgumentParser(description="Self-Healing LLM Patch Proxy")
    parser.add_argument("--patch-config", default="config/patches_synthesized.yaml")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--upstream", default="https://api.openai.com")
    args = parser.parse_args()

    patch_cfg = PatchConfig.from_yaml(args.patch_config)
    patch_engine = PatchEngine(patch_cfg)
    app = create_app(patch_engine, upstream=args.upstream)

    log.info("Starting patch proxy on http://%s:%d", args.host, args.port)
    log.info("Patches loaded from: %s", args.patch_config)
    log.info("Upstream: %s", args.upstream)
    log.info("Point Garak at this proxy with:")
    log.info("  $env:OPENAI_BASE_URL = 'http://%s:%d/v1'", args.host, args.port)

    uvicorn.run(app, host=args.host, port=args.port)
