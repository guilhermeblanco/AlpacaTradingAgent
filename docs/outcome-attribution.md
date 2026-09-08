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

## Alpaca price observations

`AlpacaHistoricalPriceProvider` implements the historical-price port with strict
Alpaca minute bars. A close is considered observable one minute after the bar's
timestamp. Descending requests select the latest close available at entry;
ascending requests select the first close available at or after an outcome
horizon. The adapter uses IEX for equities and Alpaca crypto bars for slash-form
symbols. Missing data raises `PriceObservationUnavailable`; evaluation never
falls back to a different source silently.

Historical prices are configured independently from order execution through
`HistoricalPriceProviderRegistry`. Alpaca, Tradier, and Robinhood fills all use
the same attribution path and may use the Alpaca market-data source. Selecting
an unknown source fails at startup; the execution broker name is never used as
an implicit price-provider fallback.

## Recurring worker

Run `python -m tradingagents.evaluation.worker` with PostgreSQL persistence to
resolve all due horizons and expire untouched portfolio reservations. Add
`--once` for a single operational cycle. Price API calls happen after the due
episode query transaction closes; each outcome then commits in its own short
transaction. A missing price fails only that episode and is retried on the next
cycle.

Docker Compose runs this worker as a separate service. Configure
`EVALUATION_PRICE_PROVIDER`, `EVALUATION_HORIZONS_DAYS`,
`EVALUATION_ESTIMATED_COST_PCT`, and `EVALUATION_WORKER_INTERVAL_SECONDS`.
Provider selection is unrelated to `execution_broker`.

`PersistentExecutionReconciler` can receive the configured historical price
provider and capture an episode in the same transaction as the reconciled order
and lifecycle event. This hook is broker-neutral and uses the actual terminal
fill price. It excludes risk-reducing legs. Benchmark lookup failures are stored
on the reconciliation event and never roll back order or lifecycle state.

All live broker adapters implement the reconciliation gateway. Alpaca reads a
specific order through its trading SDK. Tradier reads its documented account
order endpoint and maps cumulative `exec_quantity` and `avg_fill_price`.
Robinhood reads official `get_equity_orders` MCP history and matches either the
broker order ID or submitted `ref_id`. Unknown broker statuses remain `unknown`
and cannot be mistaken for a successful terminal fill.
