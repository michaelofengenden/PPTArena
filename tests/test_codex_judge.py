import base64
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "agent_bench"))

from llm import codex_judge  # noqa: E402


def png_bytes(color):
    buffer = io.BytesIO()
    Image.new("RGB", (32, 18), color).save(buffer, format="PNG")
    return buffer.getvalue()


def image_part(color):
    data = base64.b64encode(png_bytes(color)).decode("ascii")
    return {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{data}"}}


def text_part(text):
    return {"type": "text", "text": text}


class FakeCodex:
    """Stands in for the codex binary: records each run and writes the reply file."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.runs = []

    def __call__(self, command, prompt, timeout, env):
        images = [Path(command[i + 1]).read_bytes() for i, arg in enumerate(command) if arg == "-i"]
        self.runs.append({"command": command, "prompt": prompt, "images": images, "env": env})
        reply = self.replies.pop(0)
        if isinstance(reply, dict):
            return SimpleNamespace(returncode=reply.get("returncode", 1),
                                   stdout=reply.get("stdout", ""), stderr=reply.get("stderr", ""))
        Path(command[command.index("-o") + 1]).write_text(reply, encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")


def ask(client, user_content, model="gpt-6.2-sol"):
    return client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": "You are a judge."},
            {"role": "user", "content": user_content},
        ],
        max_tokens=1024,
        extra_body=None,
    )


class CodexJudgeClientTest(unittest.TestCase):
    def setUp(self):
        codex_judge.usage_limit_hit.clear()
        self.addCleanup(codex_judge.usage_limit_hit.clear)

    def test_codex_judge_ids_carry_a_codex_prefix(self):
        self.assertTrue(codex_judge.is_codex_model("codex/gpt-6.2-sol"))
        self.assertEqual(codex_judge.codex_model_name("codex/gpt-6.2-sol"), "gpt-6.2-sol")
        self.assertFalse(codex_judge.is_codex_model("gpt-6.2-sol"))
        self.assertFalse(codex_judge.is_codex_model("kimi-k2.6"))
        self.assertFalse(codex_judge.is_codex_model(None))

    def test_reply_comes_back_shaped_like_a_chat_completion(self):
        fake = FakeCodex('{"visual_quality_score": 4}')
        response = ask(codex_judge.CodexChatClient(runner=fake), "Score this deck.")
        self.assertEqual(response.choices[0].message.content, '{"visual_quality_score": 4}')

    def test_prompt_keeps_the_text_in_order_and_goes_in_on_stdin(self):
        fake = FakeCodex("{}")
        ask(codex_judge.CodexChatClient(runner=fake), "Score this deck.")
        prompt = fake.runs[0]["prompt"]
        self.assertLess(prompt.index("You are a judge."), prompt.index("Score this deck."))
        self.assertEqual(fake.runs[0]["command"][-1], "-")

    def test_images_are_attached_in_order_and_marked_where_they_stood(self):
        red, blue = (200, 30, 30), (30, 30, 200)
        fake = FakeCodex("{}")
        ask(codex_judge.CodexChatClient(runner=fake), [
            text_part("Compare the decks."),
            text_part("GROUND_TRUTH_SLIDE_1"), image_part(red),
            text_part("PREDICTION_SLIDE_1"), image_part(blue),
        ])
        run = fake.runs[0]
        self.assertEqual(run["images"], [png_bytes(red), png_bytes(blue)])
        self.assertIn("GROUND_TRUTH_SLIDE_1\n[attached image 1]", run["prompt"])
        self.assertIn("PREDICTION_SLIDE_1\n[attached image 2]", run["prompt"])

    def test_run_uses_the_requested_model_and_ignores_personal_codex_setup(self):
        fake = FakeCodex("{}")
        ask(codex_judge.CodexChatClient(runner=fake), "Score this deck.", model="gpt-6.2-sol")
        command = fake.runs[0]["command"]
        self.assertEqual(command[command.index("-m") + 1], "gpt-6.2-sol")
        self.assertIn("--ignore-user-config", command)
        self.assertIn("--ephemeral", command)
        self.assertEqual(command[command.index("-s") + 1], "read-only")
        disabled = {command[i + 1] for i, arg in enumerate(command) if arg == "--disable"}
        self.assertLessEqual({"hooks", "memories", "plugins"}, disabled)

    def test_a_failed_run_is_tried_again(self):
        fake = FakeCodex({"returncode": 1, "stderr": "stream disconnected before completion"}, '{"ok": 1}')
        response = ask(codex_judge.CodexChatClient(runner=fake, retry_wait=0), "Score this deck.")
        self.assertEqual(response.choices[0].message.content, '{"ok": 1}')
        self.assertEqual(len(fake.runs), 2)

    def test_a_run_that_keeps_failing_raises_with_the_codex_message(self):
        failure = {"returncode": 1, "stderr": "stream disconnected before completion"}
        fake = FakeCodex(failure, failure, failure)
        with self.assertRaisesRegex(RuntimeError, "stream disconnected"):
            ask(codex_judge.CodexChatClient(runner=fake, retry_wait=0, attempts=3), "Score this deck.")

    def test_hitting_the_plan_limit_stops_every_later_call(self):
        fake = FakeCodex({"returncode": 1, "stderr": "You've hit your usage limit. Try again at 4:00 PM."})
        client = codex_judge.CodexChatClient(runner=fake, retry_wait=0)
        with self.assertRaises(codex_judge.CodexUsageLimitError):
            ask(client, "Score this deck.")
        self.assertTrue(codex_judge.usage_limit_hit.is_set())
        with self.assertRaises(codex_judge.CodexUsageLimitError):
            ask(client, "Score the next deck.")
        self.assertEqual(len(fake.runs), 1)

    def test_a_separate_codex_home_is_passed_through_when_set(self):
        fake = FakeCodex("{}")
        with mock.patch.dict("os.environ", {"PPTARENA_CODEX_HOME": "/tmp/clean-codex-home"}):
            ask(codex_judge.CodexChatClient(runner=fake), "Score this deck.")
        self.assertEqual(fake.runs[0]["env"]["CODEX_HOME"], "/tmp/clean-codex-home")


class FakeChatClient:
    def __init__(self):
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        content = json.dumps({
            "instruction_following_score": 3, "instruction_following_reason": "r",
            "visual_quality_score": 4, "visual_quality_reason": "r",
        })
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


class VisualCallFails(FakeChatClient):
    def _create(self, **kwargs):
        if isinstance(kwargs["messages"][1]["content"], list):
            raise RuntimeError("connection reset")
        return super()._create(**kwargs)


class JudgeRoutingTest(unittest.TestCase):
    def judge_with(self, fake):
        from llm.judge import call_llm_judge

        slide = base64.b64encode(png_bytes((10, 120, 60))).decode("ascii")
        with mock.patch("llm.judge.create_codex_client", return_value=fake):
            return call_llm_judge(
                user_prompt="Instruction: Tidy the deck",
                judge_model="codex/gpt-6.2-sol",
                original_slide_xml="",
                modified_slide_xml="",
                original_slide_images_b64=[slide],
                modified_slide_images_b64=[slide],
                api_keys={},
                evaluation_mode="arena",
            )

    def test_a_failed_judge_call_is_reported_instead_of_scored_zero(self):
        result = self.judge_with(VisualCallFails())
        self.assertIn("connection reset", result["error"])
        self.assertNotIn("visual_quality_score", result)

    def test_a_codex_judge_needs_no_api_key_and_gets_the_bare_model_name(self):
        fake = FakeChatClient()
        result = self.judge_with(fake)
        self.assertNotIn("error", result)
        self.assertEqual(result["instruction_following_score"], 3)
        self.assertEqual(result["visual_quality_score"], 4)
        self.assertEqual({call["model"] for call in fake.calls}, {"gpt-6.2-sol"})
        self.assertEqual(len(fake.calls), 2)


class SampleCacheTest(unittest.TestCase):
    """Judged samples are kept on disk so an interrupted run picks up where it stopped."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cache_dir = Path(self.tmp.name) / "case_001"

    def judge(self, *replies):
        replies = list(replies)
        calls = []

        def judge_once():
            calls.append(1)
            reply = replies.pop(0)
            if isinstance(reply, Exception):
                raise reply
            return reply

        return judge_once, calls

    def test_samples_already_on_disk_are_not_judged_again(self):
        import judge_predictions as jp

        first, first_calls = self.judge({"visual_quality_score": 1}, {"visual_quality_score": 2},
                                        {"visual_quality_score": 3})
        valid, errors = jp.collect_samples(3, first, self.cache_dir, "deck-a")
        self.assertEqual([r["visual_quality_score"] for r in valid], [1, 2, 3])
        self.assertEqual(errors, [])

        second, second_calls = self.judge()
        valid, errors = jp.collect_samples(3, second, self.cache_dir, "deck-a")
        self.assertEqual([r["visual_quality_score"] for r in valid], [1, 2, 3])
        self.assertEqual((len(first_calls), len(second_calls)), (3, 0))

    def test_a_failed_sample_is_not_kept_and_is_judged_on_the_next_run(self):
        import judge_predictions as jp

        first, _ = self.judge({"visual_quality_score": 1}, {"error": "Visual judge error: boom"},
                              RuntimeError("codex exited 1"))
        valid, errors = jp.collect_samples(3, first, self.cache_dir, "deck-a")
        self.assertEqual(len(valid), 1)
        self.assertEqual(errors, ["Visual judge error: boom", "Judge call failed: codex exited 1"])

        second, second_calls = self.judge({"visual_quality_score": 2}, {"visual_quality_score": 3})
        valid, errors = jp.collect_samples(3, second, self.cache_dir, "deck-a")
        self.assertEqual([r["visual_quality_score"] for r in valid], [1, 2, 3])
        self.assertEqual((len(second_calls), errors), (2, []))

    def test_a_changed_deck_is_judged_afresh(self):
        import judge_predictions as jp

        first, _ = self.judge({"visual_quality_score": 1})
        jp.collect_samples(1, first, self.cache_dir, "deck-a")
        second, second_calls = self.judge({"visual_quality_score": 5})
        valid, _ = jp.collect_samples(1, second, self.cache_dir, "deck-b")
        self.assertEqual([r["visual_quality_score"] for r in valid], [5])
        self.assertEqual(len(second_calls), 1)

    def test_without_a_cache_folder_every_sample_is_judged(self):
        import judge_predictions as jp

        judge_once, calls = self.judge({"visual_quality_score": 1}, {"visual_quality_score": 2})
        valid, _ = jp.collect_samples(2, judge_once, None, "deck-a")
        self.assertEqual((len(valid), len(calls)), (2, 2))

    def test_a_deck_that_will_not_open_scores_zero_without_leaking_the_local_path(self):
        import judge_predictions as jp

        deck = Path(self.tmp.name) / "case_080.pptx"
        deck.write_bytes(b"")
        case = {"name": "Case 80: Empty output", "category": ["Content"]}
        row = jp.judge_case(case, deck, None, {}, "codex/gpt-6.2-sol", 1, "codex/gpt-6.2-sol",
                            prediction_label="agent_bench/predictions/some_agent/case_080.pptx")
        self.assertEqual((row["instruction_following_score"], row["visual_quality_score"]), ("0.00", "0.00"))
        self.assertIn("Artifact prep error", row["errors"])
        self.assertIn("agent_bench/predictions/some_agent/case_080.pptx", row["errors"])
        self.assertNotIn(self.tmp.name, row["errors"])

    def test_a_codex_judge_is_not_mistaken_for_an_api_key_judge(self):
        import judge_predictions as jp

        self.assertTrue(jp.is_codex_judge("codex/gpt-6.2-sol"))
        self.assertFalse(jp.is_openai_judge("codex/gpt-6.2-sol"))
        self.assertFalse(jp.is_codex_judge("gpt-5.5"))
        self.assertTrue(jp.is_openai_judge("gpt-5.5"))


if __name__ == "__main__":
    unittest.main()
