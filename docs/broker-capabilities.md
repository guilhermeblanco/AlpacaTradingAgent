# Broker capability contracts

Every execution broker declares a capability contract. The execution pipeline
checks it before loading account state and again after constructing the exact
order plan. Unsupported behavior fails closed and is written as a blocked
validation event.

| Capability | Alpaca | Tradier | Robinhood MCP |
| --- | --- | --- | --- |
| Equities | Yes | Yes | Yes |
| Crypto | Yes | No | No |
| Fractional equities | Yes | No | Yes |
| Short opening | Yes | Yes | No |
| Options adapter | Yes | No | No |
| Native protective orders implemented | Yes | No | No |
| Market orders | Yes | Yes | Yes |
| Stable client order identifier | Yes | Yes | Yes |
| Order reconciliation | Yes | Yes | Yes |
| Paper or sandbox mode | Yes | Yes | No |

The matrix describes behavior implemented by this repository, not every
feature offered by the broker itself. For example, a broker may offer bracket
orders while its current adapter does not yet submit them. Such a feature stays
disabled here until submission and reconciliation are covered by contract
tests.

Capability checks also apply when `EXECUTION_GATEWAY=dry-run`, using the
selected execution broker as the target contract. This catches plans that
would fail after changing from simulation to broker submission.
