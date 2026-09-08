# PostgreSQL persistence

PostgreSQL provides the durable decision event log and transactional projections
used by lifecycle, evaluation, and analysis admission. Existing local storage
remains available by setting `PERSISTENCE_BACKEND=local`.

## Local setup

1. Copy `env.sample` to `.env` and replace the PostgreSQL password.
2. Start the application with `docker compose up -d --build`.

Compose waits for PostgreSQL, applies `alembic upgrade head` in a one-shot
migration service, and starts the web process only after migration succeeds.
For a host installation, apply the schema directly with `alembic upgrade head`.
Compose defaults to `PERSISTENCE_BACKEND=postgres`. Host installations must set
that value explicitly after configuring `DATABASE_URL`; otherwise they retain
the local SQLite/JSONL backend.

When Alembic runs on the host, use a host URL:

```dotenv
DATABASE_URL=postgresql+psycopg://tradingagents:password@localhost:5432/tradingagents
```

Inside Compose, the application URL is set automatically and uses `postgres` as
the hostname.

## Schema ownership

Only Alembic migrations may change the schema. Run `alembic upgrade head` before
deploying application code that depends on a new migration. To inspect the
current revision, run `alembic current`.

The `decision_events` table is append-only. PostgreSQL rejects updates and
deletes through a database trigger, preserving the audit trail even if an
application code path attempts a mutation.

For equity execution, each lifecycle transition and its matching decision event
commit in one transaction. If either write fails, both roll back. Keep lifecycle
tracking enabled for autonomous execution because it supplies duplicate-decision
protection and broker idempotency keys.

The `outbox` table makes local state changes and queued external work one
transaction. Dispatch is at least once: workers use leases and `SKIP LOCKED`,
retry failures with exponential backoff, and dead-letter exhausted messages.
Every remote handler must propagate the message idempotency key because a
worker can fail after a remote call succeeds but before the local acknowledgement.

## Operations

- Back up both the database and application secrets before a production deploy.
- Use a managed PostgreSQL service with encryption, automated backups, and
  point-in-time recovery for live trading.
- Give the runtime role data access but reserve schema migration privileges for
  a separate deployment role.
- Monitor connection saturation, transaction age, outbox lag, and failed writes.
- Test restore procedures before enabling live order execution.

## Tests

The repository tests use SQLite for fast adapter contract coverage. Set
`TEST_DATABASE_URL` to run migration and PostgreSQL-only integration tests:

```bash
TEST_DATABASE_URL="$DATABASE_URL" python -m pytest tests/test_postgres_persistence.py -v
```
