"""
Storage utility for persisting user settings in localStorage
"""

import os
from typing import Dict, Any

# Default settings structure
DEFAULT_SETTINGS = {
    "ticker_input": "NVDA, AMD, TSLA",
    "analyst_market": True,
    "analyst_social": True,
    "analyst_news": True,
    "analyst_fundamentals": True,
    "analyst_macro": True,
    "research_depth": "Shallow",
    "allow_shorts": False,
    "loop_enabled": False,
    "loop_interval": 60,
    "market_hour_enabled": False,
    "market_hours_input": "",
    "trade_after_analyze": False,
    "trade_dollar_amount": 4500,
    "llm_provider": os.getenv("LLM_PROVIDER", "openai"),
    "backend_url": os.getenv("OPENAI_BASE_URL", ""),
    "output_language": "English",
    "checkpoint_enabled": False,
    "quick_llm": "gpt-5.4-nano",
    "deep_llm": "gpt-5.4-mini",
    "quick_llm_custom_model": "",
    "deep_llm_custom_model": "",
    "google_thinking_level": "",
    "anthropic_effort": "",
    "xai_reasoning_effort": "",
    "quick_reasoning_effort": "low",
    "quick_verbosity": "low",
    "quick_summary": "auto",
    "quick_max_output_tokens": None,
    "quick_store": False,
    "quick_parallel_tool_calls": True,
    "deep_reasoning_effort": "medium",
    "deep_verbosity": "medium",
    "deep_summary": "auto",
    "deep_max_output_tokens": None,
    "deep_store": False,
    "deep_parallel_tool_calls": True,
}

def get_default_settings() -> Dict[str, Any]:
    """Get the default settings structure"""
    defaults = DEFAULT_SETTINGS.copy()
    defaults["llm_provider"] = os.getenv("LLM_PROVIDER", defaults.get("llm_provider", "openai"))
    defaults["backend_url"] = os.getenv("OPENAI_BASE_URL", defaults.get("backend_url", ""))
    return defaults


def create_storage_store_component():
    """Create a dcc.Store component for localStorage persistence"""
    from dash import dcc
    return dcc.Store(id='settings-store', storage_type='local', data=get_default_settings())


def create_api_keys_store_component():
    """Create a memory-only revision signal for integration callbacks."""
    from dash import dcc
    return dcc.Store(id='api-keys-store', storage_type='memory', data={"revision": 0})
