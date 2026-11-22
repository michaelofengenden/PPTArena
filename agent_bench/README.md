# agent_bench: CLI coding agents on PPTArena

Scaffolding for running ten CLI coding agents across the PPTArena benchmark on a VM.
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
| `claude_code_opus55` | Claude Code | Opus 5.5, effort max (added November 2026) |
| `codex_gpt62astra` | Codex CLI | GPT-6.2 Astra, reasoning effort xhigh (added November 2026) |
| `agy_gemini4_neon` | Antigravity CLI | Gemini 4 Neon, effort xhigh (added November 2026) |

Exact CLI flags and model ids live in [`agents.json`](agents.json). Tweak them there if a
CLI has drifted; no code changes needed. Leaderboard entries for all ten are already
registered in `src/app.py`: rows appear automatically once result CSVs land in
`agent_bench/results/`.

## Reproducing the published leaderboard

The leaderboard was not made with the script defaults. These are the settings it used.
Set up the CLIs as in sections 1 and 2 first.

Most decks on the leaderboard were generated once, in July 2026. They have been judged
twice. The rows shown today were judged in November 2026 by GPT-6.2 Sol, after three
judge bugs were fixed (see "Judge fixes" below). The first judgements, by Kimi K2.6,
are still in the repo. Three rows, Claude Code (Opus 5.5 max), Codex (GPT-6.2 Astra) and
the Antigravity CLI (Gemini 4 Neon xhigh), were generated in November 2026 under slightly
different conditions; see "The November rows" below before comparing them with the July
rows.

The two sets of numbers are not comparable. On the Full Set the new scores are 5 to 16
points higher than the July ones. Instruction following rose for every agent, by 11 to
19 points, and visual quality moved both ways. In the largest disagreements we read,
the July judge had marked down harmless structural differences, such as a table styled
through its theme instead of explicit cell fills, that the new judge accepts.

**Commits.** The result CSVs in `agent_bench/results/` were committed in 012bdd1. Gemini
CLI's Kimi CSV was replaced in 6f26a63, after its full run finished on July 6 2026. The
runner is `run_agents.py` as of 96bc171; it has not changed since.

**Generation (July 2026).** The first seven agents, with the flags in `agents.json`.
One attempt per case, except that some failed cases were rerun; a CSV row holds the
last attempt.

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

**The November rows.** Claude Code (Opus 5.5, `--effort max`) and Codex (GPT-6.2 Astra,
`model_reasoning_effort=xhigh`) were generated on November 21 2026, on a fresh Debian 12
VM (8 vCPUs) with Claude Code 2.1.330, Codex CLI 0.171.0, python-pptx 0.6.23 and
LibreOffice 25.2.3.

```bash
python3 agent_bench/run_until_done.py --agents codex_gpt62astra --split subset --parallel 3
python3 agent_bench/run_until_done.py --agents codex_gpt62astra --split full --parallel 3
python3 agent_bench/run_until_done.py --agents claude_code_opus55 --split subset --parallel 4
python3 agent_bench/run_until_done.py --agents claude_code_opus55 --split full --parallel 6
```

`run_until_done.py` calls `run_agents.py` with `--idle-timeout 1800 --max-timeout 5400`
and runs a case again only if its session died because the plan ran out of usage or
lost its login. That never happened in this run. What differs from the July rows:

- The hard subset ran under the full-set limits (idle watchdog 1800 s, cap 5400 s), not
  under the fixed 2400 s wall clock.
- One attempt per case, with one exception: Opus 5.5 Case 89 ran twice. Claude Code in
  plain `-p` mode prints nothing until it is done, so the idle watchdog killed the first
  attempt at 1800 s while it was still working. From then on this agent ran with
  `--output-format stream-json --verbose`, which lets the watchdog see progress. The 24
  hard-subset decks made before that change all finished in under 1800 s (longest:
  1484 s). The July Opus 4.8 run was never near that limit (longest: 676 s).
- Case 90 was given its new wording (replace slide 12 in place).
- All 100 Opus 5.5 sessions returned an edited deck. Astra returned 99; on Case 99 it
  left the deck unchanged because the instruction refers to a video file that the case
  does not include. That unchanged deck is judged like any other.
- The VM held the original decks and the case list cut down to name, prompt, category
  and original path, so no ground truth and no rubrics. The agents did have network
  access, and the ground truth is public in this repo. We searched every session log
  and Claude Code transcript for access to it and found none. One Astra session
  (Case 99) listed this repo's files on GitHub while looking for the missing video.
- Ground truth and both agents' decks were rendered on that same VM after generation
  had finished, then judged like the other rows.

The Antigravity CLI row (Gemini 4 Neon, model id `gemini-4-neon-xhigh`; the effort is
part of the id) was generated on November 22 2026, on another fresh Debian 12 VM
(8 vCPUs) with Antigravity CLI 1.5.14, python-pptx 0.6.23 and LibreOffice 25.2.3, signed
in to a Google subscription.

```bash
python3 agent_bench/run_until_done.py --agents agy_gemini4_neon --split subset --parallel 5
python3 agent_bench/run_until_done.py --agents agy_gemini4_neon --split full --parallel 5
```

Same limits as the two rows above, and one attempt per case. Case 6 was made first, as
a single test session with the same flags, and kept. No session hit a plan limit or
lost its login. All 100 ended with the CLI's `SUCCESS` result and an edited deck; the
longest took 1398 s. What is particular to this row:

- Every session ran with a home folder of its own (`fresh_home` in `agents.json`). The
  CLI keeps knowledge and conversation summaries under `HOME` and reads them in later
  sessions, so a shared home would let one case inform the next. The fresh home holds
  only the login and a `settings.json` with `{"useG1Credits": false}`, which keeps the
  CLI from spending paid credits when the plan's quota runs out.
- Neon used the web. It ran 300 searches across 12 cases, for OOXML references and for
  facts on content cases (Case 50 checked the date of an agreement, Case 89 looked up
  animal facts). No query names the benchmark, this repo or a dataset, and it opened
  no pages and no browser. Case 91 installed two QR code packages from PyPI.
- On Cases 56, 77 and 78 Neon had the CLI's image tool make pictures, through five
  helper sessions that ran on Gemini 4 Hydrogen.
- The VM again held only the original decks and the cut-down case list. We searched
  every session log and full transcript for the ground truth, this repo, the dataset
  hub and the leaderboard site and found none. One session (Case 91) came across the
  runner's copy of its own original deck while looking for QR libraries.
- Ground truth and Neon's decks were rendered on that VM after generation had finished.

**Judging (the rows shown today).** GPT-6.2 Sol, three samples per case, per-metric
median. The judge runs through the Codex CLI on a ChatGPT plan, so it needs `codex`
installed and logged in and no API key. A judge id of the form `codex/<model>` selects
this route (`src/llm/codex_judge.py`).

```bash
# A Codex home that holds nothing but the login, so no personal AGENTS.md, config,
# hooks or memories reach the judge.
mkdir -p ~/codex-judge-home && ln -s ~/.codex/auth.json ~/codex-judge-home/auth.json
export PPTARENA_CODEX_HOME=~/codex-judge-home

python3 agent_bench/judge_predictions.py --split full --judge-model codex/gpt-6.2-sol --samples 3 \
  --csv-suffix _gpt62sol_judge_results.csv --no-resume \
  --sample-cache benchmark_outputs/judge_samples/gpt62sol
```

Each judge call is one fresh `codex exec` run: read-only, no saved session, reasoning
effort `low` (the model's default), with the same prompts the Kimi judge receives.
Every judged sample is kept in the `--sample-cache` folder, so the same command picks up
where it stopped if the plan's usage limit interrupts it. The three computer-use rows
use `--split subset`. Results are in `<agent_id>_gpt62sol_judge_results.csv`.

**Rendering.** The judge compares slide images made with LibreOffice 25.2.3 and
poppler's `pdftoppm`. For the GPT-6.2 Sol judgement the slides were rendered on two
machines: a Mac, and for the samples still missing on 191 decks a Debian 12 VM running
`render_decks.py` with the same LibreOffice version and the Liberation, DejaVu, Noto,
Carlito and Caladea fonts. Within one sample the ground truth and the prediction always
come from the same machine, so fonts are the same on both sides of a comparison. The
three November rows were rendered, ground truth included, on the VMs that generated
them, which have the same LibreOffice version and fonts as the Debian render VM. The first
judgements in July were rendered on the Linux VM that ran the agents.

**Judging (the first time, July 2026).** Kimi K2.6, three samples per case, per-metric
median. Needs `MOONSHOT_API_KEY` in `credentials.env`. A second judge, Qwen3.7 Plus, was
run only to check agreement. It needs `OPENROUTER_API_KEY`.

```bash
python3 agent_bench/judge_predictions.py --split full --judge-model kimi-k2.6 --samples 3
python3 agent_bench/judge_predictions.py --split full --judge-model qwen/qwen3.7-plus --samples 3 \
  --csv-suffix _qwen_judge_results.csv
```

Those results are in `<agent_id>_judge_results.csv` and
`<agent_id>_qwen_judge_results.csv`. They were made before the judge fixes below, so
running these commands today will not reproduce them.

For the hard subset alone, use `--split subset`. The leaderboard builds both views from
the same 100-row CSV per agent; the Hard Subset view keeps the 25 cases in
`subset25.json`. In 012bdd1 every row was judged again, hard-subset rows included. Pass
`--no-resume` to do the same instead of appending to an existing CSV.

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
- Both splits of the three November rows: idle watchdog 1800 s plus `--max-timeout 5400`.
- A killed session's deck is not collected, even if the agent had already edited it.
  That case scores 0.

**Sampling.** No temperature or top_p is set anywhere for the agents or the judges, so
each uses its CLI's or provider's defaults. The agent CLIs get only the flags in
`agents.json` (Codex also has `model_reasoning_effort=xhigh`, Claude Code with
Opus 5.5 has `--effort max`, and the Antigravity CLI takes its effort from the model id
`gemini-4-neon-xhigh`). The Kimi and Qwen judges
ran with thinking or reasoning disabled; the GPT-6.2 Sol judge runs at reasoning effort
`low`. Runs are not deterministic. The median of three judge samples narrows the spread
but does not remove it.

**CLI versions** were not pinned and were not recorded for the July runs. The November
rows used Claude Code 2.1.330, Codex CLI 0.171.0 and Antigravity CLI 1.5.14.
`run_agents.py --check` prints each CLI's version; record it with your run.

**Other rows.** The leaderboard also shows Baidu DuMate and three computer-use agents.
Their decks were made outside this runner. The computer-use decks were judged again
with GPT-6.2 Sol like the rest. The Baidu DuMate decks are not in this repo's
predictions folder, so that row still shows its Kimi K2.6 median-of-3 judgement and is
not directly comparable with the rows around it.

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
  replace slide 12 in place. The July decks were generated with the old wording, the
  two November rows with the new one.
- The Baidu DuMate row is still judged by Kimi K2.6 (see "Other rows").
- Four collected decks are empty files and score 0: DeepSeek V4 Pro cases 80, 83 and
  85, and MiniMax-M3 case 80.
- LibreOffice cannot open the ChatGPT Agent (CUA) deck for Case 87, so no slides are
  rendered and its visual score is 0. Its instruction score is judged as usual.

### Judge fixes

Three bugs affected the first (Kimi and Qwen) judgements. All three are fixed, and the
GPT-6.2 Sol rows were judged after the fixes.

**A copy of slide 1 in cached ground-truth renders.** Slides are rendered through
LibreOffice. Its direct PNG export writes only slide 1, named after the deck, and the
per-slide images come from a PDF fallback in the same folder. A fresh render returned
only the per-slide images. A cached render returned every PNG in the folder, so for 99
of the 100 cases the visual judge saw the ground truth with a copy of slide 1 at the
front and every later slide shifted by one. Any judging process that started after a
case's ground truth was first cached was affected, which covers most of the first
judgements. The Kimi reasons show it: several describe a "duplicate title slide" in the
ground truth or a prediction slide that "matches Ground Truth slide 3" one position
later. `render_cached` in `judge_predictions.py` now returns only the real slides.

**Failed judge calls scored as 0.** When the visual judge call failed, the case kept its
instruction score and got a visual score of 0 with no error recorded. 19 rows in the
first judgements look like this (visual score 0.00, empty reason). A failed call is now
reported as an error and that sample is judged again rather than counted.

**Slides compared by position.** Both judges compared slide i with slide i. A prediction
that inserted or dropped a slide made every later slide look changed, and slides past
the end of the shorter deck were never shown to the visual judge. The judge now pairs
slides by content first (`src/ppt/slide_alignment.py`). An extra or missing slide is
reported once, and the remaining slides are compared with their real counterparts.
Decks whose slides already line up are judged as before. Among the leaderboard decks
this changes the comparison for Case 43 (six of the seven CLI agents placed a section
divider one slide off) and Case 90 (Claude Code and Codex added a 13th slide), plus
Cases 12, 28 and 97 for two of the computer-use rows.

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

# 25-case hard subset (the default split), every agent in agents.json, five processes at a time:
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
- **Subscription plans**: a session that dies because the plan ran out of usage, or
  lost its login, leaves an untouched or half-edited deck that the runner would
  collect. `run_until_done.py --agents A,B --split full` wraps the runner: after each
  batch it finds those sessions by the end of their log, deletes their decks, waits
  25 minutes and runs the batch again. Ordinary failures and timeouts are left alone.
- **A home folder per session**: an agent that lists `fresh_home` in `agents.json` runs
  every case with `HOME` set to an empty folder under
  `~/agent_bench_workdirs/_homes/`, holding only the listed files copied from the real
  home. Use it for a CLI that carries memory from one session to the next, as the
  Antigravity CLI does. The copied files (the login) are removed when the session
  ends; the session's own records stay. Tools the agent needs, such as python-pptx,
  must then be installed system-wide, not under the real home.
- **Antigravity CLI**: keep `AGY_CLI_DISABLE_AUTO_UPDATE=true` in its run command, or the
  CLI updates itself in the middle of a run. Its stream-json log ends with a `result` event;
  `run_until_done.py` reads that event to tell a plan or login failure from a normal
  end.
- **Claude Code and the idle watchdog**: `claude -p` prints nothing until the session
  ends, so the watchdog cannot tell a long session from a stuck one. Add
  `--output-format stream-json --verbose` to the agent's flags, as `claude_code_opus55`
  has, when sessions can run longer than the idle limit.
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
uses GPT-6.2 Sol with three samples instead (see "Reproducing the published
leaderboard" above). Needs `credentials.env` at the repo root (environment variables of
the same name also work) with the judge's key: `MOONSHOT_API_KEY` for `kimi-*`,
`OPENROUTER_API_KEY` for `qwen/*`, `GEMINI_API_KEY` for `gemini-*`, or `OPENAI_API_KEY`
for `gpt-*`. A `codex/<model>` judge needs no key, only the Codex CLI logged in to a
ChatGPT plan. Also needs LibreOffice installed (slide rendering).

```bash
python3 agent_bench/judge_predictions.py --agents all              # single-sample
python3 agent_bench/judge_predictions.py --agents all --samples 3  # median of 3 (steadier)
python3 agent_bench/judge_predictions.py --split full --judge-model codex/gpt-6.2-sol --samples 3 \
  --csv-suffix _gpt62sol_judge_results.csv                         # published setting
```

`--predictions-root` reads the decks from another folder. `--sample-cache` keeps each
judged sample on disk so an interrupted run can pick up where it stopped.

Rendering is the heavy part and can run on another machine. Copy `render_decks.py` to a
Linux box with LibreOffice and poppler, render there, copy the output folder back, and
judge from it:

```bash
# On the render machine. in/gt/<case_slug>.pptx and in/pred/<agent_id>/<case_slug>.pptx
python3 render_decks.py in out --jobs 8

# On the judging machine, with LibreOffice never started here
python3 agent_bench/judge_predictions.py --split full --judge-model codex/gpt-6.2-sol --samples 3 \
  --render-cache out --no-local-render
```

Render ground truth and predictions on the same machine. When rendering locally,
`PPT_RENDER_CONCURRENCY=4` lets four LibreOffice runs go at once (default: one). The
judge's own work on each deck is single-core, so to go faster run one
`judge_predictions.py` process per agent rather than raising `--max-workers`.

Speed notes: ground-truth renders/JSON/XML are computed once per case and cached
(`benchmark_outputs/judge_render_cache/`), then shared across all agents and
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
