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
The autonomous worker appends `AUTONOMOUS_ACCOUNT_KEY` when available.

Inspect and clear a quarantine only after comparing the broker account with the
decision and order journals:

```bash
python -m tradingagents.operations.control_plane status
python -m tradingagents.operations.control_plane resume execution:alpaca:paper-primary \
  --updated-by operator
```

Executions created before this feature have no pre-trade quantity. Their account
check is recorded as skipped and they are not quarantined.
