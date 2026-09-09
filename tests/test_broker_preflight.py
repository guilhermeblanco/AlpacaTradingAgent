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
