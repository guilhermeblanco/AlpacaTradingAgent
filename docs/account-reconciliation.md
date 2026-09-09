# Account reconciliation and execution quarantine

Order reconciliation now verifies terminal fills against the resulting broker
position. Each execution plan records the exact position quantity observed
before submission. After all order legs become terminal, the worker computes:

```text
expected position = pre-trade position + signed cumulative fills
```

It then loads a fresh portfolio snapshot from the selected execution broker.
This works through the shared snapshot contract for Alpaca, Tradier, and
Robinhood.

The first position mismatch is retried because broker account views can lag an
order status briefly. If the next observation still differs, the worker pauses
the plan's execution quarantine scope in PostgreSQL. New executions in that
scope fail closed before loading a quote or submitting an order.

Set an account-specific scope in every process that shares an account:

```dotenv
EXECUTION_QUARANTINE_SCOPE=execution:alpaca:paper-primary
ACCOUNT_RECONCILIATION_QUANTITY_TOLERANCE=0.000001
```

When the scope is omitted, direct execution defaults to `execution:<broker>`.
The autonomous worker appends `AUTONOMOUS_ACCOUNT_KEY` when available, and the
periodic account monitor pauses the same `EXECUTION_QUARANTINE_SCOPE`. Set the
variable for the monitor too: without it the monitor pauses `execution:<broker>`
while the trading processes check the account-specific scope, so a drift
quarantine would never stop an order.

## Position drift versus cash drift

The periodic account monitor separates the two. Positions only move when
someone trades, so position drift quarantines the execution scope. Cash also
moves on dividends, interest, fees, and transfers, so cash-only drift is
recorded and the baseline adopts the new balance instead of halting. Set
`ACCOUNT_DRIFT_HALT_ON_CASH=true` to quarantine on cash drift as well.

Because only a verified fill refreshes the baseline, and a quarantine stops
fills, a quarantined account cannot return to a matching state on its own.
Adopt the current broker state as the baseline after reconciling by hand:

```bash
tradingagents-account-monitor --resync-baseline
```

Inspect and clear a quarantine only after comparing the broker account with the
decision and order journals:

```bash
tradingagents-control-plane status
tradingagents-control-plane resume execution:alpaca:paper-primary \
  --updated-by operator
```

Executions created before this feature have no pre-trade quantity. Their account
check is recorded as skipped and they are not quarantined.

Paused scopes are also listed in the WebUI operations cockpit, where each one
can be resumed directly.
