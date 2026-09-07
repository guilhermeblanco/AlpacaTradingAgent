from __future__ import annotations

from typing import Mapping, Optional

from pydantic import BaseModel, Field

from tradingagents.broker.models import PortfolioSnapshot


class ExposureSlice(BaseModel):
    name: str
    market_value: float
    equity_pct: float


class PositionRisk(BaseModel):
    symbol: str
    side: str
    market_value: float
    equity_pct: float
    concentration_utilization_pct: Optional[float] = None
    asset_class: str
    sector: Optional[str] = None


class PortfolioRiskView(BaseModel):
    broker: str
    captured_at: str
    equity: float
    cash: float
    gross_exposure: float
    net_exposure: float
    gross_exposure_pct: float
    net_exposure_pct: float
    cash_pct: float
    gross_limit_utilization_pct: Optional[float] = None
    positions: list[PositionRisk] = Field(default_factory=list)
    asset_classes: list[ExposureSlice] = Field(default_factory=list)
    sectors: list[ExposureSlice] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    def prompt_context(self, *, max_positions: int = 8) -> str:
        lines = [
            "PORTFOLIO RISK SNAPSHOT",
            f"Equity: ${self.equity:,.2f}; cash: {self.cash_pct:.1f}%",
            f"Gross exposure: {self.gross_exposure_pct:.1f}%; net exposure: {self.net_exposure_pct:.1f}%",
        ]
        if self.gross_limit_utilization_pct is not None:
            lines.append(f"Gross-limit utilization: {self.gross_limit_utilization_pct:.1f}%")
        for position in self.positions[:max_positions]:
            label = f", {position.sector}" if position.sector else ""
            lines.append(f"- {position.symbol}: {position.side}, {position.equity_pct:.1f}% of equity{label}")
        lines.extend(f"Warning: {warning}" for warning in self.warnings)
        return "\n".join(lines)


def build_portfolio_risk_view(
    snapshot: PortfolioSnapshot,
    *,
    max_symbol_concentration_pct: float = 25.0,
    max_gross_exposure_pct: float = 100.0,
    sectors: Optional[Mapping[str, str]] = None,
) -> PortfolioRiskView:
    equity = float(snapshot.account.equity)
    sectors = {key.upper(): value for key, value in (sectors or {}).items()}
    denominator = equity if equity > 0 else 1.0
    gross = sum(abs(position.market_value) for position in snapshot.positions)
    net = sum(position.market_value for position in snapshot.positions)
    by_asset: dict[str, float] = {}
    by_sector: dict[str, float] = {}
    positions: list[PositionRisk] = []
    warnings: list[str] = []
    for position in snapshot.positions:
        value = float(position.market_value)
        equity_pct = abs(value) / denominator * 100.0
        sector = sectors.get(position.symbol.upper())
        utilization = (
            equity_pct / max_symbol_concentration_pct * 100.0
            if max_symbol_concentration_pct > 0 else None
        )
        positions.append(PositionRisk(
            symbol=position.symbol, side=position.side, market_value=value,
            equity_pct=equity_pct, concentration_utilization_pct=utilization,
            asset_class=position.asset_class, sector=sector,
        ))
        by_asset[position.asset_class] = by_asset.get(position.asset_class, 0.0) + abs(value)
        if sector:
            by_sector[sector] = by_sector.get(sector, 0.0) + abs(value)
        if utilization is not None and utilization >= 100:
            warnings.append(f"{position.symbol} is at or above the symbol concentration limit")
    positions.sort(key=lambda row: row.equity_pct, reverse=True)
    gross_pct = gross / denominator * 100.0
    gross_utilization = (
        gross_pct / max_gross_exposure_pct * 100.0 if max_gross_exposure_pct > 0 else None
    )
    if gross_utilization is not None and gross_utilization >= 100:
        warnings.append("Portfolio is at or above the gross exposure limit")

    def slices(values: dict[str, float]) -> list[ExposureSlice]:
        return sorted(
            [ExposureSlice(name=name, market_value=value, equity_pct=value / denominator * 100.0)
             for name, value in values.items()],
            key=lambda row: row.market_value,
            reverse=True,
        )

    return PortfolioRiskView(
        broker=snapshot.broker, captured_at=snapshot.captured_at, equity=equity,
        cash=snapshot.account.cash, gross_exposure=gross, net_exposure=net,
        gross_exposure_pct=gross_pct, net_exposure_pct=net / denominator * 100.0,
        cash_pct=snapshot.account.cash / denominator * 100.0,
        gross_limit_utilization_pct=gross_utilization, positions=positions,
        asset_classes=slices(by_asset), sectors=slices(by_sector), warnings=warnings,
    )
