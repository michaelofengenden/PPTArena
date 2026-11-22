#!/usr/bin/env python3
"""
Run CLI coding agents across PPTArena cases to generate predictions.

Generation only — scoring is decoupled (see judge_predictions.py). For each
(agent, case) task the runner prepares an isolated workdir containing the
original deck as deck.pptx plus INSTRUCTION.md with the case prompt, invokes
the agent CLI headlessly inside that directory, validates the edited deck,
and collects it into predictions/<agent>/<case_slug>.pptx.

Typical usage on the VM:
    python3 agent_bench/run_agents.py --check                 # verify CLIs + auth
    python3 agent_bench/run_agents.py --agents codex_gpt55 --limit 1   # smoke test
    python3 agent_bench/run_agents.py --parallel 5            # full subset run

Completed predictions are skipped on re-run (resume by default); use --force
to regenerate. Agent stdout/stderr is kept in each workdir for debugging.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import Lock

AGENT_BENCH_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = AGENT_BENCH_DIR.parent
PAIRS_PATH = PROJECT_ROOT / "src" / "evaluation_pairs_refined.json"
SUBSET_PATH = AGENT_BENCH_DIR / "subset25.json"
AGENTS_PATH = AGENT_BENCH_DIR / "agents.json"
# Workdirs live OUTSIDE the repo tree so agent sessions can never touch the
# benchmark repo itself (GroundTruth decks, manifests) even if their project
# resolution goes wrong — see the PWD note in run_task for how it once did.
WORKDIRS_ROOT = Path(os.environ.get("AGENT_BENCH_WORKDIRS")
                     or Path.home() / "agent_bench_workdirs")
PREDICTIONS_ROOT = AGENT_BENCH_DIR / "predictions"

MANIFEST_FIELDS = [
    "timestamp",
    "agent",
    "case_name",
    "slug",
    "status",
    "duration_seconds",
    "exit_code",
    "prediction_path",
    "original_sha256",
    "prediction_sha256",
    "notes",
]

# The CLI prompt is identical for every case; the case-specific instruction
# lives in INSTRUCTION.md inside the workdir.
TASK_PROMPT = (
    "You are completing one case of the PPTArena benchmark (PowerPoint editing).\n"
    "Read INSTRUCTION.md in the current directory, then edit deck.pptx IN PLACE "
    "so that it satisfies the instruction.\n"
    "Rules:\n"
    "- Modify deck.pptx directly and keep the filename deck.pptx. You may use any "
    "tools or scripts you like (e.g. python-pptx, editing the OOXML inside the zip).\n"
    "- Apply the requested edit to the existing deck; do not build a new deck from scratch.\n"
    "- Preserve everything the instruction does not ask you to change (layout, styling, content).\n"
    "- Before finishing, verify deck.pptx is still a valid PowerPoint file that opens without repair.\n"
)

INSTRUCTION_TEMPLATE = """# PPTArena case: {case_name}

Edit `deck.pptx` (in this directory) according to the instruction below.

## Instruction

{prompt}
"""


def slugify(case_name: str) -> str:
    match = re.match(r"Case (\d+)", case_name)
    if match:
        return f"case_{int(match.group(1)):03d}"
    return re.sub(r"[^a-z0-9]+", "_", case_name.lower()).strip("_")[:60]


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_valid_pptx(path: Path) -> bool:
    try:
        if not zipfile.is_zipfile(path):
            return False
        with zipfile.ZipFile(path) as zf:
            return "[Content_Types].xml" in zf.namelist()
    except Exception:
        return False


@dataclass
class Task:
    agent_id: str
    agent: dict
    case: dict

    @property
    def case_name(self) -> str:
        return self.case["name"]

    @property
    def slug(self) -> str:
        return slugify(self.case_name)

    @property
    def workdir(self) -> Path:
        return WORKDIRS_ROOT / self.agent_id / self.slug

    @property
    def prediction_path(self) -> Path:
        return PREDICTIONS_ROOT / self.agent_id / f"{self.slug}.pptx"


def load_cases(split: str, case_filter: list[str] | None) -> list[dict]:
    pairs = json.loads(PAIRS_PATH.read_text(encoding="utf-8"))
    for pair in pairs:
        pair["name"] = pair["name"].strip()
    if split == "subset":
        subset_names = {n.strip() for n in json.loads(SUBSET_PATH.read_text(encoding="utf-8"))}
        pairs = [p for p in pairs if p["name"] in subset_names]
    if case_filter:
        wanted = {c.strip().lower() for c in case_filter}
        pairs = [
            p for p in pairs
            if p["name"].lower() in wanted
            or slugify(p["name"]) in wanted
            or p["name"].split(":")[0].strip().lower() in wanted
        ]
    return pairs


def build_command(agent: dict, prompt: str) -> list[str]:
    return [arg.replace("{prompt}", prompt) for arg in agent["run"]]


def fresh_home(seed_files: list[str], home: Path, real_home: Path) -> Path:
    """An empty HOME for one session, holding only the listed files copied from the real one.

    The Antigravity CLI keeps knowledge and conversation summaries under HOME and reads them
    in later sessions. A home of its own keeps every case independent of the others.
    """
    if home.exists():
        shutil.rmtree(home)
    home.mkdir(parents=True)
    for rel in seed_files:
        source = real_home / rel
        if source.exists():
            (home / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, home / rel)
    return home


def remove_seed(seed_files: list[str], home: Path) -> None:
    """Take the copied files (the login) back out once the session is over; its records stay."""
    for rel in seed_files:
        (home / rel).unlink(missing_ok=True)


def run_task(task: Task, manifest_lock: Lock, manifest_path: Path, verbose: bool,
             idle_limit: int | None = None, max_timeout: int | None = None) -> str:
    original_path = (PROJECT_ROOT / task.case["original"]).resolve()
    notes: list[str] = []
    exit_code: int | None = None
    started = time.time()

    if not original_path.exists():
        status = "missing_original"
        notes.append(f"original not found: {original_path}")
        duration = 0.0
        original_hash = ""
        prediction_hash = ""
    else:
        if task.workdir.exists():
            shutil.rmtree(task.workdir)
        task.workdir.mkdir(parents=True)
        # Each workdir is its own git root so OpenCode (and any git-aware CLI)
        # treats it as a standalone project; WORKDIRS_ROOT lives outside the
        # repo so there is no enclosing repo to resolve to instead.
        subprocess.run(["git", "init", "-q", str(task.workdir)], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deck_path = task.workdir / "deck.pptx"
        shutil.copy2(original_path, deck_path)
        (task.workdir / "INSTRUCTION.md").write_text(
            INSTRUCTION_TEMPLATE.format(case_name=task.case_name, prompt=task.case["prompt"]),
            encoding="utf-8",
        )
        original_hash = sha256_of(deck_path)
        prediction_hash = ""

        command = build_command(task.agent, TASK_PROMPT)
        # subprocess(cwd=...) changes the real working directory but NOT the
        # inherited $PWD, and OpenCode trusts $PWD over the process cwd when
        # resolving its project: with the runner launched from the repo root,
        # every session bootstrapped at /root/PPTArena and edited decks there
        # instead of its own workdir. Pin PWD to the workdir for all agents.
        env = dict(os.environ, PWD=str(task.workdir))
        env.pop("OLDPWD", None)
        if task.agent_id.startswith("opencode"):
            # OpenCode also persists project/session state in XDG_DATA_HOME;
            # a throwaway per-task data dir seeded with only the auth file
            # keeps concurrent sessions from sharing any state.
            xdg = task.workdir / ".xdg"
            (xdg / "opencode").mkdir(parents=True)
            real_auth = Path.home() / ".local" / "share" / "opencode" / "auth.json"
            if real_auth.exists():
                shutil.copy2(real_auth, xdg / "opencode" / "auth.json")
            env["XDG_DATA_HOME"] = str(xdg)
        seed_files = task.agent.get("fresh_home")
        session_home = WORKDIRS_ROOT / "_homes" / task.agent_id / task.slug
        if seed_files is not None:
            env["HOME"] = str(fresh_home(seed_files, session_home, Path.home()))
        log_path = task.workdir / "agent_output.log"
        # No fixed wall-clock cap: a model gets as long as it needs, as long as
        # it keeps making progress. We poll the output log and only abort if it
        # goes completely silent for `idle_limit` seconds — that signals a stuck
        # or looping agent, not a slow one. `max_timeout` is an optional
        # last-resort absolute backstop (default None = unbounded).
        idle_limit = idle_limit or int(task.agent.get("idle_timeout_seconds", 1800))

        try:
            with log_path.open("w", encoding="utf-8") as log_file:
                log_file.write(f"$ {' '.join(command)}\n\n")
                log_file.flush()
                proc = subprocess.Popen(
                    command,
                    cwd=task.workdir,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    env=env,
                )
                last_size = -1
                last_progress = time.time()
                aborted: str | None = None
                while True:
                    try:
                        proc.wait(timeout=15)
                        break  # process exited on its own
                    except subprocess.TimeoutExpired:
                        pass
                    now = time.time()
                    try:
                        size = log_path.stat().st_size
                    except OSError:
                        size = last_size
                    if size != last_size:
                        last_size = size
                        last_progress = now
                    if now - last_progress > idle_limit:
                        aborted = f"stalled: no output for {idle_limit}s"
                        break
                    if max_timeout and now - started > max_timeout:
                        aborted = f"exceeded max_timeout {max_timeout}s"
                        break
                if aborted is not None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=20)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                    notes.append(aborted)
                    exit_code = None
                else:
                    exit_code = proc.returncode
                    if exit_code != 0:
                        notes.append(f"exit code {exit_code}")
        except FileNotFoundError:
            notes.append(f"CLI not found: {command[0]}")
            exit_code = None
        finally:
            if seed_files is not None:
                remove_seed(seed_files, session_home)

        duration = round(time.time() - started, 1)

        if any(k in " ".join(notes) for k in ("stalled", "exceeded max_timeout", "timed out")):
            status = "timeout"
        elif any(n.startswith("CLI not found") for n in notes):
            status = "cli_missing"
        elif not deck_path.exists():
            status = "missing_output"
            notes.append("deck.pptx was deleted by the agent")
        elif not is_valid_pptx(deck_path):
            status = "invalid_pptx"
        else:
            prediction_hash = sha256_of(deck_path)
            if prediction_hash == original_hash:
                status = "no_change"
            elif exit_code not in (0, None):
                status = "ok_with_warnings"
            else:
                status = "ok"

        if status in ("ok", "ok_with_warnings", "no_change"):
            task.prediction_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(deck_path, task.prediction_path)

    row = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "agent": task.agent_id,
        "case_name": task.case_name,
        "slug": task.slug,
        "status": status,
        "duration_seconds": duration,
        "exit_code": "" if exit_code is None else exit_code,
        "prediction_path": str(task.prediction_path.relative_to(PROJECT_ROOT))
        if task.prediction_path.exists()
        else "",
        "original_sha256": original_hash,
        "prediction_sha256": prediction_hash,
        "notes": " | ".join(notes),
    }
    with manifest_lock:
        write_header = not manifest_path.exists()
        with manifest_path.open("a", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=MANIFEST_FIELDS)
            if write_header:
                writer.writeheader()
            writer.writerow(row)

    marker = {"ok": "+", "ok_with_warnings": "~", "no_change": "=", "timeout": "T"}.get(status, "!")
    print(f"[{marker}] {task.agent_id:<22} {task.slug:<10} {status:<16} {duration:>7}s  {' | '.join(notes)}")
    return status


def check_agents(agents: dict) -> int:
    print(f"{'agent':<24}{'status':<12}version / problem")
    print("-" * 70)
    missing = 0
    for agent_id, agent in agents.items():
        try:
            proc = subprocess.run(
                agent["check"], capture_output=True, text=True, timeout=30, check=False
            )
            version = (proc.stdout or proc.stderr).strip().splitlines()[0] if (proc.stdout or proc.stderr).strip() else "?"
            status = "ok" if proc.returncode == 0 else f"exit {proc.returncode}"
            if proc.returncode != 0:
                missing += 1
        except FileNotFoundError:
            status, version = "MISSING", f"`{agent['check'][0]}` not on PATH"
            missing += 1
        except subprocess.TimeoutExpired:
            status, version = "TIMEOUT", "check command hung"
            missing += 1
        print(f"{agent_id:<24}{status:<12}{version}")
        print(f"{'':<24}{'':<12}auth: {agent['auth_hint']}")
    print("-" * 70)
    print(f"{len(agents) - missing}/{len(agents)} agents ready")
    return missing


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate PPTArena predictions with CLI coding agents.")
    parser.add_argument("--agents", default="all",
                        help="Comma-separated agent ids from agents.json, or 'all' (default).")
    parser.add_argument("--split", choices=["subset", "full"], default="subset",
                        help="Case split: the matched 25-case hard subset (default) or all 100 cases.")
    parser.add_argument("--cases", default=None,
                        help="Comma-separated case filter (e.g. 'Case 6,case_012').")
    parser.add_argument("--parallel", type=int, default=5,
                        help="Concurrent agent processes (default: 5).")
    parser.add_argument("--limit", type=int, default=None,
                        help="Only run the first N cases per agent (smoke tests).")
    parser.add_argument("--force", action="store_true",
                        help="Re-run tasks even if a prediction already exists.")
    parser.add_argument("--idle-timeout", type=int, default=None,
                        help="Abort an agent only after this many seconds of NO output "
                             "(stuck/looping, not slow). Default: agent's idle_timeout_seconds or 1800.")
    parser.add_argument("--max-timeout", type=int, default=None,
                        help="Optional absolute wall-clock backstop in seconds "
                             "(default: none — a productive agent runs as long as it needs).")
    parser.add_argument("--check", action="store_true",
                        help="Only verify that each agent CLI is installed and exits cleanly.")
    parser.add_argument("--dry-run", action="store_true",
                        help="List planned tasks without running anything.")
    args = parser.parse_args()

    agents = json.loads(AGENTS_PATH.read_text(encoding="utf-8"))
    if args.agents != "all":
        wanted = [a.strip() for a in args.agents.split(",") if a.strip()]
        unknown = [a for a in wanted if a not in agents]
        if unknown:
            sys.exit(f"Unknown agent id(s): {', '.join(unknown)}. Known: {', '.join(agents)}")
        agents = {a: agents[a] for a in wanted}

    if args.check:
        sys.exit(1 if check_agents(agents) else 0)

    cases = load_cases(args.split, args.cases.split(",") if args.cases else None)
    if args.limit:
        cases = cases[: args.limit]
    if not cases:
        sys.exit("No cases matched the split/filter.")

    tasks: list[Task] = []
    skipped = 0
    for case in cases:
        for agent_id, agent in agents.items():
            task = Task(agent_id=agent_id, agent=agent, case=case)
            if not args.force and task.prediction_path.exists():
                skipped += 1
                continue
            tasks.append(task)

    print(f"{len(tasks)} task(s) to run ({skipped} already have predictions; "
          f"{len(cases)} case(s) x {len(agents)} agent(s), parallel={args.parallel})")
    if args.dry_run:
        for task in tasks:
            print(f"  {task.agent_id:<24}{task.slug:<12}{task.case_name}")
        return
    if not tasks:
        return

    manifest_lock = Lock()
    manifest_path = PREDICTIONS_ROOT / "manifest.csv"
    PREDICTIONS_ROOT.mkdir(parents=True, exist_ok=True)

    statuses: dict[str, int] = {}
    started = time.time()
    with ThreadPoolExecutor(max_workers=args.parallel) as pool:
        futures = {pool.submit(run_task, t, manifest_lock, manifest_path, False,
                               args.idle_timeout, args.max_timeout): t for t in tasks}
        for future in as_completed(futures):
            task = futures[future]
            try:
                status = future.result()
            except Exception as exc:  # noqa: BLE001 - keep the batch alive
                status = "runner_error"
                print(f"[!] {task.agent_id:<22} {task.slug:<10} runner_error: {exc}")
            statuses[status] = statuses.get(status, 0) + 1

    elapsed = round(time.time() - started, 1)
    print("\nDone in", elapsed, "s:", ", ".join(f"{k}={v}" for k, v in sorted(statuses.items())))
    print(f"Manifest: {manifest_path.relative_to(PROJECT_ROOT)}")
    print("Next: python3 agent_bench/judge_predictions.py --agents", ",".join(agents))


if __name__ == "__main__":
    main()
