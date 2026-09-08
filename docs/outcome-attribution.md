# Outcome attribution

Evaluation starts from executed trades, not model prose. After reconciliation
marks an exposure-adding order as filled, `FilledEpisodeAttributor` calculates a
quantity-weighted entry price from the durable order ledger and records the
benchmark price available at or before the final fill. Repeating capture for the
same decision returns the original episode.

`OutcomeAttributor` resolves configured horizons such as one day or five days.
It asks a broker-neutral `HistoricalPriceProvider` for the first asset and
benchmark observations at or after each horizon, calculates cost-adjusted and
benchmark-relative returns, and stores each decision/horizon pair once.

The price adapter must preserve observation timestamps. The service rejects a
benchmark reference after entry, an outcome observation before its horizon, and
any future observation. This keeps live evaluation point-in-time correct and
makes delayed market opens explicit rather than silently using an earlier close.

Only `BUY`, `OPEN`, `INCREASE`, `LONG`, and `SHORT` decisions create episodes.
Risk-reducing orders are excluded because their success is measured by the
position they close, not by treating the closing order as a new directional bet.
