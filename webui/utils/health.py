"""A liveness endpoint for the container runtime.

Deliberately liveness, not readiness: it answers "is this process still
serving HTTP", nothing more. It does not touch PostgreSQL, the broker, or
any LLM provider.

That restraint is the point. A readiness-style probe that checked the
database would restart the web UI every time PostgreSQL blipped — and the
web UI is exactly where an operator goes to find out that PostgreSQL has
blipped. Losing the console because the thing it monitors is unwell is the
wrong failure mode. Dependency health is already reported, per dependency,
in the vitals strip.

`/` would also answer, but rendering the whole Dash layout every thirty
seconds to learn that the process is alive is a poor trade.
"""

from __future__ import annotations

HEALTH_PATH = "/healthz"


def register_health_route(server) -> None:
    """Attach `/healthz` to the Flask server behind Dash."""

    @server.route(HEALTH_PATH)
    def healthz():  # pragma: no cover - exercised through the test client
        return "ok\n", 200, {"Content-Type": "text/plain; charset=utf-8"}
