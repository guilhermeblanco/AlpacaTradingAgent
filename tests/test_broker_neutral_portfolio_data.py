import unittest

import pandas as pd

from tradingagents.broker.models import AccountSnapshot, PortfolioSnapshot, PositionSnapshot
from tradingagents.portfolio import gather_portfolio_state


class FakeSnapshots:
    def get_portfolio_snapshot(self):
        return PortfolioSnapshot(
            broker="tradier",
            account=AccountSnapshot(equity=50_000),
            positions=[PositionSnapshot(symbol="MSFT", quantity=5, market_value=2_000)],
        )


class FakeMarketData:
    name = "test"

    def __init__(self):
        self.symbols = []

    def supports(self, symbol, timeframe="1Day"):
        return symbol != "MSFT"

    def get_bars(self, symbol, start_date, end_date=None, timeframe="1Day"):
        self.symbols.append(symbol)
        return pd.DataFrame({"close": [100, 101]})


class BrokerNeutralPortfolioDataTests(unittest.TestCase):
    def test_combines_execution_snapshot_with_research_histories(self):
        market_data = FakeMarketData()
        equity, positions, history = gather_portfolio_state(
            "AAPL", FakeSnapshots(), market_data
        )

        self.assertEqual(equity, 50_000)
        self.assertEqual(positions, {"MSFT": 2_000})
        self.assertIn("AAPL", history)
        self.assertNotIn("MSFT", history)
        self.assertEqual(market_data.symbols, ["AAPL"])


if __name__ == "__main__":
    unittest.main()
