# Virtual stop daemon

Virtual stops provide software-managed stop-loss and take-profit exits for
Alpaca positions that cannot use native protective orders. Because the daemon
can submit position-close orders, it is disabled by default and does not start
implicitly with the Web UI.

## Paper accounts

Review active virtual stops and enable the daemon explicitly:

```dotenv
ALPACA_USE_PAPER=True
VIRTUAL_STOPS_DAEMON_ENABLED=true
```

At startup, the daemon retrieves current Alpaca positions and cancels stored
stops that no longer have a corresponding position. If Alpaca is unavailable,
startup reconciliation fails and the daemon remains stopped. Immediately before
a triggered close, it verifies the position again and applies the deterministic
safety kill switch.

## Live accounts

Live accounts require both switches:

```dotenv
ALPACA_USE_PAPER=False
VIRTUAL_STOPS_DAEMON_ENABLED=true
VIRTUAL_STOPS_LIVE_ENABLED=true
```

Do not enable the live switch until stored stops, account mode, and kill-switch
behavior have been reviewed. A broker-rejected close restores the stop to active
state so it can be retried instead of leaving the position silently unprotected.

Virtual stops are currently an Alpaca compatibility feature. Other brokers must
use native protective orders or implement equivalent behavior through the
broker-neutral execution lifecycle; this daemon must not be reused by changing
only its credential source.
