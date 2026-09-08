# Portfolio decision batches

Per-symbol analyses may run concurrently, but their proposed trades must not be
approved independently. Otherwise, every candidate can observe the same cash
and exposure headroom and collectively exceed the portfolio limit.

`run_portfolio_decision_batch` separates the workflow into two phases:

1. Analyze candidates with bounded provider concurrency.
2. Capture one portfolio snapshot and allocate all successful intents against
   shared limits.

The allocator processes reductions and closes without consuming new-exposure
headroom. It then ranks additions by model confidence, screener score, symbol,
and decision ID. For each addition it applies:

- the intent's requested notional and hard `max_notional_usd` ceiling;
- remaining distance to `target_portfolio_pct`;
- correlation and inverse-volatility reductions;
- per-symbol concentration headroom; and
- remaining shared gross-exposure headroom.

Each approval reserves its allocation before the next intent is considered.
Symbols are normalized so aliases such as `BTC/USD` and `BTCUSD` share one
budget. The resulting `PortfolioDecisionBatch` includes the broker snapshot
hash, deterministic priority, approved notional, and reasons for every clip or
block.

The batch is an approval artifact, not an execution shortcut. Call the normal
execution pipeline for approved allocations so intent validation, safety checks,
idempotency, lifecycle recording, and broker reconciliation remain mandatory.
