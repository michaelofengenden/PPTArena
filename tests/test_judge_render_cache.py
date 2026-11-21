import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "agent_bench"))

import judge_predictions as jp  # noqa: E402


class RenderCacheTest(unittest.TestCase):
    """A cached render must hand back the same slides, in the same order, as a fresh one."""

    def cached(self, *names):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        cache_dir = Path(tmp.name) / "gt"
        cache_dir.mkdir()
        for name in names:
            (cache_dir / name).write_bytes(b"png")
        return [Path(p).name for p in jp.render_cached(Path("never-rendered.pptx"), cache_dir)]

    def test_leftover_first_slide_image_is_not_counted_as_a_slide(self):
        # LibreOffice's direct PNG export writes only slide 1, named after the deck.
        # The per-slide images come from the PDF fallback and sit next to it.
        slides = [f"slide-{n:02d}.png" for n in range(1, 13)]
        self.assertEqual(self.cached("Quarterly_Review.png", "Quarterly_Review.pdf", *slides), slides)

    def test_slides_come_back_in_slide_order(self):
        self.assertEqual(self.cached("slide-10.png", "slide-2.png", "slide-1.png"),
                         ["slide-1.png", "slide-2.png", "slide-10.png"])

    def test_a_deck_rendered_without_the_pdf_fallback_keeps_its_only_image(self):
        self.assertEqual(self.cached("Quarterly_Review.png"), ["Quarterly_Review.png"])


class RenderConcurrencyTest(unittest.TestCase):
    """LibreOffice runs one at a time unless PPT_RENDER_CONCURRENCY asks for more."""

    def slots(self, env):
        import importlib
        from unittest import mock

        from ppt import export
        with mock.patch.dict("os.environ", env, clear=False):
            importlib.reload(export)
        self.addCleanup(importlib.reload, export)
        taken = 0
        while export._libreoffice_lock.acquire(blocking=False):
            taken += 1
        return taken

    def test_one_render_at_a_time_by_default(self):
        import os
        from unittest import mock
        with mock.patch.dict("os.environ"):
            os.environ.pop("PPT_RENDER_CONCURRENCY", None)
            self.assertEqual(self.slots({}), 1)

    def test_more_renders_at_once_when_asked(self):
        self.assertEqual(self.slots({"PPT_RENDER_CONCURRENCY": "3"}), 3)


class PreRenderedSlidesTest(unittest.TestCase):
    """Slides rendered somewhere else can be dropped into the cache and used as they are."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def folder(self, *names):
        folder = self.root / "case_001" / "pred_some_agent"
        folder.mkdir(parents=True)
        for name in names:
            (folder / name).write_bytes(b"png:" + name.encode())
        return folder

    def judge(self, render_dir, local_render):
        from unittest import mock

        deck = self.root / "case_001.pptx"
        deck.write_bytes(b"deck")
        seen = {}

        def fake_judge(case, artifacts, pred_json, pred_b64, pred_xml, api_keys, judge_model):
            seen["images"] = pred_b64
            return {"instruction_following_score": 4, "visual_quality_score": 3}

        artifacts = mock.Mock()
        artifacts.get.return_value = {"init_json": {}, "gt_json": {}, "gt_b64": [], "gt_xml": ""}
        with mock.patch.object(jp, "pptx_to_json", return_value={}), \
                mock.patch.object(jp, "extract_specific_xml_from_pptx", return_value=""), \
                mock.patch.object(jp, "call_judge_once", side_effect=fake_judge), \
                mock.patch.object(jp, "export_slides_to_images",
                                  side_effect=AssertionError("LibreOffice must not run here")):
            row = jp.judge_case({"name": "Case 1: Demo"}, deck, artifacts, {}, "codex/gpt-6.2-sol", 1,
                                "codex/gpt-6.2-sol", render_dir=render_dir, local_render=local_render)
        return row, seen

    def test_a_folder_never_rendered_is_told_apart_from_a_deck_with_no_slides(self):
        self.assertIsNone(jp.cached_slides(self.root / "missing"))
        self.assertEqual(jp.cached_slides(self.folder(".rendered")), [])

    def test_pre_rendered_slides_are_used_without_starting_libreoffice(self):
        import base64

        row, seen = self.judge(self.folder("slide-2.png", "slide-1.png", ".rendered"), local_render=False)
        self.assertEqual(row["errors"], "")
        self.assertEqual([base64.b64decode(b) for b in seen["images"]], [b"png:slide-1.png", b"png:slide-2.png"])

    def test_without_local_rendering_a_deck_with_no_renders_fails_instead_of_scoring(self):
        row, seen = self.judge(self.root / "case_001" / "pred_other_agent", local_render=False)
        self.assertIn("No pre-rendered slides", row["errors"])
        self.assertEqual(seen, {})


if __name__ == "__main__":
    unittest.main()
