#!/usr/bin/env python3
"""
Score agent_bench predictions with the PPTArena arena judge (VLM-as-a-judge).

Reads predictions from agent_bench/predictions/<agent_id>/<case_slug>.pptx
(produced by run_agents.py), judges each against the ground truth, and writes
agent_bench/results/<agent_id>_judge_results.csv in the same schema as the
existing judge scripts — the webapp leaderboard picks these files up
automatically (sources are pre-registered in src/app.py).

Judge model defaults to Gemini 3.5 Flash; any model id llm_handler knows
(gemini-*, gpt-*, kimi-*) works via --judge-model. Two go-fast/go-steady
features:

- Ground-truth artifacts (slide renders, JSON, XML) are computed once per case
  and cached — on disk for renders (benchmark_outputs/judge_render_cache/),
  in memory for the rest — then reused across all agents and re-runs. Only
  the prediction deck is rendered per judgement.
- --samples N judges each case N times and takes the per-metric median,
  reducing judge variance (cheap with Flash-class models).

Cases in the split that have no prediction get zero-score rows, so every CSV
always covers the full split (missing work is penalized, not hidden).

Usage:
    python3 agent_bench/judge_predictions.py --agents all
    python3 agent_bench/judge_predictions.py --agents codex_gpt55 --samples 3

Requires credentials.env at the repo root with GEMINI_API_KEY (default judge)
or OPENAI_API_KEY (gpt-* judges), and LibreOffice for slide rendering. Scoring
runs on any machine with the repo; it does not need the agent CLIs.
"""

from __future__ import annotations

import argparse
import base64
import csv
import json
import re
import statistics
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Lock

AGENT_BENCH_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = AGENT_BENCH_DIR.parent
SRC_DIR = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

import llm_handler  # noqa: E402
from ppt import (  # noqa: E402
    export_slides_to_images,
    extract_specific_xml_from_pptx,
    pptx_to_json,
)

from run_agents import slugify  # noqa: E402

PAIRS_PATH = SRC_DIR / "evaluation_pairs_refined.json"
SUBSET_PATH = AGENT_BENCH_DIR / "subset25.json"
AGENTS_PATH = AGENT_BENCH_DIR / "agents.json"
PREDICTIONS_ROOT = AGENT_BENCH_DIR / "predictions"
RESULTS_ROOT = AGENT_BENCH_DIR / "results"
RENDER_CACHE_ROOT = PROJECT_ROOT / "benchmark_outputs" / "judge_render_cache"

DEFAULT_JUDGE_MODEL = "gemini-3.5-flash"

CSV_FIELDS = [
    "case_index",
    "case_name",
    "category",
    "prediction",
    "instruction_following_score",
    "visual_quality_score",
    "instruction_following_reason",
    "visual_quality_reason",
    "judge_model",
    "judge_time_seconds",
    "errors",
]


def image_to_base64(image_path: str) -> str | None:
    try:
        with open(image_path, "rb") as fh:
            return base64.b64encode(fh.read()).decode("utf-8")
    except Exception:
        return None


def is_openrouter_judge(model_id: str) -> bool:
    lower = (model_id or "").lower()
    return lower.startswith(("openrouter/", "qwen/")) or "qwen" in lower


def is_kimi_judge(model_id: str) -> bool:
    lower = (model_id or "").lower()
    if is_openrouter_judge(lower):
        return False
    return "kimi" in lower or "moonshot" in lower


def is_openai_judge(model_id: str) -> bool:
    lower = (model_id or "").lower()
    if is_kimi_judge(lower):
        return False
    return any(tok in lower for tok in ("gpt", "openai", "o1", "o3", "o4"))


def extract_case_index(name: str) -> int:
    try:
        prefix, _ = name.split(":", 1)
        return int(prefix.strip().split()[1])
    except Exception:
        return -1


def normalize_category(raw) -> str:
    if isinstance(raw, list):
        return ", ".join(str(v) for v in raw if v)
    return str(raw) if raw else "Unknown"


def compose_prompt(prompt: str, style_target: str) -> str:
    instruction = (prompt or "").strip()
    style = (style_target or "").strip()
    if not instruction.startswith("Instruction:"):
        instruction = f"Instruction: {instruction}"
    if style:
        if not style.startswith("Style Target:"):
            style = f"Style Target: {style}"
        return f"{instruction}\n{style}"
    return instruction


def render_cached(pptx_path: Path, cache_dir: Path) -> list[str]:
    """Render slides to PNGs once; reuse the cache on subsequent calls.

    A cached folder can also hold LibreOffice's own PNG export, which is slide 1
    only and named after the deck. When the per-slide images from the PDF fallback
    are there, only those are slides. Counting the leftover as well put a copy of
    slide 1 at the front of every cached ground truth and shifted the rest by one.
    """
    if cache_dir.is_dir():
        def slide_number(path: Path) -> int:
            digits = re.findall(r"\d+", path.stem)
            return int(digits[-1]) if digits else -1

        cached = sorted(cache_dir.glob("slide-*.png"), key=slide_number) or sorted(cache_dir.glob("*.png"))
        if cached:
            return [str(p) for p in cached]
    cache_dir.mkdir(parents=True, exist_ok=True)
    return export_slides_to_images(str(pptx_path), str(cache_dir))


class CaseArtifacts:
    """Prediction-independent judge inputs, computed once per case."""

    def __init__(self) -> None:
        self._store: dict[str, dict] = {}
        self._locks: dict[str, Lock] = {}
        self._master = Lock()

    def get(self, case: dict) -> dict:
        name = case["name"]
        with self._master:
            lock = self._locks.setdefault(name, Lock())
        with lock:
            if name not in self._store:
                initial_path = (PROJECT_ROOT / case["original"]).resolve()
                gt_path = (PROJECT_ROOT / case["ground_truth"]).resolve()
                gt_imgs = render_cached(gt_path, RENDER_CACHE_ROOT / slugify(name) / "gt")
                self._store[name] = {
                    "init_json": pptx_to_json(str(initial_path)),
                    "gt_json": pptx_to_json(str(gt_path)),
                    "gt_b64": [b for b in (image_to_base64(p) for p in gt_imgs) if b],
                    "gt_xml": extract_specific_xml_from_pptx(str(gt_path), "ppt/slides/slide1.xml") or "",
                }
            return self._store[name]


def zero_row(case: dict, prediction: Path | None, reason: str, judge_label: str) -> dict:
    return {
        "case_index": extract_case_index(case["name"]),
        "case_name": case["name"],
        "category": normalize_category(case.get("category")),
        "prediction": str(prediction) if prediction else "",
        "instruction_following_score": "0.00",
        "visual_quality_score": "0.00",
        "instruction_following_reason": reason,
        "visual_quality_reason": reason,
        "judge_model": judge_label,
        "judge_time_seconds": "0.000",
        "errors": reason,
    }


def call_judge_once(case: dict, artifacts: dict, pred_json: dict, pred_b64: list[str],
                    pred_xml: str, api_keys: dict, judge_model: str) -> dict:
    return llm_handler.call_llm_judge(
        user_prompt=compose_prompt(case.get("prompt", ""), case.get("style_target", "")),
        judge_model=judge_model,
        initial_ppt_json=artifacts["init_json"],
        original_ppt_json=artifacts["gt_json"],
        modified_ppt_json=pred_json,
        original_slide_images_b64=artifacts["gt_b64"],
        modified_slide_images_b64=pred_b64,
        original_slide_xml=artifacts["gt_xml"],
        modified_slide_xml=pred_xml,
        evaluation_mode="arena",
        api_keys=api_keys,
    )


def judge_case(case: dict, prediction_path: Path, artifacts_cache: CaseArtifacts,
               api_keys: dict, judge_model: str, samples: int, judge_label: str) -> dict:
    try:
        artifacts = artifacts_cache.get(case)
        pred_json = pptx_to_json(str(prediction_path))
        with tempfile.TemporaryDirectory() as tmpdir:
            pred_imgs = export_slides_to_images(str(prediction_path), str(Path(tmpdir) / "pred"))
            pred_b64 = [b for b in (image_to_base64(p) for p in pred_imgs) if b]
        pred_xml = extract_specific_xml_from_pptx(str(prediction_path), "ppt/slides/slide1.xml") or ""
    except Exception as exc:
        return zero_row(case, prediction_path, f"Artifact prep error: {exc}", judge_label)

    start = time.time()
    valid: list[dict] = []
    errors: list[str] = []
    for _ in range(samples):
        try:
            response = call_judge_once(case, artifacts, pred_json, pred_b64, pred_xml,
                                       api_keys, judge_model)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"Judge call failed: {exc}")
            continue
        if isinstance(response, dict) and not response.get("error"):
            valid.append(response)
        else:
            errors.append(response.get("error", "Invalid judge response.")
                          if isinstance(response, dict) else "Invalid judge response.")
    judge_time = round(time.time() - start, 3)

    if not valid:
        row = zero_row(case, prediction_path, " | ".join(errors) or "No valid judge response.", judge_label)
        row["judge_time_seconds"] = f"{judge_time:.3f}"
        return row

    if_scores = [float(r.get("instruction_following_score") or 0.0) for r in valid]
    vq_scores = [float(r.get("visual_quality_score") or 0.0) for r in valid]
    if_final = statistics.median(if_scores)
    vq_final = statistics.median(vq_scores)
    # Reasons come from the sample closest to the aggregated scores.
    representative = min(
        valid,
        key=lambda r: abs(float(r.get("instruction_following_score") or 0.0) - if_final)
        + abs(float(r.get("visual_quality_score") or 0.0) - vq_final),
    )

    return {
        "case_index": extract_case_index(case["name"]),
        "case_name": case["name"],
        "category": normalize_category(case.get("category")),
        "prediction": str(prediction_path),
        "instruction_following_score": f"{if_final:.2f}",
        "visual_quality_score": f"{vq_final:.2f}",
        "instruction_following_reason": (representative.get("instruction_following_reason") or "").strip()
        or "No instruction reasoning returned.",
        "visual_quality_reason": (representative.get("visual_quality_reason") or "").strip()
        or "No visual reasoning returned.",
        "judge_model": judge_label,
        "judge_time_seconds": f"{judge_time:.3f}",
        "errors": " | ".join(errors),
    }


def judge_agent(agent_id: str, cases: list[dict], artifacts_cache: CaseArtifacts,
                api_keys: dict, judge_model: str, samples: int,
                max_workers: int, resume: bool,
                suffix: str = "_judge_results.csv") -> None:
    judge_label = judge_model if samples == 1 else f"{judge_model} (median of {samples})"
    output_path = RESULTS_ROOT / f"{agent_id}{suffix}"
    RESULTS_ROOT.mkdir(parents=True, exist_ok=True)

    done: set[str] = set()
    if resume and output_path.exists():
        with output_path.open(newline="", encoding="utf-8") as fh:
            done = {row["case_name"] for row in csv.DictReader(fh)}
    else:
        with output_path.open("w", newline="", encoding="utf-8") as fh:
            csv.DictWriter(fh, fieldnames=CSV_FIELDS).writeheader()

    todo = [c for c in cases if c["name"] not in done]
    print(f"\n=== {agent_id}: judging {len(todo)} case(s) with {judge_label} "
          f"({len(done)} already in {output_path.name}) ===")
    if not todo:
        return

    lock = Lock()

    def emit(row: dict) -> None:
        with lock:
            with output_path.open("a", newline="", encoding="utf-8") as fh:
                csv.DictWriter(fh, fieldnames=CSV_FIELDS).writerow(row)
        print(f"[Case {row['case_index']:>3}] IF {row['instruction_following_score']} | "
              f"VQ {row['visual_quality_score']}  ({agent_id})"
              + (f"  !! {row['errors']}" if row["errors"] else ""))

    def work(case: dict) -> dict:
        prediction = PREDICTIONS_ROOT / agent_id / f"{slugify(case['name'])}.pptx"
        if not prediction.exists():
            return zero_row(case, prediction, "Prediction PPTX missing for this case.", judge_label)
        return judge_case(case, prediction, artifacts_cache, api_keys, judge_model, samples, judge_label)

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(work, case): case for case in todo}
        for future in as_completed(futures):
            case = futures[future]
            try:
                row = future.result()
            except Exception as exc:  # noqa: BLE001
                row = zero_row(case, None, f"Unhandled judge exception: {exc}", judge_label)
            emit(row)


def main() -> None:
    parser = argparse.ArgumentParser(description="Judge agent_bench predictions with the arena judge.")
    parser.add_argument("--agents", default="all",
                        help="Comma-separated agent ids from agents.json, or 'all' (default).")
    parser.add_argument("--split", choices=["subset", "full"], default="subset",
                        help="Judge the matched 25-case subset (default) or all 100 cases.")
    parser.add_argument("--judge-model", default=DEFAULT_JUDGE_MODEL,
                        help=f"Judge model id (default: {DEFAULT_JUDGE_MODEL}).")
    parser.add_argument("--samples", type=int, default=1,
                        help="Judge each case N times and take the per-metric median (default: 1).")
    parser.add_argument("--max-workers", type=int, default=6,
                        help="Concurrent judge calls per agent (default: 6).")
    parser.add_argument("--no-resume", action="store_true",
                        help="Rewrite result CSVs from scratch instead of appending missing cases.")
    parser.add_argument("--csv-suffix", default="_judge_results.csv",
                        help="Result filename suffix — use a distinct one per judge for "
                             "agreement studies (e.g. '_qwen_judge_results.csv').")
    args = parser.parse_args()
    if args.samples < 1:
        parser.error("--samples must be at least 1.")

    agents = json.loads(AGENTS_PATH.read_text(encoding="utf-8"))
    agent_ids = list(agents) if args.agents == "all" else [a.strip() for a in args.agents.split(",")]
    # Accept any id with a predictions directory, so non-CLI systems (e.g.
    # PPTPilot runs collected via run_xml_loop_subsample --collect-dir) can be
    # judged through the same pipeline without an agents.json entry.
    unknown = [a for a in agent_ids if a not in agents and not (PREDICTIONS_ROOT / a).is_dir()]
    if unknown:
        sys.exit(f"Unknown agent id(s): {', '.join(unknown)}. Known: {', '.join(agents)}")

    pairs = json.loads(PAIRS_PATH.read_text(encoding="utf-8"))
    for pair in pairs:
        pair["name"] = pair["name"].strip()
    if args.split == "subset":
        subset_names = {n.strip() for n in json.loads(SUBSET_PATH.read_text(encoding="utf-8"))}
        pairs = [p for p in pairs if p["name"] in subset_names]
    pairs.sort(key=lambda c: extract_case_index(c["name"]))

    keys = llm_handler.load_api_keys()
    if is_openrouter_judge(args.judge_model):
        if not (keys.get("openrouter") or keys.get("openrouter_api_key")):
            sys.exit("OPENROUTER_API_KEY missing from credentials.env; required for OpenRouter judges.")
    elif is_kimi_judge(args.judge_model):
        if not (keys.get("moonshot") or keys.get("kimi") or keys.get("kimi_api_key")):
            sys.exit("MOONSHOT_API_KEY missing from credentials.env; required for kimi-* judges.")
    elif is_openai_judge(args.judge_model):
        if not (keys.get("openai") or keys.get("openai_api_key")):
            sys.exit("OPENAI_API_KEY missing from credentials.env; required for gpt-* judges.")
    elif not (keys.get("gemini") or keys.get("gemini_api_key")):
        sys.exit("GEMINI_API_KEY missing from credentials.env; required for gemini-* judges.")

    artifacts_cache = CaseArtifacts()
    for agent_id in agent_ids:
        judge_agent(agent_id, pairs, artifacts_cache, keys, args.judge_model,
                    args.samples, args.max_workers, resume=not args.no_resume,
                    suffix=args.csv_suffix)

    print("\nAll done. Result CSVs are in agent_bench/results/ — commit them and the "
          "leaderboard rows appear automatically (sources pre-registered in src/app.py).")


if __name__ == "__main__":
    main()
