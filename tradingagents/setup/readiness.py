"""What this deployment actually needs configured, and what it has.

The Integrations screen lists sixteen credentials and describes twelve of
them as required. That is close to the opposite of true: a default install
needs three, and which three depends on choices already made elsewhere.
Ten of the rows are model providers, and you need whichever one
`llm_provider` names — the rest are there so you can switch, not so you can
fill them all in. FRED matters only if the macro analyst runs. CryptoCompare
matters only if crypto is in scope. Tradier and Robinhood matter only if
`execution_broker` names them.

So the requirement set is a *function of the configuration*, and this module
is that function. It is deliberately domain code rather than UI code: the
setup wizard, the empty states, and a future preflight command all need the
same answer, and it should not live in a Dash callback.

Four levels, and the distinction between them is the useful part:

``required``     nothing works without it.
``conditional``  required *because of a choice made elsewhere*, and the
                 requirement names the choice, so the reader can see that
                 changing the choice removes the requirement.
``recommended``  it works without this, in a specific and stated way that is
                 worse.
``optional``     a fallback or a convenience.

Nothing here reads a credential's value. It asks only whether one is
configured and where from, so this module can be called anywhere — including
somewhere that then renders the answer into a browser.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Literal, Optional

from tradingagents.dataflows.config import get_api_key_source

Level = Literal["required", "conditional", "recommended", "optional"]

LEVEL_ORDER: dict[str, int] = {
    "required": 0,
    "conditional": 1,
    "recommended": 2,
    "optional": 3,
}


@dataclass(frozen=True)
class Credential:
    """One secret, and where to go and get it."""

    key: str  #: the name `get_api_key` knows it by
    env_var: str
    label: str
    obtain_url: str = ""
    placeholder: str = ""

    def source(self, lookup: Optional[Callable[[str, str], str]] = None) -> str:
        lookup = lookup or get_api_key_source
        try:
            return lookup(self.key, self.env_var)
        except Exception:
            return ""

    def configured(self, lookup=None) -> bool:
        return bool(self.source(lookup))


@dataclass
class Requirement:
    """One thing to set up, why, and whether it is done."""

    id: str
    title: str
    why: str  #: what stops working, or works worse, without it
    level: Level
    credentials: tuple[Credential, ...] = ()
    #: For a conditional requirement: the setting that made it apply. Naming
    #: it lets a reader see that the requirement is a consequence of a
    #: decision rather than a fact about the software.
    because: str = ""
    #: Position in the setup sequence. Level decides urgency; this decides
    #: the order things are *asked* in, which is not the same question — the
    #: broker is exactly as required as the model and still makes no sense
    #: to ask for first.
    order: int = 50
    #: For requirements that are not about a credential at all — a database
    #: URL, a chosen backend — state it here. Left as None the answer comes
    #: from the credentials, and `all(())` is True, so a requirement with
    #: nothing to check would otherwise report itself permanently met.
    met: Optional[bool] = None
    sources: dict[str, str] = field(default_factory=dict)

    @property
    def satisfied(self) -> bool:
        """Every credential this needs is configured somewhere."""
        if self.met is not None:
            return self.met
        return all(self.sources.get(item.key) for item in self.credentials)

    @property
    def missing(self) -> tuple[Credential, ...]:
        return tuple(
            item for item in self.credentials if not self.sources.get(item.key)
        )

    @property
    def blocking(self) -> bool:
        return self.level in ("required", "conditional") and not self.satisfied


@dataclass
class Readiness:
    """The whole picture, ordered so the urgent part reads first."""

    requirements: list[Requirement] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        """Whether an analysis can run at all."""
        return not any(item.blocking for item in self.requirements)

    @property
    def blocking(self) -> list[Requirement]:
        return [item for item in self.requirements if item.blocking]

    @property
    def unsatisfied(self) -> list[Requirement]:
        return [item for item in self.requirements if not item.satisfied]

    def by_level(self, level: Level) -> list[Requirement]:
        return [item for item in self.requirements if item.level == level]

    def get(self, requirement_id: str) -> Optional[Requirement]:
        return next(
            (item for item in self.requirements if item.id == requirement_id), None
        )

    @property
    def credentials(self) -> list[Credential]:
        """Every credential mentioned, once, most urgent first."""
        seen: dict[str, Credential] = {}
        for requirement in sorted(
            self.requirements, key=lambda item: LEVEL_ORDER[item.level]
        ):
            for credential in requirement.credentials:
                seen.setdefault(credential.key, credential)
        return list(seen.values())


# ── The catalogue ────────────────────────────────────────────────────────────
# Model providers, keyed the way `llm_provider` names them. Exactly one of
# these is ever required.

MODEL_PROVIDERS: dict[str, Credential] = {
    "openai": Credential(
        "openai_api_key", "OPENAI_API_KEY", "OpenAI",
        "https://platform.openai.com/api-keys", "sk-...",
    ),
    "anthropic": Credential(
        "anthropic_api_key", "ANTHROPIC_API_KEY", "Anthropic",
        "https://console.anthropic.com/settings/keys", "sk-ant-...",
    ),
    "google": Credential(
        "google_api_key", "GOOGLE_API_KEY", "Google AI Studio",
        "https://aistudio.google.com/app/apikey",
    ),
    "xai": Credential("xai_api_key", "XAI_API_KEY", "xAI", "https://console.x.ai/"),
    "minimax": Credential("minimax_api_key", "MINIMAX_API_KEY", "MiniMax"),
    "deepseek": Credential(
        "deepseek_api_key", "DEEPSEEK_API_KEY", "DeepSeek",
        "https://platform.deepseek.com/api_keys",
    ),
    "dashscope": Credential("dashscope_api_key", "DASHSCOPE_API_KEY", "Qwen/DashScope"),
    "zhipu": Credential("zhipu_api_key", "ZHIPU_API_KEY", "Zhipu GLM"),
    "openrouter": Credential(
        "openrouter_api_key", "OPENROUTER_API_KEY", "OpenRouter",
        "https://openrouter.ai/keys",
    ),
    "azure": Credential(
        "azure_openai_api_key", "AZURE_OPENAI_API_KEY", "Azure OpenAI"
    ),
}

#: Providers that run against a local endpoint and need no key at all.
LOCAL_PROVIDERS = frozenset({"ollama", "local", "lmstudio", "openai_compatible"})

BROKERS: dict[str, tuple[Credential, ...]] = {
    "alpaca": (
        Credential(
            "alpaca_api_key", "ALPACA_API_KEY", "Alpaca key ID",
            "https://app.alpaca.markets/paper/dashboard/overview",
        ),
        Credential("alpaca_secret_key", "ALPACA_SECRET_KEY", "Alpaca secret key"),
    ),
    "tradier": (
        Credential(
            "tradier_access_token", "TRADIER_ACCESS_TOKEN", "Tradier access token",
            "https://developer.tradier.com/",
        ),
        Credential("tradier_account_id", "TRADIER_ACCOUNT_ID", "Tradier account id"),
    ),
    "robinhood": (
        Credential(
            "robinhood_mcp_access_token", "ROBINHOOD_MCP_ACCESS_TOKEN",
            "Robinhood MCP access token",
        ),
    ),
}

FINNHUB = Credential(
    "finnhub_api_key", "FINNHUB_API_KEY", "Finnhub",
    "https://finnhub.io/register",
)
FRED = Credential(
    "fred_api_key", "FRED_API_KEY", "FRED",
    "https://fred.stlouisfed.org/docs/api/api_key.html",
)
COINDESK = Credential(
    "coindesk_api_key", "COINDESK_API_KEY", "CryptoCompare",
    "https://www.cryptocompare.com/cryptopian/api-keys",
)
ALPHA_VANTAGE = Credential(
    "alpha_vantage_api_key", "ALPHA_VANTAGE_API_KEY", "Alpha Vantage",
    "https://www.alphavantage.co/support/#api-key",
)

DEFAULT_ANALYSTS = ("market", "social", "news", "fundamentals", "macro")


def _analysts(config) -> tuple[str, ...]:
    raw = config.get("autonomous_analysts") or config.get("selected_analysts")
    if isinstance(raw, str):
        parsed = tuple(item.strip() for item in raw.split(",") if item.strip())
        return parsed or DEFAULT_ANALYSTS
    if isinstance(raw, (list, tuple)) and raw:
        return tuple(str(item).strip() for item in raw)
    return DEFAULT_ANALYSTS


def _asset_filter(config) -> str:
    return str(config.get("autonomous_asset_filter") or "all").strip().lower()


def evaluate_readiness(config=None, *, lookup=None) -> Readiness:
    """What this configuration needs, and how much of it is in place."""
    if config is None:
        from tradingagents.dataflows.config import get_config

        try:
            config = get_config()
        except Exception:
            config = {}
    config = dict(config or {})

    readiness = Readiness()

    def add(requirement: Requirement) -> None:
        requirement.sources = {
            item.key: item.source(lookup) for item in requirement.credentials
        }
        readiness.requirements.append(requirement)

    # ── 1. A model ───────────────────────────────────────────────────────────
    provider = str(config.get("llm_provider") or "openai").strip().lower()
    if provider in LOCAL_PROVIDERS:
        add(
            Requirement(
                id="model",
                order=10,
                title=f"Model provider — {provider}",
                why="Runs against a local endpoint, so there is no key to set.",
                level="optional",
            )
        )
        readiness.notes.append(
            f"llm_provider is {provider!r}, which talks to a local endpoint. "
            "Set backend_url if it is not on the default address."
        )
    else:
        credential = MODEL_PROVIDERS.get(provider)
        add(
            Requirement(
                id="model",
                order=10,
                title=f"Model provider — {credential.label if credential else provider}",
                why=(
                    "Every analyst, researcher and the risk manager reach a "
                    "model. Nothing runs without this one."
                ),
                level="required",
                credentials=(credential,) if credential else (),
                because=f"llm_provider = {provider}",
            )
        )
        if credential is None:
            readiness.notes.append(
                f"llm_provider is {provider!r}, which is not a provider this "
                "build knows. Set it to one of: "
                + ", ".join(sorted(MODEL_PROVIDERS))
            )
        else:
            readiness.notes.append(
                f"Only the {credential.label} key is needed. The other "
                f"{len(MODEL_PROVIDERS) - 1} providers are alternatives, not "
                "additions — set llm_provider to switch."
            )

    # ── 2. A broker ──────────────────────────────────────────────────────────
    broker = str(config.get("execution_broker") or "alpaca").strip().lower()
    add(
        Requirement(
            id="broker",
            order=20,
            title=f"Broker — {broker}",
            why=(
                "Positions, buying power and prices come from here, and it is "
                "where an order would eventually go. A paper account is enough "
                "and is the default."
            ),
            level="required",
            credentials=BROKERS.get(broker, ()),
            because=f"execution_broker = {broker}",
        )
    )

    # ── 3. Market data, when it is somewhere else ────────────────────────────
    data_provider = str(
        config.get("research_market_data_provider") or broker
    ).strip().lower()
    if data_provider != broker:
        add(
            Requirement(
                id="market_data",
                order=25,
                title=f"Market data — {data_provider}",
                why="Bars and quotes for the analysts come from here.",
                level="conditional",
                credentials=BROKERS.get(data_provider, ()),
                because=(
                    f"research_market_data_provider = {data_provider}, which is "
                    f"not the execution broker ({broker}). Point it at {broker} "
                    "and this requirement goes away."
                ),
            )
        )

    # ── 4. Where the record is kept ──────────────────────────────────────────
    backend = str(config.get("persistence_backend") or "local").strip().lower()
    has_url = bool(config.get("database_url"))
    if backend == "postgres":
        add(
            Requirement(
                id="database",
                order=30,
                title="PostgreSQL",
                why=(
                    "The decision tape, the pipeline board, the gate ledgers "
                    "and every evaluation outcome are read from here. Without "
                    "it the workbench has nothing to show."
                ),
                level="required",
                met=has_url,
                because="persistence_backend = postgres",
            )
        )
        if not has_url:
            readiness.notes.append(
                "persistence_backend is postgres but database_url is empty, so "
                "the workbench will stay blank."
            )
    else:
        readiness.notes.append(
            "persistence_backend is 'local', so decisions are written to files "
            "rather than PostgreSQL and the workbench panels stay empty. Set it "
            "to 'postgres' with a database_url to fill them."
        )

    # ── 5. News, macro, crypto — each earned by a choice ─────────────────────
    analysts = _analysts(config)
    assets = _asset_filter(config)

    add(
        Requirement(
            id="equity_news",
            order=40,
            title="Finnhub",
            why=(
                "Company news, insider sentiment and insider transactions. "
                "Without it the news analyst still runs on Google News, with a "
                "thinner evidence base and no insider signal."
            ),
            level="recommended" if "news" in analysts else "optional",
            credentials=(FINNHUB,),
        )
    )

    macro_on = "macro" in analysts
    add(
        Requirement(
            id="macro",
            order=45,
            title="FRED",
            why=(
                "The macro analyst's entire data source."
                if macro_on
                else "Only read by the macro analyst, which is not enabled."
            ),
            level="conditional" if macro_on else "optional",
            credentials=(FRED,),
            because="the macro analyst is enabled" if macro_on else "",
        )
    )

    crypto_on = assets in ("all", "crypto")
    add(
        Requirement(
            id="crypto_news",
            order=50,
            title="CryptoCompare",
            why=(
                "News for crypto symbols; equities do not use it."
                if crypto_on
                else "Only used for crypto symbols, which are out of scope."
            ),
            level="recommended" if crypto_on else "optional",
            credentials=(COINDESK,),
            because=f"asset filter = {assets}" if crypto_on else "",
        )
    )

    add(
        Requirement(
            id="fallback_market_data",
            order=60,
            title="Alpha Vantage",
            why="A fallback for when the primary market-data source fails.",
            level="optional",
            credentials=(ALPHA_VANTAGE,),
        )
    )

    return readiness


def setup_steps(readiness: Readiness) -> list[Requirement]:
    """What the wizard should walk through, in order.

    Blocking things first, then the ones that visibly improve an analysis,
    and never the plainly optional ones — a wizard that asks for a FRED key
    while the macro analyst is off is the laundry list again with a progress
    bar on top.
    """
    return sorted(
        (
            item
            for item in readiness.requirements
            if item.credentials and not item.satisfied and item.level != "optional"
        ),
        key=lambda item: item.order,
    )
