import unittest

from tradingagents.broker.instruments import (
    InstrumentSnapshot,
    RobinhoodInstrumentProvider,
    TradierInstrumentProvider,
)
from tradingagents.screener import load_universe


class FakeTradier:
    def request(self, method, path, *, params=None):
        return {
            "securities": {
                "security": [
                    {"symbol": "AAPL", "description": "Apple", "type": "stock", "exchange": "Q"},
                    {"symbol": "AAPL2601C", "description": "Option", "type": "option", "exchange": "Q"},
                ]
            }
        }


class FakeRobinhood:
    def call_tool(self, name, arguments):
        return {"results": [{"symbol": arguments["symbols"][0], "tradable": True}]}


class FakeUniverse:
    def search_instruments(self, query="", limit=12):
        return [
            InstrumentSnapshot(symbol="AAPL", tradable=True),
            InstrumentSnapshot(symbol="HALT", tradable=False),
        ]


class InstrumentDiscoveryTests(unittest.TestCase):
    def test_tradier_maps_equities_and_excludes_options(self):
        results = TradierInstrumentProvider(FakeTradier()).search_instruments("apple")
        self.assertEqual([item.symbol for item in results], ["AAPL"])
        self.assertEqual(results[0].name, "Apple")

    def test_robinhood_supports_exact_symbol_lookup(self):
        results = RobinhoodInstrumentProvider(FakeRobinhood()).search_instruments("aapl")
        self.assertEqual(results[0].symbol, "AAPL")

    def test_screener_uses_normalized_tradable_instruments(self):
        universe = load_universe(FakeUniverse())
        self.assertEqual(universe["AAPL"], "stock")
        self.assertNotIn("HALT", universe)


if __name__ == "__main__":
    unittest.main()
