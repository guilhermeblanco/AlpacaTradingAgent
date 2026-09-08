# Research market data

The analyst research path selects market data independently from order
execution. Set:

```dotenv
RESEARCH_MARKET_DATA_PROVIDER=alpaca
```

Supported values are `alpaca` and `tradier`. The same setting is available as
`research_market_data_provider` in application configuration. It controls the
OHLCV, latest-quote, stockstats, and technical-brief inputs used by the market
analyst. It does not change `execution_broker` or
`EVALUATION_PRICE_PROVIDER`.

## Credentials and coverage

`alpaca` requires `ALPACA_API_KEY` and `ALPACA_SECRET_KEY` and supports the
existing equity and crypto research paths.

`tradier` requires `TRADIER_ACCESS_TOKEN` and `TRADIER_ACCOUNT_ID`. It supports
equity research only. Crypto symbols fail closed rather than silently falling
back to a different provider.

Credentials may be supplied through environment variables or the encrypted
integration vault. Keep sandbox credentials enabled while validating a new
configuration.

## Compatibility

The model-facing tools are named `get_market_data` and
`get_market_data_report`. The former Alpaca-specific Python function names are
retained as compatibility aliases for callers outside this repository.

Execution snapshots, order submission, historical outcome evaluation, and
backtests have separate provider contracts because they have different timing,
correctness, and broker-account requirements.
