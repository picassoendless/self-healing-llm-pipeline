"""
patches/config.py
=================
Configuration dataclass for all patch mechanisms.

Loaded from a YAML file, controls which patches are active
and what their parameters are. Every toggle maps directly
to a patch class in prompt_patches.py or output_patches.py.
"""

from __future__ import annotations

import yaml
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class PatchConfig:
    """
    Central configuration for the patch engine.

    All boolean flags can be toggled independently in config/patches.yaml,
    enabling ablation studies without touching any code.
    """

    # ── Prompt-level toggles ──────────────────────────────────────────
    enable_injection_guardrail: bool = True   # block injection attempts
    enable_system_hardening: bool = True      # prepend security prefix
    enable_input_sanitizer: bool = True       # strip obfuscation tricks

    # ── Output-level toggles ──────────────────────────────────────────
    enable_toxicity_filter: bool = True       # heuristic toxicity scoring
    enable_keyword_filter: bool = True        # explicit keyword blocklist
    enable_response_rewriter: bool = False    # LLM-assisted rewrite (costly)

    # ── Probe-specific semantic filters ───────────────────────────────
    enable_deadnaming_filter: bool = True     # structural regex + LLM judge
    enable_quack_medicine_filter: bool = True # hedge+list pattern + LLM judge
    enable_sexualisation_filter: bool = True  # objectification patterns + LLM judge
    enable_llm_judge: bool = True            # enables Stage B in semantic filters

    # ── Parameters ────────────────────────────────────────────────────
    injection_patterns: list[str] = field(default_factory=list)
    blocked_keywords: list[str] = field(default_factory=list)
    toxicity_threshold: float = 0.7
    hardened_system_prefix: str = ""
    fallback_message: str = "I'm not able to help with that request."
    rewriter_model: Optional[str] = None

    @classmethod
    def from_yaml(cls, path: str) -> "PatchConfig":
        """Load config from a YAML file, ignoring unknown keys."""
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        return cls(**{k: v for k, v in data.items()
                      if k in cls.__dataclass_fields__})
