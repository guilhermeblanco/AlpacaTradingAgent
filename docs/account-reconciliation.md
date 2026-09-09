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

All three processes — the execution pipeline, the autonomous worker, and the
account monitor — derive the scope through
`tradingagents.execution.quarantine`, so one environment yields one name:

| `EXECUTION_QUARANTINE_SCOPE` | `AUTONOMOUS_ACCOUNT_KEY` | Scope |
| --- | --- | --- |
| set | anything | the value you set |
| unset | unset | `execution:<broker>` |
| unset | set | `execution:<broker>:<account key>` |

Setting `AUTONOMOUS_ACCOUNT_KEY` therefore changes the scope every process
uses, not just the worker's. Pin `EXECUTION_QUARANTINE_SCOPE` if you want a
name that does not move, and check `tradingagents-control-plane status` after
changing either variable so an existing pause is not left under the old name.

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
