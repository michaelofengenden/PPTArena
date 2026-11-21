"""The two pages of the site, rendered through Flask's test client (no server, no LibreOffice)."""
import contextlib
import html
import io
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


class PagesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cwd = os.getcwd()
        os.chdir(ROOT / "src")
        with contextlib.redirect_stdout(io.StringIO()):
            import app
        import leaderboard_view
        app.convert_pptx_to_pdf = lambda *args, **kwargs: None   # the live page must not start LibreOffice here
        cls.app, cls.view = app, leaderboard_view
        cls.client = app.app.test_client()

    @classmethod
    def tearDownClass(cls):
        os.chdir(cls.cwd)

    def get(self, path):
        with contextlib.redirect_stdout(io.StringIO()):
            return self.client.get(path)

    def test_root_goes_to_the_leaderboard(self):
        response = self.get("/")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/evaluation"))

    def test_leaderboard_shows_every_ranked_system_in_both_splits(self):
        page = html.unescape(self.get("/evaluation").get_data(as_text=True))
        splits = self.view.summarize(self.app.get_leaderboard_data())
        self.assertEqual([s["key"] for s in splits], ["subset", "full"])
        for split in splits:
            self.assertIn(f'data-split-panel="{split["key"]}"', page)
            self.assertGreater(len(split["entries"]), 0)
            for entry in split["entries"]:
                self.assertIn(entry["name"], page)

    def test_leaderboard_carries_per_case_scores_for_each_split(self):
        page = self.get("/evaluation").get_data(as_text=True)
        self.assertEqual(page.count('<script type="application/json">'), 2)
        self.assertIn("Case 1:", page)

    def test_leaderboard_no_longer_contains_the_live_evaluation_tool(self):
        page = self.get("/evaluation").get_data(as_text=True)
        self.assertNotIn('id="evalForm"', page)
        self.assertNotIn('id="api_key_openai"', page)

    def test_an_old_link_that_selects_a_case_lands_on_the_live_evaluation_page(self):
        response = self.get("/evaluation?pair=Case%206%3A%20Update%20Quarter%20Two%20Data%20(A)")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/workspace?pair=Case", response.headers["Location"])

    def test_live_evaluation_page_holds_the_tool_and_its_api_key_fields(self):
        response = self.get("/workspace")
        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        for needle in ('id="evalForm"', 'id="evaluationPairSelector"', 'id="judgeButton"',
                       'id="api_key_openai"', 'id="api_key_gemini"'):
            self.assertIn(needle, page)

    def test_live_evaluation_page_opens_on_the_requested_case(self):
        pair = self.app.get_evaluation_pairs()[5]["name"]
        page = html.unescape(self.get("/workspace?pair=" + pair.replace(" ", "%20")).get_data(as_text=True))
        self.assertIn(f'<b id="activePairLabel">{pair}</b>', page)


if __name__ == "__main__":
    unittest.main()
