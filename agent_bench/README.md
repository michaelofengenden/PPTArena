# agent_bench: CLI coding agents on PPTArena

Scaffolding for running seven CLI coding agents across the PPTArena benchmark on a VM.
Generation and scoring are **decoupled**: the VM only generates predictions; judging
can run later on any machine with the repo, LibreOffice and an API key for the judge
model (Moonshot for the Kimi K2.6 judge the leaderboard uses).

| agent id | System | Backbone |
|---|---|---|
| `codex_gpt55` | Codex CLI | GPT-5.5, reasoning effort xhigh |
| `claude_code_opus48` | Claude Code | Opus 4.8 (Max plan) |
| `opencode_glm52` | OpenCode | GLM-5.2 |
| `gemini_cli_35flash` | Gemini CLI | Gemini 3.5 Flash |
| `opencode_minimax_m3` | OpenCode | MiniMax-M3 |
| `opencode_deepseek_v4` | OpenCode | DeepSeek V4 Pro |
| `opencode_kimi_k27code` | OpenCode | Kimi K2.7 Code (Moonshot key under the `moonshotai` provider) |

Exact CLI flags and model ids live in [`agents.json`](agents.json). Tweak them there if a
CLI has drifted; no code changes needed. Leaderboard entries for all seven are already
registered in `src/app.py`: rows appear automatically once result CSVs land in
`agent_bench/results/`.

## Reproducing the published leaderboard

The leaderboard was not made with the script defaults. These are the settings it used.
Set up the CLIs as in sections 1 and 2 first.

**Commits.** The result CSVs in `agent_bench/results/` were committed in 012bdd1. Gemini
CLI's Kimi CSV was replaced in 6f26a63, after its full run finished on July 6 2026. The
runner is `run_agents.py` as of 96bc171; it has not changed since.

**Generation.** All seven agents, with the flags in `agents.json`. One attempt per case,
except that some failed cases were rerun; a CSV row holds the last attempt.

```bash
# Hard subset (25 cases). The runner has no fixed-clock flag any more. Setting both
# limits to the same value kills a task at that wall clock, like the old runner did.
python3 agent_bench/run_agents.py --split subset --idle-timeout 2400 --max-timeout 2400 \
  --agents codex_gpt55,claude_code_opus48,opencode_glm52,opencode_minimax_m3,opencode_deepseek_v4,opencode_kimi_k27code
python3 agent_bench/run_agents.py --split subset --idle-timeout 1800 --max-timeout 1800 \
  --agents gemini_cli_35flash

# Full set (all 100 cases). Cases that already have a deck are skipped, so the
# 25 hard-subset decks are reused, as they were in the published run.
python3 agent_bench/run_agents.py --split full --idle-timeout 1800 --max-timeout 5400
```

**Judging.** Kimi K2.6, three samples per case, per-metric median. Needs
`MOONSHOT_API_KEY` in `credentials.env`. A second judge, Qwen3.7 Plus, was run only to
check agreement. It needs `OPENROUTER_API_KEY`.

```bash
python3 agent_bench/judge_predictions.py --split full --judge-model kimi-k2.6 --samples 3
python3 agent_bench/judge_predictions.py --split full --judge-model qwen/qwen3.7-plus --samples 3 \
  --csv-suffix _qwen_judge_results.csv
```

For the hard subset alone, use `--split subset`. The leaderboard builds both views from
the same 100-row CSV per agent; the Hard Subset view keeps the 25 cases in
`subset25.json`. In 012bdd1 every row was judged again, hard-subset rows included. Pass
`--no-resume` to do the same instead of appending to an existing CSV. Only the Kimi
CSVs (`<agent_id>_judge_results.csv`) are shown on the leaderboard.

**Score.** Per case: the mean of IF/5 and VQ/5, as a percentage (`_case_pct` in
`src/app.py`; each metric is clamped to 0 to 5). The split score averages that over
every case in the split, 25 or 100. A case with no collected deck counts as 0 and stays
in the denominator.

**Timeouts.**

- Hard subset, July 2 to 4 2026: a fixed wall clock of 2400 s per task (1800 s for
  Gemini CLI, its `timeout_seconds` in `agents.json`, which the runner before 96bc171
  read). Some failed OpenCode cases were retried later with longer limits: 3600 s, then
  the full-set settings.
- Full set, July 5 2026: idle watchdog, so a session is killed after 1800 s with no new
  output, plus `--max-timeout 5400`. Gemini CLI's full run was finished on July 6 with
  `--max-timeout 3600`; none of its final attempts timed out.
- A killed session's deck is not collected, even if the agent had already edited it.
  That case scores 0.

**Sampling.** No temperature or top_p is set anywhere for the agents or for the Kimi and
Qwen judges, so each uses its CLI's or provider's defaults. The agent CLIs get only the
flags in `agents.json` (Codex also has `model_reasoning_effort=xhigh`). Both judges run
with thinking or reasoning disabled. Runs are not deterministic. The median of three
judge samples narrows the spread but does not remove it.

**CLI versions** were not pinned. `run_agents.py --check` prints each CLI's version;
record it with your run.

**Other rows.** The leaderboard also shows Baidu DuMate and three computer-use agents.
Their decks were made outside this runner. Their CSVs use the same Kimi K2.6
median-of-3 judge.

### Known gaps

- OpenCode GLM-5.2, DeepSeek V4 Pro and MiniMax-M3 have no deck for cases 86, 88, 90,
  91, 93 to 96, 98, 99 and 100, because the full-set batch stopped before reaching
  them. Those cases currently count as 0. MiniMax-M3 is also missing 85.
- On the hard subset, Codex, Claude Code, GLM-5.2, Gemini CLI and DeepSeek V4 Pro have
  a deck for all 25 cases. MiniMax-M3 has 18 (no deck for 16, 34, 44, 46, 54, 67, 89)
  and Kimi K2.7 Code has 20 (no deck for 46, 50, 54, 67, 97).
- `gemini_cli_35flash_qwen_judge_results.csv` predates Gemini CLI's July 6 run and was
  not judged again, so its Kimi and Qwen CSVs score different decks.
- Case 90's instruction used to say "recreate this screenshot as a new, fully editable
  slide", which Claude Code and Codex read as a request to add a slide. It now says to
  replace slide 12 in place. The published Case 90 scores were produced with the old
  wording.

### Slide matching

The published scores were judged while both judges compared slide i with slide i. A
prediction that inserted or dropped a slide made every later slide look changed, and
slides past the end of the shorter deck were never shown to the visual judge.

The judge now pairs slides by content first (`src/ppt/slide_alignment.py`). An extra or
missing slide is reported once, and the remaining slides are compared with their real
counterparts. Decks whose slides already line up are judged exactly as before: the
requests sent to the judge are byte-identical.

Among the published decks this changes the comparison for Case 43 (six of the seven CLI
agents placed a section divider one slide off) and Case 90 (Claude Code and Codex added
a 13th slide), plus Cases 12, 28 and 97 for two of the computer-use rows. Those
published scores still come from the old comparison.

## 1. VM setup

```bash
git clone https://github.com/michaelofengenden/PPTArena.git && cd PPTArena
python3 -m pip install -r src/requirements.txt   # only needed for scoring, but cheap

# Install the agent CLIs (names current as of mid-2026; verify if installs fail)
npm install -g @openai/codex            # codex
npm install -g @anthropic-ai/claude-code  # claude
npm install -g opencode-ai              # opencode  (or: curl -fsSL https://opencode.ai/install | bash)
npm install -g @google/gemini-cli       # gemini
```

## 2. Authenticate each CLI

```bash
codex login            # ChatGPT account with GPT-5.5 access
claude                 # first run opens login; use a Max-plan account
opencode auth login    # run four times: Zhipu/Z.AI, MiniMax, DeepSeek, Moonshot (`moonshotai`)
gemini                 # first run opens Google OAuth
```

Then verify everything is wired up:

```bash
python3 agent_bench/run_agents.py --check
```

If an OpenCode model id doesn't match, list what's available (`opencode models`)
and fix the `-m` value in `agents.json`.

## 3. Smoke test, then the real run

```bash
# One case through one agent, watch it work:
python3 agent_bench/run_agents.py --agents codex_gpt55 --limit 1

# 25-case hard subset (the default split), all seven agents, five processes at a time:
nohup python3 agent_bench/run_agents.py --parallel 5 > agent_bench/run.log 2>&1 &
tail -f agent_bench/run.log

# All 100 cases:
nohup python3 agent_bench/run_agents.py --split full --parallel 5 > agent_bench/run.log 2>&1 &
```

- **No fixed wall clock**: the runner checks the agent's output log every 15 s and
  kills the session after `--idle-timeout` seconds without new output (default 1800,
  or `idle_timeout_seconds` in `agents.json`). `--max-timeout` adds an absolute cap;
  there is none by default. The `timeout_seconds` field in `agents.json` belongs to
  the old fixed-clock runner and is no longer read.
- **Resume-safe**: re-running skips any (agent, case) that already has a prediction;
  `--force` regenerates. This holds across splits, so `--split full` after a subset
  run only does the other 75 cases.
- Each task runs in an isolated `~/agent_bench_workdirs/<agent>/<case>/` (outside the
  repo; set `AGENT_BENCH_WORKDIRS` to move it) containing `deck.pptx` (copy of the
  original) + `INSTRUCTION.md`; the agent's stdout and stderr are kept in
  `agent_output.log` there.
- Collected decks land in `agent_bench/predictions/<agent>/<case>.pptx`, with one row
  per attempt in `agent_bench/predictions/manifest.csv`. Statuses:
  - `ok`: valid, edited deck, and the CLI exited 0.
  - `ok_with_warnings`: valid, edited deck, but the CLI exited non-zero.
  - `no_change`: the deck is byte-identical to the original. It is still collected
    and scored. Worth re-running those with `--force`.
  - `timeout`: the runner killed the session. The notes say `stalled: no output for
    Ns` for the idle watchdog or `exceeded max_timeout Ns` for the cap. Rows from the
    old fixed-clock runner say `timed out after Ns`.
  - `invalid_pptx`: the deck is no longer a readable PPTX zip.
  - `missing_output`: the agent deleted `deck.pptx`.
  - `missing_original`: the case's original deck was not found.
  - `cli_missing`: the agent CLI is not on PATH.

  Only `ok`, `ok_with_warnings` and `no_change` decks are collected. A killed
  session's deck is not collected even if the agent had already edited it, so that
  case scores 0. If the runner itself crashes on a task, the end-of-run summary
  counts it as `runner_error` and no manifest row is written.
- Predictions are gitignored (heavy binaries). To get them off the VM:
  `tar czf predictions.tgz agent_bench/predictions/` and download, or push them to
  the Hugging Face dataset.

## 4. Score later (any machine with the repo)

Judging defaults to **Gemini 3.5 Flash** with one sample. The published leaderboard
used Kimi K2.6 with three samples instead (see "Reproducing the published leaderboard"
above). Needs `credentials.env` at the repo root (environment variables of the same
name also work) with the judge's key: `MOONSHOT_API_KEY` for `kimi-*`,
`OPENROUTER_API_KEY` for `qwen/*`, `GEMINI_API_KEY` for `gemini-*`, or `OPENAI_API_KEY`
for `gpt-*`. Also needs LibreOffice installed (slide rendering).

```bash
python3 agent_bench/judge_predictions.py --agents all              # single-sample
python3 agent_bench/judge_predictions.py --agents all --samples 3  # median of 3 (steadier)
python3 agent_bench/judge_predictions.py --split full --judge-model kimi-k2.6 --samples 3  # published setting
```

Speed notes: ground-truth renders/JSON/XML are computed once per case and cached
(`benchmark_outputs/judge_render_cache/`), then shared across all seven agents and
re-runs. Only prediction decks are rendered per judgement. `--samples 3` is what the
leaderboard uses.

Writes `agent_bench/results/<agent_id>_judge_results.csv` (same schema as the
existing judge runs; `--csv-suffix` changes the suffix). Cases in the split without a
prediction get zero rows, so a CSV always covers the whole split: 25 rows for the
subset, 100 for the full set. A re-run only adds cases missing from the CSV;
`--no-resume` rewrites it. Commit the CSVs and push. The leaderboard picks them up
automatically, on the deployed site too.

Before the first real scoring run, audit the benchmark data itself:

```bash
python3 src/audit_cases.py            # integrity checks + HTML review report
```
