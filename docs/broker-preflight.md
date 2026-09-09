# Broker preflight

Run a read-only adapter certification before starting autonomous workers:

```bash
tradingagents-broker-preflight --broker alpaca --symbol AAPL
```

The command reads the account, positions, a quote, instrument metadata, and
adapter capabilities. It never submits an order. Paper or sandbox mode is
required by default. `--allow-live` permits read-only inspection of a live
account but does not enable autonomous order submission.
