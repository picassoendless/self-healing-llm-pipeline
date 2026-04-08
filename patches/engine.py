"""
patches/engine.py
=================
PatchEngine — orchestrates prompt-level and output-level patches.

This is the single entry point used by the proxy. It loads all
configured patches from PatchConfig and exposes two methods:

    sanitize_input(user, system)  → (clean_user, hardened_system)
    sanitize_output(response)     → clean_response

The proxy calls sanitize_input() before forwarding to the LLM,
and sanitize_output() before returning the response to Garak.
"""

from __future__ import annotations

import logging
from typing import Optional, TYPE_CHECKING

from patches.config import PatchConfig
from patches.prompt_patches import (
    BasePatch,
    PromptInjectionGuardrail,
    SystemPromptHardener,
    InputSanitizer,
)
from patches.output_patches import (
    ToxicityFilter,
    KeywordPolicyFilter,
    ResponseRewriter,
    DeadnamingFilter,
    QuackMedicineFilter,
    SexualisationFilter,
)

if TYPE_CHECKING:
    from core.llm_client import LLMClient

log = logging.getLogger("patches.engine")


class PatchEngine:
    """
    Loads and runs all configured patch mechanisms.

    Prompt-level patches run in sequence on the user input and
    system prompt before forwarding to the LLM.

    Output-level patches run in sequence on the model response
    before returning to the caller.

    All patches are independently toggleable via PatchConfig,
    enabling ablation studies without code changes.
    """

    def __init__(
        self,
        config: PatchConfig,
        llm_client: Optional["LLMClient"] = None,
        judge_client: Optional["LLMClient"] = None,
    ):
        self.config = config
        # judge_client is used for LLM-as-judge calls in semantic filters.
        # Falls back to llm_client if no dedicated judge client is provided.
        _judge = judge_client if judge_client is not None else llm_client

        # ── Prompt-level ──────────────────────────────────────────────
        self._prompt_patches: list[BasePatch] = []
        if config.enable_injection_guardrail:
            self._prompt_patches.append(PromptInjectionGuardrail(config))
        if config.enable_input_sanitizer:
            self._prompt_patches.append(InputSanitizer(config))

        self._system_hardener: Optional[SystemPromptHardener] = None
        if config.enable_system_hardening:
            self._system_hardener = SystemPromptHardener(config)

        # ── Output-level ──────────────────────────────────────────────
        self._output_patches: list[BasePatch] = []
        if config.enable_toxicity_filter:
            self._output_patches.append(ToxicityFilter(config))
        if config.enable_keyword_filter:
            self._output_patches.append(KeywordPolicyFilter(config))
        if config.enable_deadnaming_filter:
            self._output_patches.append(DeadnamingFilter(config, _judge))
        if config.enable_quack_medicine_filter:
            self._output_patches.append(QuackMedicineFilter(config, _judge))
        if config.enable_sexualisation_filter:
            self._output_patches.append(SexualisationFilter(config, _judge))
        if config.enable_response_rewriter and llm_client is not None:
            self._output_patches.append(ResponseRewriter(config, llm_client))

        active = (
            [p.name for p in self._prompt_patches]
            + (["SystemPromptHardener"] if self._system_hardener else [])
            + [p.name for p in self._output_patches]
        )
        log.info("PatchEngine initialised — active patches: %s", active)

    def sanitize_input(
        self,
        user_prompt: str,
        system_prompt: str = "",
    ) -> tuple[str, str]:
        """
        Run all prompt-level patches.
        Returns (sanitized_user_prompt, hardened_system_prompt).
        """
        for patch in self._prompt_patches:
            user_prompt = patch.apply(user_prompt)

        if self._system_hardener and system_prompt is not None:
            system_prompt = self._system_hardener.apply(system_prompt)

        return user_prompt, system_prompt

    def sanitize_output(self, response: str) -> str:
        """Run all output-level patches and return the cleaned response."""
        for patch in self._output_patches:
            response = patch.apply(response)
        return response
