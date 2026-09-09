import unittest

from tradingagents.broker.instruments import InstrumentSnapshot
from tradingagents.broker.models import AccountSnapshot, PortfolioSnapshot, QuoteSnapshot
from tradingagents.broker.preflight import CheckStatus, certify_broker_runtime
from tradingagents.broker.registry import BrokerCapabilities, BrokerRuntime


class Snapshots:
    def get_portfolio_snapshot(self):
        return PortfolioSnapshot(
            broker="test", account=AccountSnapshot(equity=10_000, cash=5_000)
        )

    def get_quote_snapshot(self, symbol):
        return QuoteSnapshot(symbol=symbol, bid_price=99, ask_price=101)


class Instruments:
    def search_instruments(self, query="", limit=12):
        return [InstrumentSnapshot(symbol=query, tradable=True)]


class Gateway:
    def get_order_snapshot(self, **kwargs):
        pass

    def close_position(self, symbol):
        pass


class BrokerPreflightTests(unittest.TestCase):
    def runtime(self, paper=True):
        return BrokerRuntime(
            name="test",
            capabilities=BrokerCapabilities(paper_trading=paper),
            snapshot_provider=Snapshots(),
            execution_gateway=Gateway(),
            instrument_provider=Instruments(),
        )

    def test_read_only_contract_certification_passes(self):
        report = certify_broker_runtime(self.runtime(), symbol="AAPL")
        self.assertTrue(report.ready)
        self.assertTrue(all(check.status == CheckStatus.PASS for check in report.checks))

    def test_live_runtime_fails_without_explicit_override(self):
        report = certify_broker_runtime(self.runtime(paper=False))
        self.assertFalse(report.ready)
        self.assertEqual(
            next(check for check in report.checks if check.name == "safety_mode").status,
            CheckStatus.FAIL,
        )

    def test_live_runtime_can_be_inspected_with_override(self):
        self.assertTrue(
            certify_broker_runtime(self.runtime(paper=False), require_paper=False).ready
        )


if __name__ == "__main__":
    unittest.main()


class FailingSnapshots:
    def __init__(self, *, portfolio_error=None, quote=None, quote_error=None):
        self.portfolio_error = portfolio_error
        self.quote = quote
        self.quote_error = quote_error

    def get_portfolio_snapshot(self):
        if self.portfolio_error:
            raise self.portfolio_error
        return PortfolioSnapshot(
            broker="test", account=AccountSnapshot(equity=10_000, cash=5_000)
        )

    def get_quote_snapshot(self, symbol):
        if self.quote_error:
            raise self.quote_error
        return self.quote or QuoteSnapshot(symbol=symbol, bid_price=99, ask_price=101)


class PreflightFailureTests(unittest.TestCase):
    """Preflight is read-only: every probe reports rather than raises, so the
    operator sees which capability is missing instead of one traceback."""

    def _runtime(self, *, snapshots=None, instruments=..., gateway=...):
        return BrokerRuntime(
            name="test",
            capabilities=BrokerCapabilities(paper_trading=True),
            snapshot_provider=snapshots or Snapshots(),
            execution_gateway=Gateway() if gateway is ... else gateway,
            instrument_provider=Instruments() if instruments is ... else instruments,
        )

    def _check(self, report, name):
        return next(check for check in report.checks if check.name == name)

    def test_an_unreachable_account_is_reported_not_raised(self):
        report = certify_broker_runtime(
            self._runtime(
                snapshots=FailingSnapshots(portfolio_error=RuntimeError("401"))
            ),
            symbol="AAPL",
        )

        check = self._check(report, "account_snapshot")
        self.assertEqual(check.status, CheckStatus.FAIL)
        self.assertIn("401", check.message)
        self.assertFalse(report.ready)

    def test_a_failing_quote_is_reported(self):
        report = certify_broker_runtime(
            self._runtime(
                snapshots=FailingSnapshots(quote_error=RuntimeError("no market data"))
            ),
            symbol="AAPL",
        )

        check = self._check(report, "market_quote")
        self.assertEqual(check.status, CheckStatus.FAIL)
        self.assertIn("no market data", check.message)

    def test_a_quote_without_a_reference_price_fails_the_check(self):
        """A quote with no usable price would size every order at zero."""
        report = certify_broker_runtime(
            self._runtime(
                snapshots=FailingSnapshots(
                    quote=QuoteSnapshot(symbol="AAPL", bid_price=None, ask_price=None)
                )
            ),
            symbol="AAPL",
        )

        check = self._check(report, "market_quote")
        self.assertEqual(check.status, CheckStatus.FAIL)
        self.assertIn("reference price", check.message)

    def test_an_adapter_without_instrument_discovery_only_warns(self):
        report = certify_broker_runtime(
            self._runtime(instruments=None), symbol="AAPL"
        )

        check = self._check(report, "instrument_lookup")
        self.assertEqual(check.status, CheckStatus.WARN)

    def test_an_untradable_symbol_fails_the_lookup(self):
        class Untradable:
            def search_instruments(self, query="", limit=12):
                return [InstrumentSnapshot(symbol=query, tradable=False)]

        report = certify_broker_runtime(
            self._runtime(instruments=Untradable()), symbol="AAPL"
        )

        check = self._check(report, "instrument_lookup")
        self.assertEqual(check.status, CheckStatus.FAIL)
        self.assertIn("not returned as tradable", check.message)

    def test_a_symbol_the_broker_does_not_list_fails_the_lookup(self):
        class Elsewhere:
            def search_instruments(self, query="", limit=12):
                return [InstrumentSnapshot(symbol="SOMETHINGELSE", tradable=True)]

        report = certify_broker_runtime(
            self._runtime(instruments=Elsewhere()), symbol="AAPL"
        )

        self.assertEqual(
            self._check(report, "instrument_lookup").status, CheckStatus.FAIL
        )

    def test_a_failing_instrument_search_is_reported(self):
        class Broken:
            def search_instruments(self, query="", limit=12):
                raise RuntimeError("catalog unavailable")

        report = certify_broker_runtime(
            self._runtime(instruments=Broken()), symbol="AAPL"
        )

        check = self._check(report, "instrument_lookup")
        self.assertEqual(check.status, CheckStatus.FAIL)
        self.assertIn("catalog unavailable", check.message)

    def test_a_gateway_missing_a_method_is_named(self):
        class Partial:
            def close_position(self, symbol):
                pass

        report = certify_broker_runtime(
            self._runtime(gateway=Partial()), symbol="AAPL"
        )

        missing = [
            check
            for check in report.checks
            if "missing" in check.message and check.status != CheckStatus.PASS
        ]
        self.assertTrue(missing)
        self.assertFalse(report.ready)


class PreflightCliTests(unittest.TestCase):
    def _main(self, argv, ready=True):
        import contextlib
        import io
        from types import SimpleNamespace
        from unittest import mock

        from tradingagents.broker import preflight

        report = SimpleNamespace(
            ready=ready, model_dump=lambda mode=None: {"ready": ready}
        )
        captured = {}

        def certify(runtime, symbol=None, require_paper=None):
            captured["symbol"] = symbol
            captured["require_paper"] = require_paper
            return report

        out = io.StringIO()
        with mock.patch.object(preflight, "certify_broker_runtime", certify):
            with mock.patch.object(
                preflight, "get_execution_broker_runtime", lambda config: config
            ):
                with mock.patch("sys.argv", argv):
                    with contextlib.redirect_stdout(out):
                        try:
                            preflight.main()
                            code = 0
                        except SystemExit as exit_code:
                            code = exit_code.code
        return code, out.getvalue(), captured

    def test_a_ready_broker_exits_cleanly_and_prints_the_report(self):
        code, output, captured = self._main(["preflight", "--symbol", "nvda"])

        self.assertEqual(code, 0)
        self.assertIn("ready", output)
        self.assertEqual(captured["symbol"], "NVDA")

    def test_paper_is_required_unless_live_is_asked_for(self):
        _code, _output, captured = self._main(["preflight"])

        self.assertTrue(captured["require_paper"])

    def test_live_can_be_inspected_on_request(self):
        _code, _output, captured = self._main(["preflight", "--allow-live"])

        self.assertFalse(captured["require_paper"])

    def test_an_unready_broker_exits_nonzero(self):
        code, _output, _captured = self._main(["preflight"], ready=False)

        self.assertEqual(code, 1)
