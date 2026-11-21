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


if __name__ == "__main__":
    unittest.main()
