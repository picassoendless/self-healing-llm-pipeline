"""
Vulnerability-Driven Patch Synthesizer (VDPS)
==============================================

The novel component of this pipeline.

Most self-healing systems apply STATIC patches — hardcoded keyword lists,
fixed system prompt prefixes, generic guardrails written before any scan runs.

VDPS does the opposite:
    1. Reads the actual attack prompts and outputs from the Garak hitlog
    2. Groups them by vulnerability category (probe)
    3. Calls an LLM to SYNTHESIZE targeted mitigations for exactly those patterns
    4. Writes a patches.yaml that the PatchEngine loads dynamically

The result: patches are generated FROM the wounds, not written in advance.

Research angle
--------------
This operationalises the "self-healing" metaphor literally — the system
reads its own failure cases and writes its own medicine. The open research
question (suitable for USENIX/CCS) is whether synthesized patches
GENERALISE beyond the specific attack instances they were derived from,
or whether they overfit (i.e. block the exact prompts seen but miss
paraphrases). This prototype is the first step toward answering that.

Architecture
------------
    GarakReport  ──►  AttackPatternExtractor  ──►  PatchLLM  ──►  patches.yaml
         │                     │                       │
    hitlog.jsonl         cluster by probe        synthesize:
    (raw attacks)        extract: prompt,        - system prompt additions
                         output, goal            - keyword blocklist
                                                 - injection patterns
"""

from __future__ import annotations

import json
import logging
import re
import yaml
from pathlib import Path
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.garak_runner import GarakReport
    from core.llm_client import LLMClient

log = logging.getLogger("patch_synthesizer")


# ======================================================================
# Attack pattern extraction
# ======================================================================

@dataclass
class AttackPattern:
    probe: str
    goal: str
    prompt: str
    output: str
    score: float


def extract_attack_patterns(hitlog_path: Path) -> list[AttackPattern]:
    """
    Read the Garak hitlog and extract structured attack patterns.
    Each line in the hitlog is a confirmed attack success.
    """
    patterns: list[AttackPattern] = []

    if not hitlog_path.exists():
        log.warning("Hitlog not found at %s — no patterns to extract", hitlog_path)
        return patterns

    with open(hitlog_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            patterns.append(AttackPattern(
                probe=entry.get("probe", "unknown"),
                goal=str(entry.get("goal", "")),
                prompt=str(entry.get("prompt", "")),
                output=str(entry.get("output", "")),
                score=float(entry.get("score", 1.0)),
            ))

    log.info("Extracted %d attack patterns from hitlog", len(patterns))
    return patterns


def group_by_probe(patterns: list[AttackPattern]) -> dict[str, list[AttackPattern]]:
    groups: dict[str, list[AttackPattern]] = {}
    for p in patterns:
        groups.setdefault(p.probe, []).append(p)
    return groups


# ======================================================================
# LLM-based patch synthesis
# ======================================================================

_SYNTHESIS_SYSTEM = """You are an LLM security engineer specializing in prompt injection defense and content safety.

You will be given a set of attack examples that successfully exploited a language model.
Each example shows:
- The probe category (type of attack)
- The attack goal
- The prompt that caused the failure
- The unsafe model output

Your job is to synthesize TARGETED mitigations for exactly these attack patterns.

You must respond with ONLY valid JSON (no markdown, no explanation) in this exact schema:
{
  "system_prompt_addition": "<string to prepend to the system prompt to defend against these attacks>",
  "blocked_keywords": ["<keyword1>", "<keyword2>"],
  "injection_patterns": ["<regex_pattern1>", "<regex_pattern2>"],
  "reasoning": "<1-2 sentences explaining the defense strategy>"
}

Rules:
- system_prompt_addition: specific, targeted instructions that address the attack patterns shown
- blocked_keywords: exact phrases or terms that appear in unsafe outputs
- injection_patterns: regex patterns matching the attack prompt structure
- Keep blocked_keywords and injection_patterns to the most impactful items only (max 5 each)
- Do NOT write generic safety rules — write rules targeted at the specific attacks shown
"""


def synthesize_patches_for_probe(
    probe: str,
    patterns: list[AttackPattern],
    llm: "LLMClient",
    max_examples: int = 5,
) -> dict:
    """
    Call the LLM to synthesize patches for a specific probe category.
    Returns a dict with system_prompt_addition, blocked_keywords, injection_patterns.
    """
    # Build the attack examples context (cap to avoid token bloat)
    examples = patterns[:max_examples]
    examples_text = ""
    for i, ex in enumerate(examples, 1):
        # Strip backslashes and control chars that break JSON parsing in the response
        prompt_clean = str(ex.prompt)[:300].replace("\\", " ").replace('"', "'")
        output_clean = str(ex.output)[:300].replace("\\", " ").replace('"', "'")
        examples_text += (
            f"\n--- Attack {i} ---\n"
            f"Probe: {ex.probe}\n"
            f"Goal: {ex.goal}\n"
            f"Prompt: {prompt_clean}\n"
            f"Unsafe output: {output_clean}\n"
        )

    user_message = (
        f"Synthesize mitigations for the following {len(examples)} "
        f"confirmed attack successes against probe category '{probe}':\n"
        f"{examples_text}\n\n"
        f"Return ONLY the JSON object."
    )

    log.info("Synthesizing patches for probe '%s' (%d examples)...", probe, len(examples))

    for attempt in range(3):
        try:
            response = llm.chat(system=_SYNTHESIS_SYSTEM, user=user_message)

            # Strip markdown fences
            clean = re.sub(r"```(?:json)?|```", "", response).strip()

            # Extract just the JSON object if there's surrounding text
            json_match = re.search(r'\{.*\}', clean, re.DOTALL)
            if json_match:
                clean = json_match.group(0)

            # Remove invalid backslash escapes
            clean = re.sub(r'\\(?!["\\/bfnrtu0-9])', r' ', clean)

            # Remove control characters
            clean = re.sub(r'[\x00-\x1f\x7f]', ' ', clean)

            result = json.loads(clean)
            log.info(
                "Synthesis complete for '%s' — reasoning: %s",
                probe,
                result.get("reasoning", ""),
            )
            return result
        except (json.JSONDecodeError, Exception) as e:
            log.warning("Synthesis attempt %d failed for probe '%s': %s",
                        attempt + 1, probe, e)
            if attempt == 0:
                user_message += (
                    "\n\nIMPORTANT: Return ONLY a valid JSON object. "
                    "No backslashes in string values. No special characters. "
                    "Use simple words only in string values."
                )
            elif attempt == 1:
                # Last resort — ask for minimal JSON
                user_message = (
                    f"For the probe '{probe}', return this exact JSON with your values filled in:\n"
                    '{"system_prompt_addition": "one sentence instruction here", '
                    '"blocked_keywords": ["word1", "word2"], '
                    '"injection_patterns": [], '
                    '"reasoning": "one sentence here"}'
                )
        except (json.JSONDecodeError, Exception) as e:
            log.warning("Synthesis attempt %d failed for probe '%s': %s", attempt + 1, probe, e)
            if attempt == 0:
                # Retry with a stricter prompt
                user_message += "\n\nIMPORTANT: Your previous response had invalid JSON. Return ONLY a valid JSON object with no backslashes in string values."

    log.error("Patch synthesis failed for probe '%s' after 2 attempts", probe)
    return {
        "system_prompt_addition": "",
        "blocked_keywords": [],
        "injection_patterns": [],
        "reasoning": "Synthesis failed after retry",
    }


# ======================================================================
# Main synthesizer class
# ======================================================================

class PatchSynthesizer:
    """
    Orchestrates the full VDPS pipeline:
        hitlog → extract patterns → synthesize per probe → merge → write YAML
    """

    def __init__(self, llm: "LLMClient", output_path: str = "config/patches_synthesized.yaml"):
        self.llm = llm
        self.output_path = Path(output_path)

    def run(self, report: "GarakReport", hitlog_path: Path) -> Path:
        """
        Run VDPS. Returns path to the generated patches YAML.
        """
        log.info("=== VDPS — Vulnerability-Driven Patch Synthesis ===")

        # 1. Extract attack patterns from hitlog
        patterns = extract_attack_patterns(hitlog_path)
        if not patterns:
            log.warning("No attack patterns found — writing empty patch config")
            return self._write_empty(report)

        # 2. Group by probe
        grouped = group_by_probe(patterns)
        log.info("Attack patterns by probe: %s",
                 {k: len(v) for k, v in grouped.items()})

        # 3. Synthesize patches per probe
        all_system_additions: list[str] = []
        all_blocked_keywords: list[str] = []
        all_injection_patterns: list[str] = []
        synthesis_log: list[dict] = []

        for probe, probe_patterns in grouped.items():
            result = synthesize_patches_for_probe(probe, probe_patterns, self.llm)
            if result.get("system_prompt_addition"):
                all_system_additions.append(
                    f"# [{probe}]\n{result['system_prompt_addition']}"
                )
            all_blocked_keywords.extend(result.get("blocked_keywords", []))
            all_injection_patterns.extend(result.get("injection_patterns", []))
            synthesis_log.append({"probe": probe, **result})

        # 4. Deduplicate
        all_blocked_keywords = list(dict.fromkeys(all_blocked_keywords))
        all_injection_patterns = list(dict.fromkeys(all_injection_patterns))

        # 5. Build merged system prompt hardener prefix
        hardened_prefix = (
            "You are a secure AI assistant. "
            "You must NEVER produce harmful, dangerous, or policy-violating content.\n\n"
            "SPECIFIC DEFENSES ACTIVE FOR THIS SESSION:\n"
            + "\n".join(all_system_additions)
            + "\n\n"
        )

        # 6. Write patches YAML
        patch_config = {
            # Prompt-level
            "enable_injection_guardrail": True,
            "enable_system_hardening": True,
            "enable_input_sanitizer": True,
            "injection_patterns": all_injection_patterns,
            "hardened_system_prefix": hardened_prefix,

            # Output-level generic
            "enable_toxicity_filter": True,
            "enable_keyword_filter": True,
            "enable_response_rewriter": False,
            "blocked_keywords": all_blocked_keywords,
            "toxicity_threshold": 0.7,

            # Probe-specific targeted filters + LLM judge
            "enable_deadnaming_filter": True,
            "enable_quack_medicine_filter": True,
            "enable_sexualisation_filter": True,
            "enable_llm_judge": True,

            "fallback_message": (
                "I'm not able to help with that. "
                "If you have a genuine question, please rephrase it."
            ),

            # Metadata for reproducibility
            "_synthesis_metadata": {
                "probes_analysed": list(grouped.keys()),
                "total_attack_patterns": len(patterns),
                "synthesis_log": synthesis_log,
            },
        }

        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.output_path, "w", encoding="utf-8") as f:
            yaml.dump(patch_config, f, allow_unicode=True, sort_keys=False, width=120)

        log.info("Synthesized patch config written to %s", self.output_path)
        log.info(
            "Summary — system additions: %d probes | keywords: %d | patterns: %d",
            len(all_system_additions),
            len(all_blocked_keywords),
            len(all_injection_patterns),
        )
        return self.output_path

    def _write_empty(self, report: "GarakReport") -> Path:
        patch_config = {
            "enable_injection_guardrail": True,
            "enable_system_hardening": True,
            "enable_input_sanitizer": True,
            "enable_toxicity_filter": True,
            "enable_keyword_filter": True,
            "enable_response_rewriter": False,
            "injection_patterns": [],
            "hardened_system_prefix": "",
            "blocked_keywords": [],
            "toxicity_threshold": 0.7,
            "fallback_message": "I'm not able to help with that.",
            "_synthesis_metadata": {"note": "No attack patterns found in hitlog"},
        }
        with open(self.output_path, "w", encoding="utf-8") as f:
            yaml.dump(patch_config, f, allow_unicode=True, sort_keys=False)
        return self.output_path
