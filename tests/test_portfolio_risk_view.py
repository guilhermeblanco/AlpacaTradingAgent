import unittest

from tradingagents.broker.models import AccountSnapshot, PortfolioSnapshot, PositionSnapshot
from tradingagents.portfolio.risk_view import build_portfolio_risk_view


class PortfolioRiskViewTests(unittest.TestCase):
    def test_builds_broker_neutral_exposure_view(self):
        snapshot = PortfolioSnapshot(
            broker="test", account=AccountSnapshot(equity=100_000, cash=30_000),
            positions=[
                PositionSnapshot(symbol="AAPL", quantity=100, market_value=40_000, asset_class="equity"),
                PositionSnapshot(symbol="BTC/USD", quantity=0.5, market_value=30_000, asset_class="crypto"),
            ],
        )
        view = build_portfolio_risk_view(snapshot, sectors={"AAPL": "Technology"})
        self.assertEqual(view.gross_exposure_pct, 70)
        self.assertEqual(view.cash_pct, 30)
        self.assertEqual(view.asset_classes[0].name, "equity")
        self.assertEqual(view.sectors[0].name, "Technology")
        self.assertIn("AAPL", view.prompt_context())

    def test_reports_short_net_exposure_and_limit_utilization(self):
        snapshot = PortfolioSnapshot(
            account=AccountSnapshot(equity=100_000),
            positions=[
                PositionSnapshot(symbol="LONG", quantity=1, market_value=80_000),
                PositionSnapshot(symbol="SHORT", quantity=-1, market_value=-30_000),
            ],
        )
        view = build_portfolio_risk_view(
            snapshot, max_symbol_concentration_pct=50, max_gross_exposure_pct=100,
        )
        self.assertAlmostEqual(view.gross_exposure_pct, 110)
        self.assertEqual(view.net_exposure_pct, 50)
        self.assertAlmostEqual(view.gross_limit_utilization_pct, 110)
        self.assertTrue(any("gross exposure" in warning for warning in view.warnings))

    def test_zero_equity_does_not_divide_by_zero(self):
        view = build_portfolio_risk_view(PortfolioSnapshot(account=AccountSnapshot(equity=0)))
        self.assertEqual(view.gross_exposure_pct, 0)


if __name__ == "__main__":
    unittest.main()
