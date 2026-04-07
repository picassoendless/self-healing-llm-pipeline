"""
Evaluator — before/after comparison of Garak reports.
Produces quantitative and qualitative diff tables.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from garak_runner import GarakReport

log = logging.getLogger("evaluator")


def compare_reports(baseline: "GarakReport", patched: "GarakReport") -> dict:
    """
    Compute quantitative comparison between two Garak reports.

    Returns a dict with:
        delta_pp          — percentage-point reduction in attack pass-rate
        baseline_rate     — baseline attack pass-rate (0-1)
        patched_rate      — patched attack pass-rate (0-1)
        per_probe         — per-probe breakdown
        examples          — qualitative before/after pairs
    """
    delta = (baseline.pass_rate - patched.pass_rate) * 100

    # Per-probe breakdown
    base_by_probe = _group_by_probe(baseline)
    patch_by_probe = _group_by_probe(patched)
    all_probes = set(base_by_probe) | set(patch_by_probe)
    per_probe = {}
    for probe in sorted(all_probes):
        b = base_by_probe.get(probe, {"total": 0, "succeeded": 0})
        p = patch_by_probe.get(probe, {"total": 0, "succeeded": 0})
        per_probe[probe] = {
            "baseline_succeeded": b["succeeded"],
            "patched_succeeded": p["succeeded"],
            "delta": b["succeeded"] - p["succeeded"],
        }

    # Qualitative examples — find probes that changed
    examples = _pick_examples(baseline, patched)

    comparison = {
        "baseline_rate": round(baseline.pass_rate, 4),
        "patched_rate": round(patched.pass_rate, 4),
        "delta_pp": round(delta, 2),
        "per_probe": per_probe,
        "examples": examples,
    }

    _print_summary(comparison)
    return comparison


def _group_by_probe(report: "GarakReport") -> dict:
    groups: dict[str, dict] = {}
    for r in report.results:
        bucket = groups.setdefault(r.probe, {"total": 0, "succeeded": 0})
        bucket["total"] += 1
        if r.attack_succeeded:
            bucket["succeeded"] += 1
    return groups


def _pick_examples(
    baseline: "GarakReport",
    patched: "GarakReport",
    max_examples: int = 3,
) -> list[dict]:
    """
    Pair baseline and patched results by prompt text and surface cases
    where the patch changed the outcome.
    """
    base_map = {r.prompt: r for r in baseline.results}
    patch_map = {r.prompt: r for r in patched.results}
    examples = []
    for prompt, base_r in base_map.items():
        patch_r = patch_map.get(prompt)
        if patch_r and base_r.attack_succeeded != patch_r.attack_succeeded:
            examples.append({
                "probe": base_r.probe,
                "prompt": prompt,
                "baseline_response": base_r.response[:300],
                "patched_response": patch_r.response[:300],
                "baseline_attack_succeeded": base_r.attack_succeeded,
                "patched_attack_succeeded": patch_r.attack_succeeded,
            })
        if len(examples) >= max_examples:
            break
    return examples


def _print_summary(c: dict) -> None:
    log.info("─" * 60)
    log.info("EVALUATION SUMMARY")
    log.info("  Baseline attack pass-rate : %.1f%%", c["baseline_rate"] * 100)
    log.info("  Patched  attack pass-rate : %.1f%%", c["patched_rate"] * 100)
    direction = "↓ improvement" if c["delta_pp"] > 0 else "↑ regression"
    log.info("  Delta                     : %+.1f pp  %s", c["delta_pp"], direction)
    log.info("─" * 60)
    for probe, v in c["per_probe"].items():
        log.info(
            "  %-25s baseline=%d  patched=%d  Δ=%+d",
            probe,
            v["baseline_succeeded"],
            v["patched_succeeded"],
            v["delta"],
        )
    log.info("─" * 60)
