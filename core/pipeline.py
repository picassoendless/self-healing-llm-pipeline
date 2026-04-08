"""
Self-Healing LLM Security Pipeline
====================================
Probe → Patch → Verify feedback loop.

Stage 1  —  Garak baseline vulnerability scan (bare model)
Stage 2  —  Vulnerability-Driven Patch Synthesis (VDPS)
Stage 3  —  Garak re-scan routed through PatchProxy
             Garak → localhost:8080 → PatchEngine → OpenAI
"""

import logging
import os
import yaml
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()

from dataclasses import dataclass, field

from core.llm_client import LLMClient, LLMBackend
from core.garak_runner import GarakRunner, GarakReport
from patches.synthesizer import PatchSynthesizer
from patches import PatchConfig
from patches.proxy import ProxyServer
from core.evaluator import compare_reports

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
log = logging.getLogger("pipeline")


@dataclass
class PipelineConfig:
    llm_backend: str = "openai"
    model_name: str = "gpt-4"
    garak_probes: list[str] = field(default_factory=lambda: ["lmrc"])
    max_iterations: int = 1
    patch_config_path: str = "config/patches.yaml"
    synthesized_patch_path: str = "config/patches_synthesized.yaml"
    use_vdps: bool = True
    proxy_port: int = 8080
    results_dir: str = "results"
    # Synthesis LLM — used for VDPS patch generation
    synthesis_backend: str = "openai"
    synthesis_model: str = "gpt-4"

    @classmethod
    def from_yaml(cls, path: str) -> "PipelineConfig":
        with open(path) as f:
            data = yaml.safe_load(f)
        return cls(**{k: v for k, v in data.items()
                      if k in cls.__dataclass_fields__})


class SelfHealingPipeline:
    """
    Stage 1: Garak scans the bare model — establishes vulnerability baseline.
    Stage 2: VDPS reads the hitlog, synthesizes targeted patches, writes YAML.
    Stage 3: PatchProxy starts on localhost, Garak re-scans routed through it.
             Every prompt is sanitized before reaching OpenAI; every response
             is filtered before Garak sees it.
    """

    def __init__(self, config: PipelineConfig):
        self.config = config

        # Target LLM — the model being scanned
        self.llm = LLMClient(
            backend=LLMBackend(config.llm_backend),
            model=config.model_name,
        )

        # Synthesis LLM — kept separate for VDPS JSON generation
        synthesis_llm = LLMClient(
            backend=LLMBackend(config.synthesis_backend),
            model=config.synthesis_model,
        )
        log.info(
            "Target LLM: %s/%s | Synthesis LLM: %s/%s",
            config.llm_backend, config.model_name,
            config.synthesis_backend, config.synthesis_model,
        )

        self.garak = GarakRunner(
            probes=config.garak_probes,
            results_dir=config.results_dir,
        )
        self.synthesizer = PatchSynthesizer(
            llm=synthesis_llm,
            output_path=config.synthesized_patch_path,
        )
        Path(config.results_dir).mkdir(parents=True, exist_ok=True)

    def run(self) -> dict:

        # ── STAGE 1: BASELINE PROBE ────────────────────────────────────
        log.info("=" * 60)
        log.info("STAGE 1 -- BASELINE PROBE (bare model, no patches)")
        log.info("=" * 60)

        baseline: GarakReport = self.garak.run(llm=self.llm, tag="baseline")

        log.info(
            "Baseline complete -- attack pass-rate: %.1f%% (%d/%d attacks succeeded)",
            baseline.pass_rate * 100,
            baseline.attacks_succeeded,
            baseline.total,
        )

        if baseline.attacks_succeeded == 0:
            log.info("No attacks succeeded -- nothing to patch.")
            log.info("=== PIPELINE COMPLETE ===")
            return {"baseline": baseline, "patched": None, "comparison": None}

        # ── STAGE 2: VULNERABILITY-DRIVEN PATCH SYNTHESIS ─────────────
        log.info("=" * 60)
        log.info("STAGE 2 -- VULNERABILITY-DRIVEN PATCH SYNTHESIS (VDPS)")
        log.info("=" * 60)

        if self.config.use_vdps:
            log.info("Reading hitlog and synthesizing targeted patches...")
            patch_yaml_path = self.synthesizer.run(
                report=baseline,
                hitlog_path=baseline.hitlog_path,
            )
        else:
            log.info("VDPS disabled -- using static patch config")
            patch_yaml_path = Path(self.config.patch_config_path)

        log.info("Patch config ready: %s", patch_yaml_path)

        # ── STAGE 3: VERIFY VIA PATCH PROXY ───────────────────────────
        log.info("=" * 60)
        log.info("STAGE 3 -- VERIFY (Garak routed through PatchProxy)")
        log.info("=" * 60)

        proxy = ProxyServer(
            patch_config_path=str(patch_yaml_path),
            port=self.config.proxy_port,
        )
        # Tell proxy which backend/model to use for LLM judge calls
        os.environ["LLM_BACKEND"] = self.config.llm_backend
        os.environ["LLM_MODEL"] = self.config.model_name
        proxy.start()

        # Override OPENAI_BASE_URL so Garak routes through the proxy
        original_base_url = os.environ.get("OPENAI_BASE_URL", "")
        os.environ["OPENAI_BASE_URL"] = proxy.base_url()
        log.info("Garak will route through patch proxy at %s", proxy.base_url())

        try:
            patched: GarakReport = self.garak.run(
                llm=self.llm,
                tag="patched",
            )
        finally:
            if original_base_url:
                os.environ["OPENAI_BASE_URL"] = original_base_url
            else:
                os.environ.pop("OPENAI_BASE_URL", None)
            proxy.stop()

        log.info(
            "Patched scan complete -- attack pass-rate: %.1f%% (%d/%d)",
            patched.pass_rate * 100,
            patched.attacks_succeeded,
            patched.total,
        )

        # ── EVALUATION ─────────────────────────────────────────────────
        comparison = compare_reports(baseline, patched)

        log.info("=" * 60)
        log.info("PIPELINE COMPLETE")
        log.info("  Baseline attack rate : %.1f%%", baseline.pass_rate * 100)
        log.info("  Patched  attack rate : %.1f%%", patched.pass_rate * 100)
        direction = "IMPROVEMENT" if comparison["delta_pp"] > 0 else "REGRESSION"
        log.info(
            "  Delta                : %+.1f pp  [%s]",
            comparison["delta_pp"],
            direction,
        )
        log.info("=" * 60)

        return {
            "baseline": baseline,
            "patched": patched,
            "comparison": comparison,
        }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Self-Healing LLM Security Pipeline")
    parser.add_argument("--config", default="config/pipeline.yaml")
    parser.add_argument("--no-vdps", action="store_true",
                        help="Use static patches instead of VDPS")
    args = parser.parse_args()

    cfg = PipelineConfig.from_yaml(args.config)
    if args.no_vdps:
        cfg.use_vdps = False

    pipeline = SelfHealingPipeline(cfg)
    pipeline.run()
