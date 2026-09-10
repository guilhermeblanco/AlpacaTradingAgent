# Autonomous worker

The autonomous worker is an opt-in, headless service. Each cycle discovers
candidates, filters them against the selected broker's asset capabilities and
the asset's trading session, claims durable analysis admission, runs bounded
LLM analysis, allocates all resulting intents against one portfolio snapshot,
and dispatches approved allocations through the reservation-aware coordinator.

The execution broker and market-data sources are separate concerns. Alpaca,
Tradier, and Robinhood all use the same portfolio allocation, safety,
reservation, execution-lifecycle, and reconciliation path. The current
quantitative screener and portfolio history loader use Yahoo Finance data; the
agent research tools use the providers selected in application configuration.

## Required configuration

PostgreSQL is mandatory because analysis admission, portfolio reservations,
execution lifecycle, and reconciliation must coordinate across processes.
Set these values in `.env`:

```dotenv
AUTONOMOUS_ENABLED=true
AUTONOMOUS_ACCOUNT_KEY=alpaca:paper-primary
EXECUTION_BROKER=alpaca
EXECUTION_GATEWAY=dry-run
PERSISTENCE_BACKEND=postgres
OPENAI_API_KEY=...
```

Use a stable, unique `AUTONOMOUS_ACCOUNT_KEY` for each real broker account.
Select `tradier` or `robinhood` with `EXECUTION_BROKER` and configure that
broker's credentials from `env.sample`. Start with `EXECUTION_GATEWAY=dry-run`;
any other value selects the broker's configured paper or live gateway, still
subject to its broker-specific live-order switches.

Run one cycle locally with:

```bash
python -m tradingagents.orchestration.autonomous_worker --once
```

Run the complete service set with:

```bash
AUTONOMOUS=1 make -C infrastructure/local up
```

The worker lives in its own compose overlay rather than behind a compose
profile, so that starting it is always something somebody typed. Two further
switches still stand between it and a real order: `AUTONOMOUS_ENABLED` and
`EXECUTION_GATEWAY`, both defaulting to the safe value. See
[deployment](deployment.md).

The exchange calendar is independent of the broker: equities follow the XNYS
calendar including holidays and early closes, while crypto candidates remain
eligible continuously when the selected broker supports crypto.
