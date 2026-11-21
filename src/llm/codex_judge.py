"""Judge through the Codex CLI, so a ChatGPT plan can stand in for an API key.

A judge model id of the form ``codex/<model>`` (for example ``codex/gpt-6.2-sol``)
is answered by ``codex exec`` instead of an HTTP API. The client below mimics the
one call the judge makes on an OpenAI-style client,
``client.chat.completions.create(model=..., messages=[...])``, so the prompts are
the same ones the Kimi and OpenRouter judges receive.

Each request is one fresh, read-only ``codex exec`` run with no saved session.
Text parts are joined into a single prompt in their original order. Images are
attached in the same order, and a numbered marker is left where each one stood.

Codex is started without the personal ``config.toml`` and with hooks, memories,
plugins and apps turned off. It still reads ``AGENTS.md`` from its home folder, so
for a clean judge point ``PPTARENA_CODEX_HOME`` at a folder that holds nothing but
the Codex login (``auth.json``).

Environment:
    PPTARENA_CODEX_HOME        Codex home folder to use instead of ~/.codex.
    CODEX_JUDGE_REASONING_EFFORT   Reasoning effort, default "low".
    CODEX_JUDGE_TIMEOUT        Seconds allowed per run, default 600.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace

CODEX_PREFIX = "codex/"
DISABLED_FEATURES = ("hooks", "memories", "plugins", "apps", "multi_agent")
PREAMBLE = (
    "Answer directly from the text and attached images in this message. "
    "Do not run commands or use tools."
)
USAGE_LIMIT_MARKERS = ("usage limit", "usage_limit")

# Set once the plan's usage limit is reported. Later calls fail at once, so a long
# batch stops cleanly instead of writing hundreds of failed rows.
usage_limit_hit = threading.Event()

_usage_lock = threading.Lock()
usage_totals = {"runs": 0, "input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0}


class CodexUsageLimitError(RuntimeError):
    """The ChatGPT plan behind Codex has no usage left for now."""


def is_codex_model(model_id) -> bool:
    return bool(model_id) and str(model_id).lower().startswith(CODEX_PREFIX)


def codex_model_name(model_id: str) -> str:
    return model_id[len(CODEX_PREFIX):] if is_codex_model(model_id) else model_id


def _run_codex(command, prompt, timeout, env):
    return subprocess.run(command, input=prompt, capture_output=True, text=True, timeout=timeout, env=env)


def _flatten(messages, image_dir: Path):
    """Turn chat messages into one prompt plus the image files to attach, in order."""
    lines = [PREAMBLE, ""]
    images = []
    for message in messages:
        content = message.get("content")
        parts = [{"type": "text", "text": content}] if isinstance(content, str) else (content or [])
        for part in parts:
            if part.get("type") == "image_url":
                data = part["image_url"]["url"].split("base64,", 1)[-1]
                path = image_dir / f"image_{len(images) + 1:02d}.png"
                path.write_bytes(base64.b64decode(data))
                images.append(path)
                lines.append(f"[attached image {len(images)}]")
            else:
                lines.append(str(part.get("text", "")))
        lines.append("")
    if images:
        lines.insert(2, f"{len(images)} images are attached, numbered in the order they are attached. "
                        "Each [attached image N] marker below shows where that image belongs.")
        lines.insert(3, "")
    return "\n".join(lines).strip() + "\n", images


def _error_text(done) -> str:
    """What Codex itself reported as going wrong: stderr plus its error events.

    The judge's own answer is left out on purpose, so a deck that happens to talk
    about usage limits cannot be mistaken for the plan running out.
    """
    problems = [done.stderr or ""]
    for line in (done.stdout or "").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            problems.append(line)
            continue
        if isinstance(event, dict) and event.get("type") in ("error", "turn.failed"):
            problems.append(json.dumps(event))
    return "\n".join(part for part in problems if part.strip())


def _record_usage(stdout: str) -> None:
    for line in (stdout or "").splitlines():
        if '"turn.completed"' not in line:
            continue
        try:
            usage = json.loads(line).get("usage") or {}
        except ValueError:
            continue
        with _usage_lock:
            usage_totals["runs"] += 1
            for key in ("input_tokens", "cached_input_tokens", "output_tokens"):
                usage_totals[key] += int(usage.get(key) or 0)


class CodexChatClient:
    def __init__(self, runner=_run_codex, attempts: int = 3, retry_wait: float = 20.0):
        self._runner = runner
        self._attempts = attempts
        self._retry_wait = retry_wait
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, model, messages, **_unused):
        if usage_limit_hit.is_set():
            raise CodexUsageLimitError("Codex usage limit was reached earlier in this run.")
        with tempfile.TemporaryDirectory(prefix="codex_judge_") as tmp:
            work = Path(tmp)
            (work / "cwd").mkdir()
            prompt, images = _flatten(messages, work)
            reply_path = work / "reply.txt"
            command = [
                "codex", "exec",
                "--ignore-user-config", "--ignore-rules", "--ephemeral", "--skip-git-repo-check",
                "-s", "read-only",
            ]
            for feature in DISABLED_FEATURES:
                command += ["--disable", feature]
            effort = os.environ.get("CODEX_JUDGE_REASONING_EFFORT", "low")
            command += ["-m", model, "-c", f'model_reasoning_effort="{effort}"', "-C", str(work / "cwd")]
            for image in images:
                command += ["-i", str(image)]
            command += ["--json", "-o", str(reply_path), "-"]

            env = dict(os.environ)
            if os.environ.get("PPTARENA_CODEX_HOME"):
                env["CODEX_HOME"] = os.environ["PPTARENA_CODEX_HOME"]
            timeout = float(os.environ.get("CODEX_JUDGE_TIMEOUT", "600"))

            failure = "no attempt made"
            for attempt in range(self._attempts):
                if attempt:
                    time.sleep(self._retry_wait * attempt)
                if usage_limit_hit.is_set():
                    raise CodexUsageLimitError("Codex usage limit was reached earlier in this run.")
                reply_path.unlink(missing_ok=True)
                try:
                    done = self._runner(command, prompt, timeout, env)
                except subprocess.TimeoutExpired:
                    failure = f"codex exec gave no answer within {timeout:.0f} s"
                    continue
                output = _error_text(done)
                if any(marker in output.lower() for marker in USAGE_LIMIT_MARKERS):
                    usage_limit_hit.set()
                    raise CodexUsageLimitError(output.strip()[-400:])
                text = reply_path.read_text(encoding="utf-8").strip() if reply_path.exists() else ""
                if done.returncode == 0 and text:
                    _record_usage(done.stdout)
                    message = SimpleNamespace(content=text)
                    return SimpleNamespace(choices=[SimpleNamespace(message=message)])
                failure = f"codex exec exited {done.returncode}: {output.strip()[-400:]}"
            raise RuntimeError(failure)


def create_codex_client():
    """A Codex-backed judge client, or None when the codex CLI is not installed."""
    if not shutil.which("codex"):
        return None
    return CodexChatClient()
