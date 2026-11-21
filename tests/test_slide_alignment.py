import base64
import io
import json
import re
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ppt import diff_pptx_json, format_diff_for_judge  # noqa: E402


def make_slide(title, body):
    return {
        "slide_number": 0,
        "slide_id": 256,
        "slide_layout": "Title and Content",
        "notes": "",
        "shapes": [
            {
                "shape_id": 2, "name": "Title 1", "type": "PLACEHOLDER (14)", "text": title,
                "left": 50.0, "top": 30.0, "width": 600.0, "height": 80.0, "z_order": 0,
            },
            {
                "shape_id": 3, "name": "Content 2", "type": "PLACEHOLDER (14)", "text": body,
                "left": 50.0, "top": 130.0, "width": 600.0, "height": 300.0, "z_order": 1,
            },
        ],
    }


def make_deck(*slides):
    """Number the slides by position, the way pptx_to_json does."""
    numbered = []
    for position, slide in enumerate(slides, start=1):
        copy = {**slide, "shapes": [dict(shape) for shape in slide["shapes"]]}
        copy["slide_number"] = position
        copy["slide_id"] = 255 + position
        numbered.append(copy)
    return {"slides": numbered}


INTRO = make_slide("Quarterly business review", "Agenda: revenue, hiring, product roadmap")
REVENUE = make_slide("Revenue", "Revenue grew 12% year over year, led by the enterprise segment")
HIRING = make_slide("Hiring", "Forty engineers joined across Berlin, Toronto and Singapore")
ROADMAP = make_slide("Roadmap", "Offline mode ships in March; the mobile redesign follows in June")
EXTRA = make_slide("Appendix: methodology", "Survey of 300 customers conducted by an outside firm")


class AlignSlidesTest(unittest.TestCase):
    def align(self, gt_deck, pred_deck):
        from ppt.slide_alignment import align_slides
        return align_slides(gt_deck["slides"], pred_deck["slides"])

    def test_identical_decks_pair_slide_for_slide(self):
        deck = make_deck(INTRO, REVENUE, HIRING)
        self.assertEqual(self.align(deck, deck), [(0, 0), (1, 1), (2, 2)])

    def test_slide_inserted_mid_deck_has_no_ground_truth_partner(self):
        gt = make_deck(INTRO, REVENUE, HIRING, ROADMAP)
        pred = make_deck(INTRO, EXTRA, REVENUE, HIRING, ROADMAP)
        self.assertEqual(self.align(gt, pred), [(0, 0), (None, 1), (1, 2), (2, 3), (3, 4)])

    def test_slide_dropped_from_prediction_has_no_prediction_partner(self):
        gt = make_deck(INTRO, REVENUE, HIRING, ROADMAP)
        pred = make_deck(INTRO, HIRING, ROADMAP)
        self.assertEqual(self.align(gt, pred), [(0, 0), (1, None), (2, 1), (3, 2)])

    def test_rewritten_slide_stays_paired_when_slide_counts_match(self):
        gt = make_deck(INTRO, REVENUE, HIRING)
        pred = make_deck(INTRO, EXTRA, HIRING)
        self.assertEqual(self.align(gt, pred), [(0, 0), (1, 1), (2, 2)])

    def test_empty_prediction_leaves_every_ground_truth_slide_unpaired(self):
        gt = make_deck(INTRO, REVENUE)
        self.assertEqual(self.align(gt, {"slides": []}), [(0, None), (1, None)])


class DiffWithShiftedSlidesTest(unittest.TestCase):
    def test_inserted_slide_is_reported_once_and_later_slides_are_not_flagged(self):
        gt = make_deck(INTRO, REVENUE, HIRING, ROADMAP)
        pred = make_deck(INTRO, EXTRA, REVENUE, HIRING, ROADMAP)

        result = diff_pptx_json(gt, pred)

        self.assertEqual(result["total_differences"], 1)
        entry = result["differences"][0]
        self.assertEqual(entry["slide_change"], "inserted")
        self.assertEqual(entry["prediction_slide_number"], 2)
        self.assertIsNone(entry["ground_truth_slide_number"])
        self.assertIn("Appendix: methodology", entry["prediction_value"])

    def test_appended_slide_is_reported_as_inserted(self):
        gt = make_deck(INTRO, REVENUE)
        pred = make_deck(INTRO, REVENUE, EXTRA)

        result = diff_pptx_json(gt, pred)

        self.assertEqual(result["total_differences"], 1)
        self.assertEqual(result["differences"][0]["slide_change"], "inserted")
        self.assertEqual(result["differences"][0]["prediction_slide_number"], 3)

    def test_dropped_slide_is_reported_once_as_missing(self):
        gt = make_deck(INTRO, REVENUE, HIRING, ROADMAP)
        pred = make_deck(INTRO, HIRING, ROADMAP)

        result = diff_pptx_json(gt, pred)

        self.assertEqual(result["total_differences"], 1)
        entry = result["differences"][0]
        self.assertEqual(entry["slide_change"], "missing")
        self.assertEqual(entry["ground_truth_slide_number"], 2)
        self.assertIsNone(entry["prediction_slide_number"])
        self.assertIn("Revenue grew 12%", entry["ground_truth_value"])

    def test_edit_after_an_inserted_slide_is_reported_against_its_matching_slide(self):
        edited_hiring = make_slide("Hiring", "Fifty engineers joined across Berlin, Toronto and Singapore")
        gt = make_deck(INTRO, REVENUE, HIRING, ROADMAP)
        pred = make_deck(INTRO, EXTRA, REVENUE, edited_hiring, ROADMAP)

        result = diff_pptx_json(gt, pred)

        self.assertEqual(result["total_differences"], 2)
        inserted, edit = result["differences"]
        self.assertEqual(inserted["slide_change"], "inserted")
        self.assertEqual(edit["property_path"], "slide_3.shapes[1].text")
        self.assertEqual(edit["change_type"], "modified")
        self.assertEqual(edit["ground_truth_slide_number"], 3)
        self.assertEqual(edit["prediction_slide_number"], 4)
        self.assertEqual(edit["shape_name"], "Content 2")

    def test_same_slide_count_keeps_the_positional_diff_unchanged(self):
        edited_revenue = make_slide("Revenue", "Revenue grew 21% year over year, led by the enterprise segment")
        gt = make_deck(INTRO, REVENUE, HIRING)
        pred = make_deck(INTRO, edited_revenue, HIRING)

        result = diff_pptx_json(gt, pred)

        self.assertEqual(result["differences"], [{
            "property_path": "slide_2.shapes[1].text",
            "change_type": "modified",
            "ground_truth_value": "Revenue grew 12% year over year, led by the enterprise segment",
            "prediction_value": "Revenue grew 21% year over year, led by the enterprise segment",
            "initial_value": None,
            "slide_number": 2,
            "shape_name": "Content 2",
            "shape_type": "PLACEHOLDER (14)",
        }])
        self.assertNotIn("slide_alignment", result)


class FormatDiffWithShiftedSlidesTest(unittest.TestCase):
    def test_judge_text_names_the_extra_slide_and_the_matching_slide_numbers(self):
        edited_hiring = make_slide("Hiring", "Fifty engineers joined across Berlin, Toronto and Singapore")
        gt = make_deck(INTRO, REVENUE, HIRING, ROADMAP)
        pred = make_deck(INTRO, EXTRA, REVENUE, edited_hiring, ROADMAP)

        text = format_diff_for_judge(diff_pptx_json(gt, pred))

        self.assertIn("Prediction slide 2 has no counterpart in the Ground Truth", text)
        self.assertIn("Slide 3 (Prediction slide 4)", text)
        self.assertNotIn("Slide 5", text)

    def test_judge_text_names_a_missing_slide(self):
        gt = make_deck(INTRO, REVENUE, HIRING, ROADMAP)
        pred = make_deck(INTRO, HIRING, ROADMAP)

        text = format_diff_for_judge(diff_pptx_json(gt, pred))

        self.assertIn("Ground Truth slide 2 has no counterpart in the Prediction", text)


class VisualSlidePairsTest(unittest.TestCase):
    def pairs(self, gt, pred, gt_images, pred_images):
        from ppt.slide_alignment import visual_slide_pairs
        return visual_slide_pairs(gt, pred, gt_images, pred_images)

    def test_inserted_slide_gives_content_matched_pairs(self):
        gt = make_deck(INTRO, REVENUE, HIRING)
        pred = make_deck(INTRO, EXTRA, REVENUE, HIRING)
        self.assertEqual(self.pairs(gt, pred, 3, 4), [(0, 0), (None, 1), (1, 2), (2, 3)])

    def test_decks_that_already_line_up_keep_positional_pairing(self):
        deck = make_deck(INTRO, REVENUE, HIRING)
        self.assertIsNone(self.pairs(deck, deck, 3, 3))

    def test_image_counts_that_disagree_with_the_deck_keep_positional_pairing(self):
        gt = make_deck(INTRO, REVENUE, HIRING)
        pred = make_deck(INTRO, EXTRA, REVENUE, HIRING)
        self.assertIsNone(self.pairs(gt, pred, 3, 3))

    def test_missing_deck_summary_keeps_positional_pairing(self):
        gt = make_deck(INTRO, REVENUE, HIRING)
        self.assertIsNone(self.pairs(gt, None, 3, 4))


def slide_image_b64(seed):
    """A distinct, deterministic picture standing in for one rendered slide."""
    image = Image.new("RGB", (320, 180), "white")
    draw = ImageDraw.Draw(image)
    for k in range(4):
        x = (seed * 53 + k * 71) % 240
        y = (seed * 29 + k * 37) % 120
        draw.rectangle([x, y, x + 60, y + 40], fill=((seed * 40) % 256, (k * 60) % 256, 90))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


class FakeChatClient:
    """Stands in for the judge's HTTP client and records what it was sent."""

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


class VisualJudgeRequestTest(unittest.TestCase):
    """What the visual judge is shown when the decks do or do not line up."""

    SLIDES = [
        make_slide(f"Topic {name}", f"{name} details that only this slide carries")
        for name in ("alpha", "bravo", "charlie", "delta", "echo", "foxtrot")
    ]
    IMAGES = {slide["shapes"][0]["text"]: slide_image_b64(seed) for seed, slide in enumerate(SLIDES, start=1)}
    IMAGES[EXTRA["shapes"][0]["text"]] = slide_image_b64(40)

    def visual_requests(self, gt_slides, pred_slides, pred_image_overrides=None):
        from llm.judge import call_llm_judge

        def images(slides):
            return [self.IMAGES[slide["shapes"][0]["text"]] for slide in slides]

        pred_images = images(pred_slides)
        for index, image in (pred_image_overrides or {}).items():
            pred_images[index] = image
        fake = FakeChatClient()
        with mock.patch("llm.judge._create_kimi_client", return_value=fake):
            call_llm_judge(
                user_prompt="Instruction: Tidy the deck",
                judge_model="kimi-k2.6",
                original_slide_xml="",
                modified_slide_xml="",
                original_slide_images_b64=images(gt_slides),
                modified_slide_images_b64=pred_images,
                original_ppt_json=make_deck(*gt_slides),
                modified_ppt_json=make_deck(*pred_slides),
                api_keys={"moonshot": "test-key"},
                evaluation_mode="arena",
            )
        return [
            call["messages"][1]["content"] for call in fake.calls
            if isinstance(call["messages"][1]["content"], list)
        ]

    @staticmethod
    def slide_labels(content):
        return [
            item["text"] for item in content
            if item["type"] == "text" and re.fullmatch(r"(GROUND_TRUTH|PREDICTION)_SLIDE_\d+", item["text"])
        ]

    def test_inserted_slide_is_the_only_slide_shown_when_the_rest_match(self):
        s = self.SLIDES
        requests = self.visual_requests(s, [s[0], EXTRA, s[1], s[2], s[3], s[4], s[5]])

        self.assertEqual(len(requests), 1)
        self.assertEqual(self.slide_labels(requests[0]), ["PREDICTION_SLIDE_2"])
        self.assertIn("PREDICTION_SLIDE_2 has no counterpart in the Ground Truth", requests[0][0]["text"])

    def test_changed_slide_after_an_insertion_is_shown_with_its_matching_slide(self):
        s = self.SLIDES
        requests = self.visual_requests(
            s, [s[0], EXTRA, s[1], s[2], s[3], s[4], s[5]],
            pred_image_overrides={3: slide_image_b64(77)},
        )

        self.assertEqual(len(requests), 1)
        self.assertEqual(
            self.slide_labels(requests[0]),
            ["GROUND_TRUTH_SLIDE_3", "PREDICTION_SLIDE_2", "PREDICTION_SLIDE_4"],
        )
        self.assertIn("GROUND_TRUTH_SLIDE_3 corresponds to PREDICTION_SLIDE_4", requests[0][0]["text"])

    def test_small_deck_prompt_names_the_extra_slide(self):
        s = self.SLIDES
        requests = self.visual_requests(s[:3], [s[0], EXTRA, s[1], s[2]])

        self.assertEqual(len(requests), 1)
        self.assertEqual(len(self.slide_labels(requests[0])), 7)
        self.assertIn("PREDICTION_SLIDE_2 has no counterpart in the Ground Truth", requests[0][0]["text"])
        self.assertIn("GROUND_TRUTH_SLIDE_2 corresponds to PREDICTION_SLIDE_3", requests[0][0]["text"])

    def test_decks_that_line_up_are_still_compared_by_position(self):
        s = self.SLIDES
        requests = self.visual_requests(s, s, pred_image_overrides={2: slide_image_b64(77)})

        self.assertEqual(len(requests), 1)
        self.assertEqual(self.slide_labels(requests[0]), ["GROUND_TRUTH_SLIDE_3", "PREDICTION_SLIDE_3"])
        self.assertNotIn("counterpart", requests[0][0]["text"])
        self.assertIn("image sequences for slides: 3\n", requests[0][0]["text"])


if __name__ == "__main__":
    unittest.main()
