"""Tests for operator-facing error diagnosis.

This module turns a raw exception string into the steps an operator should
take. A pattern that stops matching leaves them with the raw message and no
guidance, which is exactly when they need it most.
"""

from __future__ import annotations

import unittest
from unittest import mock

from tradingagents.error_diagnostics import (
    ErrorDiagnostics,
    print_error_diagnosis,
    quick_diagnose,
)


class DiagnosisTests(unittest.TestCase):
    def test_an_openai_key_failure_is_recognized(self):
        diagnosis = ErrorDiagnostics.diagnose_error(
            "Incorrect API key provided for OpenAI"
        )

        self.assertEqual(diagnosis, ErrorDiagnostics.ERROR_SOLUTIONS["openai_api_key"])

    def test_an_organization_verification_failure_is_recognized(self):
        diagnosis = ErrorDiagnostics.diagnose_error(
            "Your organization must complete verification"
        )

        self.assertEqual(
            diagnosis, ErrorDiagnostics.ERROR_SOLUTIONS["organization_verification"]
        )

    def test_an_alpaca_key_failure_is_recognized(self):
        diagnosis = ErrorDiagnostics.diagnose_error("alpaca api key not found")

        self.assertEqual(diagnosis, ErrorDiagnostics.ERROR_SOLUTIONS["alpaca_api_key"])

    def test_a_trading_key_failure_maps_to_the_broker_entry(self):
        diagnosis = ErrorDiagnostics.diagnose_error("trading api key rejected")

        self.assertEqual(diagnosis, ErrorDiagnostics.ERROR_SOLUTIONS["alpaca_api_key"])

    def test_rate_limiting_is_recognized_in_both_spellings(self):
        for message in ("Rate limit exceeded", "rate_limit_exceeded"):
            self.assertEqual(
                ErrorDiagnostics.diagnose_error(message),
                ErrorDiagnostics.ERROR_SOLUTIONS["rate_limit"],
                message,
            )

    def test_connectivity_failures_are_recognized(self):
        for message in ("Connection refused", "network is unreachable"):
            self.assertEqual(
                ErrorDiagnostics.diagnose_error(message),
                ErrorDiagnostics.ERROR_SOLUTIONS["network_connection"],
                message,
            )

    def test_a_timeout_is_distinguished_from_a_plain_connection_failure(self):
        self.assertEqual(
            ErrorDiagnostics.diagnose_error("Request timeout after 30s"),
            ErrorDiagnostics.ERROR_SOLUTIONS["timeout"],
        )

    def test_a_timeout_error_type_is_honoured_without_the_word(self):
        self.assertEqual(
            ErrorDiagnostics.diagnose_error("connection dropped", "TimeoutError"),
            ErrorDiagnostics.ERROR_SOLUTIONS["timeout"],
        )

    def test_a_timeout_error_type_alone_is_enough(self):
        self.assertEqual(
            ErrorDiagnostics.diagnose_error("something odd", "TimeoutError"),
            ErrorDiagnostics.ERROR_SOLUTIONS["timeout"],
        )

    def test_missing_market_data_is_recognized(self):
        for message in ("Insufficient data for symbol", "no data returned"):
            self.assertEqual(
                ErrorDiagnostics.diagnose_error(message),
                ErrorDiagnostics.ERROR_SOLUTIONS["insufficient_data"],
                message,
            )

    def test_matching_ignores_case(self):
        self.assertEqual(
            ErrorDiagnostics.diagnose_error("RATE LIMIT EXCEEDED"),
            ErrorDiagnostics.ERROR_SOLUTIONS["rate_limit"],
        )

    def test_an_unrecognized_error_yields_no_diagnosis(self):
        self.assertIsNone(ErrorDiagnostics.diagnose_error("something went sideways"))

    def test_every_catalogued_entry_is_shaped_the_same(self):
        """The report builder reads all four keys off any entry it gets."""
        for name, entry in ErrorDiagnostics.ERROR_SOLUTIONS.items():
            self.assertIn("title", entry, name)
            self.assertIn("description", entry, name)
            self.assertTrue(entry["solutions"], name)
            self.assertIsInstance(entry["links"], list, name)


class ReportTests(unittest.TestCase):
    def test_a_recognized_error_reports_its_solutions(self):
        report = ErrorDiagnostics.generate_error_report("openai api key invalid")

        self.assertIn("OpenAI API Key Error", report)
        self.assertIn("platform.openai.com", report)

    def test_the_failing_tool_and_error_type_are_named(self):
        report = ErrorDiagnostics.generate_error_report(
            "rate limit exceeded",
            error_type="RateLimitError",
            tool_name="get_stock_news",
        )

        self.assertIn("get_stock_news", report)
        self.assertIn("RateLimitError", report)

    def test_extra_context_is_included(self):
        report = ErrorDiagnostics.generate_error_report(
            "rate limit exceeded", context={"symbol": "NVDA"}
        )

        self.assertIn("NVDA", report)

    def test_an_unrecognized_error_still_produces_a_report(self):
        """No pattern matched is not a reason to show the operator nothing."""
        report = ErrorDiagnostics.generate_error_report("something went sideways")

        self.assertIn("something went sideways", report)
        self.assertTrue(report.strip())


class QuickDiagnoseTests(unittest.TestCase):
    def test_a_recognized_error_returns_its_steps(self):
        solution = quick_diagnose("openai api key invalid")

        self.assertIsNotNone(solution)
        self.assertIn("OPENAI_API_KEY", solution)

    def test_an_unrecognized_error_returns_nothing(self):
        self.assertIsNone(quick_diagnose("something went sideways"))


class ConfigurationCheckTests(unittest.TestCase):
    def _patched(self, *, openai, alpaca_key, alpaca_secret):
        return [
            mock.patch(
                "tradingagents.dataflows.config.get_openai_api_key", lambda: openai
            ),
            mock.patch(
                "tradingagents.dataflows.config.get_alpaca_api_key",
                lambda: alpaca_key,
            ),
            mock.patch(
                "tradingagents.dataflows.config.get_alpaca_secret_key",
                lambda: alpaca_secret,
            ),
        ]

    def _check(self, **kwargs):
        patches = self._patched(**kwargs)
        for patch in patches:
            patch.start()
        try:
            return ErrorDiagnostics.check_configuration()
        finally:
            for patch in patches:
                patch.stop()

    def test_a_fully_configured_system_reports_nothing(self):
        issues = self._check(openai="sk-x", alpaca_key="PK", alpaca_secret="secret")

        self.assertEqual(issues, [])

    def test_a_missing_llm_credential_is_reported(self):
        issues = self._check(openai="", alpaca_key="PK", alpaca_secret="secret")

        self.assertEqual(len(issues), 1)
        self.assertIn("OpenAI", issues[0]["message"])
        self.assertEqual(issues[0]["severity"], "high")

    def test_a_half_configured_broker_is_reported(self):
        """A key without its secret cannot authenticate."""
        issues = self._check(openai="sk-x", alpaca_key="PK", alpaca_secret="")

        self.assertEqual(len(issues), 1)
        self.assertIn("Alpaca", issues[0]["message"])

    def test_every_missing_credential_is_reported_together(self):
        issues = self._check(openai="", alpaca_key="", alpaca_secret="")

        self.assertEqual(len(issues), 2)
        for issue in issues:
            self.assertEqual(issue["type"], "missing_config")
            self.assertTrue(issue["solution"])


class PrintDiagnosisTests(unittest.TestCase):
    def test_the_report_is_printed(self):
        with mock.patch("builtins.print") as printed:
            print_error_diagnosis("openai api key invalid", tool_name="market")

        printed.assert_called_once()
        self.assertIn("OpenAI API Key Error", printed.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
