import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tradingagents.operations import AnalysisAdmissionPolicy, HeartbeatStore, SourceHealthLog


class OperationalControlsTests(unittest.TestCase):
    def test_admission_blocks_inflight_and_cooldown_but_allows_material_move(self):
        with tempfile.TemporaryDirectory() as tmp:
            policy = AnalysisAdmissionPolicy(Path(tmp) / "admission.sqlite3", material_price_move_pct=3)
            now = datetime(2026, 1, 1, tzinfo=timezone.utc)
            self.assertTrue(policy.try_admit("aapl", price=100, now=now).allowed)
            self.assertIn("in flight", policy.try_admit("AAPL", price=100, now=now).reason)
            policy.complete("AAPL")
            self.assertIn("cooldown", policy.try_admit("AAPL", price=101, now=now).reason)
            self.assertTrue(policy.try_admit("AAPL", price=104, now=now).allowed)

    def test_token_budget_resets_on_utc_day(self):
        with tempfile.TemporaryDirectory() as tmp:
            policy = AnalysisAdmissionPolicy(Path(tmp) / "admission.sqlite3", cooldown_seconds=0, daily_token_budget=100)
            now = datetime(2026, 1, 1, 23, 59, tzinfo=timezone.utc)
            self.assertTrue(policy.try_admit("AAPL", estimated_tokens=80, now=now).allowed)
            policy.complete("AAPL")
            self.assertFalse(policy.try_admit("AAPL", estimated_tokens=30, now=now).allowed)
            self.assertTrue(policy.try_admit("AAPL", estimated_tokens=30, now=now + timedelta(minutes=2)).allowed)

    def test_heartbeat_and_structured_source_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            heartbeat = HeartbeatStore(Path(tmp) / "heartbeat.json")
            payload = heartbeat.beat("scheduler", details={"cycle": 3})
            self.assertEqual(heartbeat.read()["pid"], payload["pid"])
            self.assertFalse(heartbeat.stale(60))
            path = SourceHealthLog(Path(tmp) / "source-health.jsonl").record_failure(
                provider="finnhub", operation="news", symbol="AAPL",
                error="rate limited", status_code=429, retryable=True,
            )
            event = json.loads(Path(path).read_text().strip())
            self.assertEqual(event["status_code"], 429)
            self.assertTrue(event["retryable"])


if __name__ == "__main__":
    unittest.main()
