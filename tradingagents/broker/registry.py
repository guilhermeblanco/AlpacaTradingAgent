from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from tradingagents.agents.schemas import TradeIntent
    from tradingagents.broker.snapshot import SnapshotProvider
    from tradingagents.execution.gateway import ExecutionGateway
    from tradingagents.execution.models import ExecutionPlan
    from tradingagents.broker.instruments import InstrumentProvider


@dataclass(frozen=True)
class BrokerCapabilities:
    equities: bool = True
    crypto: bool = False
    fractional_equities: bool = False
    shorting: bool = False
    options: bool = False
    native_brackets: bool = False
    paper_trading: bool = False
    order_types: frozenset[str] = field(
        default_factory=lambda: frozenset({"market"})
    )
    time_in_force: frozenset[str] = field(
        default_factory=lambda: frozenset({"day"})
    )
    client_order_ids: bool = True
    order_reconciliation: bool = True

    def to_dict(self) -> dict:
        return {
            **{
                name: getattr(self, name)
                for name in (
                    "equities",
                    "crypto",
                    "fractional_equities",
                    "shorting",
                    "options",
                    "native_brackets",
                    "paper_trading",
                    "client_order_ids",
                    "order_reconciliation",
                )
            },
            "order_types": sorted(self.order_types),
            "time_in_force": sorted(self.time_in_force),
        }

    def supports_asset_class(self, asset_class: str) -> bool:
        normalized = str(asset_class or "").lower()
        return (normalized == "equity" and self.equities) or (
            normalized == "crypto" and self.crypto
        )

    def validate_intent(self, intent: TradeIntent) -> list[str]:
        errors: list[str] = []
        asset_class = intent.execution_constraints.asset_class.lower()
        if not self.supports_asset_class(asset_class):
            errors.append(f"Broker does not support {asset_class} execution.")
        if intent.target_position.value == "SHORT" and not self.shorting:
            errors.append("Broker does not support opening short positions.")
        order_type = str(intent.order_intent.order_type or "none").lower()
        if order_type not in {"none", "close_position"} and order_type not in self.order_types:
            errors.append(f"Broker does not support {order_type} orders.")
        tif = str(intent.order_intent.time_in_force or "").lower()
        if tif and tif not in self.time_in_force:
            errors.append(f"Broker does not support {tif} time in force.")
        if (
            intent.execution_constraints.broker_protective_orders_enabled
            and not self.native_brackets
        ):
            errors.append("Broker adapter does not support native protective orders.")
        return errors

    def validate_plan(self, intent: TradeIntent, plan: ExecutionPlan) -> list[str]:
        if self.fractional_equities:
            return []
        if intent.execution_constraints.asset_class.lower() != "equity":
            return []
        fractional = [
            leg.quantity
            for leg in plan.legs
            if leg.quantity is not None
            and abs(float(leg.quantity) - round(float(leg.quantity))) > 1e-9
        ]
        if fractional:
            return ["Broker requires whole-share equity quantities."]
        return []


ALPACA_CAPABILITIES = BrokerCapabilities(
    crypto=True,
    fractional_equities=True,
    shorting=True,
    options=True,
    native_brackets=True,
    paper_trading=True,
    time_in_force=frozenset({"day", "gtc"}),
)

TRADIER_CAPABILITIES = BrokerCapabilities(
    shorting=True,
    options=False,
    native_brackets=False,
    paper_trading=True,
)

ROBINHOOD_CAPABILITIES = BrokerCapabilities(
    fractional_equities=True,
    client_order_ids=True,
    order_reconciliation=True,
)

BROKER_CAPABILITY_MATRIX = {
    "alpaca": ALPACA_CAPABILITIES,
    "tradier": TRADIER_CAPABILITIES,
    "robinhood": ROBINHOOD_CAPABILITIES,
}


@dataclass(frozen=True)
class BrokerRuntime:
    name: str
    capabilities: BrokerCapabilities
    snapshot_provider: SnapshotProvider
    execution_gateway: ExecutionGateway
    instrument_provider: InstrumentProvider | None = None


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
    from tradingagents.broker.instruments import (
        AlpacaInstrumentProvider,
        RobinhoodInstrumentProvider,
        TradierInstrumentProvider,
    )
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
            capabilities=replace(ALPACA_CAPABILITIES, paper_trading=paper),
            snapshot_provider=AlpacaSnapshotProvider(),
            execution_gateway=AlpacaPaperExecutionGateway() if paper else AlpacaExecutionGateway(),
            instrument_provider=AlpacaInstrumentProvider(),
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
            capabilities=replace(TRADIER_CAPABILITIES, paper_trading=sandbox),
            snapshot_provider=TradierSnapshotProvider(client),
            execution_gateway=TradierExecutionGateway(client),
            instrument_provider=TradierInstrumentProvider(client),
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
            capabilities=ROBINHOOD_CAPABILITIES,
            snapshot_provider=RobinhoodSnapshotProvider(client, account_number=account_number),
            execution_gateway=RobinhoodExecutionGateway(
                client, account_number=account_number, review_only=review_only,
                live_orders_enabled=live_enabled,
            ),
            instrument_provider=RobinhoodInstrumentProvider(client),
        )

    registry.register("robinhood", robinhood)
    return registry


def get_execution_broker_runtime(config: dict | None = None) -> BrokerRuntime:
    if config is None:
        from tradingagents.dataflows.config import get_config

        config = get_config() or {}
    name = str((config or {}).get("execution_broker") or "alpaca")
    return default_broker_registry().create(name, config or {})
