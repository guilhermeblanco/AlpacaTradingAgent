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
from tradingagents.setup.providers import ROLES, Field, role as get_role, selected, spec

Level = Literal["required", "conditional", "recommended", "optional"]

LEVEL_ORDER: dict[str, int] = {
    "required": 0,
    "conditional": 1,
    "recommended": 2,
    "optional": 3,
}


#: A credential is just a provider field. The two were separate lists of
#: the same thing, which is how the catalogue came to offer providers the
#: factories could not build; see tradingagents/setup/providers.py.
Credential = Field


def credential_source(
    credential: Field, lookup: Optional[Callable[[str, str], str]] = None
) -> str:
    """Which layer supplies this credential, or "" when nothing does."""
    lookup = lookup or get_api_key_source
    try:
        return lookup(credential.key, credential.env_var)
    except Exception:
        return ""


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


# ── Deriving the requirements ────────────────────────────────────────────────
# Everything below reads the provider registry. Nothing here knows the name
# of a vendor, which is the point: adding a provider is a registration, and
# the wizard, this model and the integrations screen all pick it up.

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


def _required_fields(provider) -> tuple[Field, ...]:
    """Only what must be supplied. An optional endpoint is not a blocker."""
    return tuple(item for item in (provider.fields if provider else ()) if item.required)


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
            item.key: credential_source(item, lookup)
            for item in requirement.credentials
        }
        readiness.requirements.append(requirement)

    def role_requirement(
        role_id: str, *, level: Level, order: int, because: str = "",
        why: str = "", title: str = "",
    ) -> Requirement:
        item = get_role(role_id)
        chosen = selected(role_id, config)
        provider = spec(role_id, config)
        return Requirement(
            id=role_id,
            order=order,
            title=title or f"{item.label} — {provider.label if provider else chosen}",
            why=why or (provider.blurb if provider else item.blurb),
            level=level,
            credentials=_required_fields(provider),
            because=because or f"{item.setting} = {chosen}",
        )

    # ── A model ──────────────────────────────────────────────────────────────
    model = role_requirement(
        "model", level="required", order=10,
        why=(
            "Every analyst, researcher and the risk manager reach a model. "
            "Nothing runs without one."
        ),
    )
    provider = spec("model", config)
    if provider is not None and not _required_fields(provider):
        # A local endpoint needs no key. Required with nothing to supply
        # would read as an unsatisfiable blocker.
        model.level = "optional"
        model.why = provider.note or model.why
    add(model)

    chosen_model = selected("model", config)
    alternatives = len(get_role("model").providers) - 1
    readiness.notes.append(
        f"{alternatives} other model providers are configured in this build. "
        "They are alternatives, not additions — change the provider and the "
        "key it needs changes with it."
    )
    if get_role("model").provider(str(config.get("llm_provider") or "").lower()) is None \
            and config.get("llm_provider"):
        readiness.notes.append(
            f"llm_provider is {config['llm_provider']!r}, which this build "
            f"cannot construct. Using {chosen_model} instead."
        )

    # ── A broker ─────────────────────────────────────────────────────────────
    add(
        role_requirement(
            "broker", level="required", order=20,
            why=(
                "Positions, buying power and prices come from here, and it is "
                "where an order would eventually go."
            ),
        )
    )

    # ── Market data, when it is somewhere else ───────────────────────────────
    broker = selected("broker", config)
    data_provider = selected("market_data", config)
    if config.get("research_market_data_provider") and data_provider != broker:
        add(
            role_requirement(
                "market_data", level="conditional", order=25,
                because=(
                    f"research_market_data_provider = {data_provider}, which is "
                    f"not the execution broker ({broker}). Point it at {broker} "
                    "and this requirement goes away."
                ),
            )
        )

    # ── Where the record is kept ─────────────────────────────────────────────
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

    # ── The data sources, each earned by a choice ────────────────────────────
    analysts = _analysts(config)
    assets = _asset_filter(config)

    news_role = get_role("news")
    finnhub = news_role.provider("finnhub")
    add(
        Requirement(
            id="equity_news",
            order=40,
            title=f"News — {finnhub.label}",
            why=(
                "Company news, insider sentiment and insider transactions. "
                "Without it the news analyst still runs on Google News, with a "
                "thinner evidence base and no insider signal."
            ),
            level="recommended" if "news" in analysts else "optional",
            credentials=_required_fields(finnhub),
        )
    )

    macro_on = "macro" in analysts
    macro = role_requirement(
        "macro",
        level="conditional" if macro_on else "optional",
        order=45,
        because="the macro analyst is enabled" if macro_on else "",
        why=(
            "The macro analyst's entire data source."
            if macro_on
            else "Only read by the macro analyst, which is not enabled."
        ),
    )
    add(macro)

    crypto_on = assets in ("all", "crypto")
    add(
        role_requirement(
            "crypto_news",
            level="recommended" if crypto_on else "optional",
            order=50,
            because=f"asset filter = {assets}" if crypto_on else "",
            why=(
                "News for crypto symbols; equities do not use it."
                if crypto_on
                else "Only used for crypto symbols, which are out of scope."
            ),
        )
    )

    add(
        role_requirement(
            "fallback_market_data", level="optional", order=60,
            why="A fallback for when the primary market-data source fails.",
        )
    )

    return readiness


def setup_steps(readiness: Readiness) -> list[Requirement]:
    """What the wizard should walk through, in order.

    Blocking things first, then the ones that visibly improve an analysis,
    and never the plainly optional ones — a wizard that asks for a FRED key
    while the macro analyst is off is the laundry list again with a progress
    bar on top.

    Ordered by `order` rather than by level, because urgency and sequence
    are different questions: the broker is exactly as required as the model
    and still makes no sense to ask for first.
    """
    return sorted(
        (
            item
            for item in readiness.requirements
            if item.credentials and not item.satisfied and item.level != "optional"
        ),
        key=lambda item: item.order,
    )
