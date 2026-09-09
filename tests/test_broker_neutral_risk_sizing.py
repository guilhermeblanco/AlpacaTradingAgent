import unittest

import pandas as pd

from tradingagents.broker.models import AccountSnapshot, PortfolioSnapshot, PositionSnapshot, QuoteSnapshot
from tradingagents.risk import RiskParameters, RiskSizingService


class FakeMarketData:
    name = "fake"

    def __init__(self):
        self.requests = []

    def get_bars(self, symbol, start_date, end_date=None, timeframe="1Day"):
        self.requests.append((symbol, timeframe))
        close = [100.0 + index * 0.1 for index in range(50)]
        return pd.DataFrame(
            {
                "high": [value + 1 for value in close],
                "low": [value - 1 for value in close],
                "close": close,
            }
        )


class BrokerNeutralRiskSizingTests(unittest.TestCase):
    def test_sizes_from_normalized_snapshots_and_research_bars(self):
        market_data = FakeMarketData()
        service = RiskSizingService(market_data, RiskParameters())
        portfolio = PortfolioSnapshot(
            broker="tradier",
            account=AccountSnapshot(equity=100_000),
            positions=[PositionSnapshot(symbol="MSFT", quantity=10, market_value=4_000)],
        )

        decision = service(
            symbol="AAPL",
            confidence="high",
            requested_notional=50_000,
            portfolio=portfolio,
            quote=QuoteSnapshot(symbol="AAPL", bid_price=99, ask_price=101),
        )

        self.assertTrue(decision.approved)
        self.assertEqual(decision.notional, 12_500)
        self.assertEqual(market_data.requests, [("AAPL", "1Day")])

    def test_missing_quote_fails_closed(self):
        service = RiskSizingService(FakeMarketData())
        with self.assertRaisesRegex(ValueError, "No reference price"):
            service(
                symbol="AAPL",
                confidence="high",
                requested_notional=1_000,
                portfolio=PortfolioSnapshot(account=AccountSnapshot(equity=10_000)),
                quote=QuoteSnapshot(symbol="AAPL"),
            )


if __name__ == "__main__":
    unittest.main()
