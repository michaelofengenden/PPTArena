# PPTArena Roadmap

Two tracks: (1) grow leaderboard coverage with more models, (2) add a deterministic scoring method alongside the VLM judges.

---

## 1. Expanding model coverage

### Where coverage stands today

| System | Hard subset (25) | Full set (100) |
|---|---|---|
| PPTPilot (Gemini 3.1 Pro) | ✅ | ✅ (97/100 scored) |
| PPTPilot (GPT-5.2) | ✅ | ✅ |
| ChatGPT (web) | — | ✅ |
| Claude 3.7 Sonnet | ✅ | ❌ |
| ChatGPT Agent | ✅ | ❌ |
| Gemini CLI | ✅ | ❌ |
| MiniMax Agent | ✅ | ❌ |
| Kimi K2.6 | ✅ (paper-reported) | ❌ |
| PPTAgent | ✅ (paper-reported) | ❌ |

### Wave 1 — fill gaps with the existing harness (low lift, high value)

1. **Claude 3.7 Sonnet on the full 100.** It leads the hard subset (47.2), so its full-set score is the single most interesting missing number.
2. **Gemini CLI on the full 100** — same rationale, and the CLI harness already exists (`src/rejudge_gemini_cli_subset25.py` shows the pattern).
3. **Re-run Kimi K2.6 live** (25 cases) to replace the paper-reported entry with a run whose raw judge outputs live in the repo.
4. **Re-score PPTPilot (Gemini 3.1 Pro)'s 3 missing full-set cases** so it's 100/100.

### Wave 2 — VM coding-agent cohort (scaffolded in `agent_bench/`, runs when the VM lands)

Six CLI coding agents run headlessly over the benchmark on a dedicated VM, five
processes at a time, with scoring decoupled (judge later, anywhere):

| Agent | Backbone |
|---|---|
| Codex CLI | GPT-5.5, xhigh reasoning |
| Claude Code | Opus 4.8 (Max plan) |
| OpenCode | GLM-5.2 |
| Gemini CLI | Gemini 3.5 Flash |
| OpenCode | MiniMax-M3 |
| OpenCode | DeepSeek V4 Pro |

Workflow: `run_agents.py --check` → smoke test → `run_agents.py --parallel 5` on the
25-case hard subset → download predictions → `judge_predictions.py` → commit CSVs to
`agent_bench/results/` (leaderboard sources are pre-registered, rows appear on push).
See [agent_bench/README.md](agent_bench/README.md) for the full VM playbook.

**PPTPilot retirement gate:** if every agent in the cohort outscores both PPTPilot
rows on the matched subset (38.8 and 30.4), PPTPilot moves off the headline
leaderboard (kept in the paper and repo history as the reference system).

### Wave 3 — new backbones through PPTPilot (API-driven, cheap to add)

The `run_dual_model_benchmark.py` pattern (editor model + judge model → per-case CSV) works for any model `llm_handler` can call:

- **Claude Opus 4.x / Sonnet as PPTPilot backbone** — requires adding Anthropic API support to `llm_handler.py` (currently OpenAI + Gemini only).
- **DeepSeek and Qwen** — both expose OpenAI-compatible endpoints, so they mostly need a base-URL parameter in the OpenAI client path.
- **Open-weight baselines** (e.g., Llama) via any OpenAI-compatible server.

Subset first (25 cases) for every new backbone; promote to full 100 only if the subset score is competitive (>25) — this keeps judge cost proportional to signal.

### Wave 4 — agent products (manual or scaffolded runs)

- **Microsoft Copilot in PowerPoint** — the most on-thesis competitor; runs are manual (upload deck, paste instruction, export), same protocol as the ChatGPT Agent / MiniMax samples.
- **Claude Code / computer-use agents, Manus-style agents** — batch-scriptable where APIs allow.

### Per-model checklist (what "adding a model" means now)

1. Generate predictions over `src/evaluation_pairs_refined.json` (harness run or manual agent export).
2. Judge with the standard judge for that track (keep GPT-5.2 judge for subset comparability; record the judge string).
3. Drop the per-case CSV/JSON in `src/benchmark_runs/` (columns must include `case_name` or `pair_name`, `instruction_following_score`, `visual_quality_score`).
4. Add one entry to `LEADERBOARD_SOURCES` in `src/app.py` (path, name, model, provider, `brand`, color, split, expected_cases, judge).
5. If it's a new brand, add an SVG branch to the `model_icon` macro in `src/templates/_model_icon.html` (unknown brands automatically fall back to a colored monogram).

Consistency rules: identical 25-case subset for all agents; both metrics from the same judge call; keep raw judge JSON alongside the CSV; one result file per system per split.

---

## 2. Judging: Gemini 3.5 Flash as the standard judge

Status: **active** (deterministic scoring below is parked — reference-anchored
checks penalize valid alternate solutions too often).

- `agent_bench/judge_predictions.py` now defaults to `gemini-3.5-flash`, with
  ground-truth artifact caching (renders computed once per case, shared across
  systems) and `--samples N` per-metric median aggregation for variance control.
- **Comparability rule:** scores from different judges must not be ranked against
  each other. Before the Flash-judged cohort lands on the main board, re-judge the
  existing systems' predictions with the same Flash judge so the whole leaderboard
  is single-judge. Validate first: run Flash over one already-judged prediction set
  and check per-case correlation + ranking preservation vs the GPT-5.2 numbers.
- **Rubric hygiene:** `src/audit_cases.py` runs deterministic integrity checks over
  all 100 cases (deck/rubric mismatches, missing literals, slide-count changes) and
  emits an HTML review report — run it before any large judging campaign. First run
  found one broken rubric (Case 12 references slides the deck doesn't have) and a
  ~25-case human-review queue.

## 3. Deterministic scoring method (parked)

### Why

The VLM judge is expensive, stochastic across re-runs, and drifts when providers update models. A deterministic scorer is reproducible, free to re-run, CI-friendly, and lets anyone verify leaderboard numbers offline. It complements — not replaces — the VLM judge: instruction following is largely machine-checkable; visual quality stays perceptual.

### Design: per-case check specs

Each case gets a machine-readable spec of assertions derived from its `style_target` (which is already fully specified per case):

```yaml
case: "Case 3: Add Footer Text Box"
checks:
  - {type: text_present, slide: 5, text: "Source: www.VerifyMe.com", weight: critical}
  - {type: shape_in_region, slide: 5, match: "Source: www.VerifyMe.com", region: bottom_15pct, weight: major}
  - {type: no_overlap, slide: 5, weight: minor}
preserve:
  - {type: slides_unchanged, except: [5], tolerance: none}
```

Check vocabulary (implemented on python-pptx + raw lxml for what it can't reach):

- **Text**: presence/absence/equality (whitespace- and run-normalized), per-slide or deck-wide.
- **Shapes**: existence, deletion, position/size within tolerance (EMU or % of slide), z-order, overlap detection.
- **Formatting**: font family/size/bold/color, fill/line, alignment — compared as *effective* values (resolving theme/master inheritance) to avoid false negatives from equivalent XML.
- **Structure**: slide count/order, master/layout edits, tables (cell values), charts (series/values via embedded XML), hyperlinks.
- **Interactivity**: transition/animation nodes in `p:timing` / `p:transition` (presence + type).
- **Preservation**: untouched slides byte-normalized-equal or attribute-equal within tolerance.

Scoring: `deterministic IF = weighted pass fraction` (critical checks gate to 0 if failed), reported per case and averaged per split with the same missing-counts-as-0 convention. Optional secondary signal: SSIM on pinned-version LibreOffice renders (still deterministic given a pinned renderer).

### How to get 100 specs without hand-writing them

- **Phase A — auto-draft.** For each case, diff Original vs GroundTruth OOXML to propose candidate assertions, and have an LLM translate the `style_target` prose into the check schema. Human-review each spec (the existing `enhancement_notes` encode the right tolerance philosophy: semantic goals, not pixel-perfect equality).
- **Phase B — calibrate.** Run the deterministic scorer over all existing predictions in the repo and correlate with judge IF scores. Target: Spearman ≥ 0.8 on the 25-case subset. Inspect the biggest disagreements; loosen over-strict checks with tolerances and any-of groups (multiple valid realizations of the same instruction).
- **Phase C — ship.** `src/score_deterministic.py` CLI (prediction dir + specs → CSV), a "Det-IF" column or leaderboard tab in the webapp, and spec files published with the HF dataset so others can score offline. The headline PPTArena score stays (IF+VQ)/10; deterministic IF is reported alongside until it's validated enough to define a v2 metric.

### Known risks

- **Valid alternates failing rigid checks** → any-of assertion groups, tolerance bands, human review before a spec ships.
- **OOXML equivalence traps** (same visual result, different XML: split runs, inherited vs. explicit attributes) → normalize before comparing; compare effective values, not raw attributes.
- **Renderer nondeterminism** for pixel checks → pin the LibreOffice version or keep pixel checks out of v1.

### Suggested milestones

- **M1**: check schema + checker library, validated on 5 pilot cases spanning all 5 categories.
- **M2**: auto-drafted, human-reviewed specs for the 25-case hard subset + calibration report vs. existing judge scores.
- **M3**: full 100 specs, `score_deterministic.py`, and webapp integration.
