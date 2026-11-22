import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent_bench"))

import run_agents  # noqa: E402

LOGIN = ".gemini/antigravity-cli/antigravity-oauth-token"


class FreshHomeTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.real = Path(tmp.name) / "real"
        self.home = Path(tmp.name) / "homes" / "agent" / "case_001"
        (self.real / ".gemini/antigravity-cli/brain").mkdir(parents=True)
        (self.real / LOGIN).write_text("login")
        (self.real / ".gemini/antigravity-cli/brain/notes.md").write_text("what earlier cases taught me")

    def files(self):
        return sorted(str(p.relative_to(self.home)) for p in self.home.rglob("*") if p.is_file())

    def test_holds_only_the_listed_files(self):
        run_agents.fresh_home([LOGIN], self.home, self.real)
        self.assertEqual(self.files(), [LOGIN])
        self.assertEqual((self.home / LOGIN).read_text(), "login")

    def test_replaces_what_an_earlier_attempt_left_behind(self):
        (self.home / ".gemini/antigravity-cli/brain").mkdir(parents=True)
        (self.home / ".gemini/antigravity-cli/brain/notes.md").write_text("left by the first attempt")
        run_agents.fresh_home([LOGIN], self.home, self.real)
        self.assertEqual(self.files(), [LOGIN])

    def test_a_listed_file_the_real_home_lacks_is_skipped(self):
        run_agents.fresh_home([LOGIN, ".gemini/antigravity-cli/settings.json"], self.home, self.real)
        self.assertEqual(self.files(), [LOGIN])

    def test_remove_seed_takes_the_login_out_and_keeps_the_session_records(self):
        run_agents.fresh_home([LOGIN], self.home, self.real)
        record = self.home / ".gemini/antigravity-cli/conversations/one.json"
        record.parent.mkdir(parents=True)
        record.write_text("{}")
        run_agents.remove_seed([LOGIN], self.home)
        self.assertEqual(self.files(), [".gemini/antigravity-cli/conversations/one.json"])


if __name__ == "__main__":
    unittest.main()
