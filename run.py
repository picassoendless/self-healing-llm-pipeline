"""
run.py — Entry point for the Self-Healing LLM Security Pipeline.

Usage
-----
    python run.py --config config/pipeline.yaml
    python run.py --config config/ablation_prompt_only.yaml
    python run.py --config config/ablation_output_only.yaml --no-vdps
"""

import sys
import argparse
from dotenv import load_dotenv

load_dotenv()

from core.pipeline import SelfHealingPipeline, PipelineConfig


def main():
    parser = argparse.ArgumentParser(
        description="Self-Healing LLM Security Pipeline"
    )
    parser.add_argument(
        "--config",
        default="config/pipeline.yaml",
        help="Path to pipeline YAML config (default: config/pipeline.yaml)",
    )
    parser.add_argument(
        "--no-vdps",
        action="store_true",
        help="Skip VDPS synthesis and use static patch config instead",
    )
    args = parser.parse_args()

    cfg = PipelineConfig.from_yaml(args.config)
    if args.no_vdps:
        cfg.use_vdps = False

    pipeline = SelfHealingPipeline(cfg)
    pipeline.run()


if __name__ == "__main__":
    main()
