#!/usr/bin/env python3
"""
Keep running run_agents.py until every case has a real attempt.

A session that dies because the plan ran out of usage (or lost its login) leaves the
deck untouched or half edited, and the runner would collect that as a result. After
each batch this script finds those sessions by their output log, removes the deck
they produced, waits, and runs the batch again. A normal failure or timeout is left
alone: it is the agent's result.

Usage: python3 run_until_done.py --agents A,B --split subset [--parallel 6]
"""
import argparse, csv, json, re, subprocess, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORKDIRS = Path.home() / "agent_bench_workdirs"
PRED = HERE / "predictions"
LIMIT = re.compile(r"usage limit|hit your limit|limit reached|limit will reset|rate.?limit|too many requests|"
                   r"\b429\b|quota|overloaded|failed to authenticate|oauth|invalid api key|\b401\b|"
                   r"not logged in|please run /login|credit balance", re.I)


def plan_failure(log_text):
    """The plan or login error a session log ends with, or "" if it shows none.

    A plain log (Codex, OpenCode, Gemini) is judged by its last 3000 characters. A
    stream-json log (Claude Code with `--output-format stream-json`) is full of deck XML
    from tool calls, where a bare 401 or 429 means nothing, so only its closing `result`
    event counts, and only when that event is an error.
    """
    events = [line for line in log_text.splitlines() if line.startswith("{")]
    if events:
        closing = ""
        for line in reversed(events):
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict) and event.get("type") == "result":
                closing = str(event.get("result") or "") if event.get("is_error") else ""
                break
        match = LIMIT.search(closing)
    else:
        match = LIMIT.search(log_text[-3000:])
    return match.group(0) if match else ""


def plan_failures(agents):
    """(agent, slug, matched text) for the latest attempt of each task that looks like a plan or login failure."""
    latest = {}
    manifest = PRED / "manifest.csv"
    if manifest.exists():
        for row in csv.DictReader(manifest.open(encoding="utf-8")):
            if row["agent"] in agents:
                latest[(row["agent"], row["slug"])] = row
    hits = []
    for (agent, slug), row in latest.items():
        log = WORKDIRS / agent / slug / "agent_output.log"
        if not log.exists():
            continue
        text = log.read_text(encoding="utf-8", errors="replace")
        # Only a session that did not exit cleanly or left the deck untouched is checked.
        # A clean run may mention limits in its own notes.
        clean = row["status"] == "ok" and row["exit_code"] in ("0", "")
        why = plan_failure(text)
        if why and not clean:
            hits.append((agent, slug, why))
    return hits


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--agents", required=True)
    parser.add_argument("--split", default="subset")
    parser.add_argument("--parallel", default="6")
    parser.add_argument("--wait", type=int, default=1500, help="Seconds to wait after a plan failure.")
    parser.add_argument("--rounds", type=int, default=40)
    args = parser.parse_args()
    agents = args.agents.split(",")
    for round_no in range(1, args.rounds + 1):
        print(f"\n===== round {round_no} {time.strftime('%Y-%m-%d %H:%M:%S')} =====", flush=True)
        subprocess.run([sys.executable, "-u", str(HERE / "run_agents.py"), "--agents", args.agents,
                        "--split", args.split, "--parallel", args.parallel,
                        "--idle-timeout", "1800", "--max-timeout", "5400"])
        hits = plan_failures(agents)
        if not hits:
            print("ALL DONE: no plan or login failures left", flush=True)
            return
        for agent, slug, why in hits:
            deck = PRED / agent / f"{slug}.pptx"
            deck.unlink(missing_ok=True)
            print(f"  will retry {agent}/{slug}: '{why}'", flush=True)
        print(f"PLAN LIMIT: {len(hits)} session(s) to retry; waiting {args.wait}s", flush=True)
        time.sleep(args.wait)
    print("GAVE UP after the configured number of rounds", flush=True)


if __name__ == "__main__":
    main()
