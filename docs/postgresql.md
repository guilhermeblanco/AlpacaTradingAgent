# PostgreSQL persistence

PostgreSQL provides the durable decision event log and transactional projections
used by lifecycle, evaluation, and analysis admission. Existing local storage
remains the application default until the persistence cutover is enabled.

## Local setup

1. Copy `env.sample` to `.env` and replace the PostgreSQL password.
2. Start the database with `docker compose up -d postgres`.
3. Apply the schema with `alembic upgrade head`.
4. Start the application with `docker compose up -d --build`.

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
