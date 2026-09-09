from __future__ import annotations

import pytest
from pydantic import ValidationError

from tradingagents.configuration import validate_application_config
from tradingagents.dataflows.config import get_config, set_config


def test_partial_config_is_merged_with_defaults_and_coerced() -> None:
    config = validate_application_config(
        {"execution_broker": " TRADIER ", "batch_max_workers": "8"}
    )

    assert config["execution_broker"] == "tradier"
    assert config["batch_max_workers"] == 8
    assert config["quick_think_llm"]


@pytest.mark.parametrize(
    ("override", "field"),
    [
        ({"execution_broker": "unsupported"}, "execution_broker"),
        ({"research_market_data_provider": "execution-broker"}, "research_market_data_provider"),
        ({"max_symbol_concentration_pct": 101}, "max_symbol_concentration_pct"),
        ({"batch_max_workers": 0}, "batch_max_workers"),
    ],
)
def test_invalid_operational_config_fails_early(override, field) -> None:
    with pytest.raises(ValidationError) as raised:
        validate_application_config(override)

    assert field in str(raised.value)


def test_unknown_extension_keys_are_preserved() -> None:
    config = validate_application_config({"custom_strategy_flag": "enabled"})

    assert config["custom_strategy_flag"] == "enabled"


def test_global_config_returns_a_deep_copy() -> None:
    original = get_config()
    try:
        set_config({"quick_llm_params": {"reasoning_effort": "high"}})
        first = get_config()
        first["quick_llm_params"]["reasoning_effort"] = "mutated"

        assert get_config()["quick_llm_params"]["reasoning_effort"] == "high"
    finally:
        set_config(original)
