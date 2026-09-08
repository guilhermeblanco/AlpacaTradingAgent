# Integration credential vault

The Web UI can store broker, market-data, and LLM provider credentials in
PostgreSQL instead of requiring each provider key at deployment time. Stored
values are encrypted before persistence and are never returned to the browser.
Environment variables remain supported as a read-only fallback for automated
deployments.

## Enable the vault

Generate a root encryption key once:

```bash
python -m tradingagents.integrations generate-key
```

Set the generated value and the PostgreSQL connection in `.env`:

```dotenv
DATABASE_URL=postgresql+psycopg://tradingagents:password@localhost:5432/tradingagents
INTEGRATION_VAULT_KEY=<generated Fernet key>
INTEGRATION_VAULT_SCOPE=default
```

Apply the schema and restart every application and worker process:

```bash
alembic upgrade head
```

Open **Integrations** in the Web UI. Enter only credentials that should be
created or replaced; blank fields preserve their current values. The status
indicator shows whether a value comes from the encrypted vault, an environment
fallback, or is not configured.

## Security properties

- Credential plaintext is submitted to the server but never stored in browser
  local storage and never sent back after submission.
- PostgreSQL stores only Fernet ciphertext. Creation, rotation, and deletion
  generate audit records without credential values.
- Runtime resolution order is temporary server override, encrypted vault,
  environment variable, then application configuration.
- A wrong or replaced root key fails closed instead of falling back silently.
- Disconnecting stored credentials does not alter environment variables.

Use HTTPS whenever the UI is reachable beyond localhost. Protect and back up
the root key separately from PostgreSQL. Losing the key makes stored credentials
unrecoverable; exposing both the key and database defeats encryption at rest.

This first increment uses one configured vault scope. Authentication and
per-user or per-workspace scopes belong to the product authentication layer and
must be added before hosting the UI for untrusted users.
