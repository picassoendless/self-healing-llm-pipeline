# Self-Healing LLM Security Pipeline

A prototype self-healing LLM security pipeline integrating [Garak](https://github.com/NVIDIA/garak) for automated vulnerability discovery and **Vulnerability-Driven Patch Synthesis (VDPS)** for automated mitigation.

> **Result:** Overall attack pass-rate reduced from **98.1% → 38.7%** (−59.4 pp) against GPT-3.5-turbo.

---

## Architecture

```
STAGE 1 — BASELINE PROBE
Garak ──► GPT-3.5-turbo (bare model, no patches) ──► hitlog.jsonl

STAGE 2 — VULNERABILITY-DRIVEN PATCH SYNTHESIS (VDPS)
hitlog ──► GPT reads attack data ──► synthesizes targeted patches ──► patches_synthesized.yaml

STAGE 3 — VERIFY (Garak routed through PatchProxy)
Garak ──► PatchProxy (localhost:8080)
                │
   [PROMPT-LEVEL PATCHES]          (before model call)
   • PromptInjectionGuardrail      block injection attempts
   • SystemPromptHardener          prepend security prefix
   • InputSanitizer                strip obfuscation tricks
                │
          GPT-3.5-turbo
                │
   [OUTPUT-LEVEL PATCHES]          (after model call)
   • ToxicityFilter
   • KeywordPolicyFilter
   • DeadnamingFilter              two-stage: regex + LLM judge
   • QuackMedicineFilter           two-stage: regex + LLM judge
   • SexualisationFilter           two-stage: regex + LLM judge
                │
           Garak evaluates
```

**Key design:** Garak owns its own HTTP client — Python monkey-patching cannot intercept it. Setting `OPENAI_BASE_URL=http://localhost:8080/v1` routes all scanner traffic through the proxy transparently, making the system scanner-agnostic and model-agnostic.

---

## Quick Start

### Prerequisites

- Python 3.10+
- OpenAI API key
- Garak v0.14.0

### 1. Install

```bash
python -m venv .venv

# Windows
.venv\Scripts\Activate.ps1

# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
pip install garak
```

### 2. Configure environment

Create `.env` in the project root:

```env
OPENAI_API_KEY=sk-...
LLM_BACKEND=openai
LLM_MODEL=gpt-3.5-turbo
```

### 3. Run

```bash
python run.py --config config/pipeline.yaml
```

### 4. Docker

```bash
# Full pipeline
docker-compose up pipeline

# Ablation variants
docker-compose up ablation-prompt   # prompt-level patches only
docker-compose up ablation-output   # output-level patches only
docker-compose up ablation-both     # both (full system)
```

---

## Configuration

### `config/pipeline.yaml` — Pipeline settings

| Field | Description |
|---|---|
| `llm_backend` | Target LLM backend (`openai` or `claude`) |
| `model_name` | Target model name |
| `synthesis_backend` | LLM used for VDPS patch generation |
| `synthesis_model` | Model name for VDPS synthesis |
| `garak_probes` | Garak probe modules to run |
| `use_vdps` | Enable VDPS (`true`) or use static patches (`false`) |
| `proxy_port` | Patch proxy port (default: `8080`) |
| `results_dir` | Output directory |

### `config/patches.yaml` — Patch toggles

Every patch is independently configurable — flip a flag, no code changes needed:

```yaml
# Prompt-level
enable_injection_guardrail: true
enable_system_hardening: true
enable_input_sanitizer: true

# Output-level
enable_toxicity_filter: true
enable_keyword_filter: true
enable_deadnaming_filter: true
enable_quack_medicine_filter: true
enable_sexualisation_filter: true
enable_llm_judge: true            # LLM-as-judge for semantic filters
enable_response_rewriter: false   # disabled by default (adds latency)
```

---

## Experiments

### Full pipeline

```bash
python run.py --config config/pipeline.yaml
```

### Ablation study

```bash
# Prompt-level patches only (output patches disabled)
python run.py --config config/ablation_prompt_only.yaml

# Output-level patches only (prompt patches disabled)
python run.py --config config/ablation_output_only.yaml

# Both patches enabled (full system)
python run.py --config config/ablation_both.yaml
```

### Skip VDPS — use static patch config

```bash
python run.py --config config/pipeline.yaml --no-vdps
```

### Qualitative before/after examples

```bash
python experiments/qualitative_examples.py \
    --baseline results/baseline/garak.hitlog.jsonl \
    --patched  results/patched/garak.hitlog.jsonl \
    --output   results/qualitative_examples.md
```

### Generate research presentation

```bash
python experiments/create_presentation.py --output results/presentation.pptx
```

### Tests

```bash
pytest tests/ -v
```

---

## Results Summary

| Probe | Baseline | Patched | Change |
|---|---|---|---|
| lmrc.Bullying | 0% | 0% | — |
| lmrc.Deadnaming | 83% | Partial | Partial mitigation |
| lmrc.QuackMedicine | 60% | 0% | Eliminated |
| lmrc.Sexualisation | 27% | 0% | Eliminated |
| promptinject.HijackHateHumans | 56% | 0% | Eliminated |
| promptinject.HijackKillHumans | 50% | 0% | Eliminated |
| **Overall** | **98.1%** | **38.7%** | **−59.4 pp** |

---

## File Structure

```
llm-security-pipeline/
├── run.py                               # Entry point (CLI)
├── docker-compose.yml                   # Docker: full pipeline + ablation services
├── Dockerfile
├── requirements.txt
│
├── core/                                # Orchestration & infrastructure
│   ├── pipeline.py                      # Stage 1 → 2 → 3 orchestrator
│   ├── llm_client.py                    # Unified OpenAI / Claude backend
│   ├── garak_runner.py                  # Garak subprocess wrapper + report parser
│   └── evaluator.py                     # Before/after quantitative comparison
│
├── patches/                             # Mitigation system
│   ├── config.py                        # PatchConfig dataclass (all toggles)
│   ├── engine.py                        # PatchEngine — orchestrates all patches
│   ├── prompt_patches.py                # PromptInjectionGuardrail, SystemPromptHardener, InputSanitizer
│   ├── output_patches.py                # ToxicityFilter, KeywordPolicyFilter, DeadnamingFilter,
│   │                                    #   QuackMedicineFilter, SexualisationFilter, ResponseRewriter
│   ├── proxy.py                         # FastAPI PatchProxy server (localhost:8080)
│   └── synthesizer.py                   # VDPS — hitlog → LLM synthesis → patches.yaml
│
├── config/
│   ├── pipeline.yaml                    # Main pipeline config
│   ├── patches.yaml                     # Static patch toggles
│   ├── patches_synthesized.yaml         # VDPS output (auto-generated each run)
│   ├── ablation_prompt_only.yaml        # Ablation: prompt patches only
│   ├── ablation_output_only.yaml        # Ablation: output patches only
│   ├── ablation_both.yaml               # Ablation: full system
│   ├── patches_ablation_prompt_only.yaml
│   └── patches_ablation_output_only.yaml
│
├── experiments/
│   ├── qualitative_examples.py          # Baseline vs patched markdown report
│   └── create_presentation.py           # Generates 10-slide PPTX presentation
│
├── tests/
│   ├── test_patches.py                  # Unit tests for all patch classes (48 tests)
│   └── test_stage2_patches.py           # Integration tests for PatchEngine
│
└── results/                             # Scan outputs (gitignored)
    ├── baseline/                        # Stage 1 Garak report + hitlog
    ├── patched/                         # Stage 3 Garak report + hitlog
    └── presentation.pptx                # Generated research presentation
```

---

## Novel Contributions

**VDPS (Vulnerability-Driven Patch Synthesis)** — patches are synthesized automatically from red-team findings rather than hardcoded in advance. The system reads its own failure cases and writes its own targeted mitigations.

**Network-layer proxy architecture** — patches intercept at the HTTP layer via `OPENAI_BASE_URL`, making the system fully scanner-agnostic. Any tool speaking the OpenAI chat completions protocol is transparently patched.

**Two-stage semantic filters** — structural regex (O(1), free) followed by LLM-as-judge (fires only on relevant content via pre-check). Balances precision against API cost.

**Ablation-ready config** — every patch is a YAML boolean toggle. All four experimental conditions (baseline / prompt-only / output-only / both) require only a config file swap.
