import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import leaderboard_view  # noqa: E402


def entry(name, score, scored=100, expected=100, judge="GPT-6.2 Sol judge", edit_time=None, chart_label=None):
    return {
        "name": name, "chart_label": chart_label or name, "provider": "Maker of " + name, "brand": "Brand",
        "icon": name[:1], "color": "#123456", "score_pct": score, "if_pct": score + 5, "vq_pct": score - 5,
        "scored_cases": scored, "expected_cases": expected, "judge": judge,
        "edit_time_s": edit_time, "edit_time_display": None if edit_time is None else f"{edit_time / 60:.1f} min",
    }


def board():
    """Shaped like app.get_leaderboard_data(): per base split an "All" view, then category views."""
    full = {"key": "full", "label": "All", "case_count": 100, "entries": [
        entry("Codex (GPT-5.5 xhigh)", 66.0, edit_time=288.4, chart_label="GPT-5.5"),
        entry("OpenCode (GLM-5.2)", 46.0, scored=89),
        entry("Baidu DuMate", 58.1, judge="Kimi K2.6 judge"),
    ]}
    content = {"key": "full:category_content", "label": "Content", "case_count": 42, "entries": [
        entry("OpenCode (GLM-5.2)", 40.0), entry("Codex (GPT-5.5 xhigh)", 70.0),
    ]}
    layout = {"key": "full:category_layout", "label": "Layout", "case_count": 30, "entries": [
        entry("Codex (GPT-5.5 xhigh)", 61.5), entry("OpenCode (GLM-5.2)", 50.0), entry("Baidu DuMate", 55.0),
    ]}
    subset = {"key": "subset", "label": "All", "case_count": 25, "entries": [entry("Codex (GPT-5.5 xhigh)", 58.8, expected=25, scored=25)]}
    return {"default_split": "full", "groups": [
        {"base": "subset", "label": "Hard Subset", "views": [subset]},
        {"base": "full", "label": "Full Set", "views": [full, content, layout]},
    ]}


class SplitSummaryTest(unittest.TestCase):
    def setUp(self):
        self.splits = {s["key"]: s for s in leaderboard_view.summarize(board())}
        self.full = self.splits["full"]
        self.rows = {r["name"]: r for r in self.full["entries"]}

    def test_one_summary_per_base_split_with_its_label_and_case_count(self):
        self.assertEqual([(s["key"], s["label"], s["case_count"]) for s in leaderboard_view.summarize(board())],
                         [("subset", "Hard Subset", 25), ("full", "Full Set", 100)])

    def test_categories_are_the_views_after_all_in_order(self):
        self.assertEqual(self.full["categories"],
                         [{"label": "Content", "case_count": 42}, {"label": "Layout", "case_count": 30}])

    def test_each_row_carries_its_category_scores_in_category_order(self):
        self.assertEqual(self.rows["Codex (GPT-5.5 xhigh)"]["category_scores"], [70.0, 61.5])

    def test_a_row_missing_from_a_category_view_has_no_score_there(self):
        self.assertEqual(self.rows["Baidu DuMate"]["category_scores"], [None, 55.0])

    def test_rows_keep_the_all_view_order_and_get_a_rank(self):
        self.assertEqual([(r["rank"], r["name"]) for r in self.full["entries"]],
                         [(1, "Codex (GPT-5.5 xhigh)"), (2, "OpenCode (GLM-5.2)"), (3, "Baidu DuMate")])

    def test_ids_are_slugs_shared_by_the_same_system_across_splits(self):
        self.assertEqual(self.rows["Codex (GPT-5.5 xhigh)"]["id"], "codex-gpt-5-5-xhigh")
        self.assertEqual(self.splits["subset"]["entries"][0]["id"], "codex-gpt-5-5-xhigh")

    def test_a_row_with_unscored_cases_says_how_many_were_scored(self):
        self.assertEqual(self.rows["OpenCode (GLM-5.2)"]["note"], "89 of 100 cases scored")

    def test_a_row_judged_by_another_judge_than_most_rows_names_it(self):
        self.assertEqual(self.rows["Baidu DuMate"]["note"], "Kimi K2.6 judge")

    def test_a_fully_scored_row_with_the_common_judge_has_no_note(self):
        self.assertEqual(self.rows["Codex (GPT-5.5 xhigh)"]["note"], "")

    def test_rows_carry_the_short_label_used_inside_charts(self):
        self.assertEqual(self.rows["Codex (GPT-5.5 xhigh)"]["short"], "GPT-5.5")
        self.assertEqual(self.rows["Baidu DuMate"]["short"], "Baidu DuMate")

    def test_rows_keep_the_fields_the_brand_icon_is_drawn_from(self):
        row = self.rows["Baidu DuMate"]
        self.assertEqual((row["brand"], row["provider"], row["icon"], row["color"]),
                         ("Brand", "Maker of Baidu DuMate", "B", "#123456"))

    def test_edit_time_is_given_in_minutes_or_none(self):
        self.assertAlmostEqual(self.rows["Codex (GPT-5.5 xhigh)"]["edit_minutes"], 4.81, places=2)
        self.assertIsNone(self.rows["Baidu DuMate"]["edit_minutes"])


class CaseStripTest(unittest.TestCase):
    """One row per system, one column per case, cases ordered from easiest to hardest."""

    def setUp(self):
        entries = [{"id": "a", "name": "A"}, {"id": "b", "name": "B"}]
        scores = {"A": {"Case 1: Easy": 90.0, "Case 2: Hard": 20.0, "Case 3: Mid": 60.0},
                  "B": {"Case 1: Easy": 70.0, "Case 3: Mid": 50.0, "Case 9: Other split": 100.0}}
        self.strip = leaderboard_view.case_strip(entries, scores, ["Case 2: Hard", "Case 3: Mid", "Case 1: Easy"])

    def test_cases_run_from_highest_to_lowest_mean_score(self):
        self.assertEqual(self.strip["cases"], ["Case 1: Easy", "Case 3: Mid", "Case 2: Hard"])

    def test_rows_follow_the_ranking_and_line_up_with_the_cases(self):
        self.assertEqual([(r["id"], r["scores"]) for r in self.strip["rows"]],
                         [("a", [90.0, 60.0, 20.0]), ("b", [70.0, 50.0, None])])

    def test_a_case_a_system_never_scored_counts_as_zero_when_ordering(self):
        # Case 2 averages (20 + 0) / 2 = 10, so it sorts last even though A scored 20 on it.
        self.assertEqual(self.strip["cases"][-1], "Case 2: Hard")

    def test_cases_outside_the_split_are_left_out(self):
        self.assertNotIn("Case 9: Other split", self.strip["cases"])


if __name__ == "__main__":
    unittest.main()
