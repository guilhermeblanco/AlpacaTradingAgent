# Installation profiles and configuration validation

Use Python 3.11 or 3.12. From the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[app]"
cp env.sample .env
```

On Windows, activate with `.venv\Scripts\activate`.

The `app` extra installs the Web UI, CLI, research providers, Alpaca SDK,
PostgreSQL support, backtesting, and extended data integrations. Developers
should install `.[app,dev]`.

Smaller installations can combine these extras:

| Extra | Purpose |
| --- | --- |
| `analysis` | LLM agents, memory, news, and research data |
| `brokers` | Alpaca SDK, market calendars, and credential encryption |
| `postgres` | PostgreSQL persistence and migrations |
| `cli` | Interactive terminal interface |
| `web` | Dash/Gradio Web UI dependencies |
| `backtest` | Backtrader engine |
| `extended-data` | AkShare, Tushare, and Parsel |
| `redis` | Optional Redis integration |
| `dev` | Test and package-build tools |

For example, a headless execution worker can start with:

```bash
python -m pip install -e ".[brokers,postgres]"
```

Configuration dictionaries are validated with Pydantic when global settings
are updated, when a trading graph is created, and when the autonomous worker
starts. Invalid broker/provider names, impossible percentages, negative limits,
and zero worker counts now fail during startup rather than during a trade.
Unknown strategy-specific keys remain supported.

Verify a complete development installation:

```bash
python -m pip check
python -m pytest
```
