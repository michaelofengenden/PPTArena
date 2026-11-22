import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent_bench"))

import run_until_done  # noqa: E402


def stream_log(*events):
    """A Claude Code `--output-format stream-json` session log: the echoed command, then one JSON event per line."""
    return "claude -p ... --output-format stream-json --verbose\n" + "".join(json.dumps(e) + "\n" for e in events)


def agy_log(*events, after=""):
    """An Antigravity CLI `--output-format stream-json` session log: the echoed command, one JSON
    event per line, then whatever the CLI printed after its last event."""
    return ("$ agy -p ... --output-format stream-json\n\n"
            + "".join(json.dumps(e) + "\n" for e in events) + after)


class PlanFailureTest(unittest.TestCase):
    def test_plain_log_ending_in_a_usage_limit_is_a_plan_failure(self):
        log = "working on the deck\n" * 400 + "ERROR: You've hit your usage limit. Try again at 9pm.\n"
        self.assertEqual(run_until_done.plan_failure(log), "usage limit")

    def test_plain_log_that_only_mentions_a_limit_early_on_is_not(self):
        log = "note: the API has a rate limit\n" + "working on the deck\n" * 400
        self.assertEqual(run_until_done.plan_failure(log), "")

    def test_stream_log_killed_mid_session_ignores_numbers_in_tool_output(self):
        deck_xml = '<p:cNvPr id="401" name="Quota chart 429"/>'
        log = stream_log({"type": "user", "message": {"content": [{"type": "tool_result", "content": deck_xml}]}})
        self.assertEqual(run_until_done.plan_failure(log), "")

    def test_stream_log_closing_with_a_limit_error_is_a_plan_failure(self):
        log = stream_log(
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "Reading the deck."}]}},
            {"type": "result", "is_error": True, "result": "Claude usage limit reached. Your limit will reset at 7pm."},
        )
        self.assertEqual(run_until_done.plan_failure(log), "usage limit")

    def test_stream_log_closing_with_a_login_error_is_a_plan_failure(self):
        log = stream_log({"type": "result", "is_error": True,
                          "result": "Failed to authenticate: OAuth token revoked. Please log in again."})
        self.assertEqual(run_until_done.plan_failure(log), "Failed to authenticate")

    def test_stream_log_closing_with_a_normal_result_is_not(self):
        log = stream_log({"type": "result", "is_error": False,
                          "result": "Done. The table now shows the 429 units against the quota."})
        self.assertEqual(run_until_done.plan_failure(log), "")

    def test_agy_log_closing_with_success_is_not(self):
        log = agy_log(
            {"event": "step_update", "step_update": {"step_type": "tool", "tool_name": "run_command"}},
            {"event": "result", "result": {"status": "SUCCESS", "response": "Done. Updated the quota table.\n"}},
        )
        self.assertEqual(run_until_done.plan_failure(log), "")

    def test_agy_log_closing_with_a_quota_error_is_a_plan_failure(self):
        log = agy_log({"event": "result", "result": {
            "status": "ERROR", "error": "RESOURCE_EXHAUSTED: You have exhausted your capacity on this model."}})
        self.assertEqual(run_until_done.plan_failure(log), "RESOURCE_EXHAUSTED")

    def test_stream_log_cut_short_by_a_login_error_is_a_plan_failure(self):
        log = agy_log(
            {"event": "step_update", "step_update": {"step_type": "agent_response", "state": "ACTIVE"}},
            after="Error: authentication required. Run agy to sign in.\n",
        )
        self.assertEqual(run_until_done.plan_failure(log), "authentication required")

    def test_plain_log_ending_in_authentication_required_is_a_plan_failure(self):
        log = "$ agy -p ...\n\nError: authentication required\n"
        self.assertEqual(run_until_done.plan_failure(log), "authentication required")


if __name__ == "__main__":
    unittest.main()
