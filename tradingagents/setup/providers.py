"""Which third parties this build can talk to, and what each one needs.

The application already has adapter registries — `BrokerRegistry`,
`MarketDataProviderRegistry`, `create_llm_client` — that know how to
*construct* a provider. None of them knows what a person has to type in
to make one work, so the setup screens had that knowledge hard-coded, one
provider at a time, and it drifted.

This module is the description layer for those registries: for each role,
which providers exist and what fields each needs. The registries stay the
implementation layer. Keeping them apart is what lets a new provider be
added in one place and appear in the wizard, the readiness model and the
integrations screen without any of them being edited.

It drifted in exactly the way you would expect, and the drift is why the
provider names below are asserted against the factories rather than typed
out again:

  * The setup catalogue offered `dashscope` and `zhipu`. The LLM factory
    accepts `qwen` and `glm`. Choosing either from the old list produced
    `Unsupported LLM provider` at the first model call.
  * It offered `local`, `lmstudio` and `openai_compatible` as key-free
    local providers. The factory accepts `ollama` and `local_openai`.

A test walks every provider here through the real factory's accepted set,
so the next rename breaks a test rather than a deployment.

Roles differ in whether choosing is even the right verb. A broker is one
of three. News is several sources used *together* — Finnhub, Google News
and the hosted search each contribute — so its role is a set to enable
rather than a choice to make. `Role.selection` says which.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

FieldType = Literal["secret", "text", "bool", "choice"]
Selection = Literal["one", "many"]


@dataclass(frozen=True)
class Field:
    """One thing a person types, or ticks.

    `key` is the name `get_api_key` and the config dict both know it by,
    which is why it is not a display concern: the same string addresses
    the vault, the environment (uppercased) and the runtime settings.
    """

    key: str
    label: str
    type: FieldType = "secret"
    help: str = ""
    default: object = None
    required: bool = True
    placeholder: str = ""
    obtain_url: str = ""
    choices: tuple[tuple[str, str], ...] = ()

    @property
    def env_var(self) -> str:
        return self.key.upper()

    @property
    def secret(self) -> bool:
        return self.type == "secret"


@dataclass(frozen=True)
class ProviderSpec:
    """One third party, and what it needs before it will answer."""

    id: str
    label: str
    blurb: str
    fields: tuple[Field, ...] = ()
    #: Shown when a provider needs no credential, to say why rather than
    #: leaving an empty form that looks broken.
    note: str = ""

    @property
    def needs_credentials(self) -> bool:
        return any(item.required for item in self.fields)


@dataclass(frozen=True)
class Role:
    """A job some third party has to do."""

    id: str
    label: str
    blurb: str
    #: The config key holding the current choice. Empty for a role whose
    #: sources are enabled together rather than chosen between.
    setting: str
    providers: tuple[ProviderSpec, ...]
    selection: Selection = "one"
    default: str = ""
    #: Said plainly where it is true: a picker with one entry is a seam,
    #: not a choice, and pretending otherwise wastes the reader's time.
    single_implementation_note: str = ""

    def provider(self, provider_id: str) -> Optional[ProviderSpec]:
        return next(
            (item for item in self.providers if item.id == provider_id), None
        )

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(item.id for item in self.providers)


def _key(key: str, label: str, url: str = "", placeholder: str = "") -> Field:
    return Field(key, label, "secret", obtain_url=url, placeholder=placeholder)


# ── Models ───────────────────────────────────────────────────────────────────
# Ids are the strings `create_llm_client` dispatches on. Nothing else is a
# valid id, whatever the vendor calls itself.

MODEL_PROVIDERS: tuple[ProviderSpec, ...] = (
    ProviderSpec(
        "openai", "OpenAI", "GPT models. The default.",
        (_key("openai_api_key", "API key",
              "https://platform.openai.com/api-keys", "sk-..."),),
    ),
    ProviderSpec(
        "anthropic", "Anthropic", "Claude models.",
        (_key("anthropic_api_key", "API key",
              "https://console.anthropic.com/settings/keys", "sk-ant-..."),),
    ),
    ProviderSpec(
        "google", "Google", "Gemini models, via AI Studio.",
        (_key("google_api_key", "API key",
              "https://aistudio.google.com/app/apikey"),),
    ),
    ProviderSpec(
        "deepseek", "DeepSeek", "OpenAI-compatible endpoint.",
        (_key("deepseek_api_key", "API key",
              "https://platform.deepseek.com/api_keys"),),
    ),
    ProviderSpec(
        "openrouter", "OpenRouter", "One key, many vendors' models.",
        (_key("openrouter_api_key", "API key", "https://openrouter.ai/settings/keys"),),
    ),
    ProviderSpec(
        "xai", "xAI", "Grok models, OpenAI-compatible.",
        (_key("xai_api_key", "API key", "https://console.x.ai/"),),
    ),
    ProviderSpec(
        "minimax", "MiniMax", "OpenAI-compatible endpoint.",
        (_key("minimax_api_key", "API key",
              "https://platform.minimax.io/user-center/basic-information/interface-key"),),
    ),
    # `qwen`, not `dashscope`, and `glm`, not `zhipu`: these are the ids the
    # factory dispatches on. The environment variables keep the vendor's
    # own naming, which is the mismatch that broke the old catalogue.
    ProviderSpec(
        "qwen", "Qwen (DashScope)", "Alibaba's models, OpenAI-compatible.",
        (_key("dashscope_api_key", "API key",
              "https://dashscope.console.aliyun.com/apiKey"),),
    ),
    ProviderSpec(
        "glm", "GLM (Zhipu)", "Zhipu's models, OpenAI-compatible.",
        (_key("zhipu_api_key", "API key",
              "https://bigmodel.cn/usercenter/proj-mgmt/apikeys"),),
    ),
    ProviderSpec(
        "azure", "Azure OpenAI", "OpenAI models on your Azure resource.",
        (
            _key("azure_openai_api_key", "API key", "https://portal.azure.com/"),
            Field("azure_openai_endpoint", "Endpoint", "text",
                  placeholder="https://your-resource.openai.azure.com/"),
            Field("azure_openai_deployment_name", "Deployment name", "text"),
            Field("azure_openai_api_version", "API version", "text",
                  default="2024-12-01-preview", required=False),
        ),
    ),
    ProviderSpec(
        "ollama", "Ollama", "Models on a machine you run.",
        (
            Field("backend_url", "Endpoint", "text", required=False,
                  default="http://localhost:11434/v1",
                  help="Where Ollama is listening."),
        ),
        note="Runs locally, so there is no key to set.",
    ),
    ProviderSpec(
        "local_openai", "Local OpenAI-compatible",
        "Anything speaking the OpenAI API — LM Studio, vLLM, llama.cpp.",
        (
            Field("backend_url", "Endpoint", "text",
                  default="http://localhost:1234/v1",
                  help="The server's /v1 base URL."),
            _key("openai_api_key", "API key"),
        ),
        note="Most local servers ignore the key; send any non-empty string.",
    ),
)

# ── Brokers ──────────────────────────────────────────────────────────────────
# Ids are what `BrokerRegistry.register` was called with.

BROKER_PROVIDERS: tuple[ProviderSpec, ...] = (
    ProviderSpec(
        "alpaca", "Alpaca",
        "Equities and crypto. Market data and execution in one, and a "
        "paper account is free.",
        (
            _key("alpaca_api_key", "Key ID",
                 "https://app.alpaca.markets/paper/dashboard/overview"),
            _key("alpaca_secret_key", "Secret key",
                 "https://app.alpaca.markets/paper/dashboard/overview"),
            Field("alpaca_use_paper", "Use the paper account", "bool",
                  default=True, required=False,
                  help="Off means orders reach the live account."),
        ),
    ),
    ProviderSpec(
        "tradier", "Tradier", "Equities and options.",
        (
            _key("tradier_access_token", "Access token",
                 "https://documentation.tradier.com/brokerage-api/getting-started"),
            Field("tradier_account_id", "Account id", "text"),
            Field("tradier_use_sandbox", "Use the sandbox", "bool",
                  default=True, required=False),
        ),
    ),
    ProviderSpec(
        "robinhood", "Robinhood", "Through the Robinhood MCP bridge.",
        (
            _key("robinhood_mcp_access_token", "MCP access token",
                 "https://robinhood.com/"),
            Field("robinhood_account_number", "Account number", "text",
                  required=False),
        ),
    ),
)

MARKET_DATA_PROVIDERS: tuple[ProviderSpec, ...] = (
    ProviderSpec(
        "alpaca", "Alpaca", "Bars and quotes from the execution broker.",
        (), note="Uses the Alpaca credentials configured for the broker.",
    ),
    ProviderSpec(
        "tradier", "Tradier", "Bars and quotes from Tradier.",
        (), note="Uses the Tradier credentials configured for the broker.",
    ),
)

# ── Data sources ─────────────────────────────────────────────────────────────

NEWS_SOURCES: tuple[ProviderSpec, ...] = (
    ProviderSpec(
        "finnhub", "Finnhub",
        "Company news, insider sentiment and insider transactions.",
        (_key("finnhub_api_key", "API key", "https://finnhub.io/register"),),
    ),
    ProviderSpec(
        "google_news", "Google News", "Headlines, date-bounded.",
        (), note="No key. Always available, and the fallback when others are not.",
    ),
    ProviderSpec(
        "hosted_search", "Hosted web search",
        "The model provider's own search, for current dates only.",
        (), note="Uses the model provider's key. Stands down for past dates.",
    ),
)

MACRO_SOURCES: tuple[ProviderSpec, ...] = (
    ProviderSpec(
        "fred", "FRED", "Federal Reserve economic series.",
        (_key("fred_api_key", "API key",
              "https://fred.stlouisfed.org/docs/api/api_key.html"),),
    ),
)

CRYPTO_NEWS_SOURCES: tuple[ProviderSpec, ...] = (
    ProviderSpec(
        "coindesk", "CryptoCompare / CoinDesk", "News for crypto symbols.",
        (_key("coindesk_api_key", "API key",
              "https://www.cryptocompare.com/cryptopian/api-keys"),),
    ),
)

FALLBACK_DATA_SOURCES: tuple[ProviderSpec, ...] = (
    ProviderSpec(
        "alpha_vantage", "Alpha Vantage",
        "A fallback when the primary market-data source fails.",
        (_key("alpha_vantage_api_key", "API key",
              "https://www.alphavantage.co/support/#api-key"),),
    ),
)


ROLES: tuple[Role, ...] = (
    Role(
        "model", "Model provider",
        "Every analyst, researcher and the risk manager reach a model.",
        "llm_provider", MODEL_PROVIDERS, default="openai",
    ),
    Role(
        "broker", "Broker",
        "Positions, buying power, and where an order would go.",
        "execution_broker", BROKER_PROVIDERS, default="alpaca",
    ),
    Role(
        "market_data", "Market data",
        "Bars and quotes for the analysts.",
        "research_market_data_provider", MARKET_DATA_PROVIDERS,
        default="alpaca",
    ),
    Role(
        "news", "News",
        "Evidence for the news and social analysts.",
        "", NEWS_SOURCES, selection="many",
    ),
    Role(
        "macro", "Macro data",
        "The macro analyst's data source.",
        "macro_provider", MACRO_SOURCES, default="fred",
        single_implementation_note=(
            "FRED is the only macro source implemented. The seam is here, so "
            "a second one is a registration rather than a rewrite."
        ),
    ),
    Role(
        "crypto_news", "Crypto news",
        "News for crypto symbols; equities do not use it.",
        "crypto_news_provider", CRYPTO_NEWS_SOURCES, default="coindesk",
        single_implementation_note=(
            "CryptoCompare is the only crypto news source implemented."
        ),
    ),
    Role(
        "fallback_market_data", "Fallback market data",
        "Used only when the primary source fails.",
        "fallback_market_data_provider", FALLBACK_DATA_SOURCES,
        default="alpha_vantage",
        single_implementation_note=(
            "Alpha Vantage is the only fallback implemented."
        ),
    ),
)


def role(role_id: str) -> Optional[Role]:
    return next((item for item in ROLES if item.id == role_id), None)


def selected(role_id: str, config) -> str:
    """Which provider a configuration has chosen for a role."""
    item = role(role_id)
    if item is None or not item.setting:
        return ""
    chosen = str((config or {}).get(item.setting) or item.default).strip().lower()
    return chosen if item.provider(chosen) else item.default


def spec(role_id: str, config) -> Optional[ProviderSpec]:
    item = role(role_id)
    return item.provider(selected(role_id, config)) if item else None


def all_fields() -> tuple[Field, ...]:
    """Every field any provider could ask for, deduplicated by key."""
    seen: dict[str, Field] = {}
    for item in ROLES:
        for provider in item.providers:
            for entry in provider.fields:
                seen.setdefault(entry.key, entry)
    return tuple(seen.values())
