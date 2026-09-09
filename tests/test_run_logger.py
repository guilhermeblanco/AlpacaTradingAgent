"""Tests for the per-run audit trail.

Every analysis writes one JSON file incrementally, so a run that dies
mid-way is still debuggable. Two things matter beyond the counters: the
configuration is persisted with credentials removed, and a run left in
"running" by a killed process is recovered rather than counted as live
forever.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tradingagents.run_logger import (
    RunAuditLogger,
    _redact_sensitive_config,
    _sanitize_for_path,
    get_run_audit_logger,
    load_final_state_snapshot,
)


class SanitizationTests(unittest.TestCase):
    def test_a_pair_separator_is_replaced(self):
        self.assertNotIn("/", _sanitize_for_path("BTC/USD"))

    def test_an_ordinary_symbol_is_unchanged(self):
        self.assertEqual(_sanitize_for_path("NVDA"), "NVDA")


class RedactionTests(unittest.TestCase):
    """A run log is shared when debugging; it must not carry credentials."""

    def test_the_named_secrets_are_removed(self):
        redacted = _redact_sensitive_config(
            {
                "api_key": "sk-live",
                "secret": "s",
                "password": "p",
                "token": "t",
                "webhook_url": "https://hooks",
                "alert_webhook_url": "https://hooks",
            }
        )

        self.assertEqual(set(redacted.values()), {"[REDACTED]"})

    def test_suffixed_secrets_are_removed(self):
        redacted = _redact_sensitive_config(
            {
                "openai_api_key": "sk",
                "alpaca_secret_key": "s",
                "azure_client_secret": "c",
                "db_password": "p",
                "telegram_bot_token": "t",
                "telegram_chat_id": "c",
                "tradier_api_secret": "s",
            }
        )

        self.assertEqual(set(redacted.values()), {"[REDACTED]"})

    def test_the_key_name_is_matched_case_insensitively(self):
        self.assertEqual(
            _redact_sensitive_config({"OPENAI_API_KEY": "sk"})["OPENAI_API_KEY"],
            "[REDACTED]",
        )

    def test_an_unset_secret_is_not_marked_redacted(self):
        """Otherwise a missing key looks configured."""
        redacted = _redact_sensitive_config({"openai_api_key": "", "token": None})

        self.assertEqual(redacted["openai_api_key"], "")
        self.assertIsNone(redacted["token"])

    def test_ordinary_settings_survive(self):
        redacted = _redact_sensitive_config(
            {"research_depth": "Deep", "max_debate_rounds": 2}
        )

        self.assertEqual(redacted["research_depth"], "Deep")

    def test_nested_and_listed_settings_are_redacted_too(self):
        redacted = _redact_sensitive_config(
            {"brokers": [{"name": "alpaca", "api_key": "sk"}]}
        )

        self.assertEqual(redacted["brokers"][0]["api_key"], "[REDACTED]")
        self.assertEqual(redacted["brokers"][0]["name"], "alpaca")


class RunLoggerFixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        previous = os.getcwd()
        os.chdir(self.root)
        self.addCleanup(lambda: os.chdir(previous))
        self.logger = RunAuditLogger()

    def _payload(self, run_id, symbol="NVDA"):
        path = (
            self.root
            / "eval_results"
            / symbol
            / "TradingAgentsStrategy_logs"
            / "runs"
            / f"{run_id}.json"
        )
        return json.loads(path.read_text(encoding="utf-8"))


class RunLifecycleTests(RunLoggerFixture):
    def test_a_run_is_written_to_disk_as_soon_as_it_starts(self):
        run_id = self.logger.start_run("NVDA", "2026-09-09")

        payload = self._payload(run_id)
        self.assertEqual(payload["status"], "running")
        self.assertEqual(payload["symbol"], "NVDA")
        self.assertIsNone(payload["ended_at"])

    def test_the_configuration_is_persisted_without_credentials(self):
        run_id = self.logger.start_run(
            "NVDA", "2026-09-09", config={"openai_api_key": "sk", "research_depth": "Deep"}
        )

        payload = self._payload(run_id)
        self.assertEqual(payload["config"]["openai_api_key"], "[REDACTED]")
        self.assertEqual(payload["config"]["research_depth"], "Deep")

    def test_a_pair_symbol_gets_a_filesystem_safe_directory(self):
        run_id = self.logger.start_run("BTC/USD", "2026-09-09")

        self.assertTrue(self._payload(run_id, symbol="BTC_USD"))

    def test_finishing_marks_the_run_complete_and_records_the_signal(self):
        run_id = self.logger.start_run("NVDA", "2026-09-09")

        self.logger.finish_run(
            symbol="NVDA", final_signal="BUY", final_state={"decision": "BUY"}
        )

        payload = self._payload(run_id)
        self.assertEqual(payload["status"], "completed")
        self.assertIsNotNone(payload["ended_at"])
        self.assertEqual(payload["summary"]["final_signal"], "BUY")
        self.assertEqual(payload["snapshots"]["final_state"]["decision"], "BUY")

    def test_a_failed_run_records_its_error(self):
        run_id = self.logger.start_run("NVDA", "2026-09-09")

        self.logger.finish_run(
            symbol="NVDA", status="failed", error_message="provider outage"
        )

        payload = self._payload(run_id)
        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["summary"]["error_message"], "provider outage")
        self.assertEqual(payload["summary"]["error_events"], 1)

    def test_finishing_an_unknown_run_does_nothing(self):
        self.logger.finish_run(symbol="MISSING")

    def test_events_after_a_run_finishes_are_dropped(self):
        run_id = self.logger.start_run("NVDA", "2026-09-09")
        self.logger.finish_run(symbol="NVDA")

        self.logger.log_event("error", symbol="NVDA")

        self.assertEqual(self._payload(run_id)["summary"]["error_events"], 0)


class EventCountingTests(RunLoggerFixture):
    def setUp(self):
        super().setUp()
        self.run_id = self.logger.start_run("NVDA", "2026-09-09")

    def _summary(self):
        return self._payload(self.run_id)["summary"]

    def test_a_prompt_is_counted_with_its_length(self):
        self.logger.log_prompt("market_report", "a" * 40, symbol="NVDA")

        summary = self._summary()
        self.assertEqual(summary["prompt_events"], 1)
        self.assertEqual(summary["total_prompt_chars"], 40)

    def test_a_tool_call_is_counted_with_its_time_and_output(self):
        self.logger.log_tool_call(
            tool_name="get_google_news",
            inputs={"query": "NVDA"},
            output="x" * 25,
            status="success",
            execution_time_seconds=1.5,
            symbol="NVDA",
        )

        summary = self._summary()
        self.assertEqual(summary["tool_events"], 1)
        self.assertEqual(summary["total_tool_time_seconds"], 1.5)
        self.assertEqual(summary["total_tool_output_chars"], 25)

    def test_a_tool_call_flagged_as_suspect_is_counted_separately(self):
        self.logger.log_tool_call(
            tool_name="get_google_news",
            inputs={},
            output="",
            status="success",
            execution_time_seconds=0.0,
            symbol="NVDA",
            quality_details={"is_suspect": True},
        )

        self.assertEqual(self._summary()["suspect_tool_events"], 1)

    def test_an_llm_call_accumulates_its_tokens(self):
        self.logger.log_event(
            "llm_call",
            symbol="NVDA",
            payload={
                "latency_seconds": 2.0,
                "input_chars": 100,
                "output_chars": 50,
                "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
            },
        )

        summary = self._summary()
        self.assertEqual(summary["llm_call_events"], 1)
        self.assertEqual(summary["total_llm_time_seconds"], 2.0)
        self.assertEqual(summary["total_llm_input_tokens"], 10)
        self.assertEqual(summary["total_llm_tokens"], 15)

    def test_llm_tokens_feed_the_daily_budget(self):
        guard = mock.MagicMock()

        with mock.patch("tradingagents.safety.get_safety_guard", lambda: guard):
            self.logger.log_event(
                "llm_call",
                symbol="NVDA",
                payload={"usage": {"total_tokens": 15}},
            )

        guard.record_llm_tokens.assert_called_once_with(15)

    def test_an_unavailable_budget_guard_does_not_lose_the_log(self):
        """Logging must never be the thing that fails a run."""
        with mock.patch(
            "tradingagents.safety.get_safety_guard",
            mock.Mock(side_effect=RuntimeError("guard unavailable")),
        ):
            self.logger.log_event(
                "llm_call", symbol="NVDA", payload={"usage": {"total_tokens": 15}}
            )

        self.assertEqual(self._summary()["total_llm_tokens"], 15)

    def test_each_event_type_increments_its_own_counter(self):
        for event_type, key in (
            ("agent_output", "agent_output_events"),
            ("node_execution", "node_events"),
            ("tool_retry", "tool_retry_events"),
        ):
            self.logger.log_event(event_type, symbol="NVDA")

            self.assertEqual(self._summary()[key], 1, event_type)

    def test_every_error_flavour_counts_as_an_error(self):
        for event_type in ("error", "tool_error", "node_error"):
            self.logger.log_event(event_type, symbol="NVDA")

        self.assertEqual(self._summary()["error_events"], 3)

    def test_an_agent_output_is_recorded(self):
        self.logger.log_agent_output(
            output_type="market_report", content="the read", symbol="NVDA"
        )

        self.assertEqual(self._summary()["agent_output_events"], 1)

    def test_a_state_snapshot_is_stored(self):
        self.logger.log_state_snapshot("post_analysis", {"a": 1}, symbol="NVDA")

        self.assertIn("post_analysis", self._payload(self.run_id)["snapshots"])

    def test_a_value_that_cannot_be_serialized_is_stringified(self):
        self.logger.log_event("error", symbol="NVDA", payload={"exc": object()})

        payload = self._payload(self.run_id)
        self.assertIsInstance(payload["events"][-1]["payload"]["exc"], str)


class RunResolutionTests(RunLoggerFixture):
    def test_the_only_active_run_is_assumed(self):
        run_id = self.logger.start_run("NVDA", "2026-09-09")

        self.logger.log_event("error")

        self.assertEqual(self._payload(run_id)["summary"]["error_events"], 1)

    def test_with_several_runs_the_symbol_disambiguates(self):
        nvda = self.logger.start_run("NVDA", "2026-09-09")
        aapl = self.logger.start_run("AAPL", "2026-09-09")

        self.logger.log_event("error", symbol="AAPL")

        self.assertEqual(self._payload(nvda)["summary"]["error_events"], 0)
        self.assertEqual(
            self._payload(aapl, symbol="AAPL")["summary"]["error_events"], 1
        )

    def test_an_ambiguous_event_is_dropped_rather_than_misfiled(self):
        nvda = self.logger.start_run("NVDA", "2026-09-09")
        aapl = self.logger.start_run("AAPL", "2026-09-09")

        self.logger.log_event("error")

        self.assertEqual(self._payload(nvda)["summary"]["error_events"], 0)
        self.assertEqual(
            self._payload(aapl, symbol="AAPL")["summary"]["error_events"], 0
        )

    def test_an_explicit_run_id_wins(self):
        nvda = self.logger.start_run("NVDA", "2026-09-09")
        self.logger.start_run("AAPL", "2026-09-09")

        self.logger.log_event("error", run_id=nvda)

        self.assertEqual(self._payload(nvda)["summary"]["error_events"], 1)


class StaleRunRecoveryTests(RunLoggerFixture):
    def _write_run(self, run_id, payload, symbol="NVDA"):
        path = (
            self.root
            / "eval_results"
            / symbol
            / "TradingAgentsStrategy_logs"
            / "runs"
            / f"{run_id}.json"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_a_run_left_running_by_a_killed_process_is_marked_aborted(self):
        path = self._write_run(
            "stale", {"run_id": "stale", "status": "running", "ended_at": None}
        )

        RunAuditLogger()

        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(payload["status"], "aborted")
        self.assertIsNotNone(payload["ended_at"])
        self.assertIn("Recovered stale", payload["summary"]["error_message"])

    def test_a_finished_run_is_left_alone(self):
        path = self._write_run(
            "done", {"run_id": "done", "status": "completed", "ended_at": "x"}
        )

        RunAuditLogger()

        self.assertEqual(json.loads(path.read_text())["status"], "completed")

    def test_a_running_log_that_already_has_an_end_time_is_left_alone(self):
        path = self._write_run(
            "odd", {"run_id": "odd", "status": "running", "ended_at": "x"}
        )

        RunAuditLogger()

        self.assertEqual(json.loads(path.read_text())["status"], "running")

    def test_an_unreadable_log_is_skipped(self):
        path = self._write_run("bad", {})
        path.write_text("not json", encoding="utf-8")

        RunAuditLogger()

        self.assertEqual(path.read_text(), "not json")

    def test_no_logs_at_all_is_fine(self):
        RunAuditLogger()

    def test_an_in_flight_run_is_closed_at_process_exit(self):
        run_id = self.logger.start_run("NVDA", "2026-09-09")

        self.logger._close_active_runs_on_exit()

        payload = self._payload(run_id)
        self.assertEqual(payload["status"], "aborted")
        self.assertIn("terminated before finish_run", payload["summary"]["error_message"])

    def test_exit_with_nothing_in_flight_does_nothing(self):
        self.logger._close_active_runs_on_exit()

    def test_the_exit_handler_never_raises(self):
        self.logger.start_run("NVDA", "2026-09-09")

        with mock.patch.object(
            self.logger, "_flush_unlocked", side_effect=RuntimeError("disk full")
        ):
            self.logger._close_active_runs_on_exit()


class FinalStateSnapshotTests(RunLoggerFixture):
    def _write(self, run_id, **overrides):
        payload = {
            "run_id": run_id,
            "status": "completed",
            "trade_date": "2026-09-09",
            "started_at": "2026-09-09T14:00:00Z",
            "snapshots": {"final_state": {"decision": run_id}},
        }
        payload.update(overrides)
        path = (
            self.root
            / "eval_results"
            / "NVDA"
            / "TradingAgentsStrategy_logs"
            / "runs"
            / f"{run_id}.json"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")

    def _load(self, symbol="NVDA", trade_date="2026-09-09"):
        return load_final_state_snapshot(symbol, trade_date)

    def test_the_snapshot_of_a_completed_run_is_returned(self):
        self._write("run-1")

        self.assertEqual(self._load()["decision"], "run-1")

    def test_the_newest_run_for_the_date_wins(self):
        self._write("older", started_at="2026-09-09T09:00:00Z")
        self._write("newer", started_at="2026-09-09T15:00:00Z")

        self.assertEqual(self._load()["decision"], "newer")

    def test_an_unfinished_run_is_not_used(self):
        self._write("running", status="running")

        self.assertIsNone(self._load())

    def test_another_dates_run_is_not_used(self):
        self._write("other", trade_date="2026-01-01")

        self.assertIsNone(self._load())

    def test_a_run_without_a_final_state_is_not_used(self):
        self._write("empty", snapshots={})

        self.assertIsNone(self._load())

    def test_an_unreadable_run_is_skipped(self):
        self._write("good")
        bad = (
            self.root
            / "eval_results"
            / "NVDA"
            / "TradingAgentsStrategy_logs"
            / "runs"
            / "bad.json"
        )
        bad.write_text("not json", encoding="utf-8")

        self.assertEqual(self._load()["decision"], "good")

    def test_a_symbol_that_was_never_analyzed_yields_nothing(self):
        self.assertIsNone(self._load(symbol="MISSING"))


class SingletonTests(unittest.TestCase):
    def test_the_process_shares_one_logger(self):
        self.assertIs(get_run_audit_logger(), get_run_audit_logger())


if __name__ == "__main__":
    unittest.main()
