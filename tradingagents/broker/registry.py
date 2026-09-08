from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from tradingagents.broker.snapshot import SnapshotProvider
    from tradingagents.execution.gateway import ExecutionGateway


@dataclass(frozen=True)
class BrokerCapabilities:
    equities: bool = True
    crypto: bool = False
    fractional_equities: bool = False
    shorting: bool = False
    options: bool = False
    native_brackets: bool = False
    paper_trading: bool = False


@dataclass(frozen=True)
class BrokerRuntime:
    name: str
    capabilities: BrokerCapabilities
    snapshot_provider: SnapshotProvider
    execution_gateway: ExecutionGateway


class BrokerRegistry:
    def __init__(self):
        self._factories: dict[str, Callable[[dict], BrokerRuntime]] = {}

    def register(self, name: str, factory: Callable[[dict], BrokerRuntime]) -> None:
        key = name.lower().strip()
        if not key:
            raise ValueError("Broker name is required")
        self._factories[key] = factory

    def create(self, name: str, config: dict | None = None) -> BrokerRuntime:
        key = name.lower().strip()
        if key not in self._factories:
            raise ValueError(f"Unknown broker {name!r}; available: {', '.join(self.names())}")
        return self._factories[key](config or {})

    def names(self) -> list[str]:
        return sorted(self._factories)


def default_broker_registry() -> BrokerRegistry:
    from tradingagents.broker.alpaca_snapshot import AlpacaSnapshotProvider
    from tradingagents.dataflows.config import get_alpaca_use_paper
    from tradingagents.execution.alpaca_gateway import AlpacaExecutionGateway, AlpacaPaperExecutionGateway
    from .robinhood import (
        DEFAULT_ROBINHOOD_MCP_URL, RobinhoodExecutionGateway, RobinhoodMCPClient,
        RobinhoodSnapshotProvider, load_robinhood_access_token,
    )
    from .tradier import TradierClient, TradierExecutionGateway, TradierSnapshotProvider

    registry = BrokerRegistry()

    def alpaca(config: dict) -> BrokerRuntime:
        paper = str(get_alpaca_use_paper()).strip().lower() in {"1", "true", "yes", "on"}
        return BrokerRuntime(
            name="alpaca",
            capabilities=BrokerCapabilities(
                crypto=True, fractional_equities=True, shorting=True, options=True,
                native_brackets=True, paper_trading=paper,
            ),
            snapshot_provider=AlpacaSnapshotProvider(),
            execution_gateway=AlpacaPaperExecutionGateway() if paper else AlpacaExecutionGateway(),
        )

    def tradier(config: dict) -> BrokerRuntime:
        import os
        from tradingagents.dataflows.config import get_api_key

        token = get_api_key(
            "tradier_access_token", "TRADIER_ACCESS_TOKEN"
        ) or config.get("tradier_access_token")
        account_id = get_api_key(
            "tradier_account_id", "TRADIER_ACCOUNT_ID"
        ) or config.get("tradier_account_id")
        sandbox = str(os.getenv("TRADIER_USE_SANDBOX", config.get("tradier_use_sandbox", True))).lower() in {
            "1", "true", "yes", "on",
        }
        if not token or not account_id:
            raise ValueError("TRADIER_ACCESS_TOKEN and TRADIER_ACCOUNT_ID are required")
        client = TradierClient(token=token, account_id=account_id, sandbox=sandbox)
        return BrokerRuntime(
            name="tradier",
            capabilities=BrokerCapabilities(
                equities=True, crypto=False, fractional_equities=False, shorting=True,
                options=True, native_brackets=True, paper_trading=sandbox,
            ),
            snapshot_provider=TradierSnapshotProvider(client),
            execution_gateway=TradierExecutionGateway(client),
        )

    registry.register("alpaca", alpaca)
    registry.register("tradier", tradier)
    def robinhood(config: dict) -> BrokerRuntime:
        import os
        from tradingagents.dataflows.config import get_api_key

        access_token = load_robinhood_access_token(
            access_token=get_api_key(
                "robinhood_mcp_access_token", "ROBINHOOD_MCP_ACCESS_TOKEN"
            )
            or config.get("robinhood_mcp_access_token"),
            token_path=os.getenv("ROBINHOOD_MCP_TOKEN_PATH") or config.get("robinhood_mcp_token_path"),
        )
        client = RobinhoodMCPClient(
            access_token=access_token,
            mcp_url=os.getenv("ROBINHOOD_MCP_URL") or config.get("robinhood_mcp_url")
            or DEFAULT_ROBINHOOD_MCP_URL,
            timeout_seconds=float(os.getenv("ROBINHOOD_MCP_TIMEOUT_SECONDS")
                                  or config.get("robinhood_mcp_timeout_seconds", 20)),
        )
        account_number = get_api_key(
            "robinhood_account_number", "ROBINHOOD_ACCOUNT_NUMBER"
        ) or config.get("robinhood_account_number")
        review_only = str(os.getenv("ROBINHOOD_MCP_REVIEW_ONLY",
                                     config.get("robinhood_mcp_review_only", True))).lower() in {
            "1", "true", "yes", "on",
        }
        live_enabled = str(os.getenv("ROBINHOOD_MCP_LIVE_ORDERS_ENABLED",
                                      config.get("robinhood_mcp_live_orders_enabled", False))).lower() in {
            "1", "true", "yes", "on",
        }
        return BrokerRuntime(
            name="robinhood",
            capabilities=BrokerCapabilities(
                equities=True, crypto=False, fractional_equities=True, shorting=False,
                options=False, native_brackets=False, paper_trading=False,
            ),
            snapshot_provider=RobinhoodSnapshotProvider(client, account_number=account_number),
            execution_gateway=RobinhoodExecutionGateway(
                client, account_number=account_number, review_only=review_only,
                live_orders_enabled=live_enabled,
            ),
        )

    registry.register("robinhood", robinhood)
    return registry
