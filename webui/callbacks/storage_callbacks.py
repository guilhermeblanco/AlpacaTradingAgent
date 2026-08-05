"""
Storage callbacks for persisting user settings in localStorage
"""

from dash import Input, Output, State, callback_context as ctx
from webui.utils.storage import get_default_settings


def _parse_symbols(value):
    if isinstance(value, list):
        return [str(symbol).strip().upper() for symbol in value if str(symbol).strip()]
    return [symbol.strip().upper() for symbol in (value or "").split(",") if symbol.strip()]


def register_storage_callbacks(app):
    """Register storage-related callbacks"""

    # Callback to save settings to localStorage when they change
    @app.callback(
        Output("settings-store", "data"),
        [
            Input("ticker-input", "value"),
            Input("analyst-market", "value"),
            Input("analyst-social", "value"),
            Input("analyst-news", "value"),
            Input("analyst-fundamentals", "value"),
            Input("analyst-macro", "value"),
            Input("research-depth", "value"),
            Input("allow-shorts", "value"),
            Input("loop-interval", "value"),
            Input("market-hours-input", "value"),
            Input("trade-after-analyze", "value"),
            Input("trade-dollar-amount", "value"),
            Input("llm-provider", "value"),
            Input("backend-url", "value"),
            Input("output-language", "value"),
            Input("checkpoint-enabled", "value"),
            Input("quick-llm", "value"),
            Input("deep-llm", "value"),
            Input("quick-llm-custom-model", "value"),
            Input("deep-llm-custom-model", "value"),
            Input("google-thinking-level", "value"),
            Input("anthropic-effort", "value"),
            Input("xai-reasoning-effort", "value"),
            Input("quick-llm-reasoning-effort", "value"),
            Input("quick-llm-verbosity", "value"),
            Input("quick-llm-summary", "value"),
            Input("quick-llm-max-output-tokens", "value"),
            Input("quick-llm-store", "value"),
            Input("quick-llm-parallel-tool-calls", "value"),
            Input("deep-llm-reasoning-effort", "value"),
            Input("deep-llm-verbosity", "value"),
            Input("deep-llm-summary", "value"),
            Input("deep-llm-max-output-tokens", "value"),
            Input("deep-llm-store", "value"),
            Input("deep-llm-parallel-tool-calls", "value"),
            Input("loop-enabled", "value"),
            Input("market-hour-enabled", "value"),
        ],
        [
            State("settings-store", "data"),
        ],
        prevent_initial_call=True
    )
    def save_settings(ticker_symbols, analyst_market, analyst_social, analyst_news,
                     analyst_fundamentals, analyst_macro, research_depth, allow_shorts,
                     loop_interval, market_hours_input,
                     trade_after_analyze, trade_dollar_amount,
                     llm_provider, backend_url, output_language, checkpoint_enabled,
                     quick_llm, deep_llm, quick_llm_custom_model, deep_llm_custom_model,
                     google_thinking_level, anthropic_effort, xai_reasoning_effort,
                     quick_reasoning_effort, quick_verbosity, quick_summary, quick_max_output_tokens,
                     quick_store, quick_parallel_tool_calls,
                     deep_reasoning_effort, deep_verbosity, deep_summary, deep_max_output_tokens,
                     deep_store, deep_parallel_tool_calls,
                     loop_enabled, market_hour_enabled,
                     current_settings):
        """Save settings to localStorage store"""
        
        # Don't save if triggered by initial load
        if not ctx.triggered:
            return current_settings or get_default_settings()
        
        new_settings = {
            "ticker_input": ", ".join(_parse_symbols(ticker_symbols)),
            "analyst_market": analyst_market,
            "analyst_social": analyst_social,
            "analyst_news": analyst_news,
            "analyst_fundamentals": analyst_fundamentals,
            "analyst_macro": analyst_macro,
            "research_depth": research_depth,
            "allow_shorts": allow_shorts,
            "loop_enabled": loop_enabled or False,
            "loop_interval": loop_interval,
            "market_hour_enabled": market_hour_enabled or False,
            "market_hours_input": market_hours_input,
            "trade_after_analyze": trade_after_analyze,
            "trade_dollar_amount": trade_dollar_amount,
            "llm_provider": llm_provider,
            "backend_url": backend_url,
            "output_language": output_language,
            "checkpoint_enabled": checkpoint_enabled,
            "quick_llm": quick_llm,
            "deep_llm": deep_llm,
            "quick_llm_custom_model": quick_llm_custom_model or "",
            "deep_llm_custom_model": deep_llm_custom_model or "",
            "google_thinking_level": google_thinking_level or "",
            "anthropic_effort": anthropic_effort or "",
            "xai_reasoning_effort": xai_reasoning_effort or "",
            "quick_reasoning_effort": quick_reasoning_effort or "low",
            "quick_verbosity": quick_verbosity or "low",
            "quick_summary": quick_summary or "auto",
            "quick_max_output_tokens": quick_max_output_tokens,
            "quick_store": quick_store or False,
            "quick_parallel_tool_calls": quick_parallel_tool_calls if quick_parallel_tool_calls is not None else True,
            "deep_reasoning_effort": deep_reasoning_effort or "medium",
            "deep_verbosity": deep_verbosity or "medium",
            "deep_summary": deep_summary or "auto",
            "deep_max_output_tokens": deep_max_output_tokens,
            "deep_store": deep_store or False,
            "deep_parallel_tool_calls": deep_parallel_tool_calls if deep_parallel_tool_calls is not None else True,
        }
        
        # Check if settings actually changed to prevent circular updates
        if current_settings:
            settings_changed = False
            for key, value in new_settings.items():
                if current_settings.get(key) != value:
                    settings_changed = True
                    break
            
            # If no changes, don't update the store to prevent circular callback
            if not settings_changed:
                return current_settings
        
        return new_settings

    # Callback to load settings from localStorage into UI components when page loads/refreshes
    @app.callback(
        [
            Output("ticker-input", "value"),
            Output("analyst-market", "value"),
            Output("analyst-social", "value"),
            Output("analyst-news", "value"),
            Output("analyst-fundamentals", "value"),
            Output("analyst-macro", "value"),
            Output("research-depth", "value"),
            Output("allow-shorts", "value"),
            Output("loop-interval", "value"),
            Output("market-hours-input", "value"),
            Output("trade-after-analyze", "value"),
            Output("trade-dollar-amount", "value"),
            Output("llm-provider", "value"),
            Output("backend-url", "value"),
            Output("output-language", "value"),
            Output("checkpoint-enabled", "value"),
            Output("quick-llm", "value"),
            Output("deep-llm", "value"),
            Output("quick-llm-custom-model", "value"),
            Output("deep-llm-custom-model", "value"),
            Output("google-thinking-level", "value"),
            Output("anthropic-effort", "value"),
            Output("quick-llm-reasoning-effort", "value"),
            Output("quick-llm-verbosity", "value"),
            Output("quick-llm-summary", "value"),
            Output("quick-llm-max-output-tokens", "value"),
            Output("quick-llm-store", "value"),
            Output("quick-llm-parallel-tool-calls", "value"),
            Output("deep-llm-reasoning-effort", "value"),
            Output("deep-llm-verbosity", "value"),
            Output("deep-llm-summary", "value"),
            Output("deep-llm-max-output-tokens", "value"),
            Output("deep-llm-store", "value"),
            Output("deep-llm-parallel-tool-calls", "value"),
            Output("loop-enabled", "value"),
            Output("market-hour-enabled", "value"),
        ],
        [Input("settings-store", "data")],
        prevent_initial_call=False
    )
    def load_settings(stored_settings):
        """Restore UI component values from stored localStorage settings on page load"""
        defaults = get_default_settings()
        s = stored_settings or defaults

        return (
            s.get("ticker_input", defaults.get("ticker_input", "NVDA, AMD, TSLA")),
            s.get("analyst_market", defaults.get("analyst_market", True)),
            s.get("analyst_social", defaults.get("analyst_social", True)),
            s.get("analyst_news", defaults.get("analyst_news", True)),
            s.get("analyst_fundamentals", defaults.get("analyst_fundamentals", True)),
            s.get("analyst_macro", defaults.get("analyst_macro", True)),
            s.get("research_depth", defaults.get("research_depth", "Shallow")),
            s.get("allow_shorts", defaults.get("allow_shorts", False)),
            s.get("loop_interval", defaults.get("loop_interval", 60)),
            s.get("market_hours_input", defaults.get("market_hours_input", "")),
            s.get("trade_after_analyze", defaults.get("trade_after_analyze", False)),
            s.get("trade_dollar_amount", defaults.get("trade_dollar_amount", 4500)),
            s.get("llm_provider", defaults.get("llm_provider", "openai")),
            s.get("backend_url", defaults.get("backend_url", "")),
            s.get("output_language", defaults.get("output_language", "English")),
            s.get("checkpoint_enabled", defaults.get("checkpoint_enabled", False)),
            s.get("quick_llm", defaults.get("quick_llm", "gpt-5.4-nano")),
            s.get("deep_llm", defaults.get("deep_llm", "gpt-5.4-mini")),
            s.get("quick_llm_custom_model", defaults.get("quick_llm_custom_model", "")),
            s.get("deep_llm_custom_model", defaults.get("deep_llm_custom_model", "")),
            s.get("google_thinking_level", defaults.get("google_thinking_level", "")),
            s.get("anthropic_effort", defaults.get("anthropic_effort", "")),
            s.get("quick_reasoning_effort", defaults.get("quick_reasoning_effort", "low")),
            s.get("quick_verbosity", defaults.get("quick_verbosity", "low")),
            s.get("quick_summary", defaults.get("quick_summary", "auto")),
            s.get("quick_max_output_tokens", defaults.get("quick_max_output_tokens", None)),
            s.get("quick_store", defaults.get("quick_store", False)),
            s.get("quick_parallel_tool_calls", defaults.get("quick_parallel_tool_calls", True)),
            s.get("deep_reasoning_effort", defaults.get("deep_reasoning_effort", "medium")),
            s.get("deep_verbosity", defaults.get("deep_verbosity", "medium")),
            s.get("deep_summary", defaults.get("deep_summary", "auto")),
            s.get("deep_max_output_tokens", defaults.get("deep_max_output_tokens", None)),
            s.get("deep_store", defaults.get("deep_store", False)),
            s.get("deep_parallel_tool_calls", defaults.get("deep_parallel_tool_calls", True)),
            s.get("loop_enabled", defaults.get("loop_enabled", False)),
            s.get("market_hour_enabled", defaults.get("market_hour_enabled", False)),
        )
