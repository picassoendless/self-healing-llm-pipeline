"""
qualitative_examples.py
=======================
Extracts before/after qualitative examples from Garak hitlogs.

Produces a readable report showing:
    - Original unsafe model output (baseline)
    - Patched system response (Stage 3)

Usage:
    python qualitative_examples.py \
        --baseline results/baseline/garak.hitlog.jsonl \
        --patched  results/patched/garak.hitlog.jsonl \
        --output   results/qualitative_examples.md
"""

import argparse
import json
from pathlib import Path
from collections import defaultdict


def load_hitlog(path: Path) -> dict[str, list[dict]]:
    """Load hitlog grouped by probe."""
    groups: dict[str, list[dict]] = defaultdict(list)
    if not path.exists():
        return groups
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                probe = entry.get("probe", "unknown")
                groups[probe].append(entry)
            except json.JSONDecodeError:
                continue
    return groups


def extract_text(field) -> str:
    """Extract readable text from Garak prompt/output fields (str or nested dict)."""
    if isinstance(field, str):
        return field
    if isinstance(field, dict):
        # Garak v0.14 stores text inside {"text": "...", ...}
        return field.get("text", str(field))
    return str(field)


def extract_prompt(prompt_field) -> str:
    """Extract the user-visible prompt text from a Garak prompt object."""
    if isinstance(prompt_field, str):
        return prompt_field
    if isinstance(prompt_field, dict):
        # Garak v0.14: {"turns": [{"role": "user", "content": {"text": "..."}}]}
        turns = prompt_field.get("turns", [])
        for turn in turns:
            role = turn.get("role", "")
            if role == "user":
                content = turn.get("content", {})
                return extract_text(content)
        # fallback: goal or raw dict
        return prompt_field.get("goal", str(prompt_field))
    return str(prompt_field)


def generate_report(
    baseline_path: Path,
    patched_path: Path,
    output_path: Path,
    max_per_probe: int = 2,
) -> None:
    baseline = load_hitlog(baseline_path)
    patched = load_hitlog(patched_path)

    all_probes = sorted(set(baseline.keys()) | set(patched.keys()))

    lines = [
        "# Qualitative Examples — Baseline vs Patched",
        "",
        "This document shows example prompts and responses demonstrating",
        "how the patch system changed model behaviour.",
        "",
        f"- **Baseline hitlog**: `{baseline_path}`",
        f"- **Patched hitlog**: `{patched_path}`",
        "",
        "---",
        "",
    ]

    for probe in all_probes:
        baseline_hits = baseline.get(probe, [])
        patched_hits = patched.get(probe, [])

        lines.append(f"## Probe: `{probe}`")
        lines.append("")
        lines.append(
            f"- Baseline attacks succeeded: **{len(baseline_hits)}**"
        )
        lines.append(
            f"- Patched attacks succeeded: **{len(patched_hits)}**"
        )
        lines.append("")

        # Show baseline examples
        if baseline_hits:
            lines.append("### Baseline — Unsafe Outputs")
            lines.append("")
            for i, entry in enumerate(baseline_hits[:max_per_probe], 1):
                prompt = extract_prompt(entry.get("prompt", ""))[:300]
                output = extract_text(entry.get("output", ""))[:500]
                lines.append(f"**Example {i}**")
                lines.append("")
                lines.append(f"> **Prompt**: {prompt}")
                lines.append("")
                lines.append(f"> **Unsafe output**: {output}")
                lines.append("")

        # Show patched examples (remaining hits after patching)
        if patched_hits:
            lines.append("### Patched — Remaining Vulnerabilities")
            lines.append("")
            for i, entry in enumerate(patched_hits[:max_per_probe], 1):
                prompt = extract_prompt(entry.get("prompt", ""))[:300]
                output = extract_text(entry.get("output", ""))[:500]
                lines.append(f"**Example {i}**")
                lines.append("")
                lines.append(f"> **Prompt**: {prompt}")
                lines.append("")
                lines.append(f"> **Patched output**: {output}")
                lines.append("")
        else:
            lines.append("### Patched — ✅ No attacks succeeded")
            lines.append("")
            lines.append(
                "> All attack attempts for this probe were successfully mitigated."
            )
            lines.append("")

        lines.append("---")
        lines.append("")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"Qualitative examples written to: {output_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate qualitative before/after examples from Garak hitlogs"
    )
    parser.add_argument(
        "--baseline",
        default="results/baseline/garak.hitlog.jsonl",
        help="Path to baseline hitlog",
    )
    parser.add_argument(
        "--patched",
        default="results/patched/garak.hitlog.jsonl",
        help="Path to patched hitlog",
    )
    parser.add_argument(
        "--output",
        default="results/qualitative_examples.md",
        help="Output markdown file",
    )
    parser.add_argument(
        "--max-per-probe",
        type=int,
        default=2,
        help="Max examples to show per probe",
    )
    args = parser.parse_args()

    generate_report(
        baseline_path=Path(args.baseline),
        patched_path=Path(args.patched),
        output_path=Path(args.output),
        max_per_probe=args.max_per_probe,
    )
