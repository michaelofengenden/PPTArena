#!/usr/bin/env python3
"""Record per-system average edit (generation) completion times.

Writes agent_bench/results/edit_times.json, keyed by system id, from:
  * CLI/OpenCode agents  -> predictions/manifest.csv `duration_seconds`
    (last ok row per case, so retries are not double-counted)
  * PPTPilot runs        -> src/benchmark_runs/pptpilot_<model>_gen.csv
    `generation_time_seconds` (only completed cases appear there)
  * CUA systems          -> no timing captured (decks generated externally)

The deployed leaderboard (src/app.py) and the live board read this file to
show an "avg edit time" column. Re-run this after generation changes.

Usage (on the VM, from repo root):  python3 agent_bench/compute_edit_times.py
"""
from __future__ import annotations

import csv
import json
import statistics as st
from pathlib import Path

BENCH = Path(__file__).resolve().parent
ROOT = BENCH.parent
MANIFEST = BENCH / "predictions" / "manifest.csv"
OUT = BENCH / "results" / "edit_times.json"

# CLI/OpenCode agents timed via the run manifest.
MANIFEST_AGENTS = [
    "codex_gpt55", "claude_code_opus48", "claude_code_opus55", "codex_gpt62astra", "gemini_cli_35flash",
    "opencode_glm52", "opencode_minimax_m3", "opencode_deepseek_v4",
    "opencode_kimi_k27code",
]
# PPTPilot runs timed via their generation CSVs.
PILOT_GEN = {
    "pptpilot_kimi_k26": ROOT / "src" / "benchmark_runs" / "pptpilot_kimi_k26_gen.csv",
    "pptpilot_gemma431": ROOT / "src" / "benchmark_runs" / "pptpilot_gemma431_gen.csv",
}


def _summary(durations: list[float], source: str) -> dict:
    if not durations:
        return {"n": 0, "mean_s": None, "median_s": None, "max_s": None, "source": source}
    return {
        "n": len(durations),
        "mean_s": round(st.mean(durations), 1),
        "median_s": round(st.median(durations), 1),
        "max_s": round(max(durations), 1),
        "source": source,
    }


def from_manifest() -> dict:
    out = {}
    if not MANIFEST.exists():
        return out
    last: dict[tuple, float] = {}
    with MANIFEST.open() as fh:
        for r in csv.DictReader(fh):
            if r["agent"] in MANIFEST_AGENTS and r["status"] == "ok" and r.get("duration_seconds"):
                last[(r["agent"], r["slug"])] = float(r["duration_seconds"])
    per: dict[str, list] = {}
    for (agent, _), d in last.items():
        per.setdefault(agent, []).append(d)
    for agent in MANIFEST_AGENTS:
        out[agent] = _summary(per.get(agent, []), "manifest")
    return out


def from_pilots() -> dict:
    out = {}
    for sid, path in PILOT_GEN.items():
        durs = []
        if path.exists():
            with path.open() as fh:
                for r in csv.DictReader(fh):
                    v = r.get("generation_time_seconds")
                    if v:
                        try:
                            durs.append(float(v))
                        except ValueError:
                            pass
        out[sid] = _summary(durs, "gen_csv")
    return out


def main() -> None:
    data = {}
    data.update(from_manifest())
    data.update(from_pilots())
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}")
    for sid, s in data.items():
        if s["mean_s"] is not None:
            print(f"  {sid:<24} n={s['n']:>2} mean={s['mean_s']/60:>5.1f}min "
                  f"median={s['median_s']/60:>5.1f}min max={s['max_s']/60:>5.1f}min")
        else:
            print(f"  {sid:<24} (no timing data)")


if __name__ == "__main__":
    main()
