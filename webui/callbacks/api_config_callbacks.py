"""Secure server-side integration configuration callbacks."""

from __future__ import annotations

import os
from datetime import datetime, timezone

import dash_bootstrap_components as dbc
from dash import Input, Output, State, ctx, html
from dash.exceptions import PreventUpdate

from tradingagents.dataflows.config import get_alpaca_use_paper, get_config, set_config
from tradingagents.integrations import get_integration_vault
from webui.components.api_config_modal import get_api_configs


CONFIG_KEY_BY_ID = {
    "openai": "openai_api_key",
    "google": "google_api_key",
    "anthropic": "anthropic_api_key",
    "xai": "xai_api_key",
    "minimax": "minimax_api_key",
    "deepseek": "deepseek_api_key",
    "dashscope": "dashscope_api_key",
    "zhipu": "zhipu_api_key",
    "openrouter": "openrouter_api_key",
    "azure-openai": "azure_openai_api_key",
    "alpaca-key": "alpaca_api_key",
    "alpaca-secret": "alpaca_secret_key",
    "tradier-token": "tradier_access_token",
    "tradier-account": "tradier_account_id",
    "robinhood-token": "robinhood_mcp_access_token",
    "robinhood-account": "robinhood_account_number",
    "finnhub": "finnhub_api_key",
    "fred": "fred_api_key",
    "coindesk": "coindesk_api_key",
    "alpha-vantage": "alpha_vantage_api_key",
}


def _is_real_env_value(value: str | None) -> bool:
    value = str(value or "").strip()
    return bool(value and not value.lower().startswith("your_"))


def _paper_value() -> bool:
    value = get_alpaca_use_paper()
    return str(value if value is not None else "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def register_api_config_callbacks(app):
    api_configs = get_api_configs()
    api_ids = [item["id"] for item in api_configs]
    config_keys = [CONFIG_KEY_BY_ID[api_id] for api_id in api_ids]

    # The header button opens the Integrations screen now, which shows
    # what is configured rather than one field per provider this build
    # can talk to. This modal is still reachable from Set up, because it
    # is how a plain vault entry is set — the arrangement a deployment
    # made before instances existed, which still resolves underneath
    # them. It goes when nothing needs that path any more.
    @app.callback(
        Output("api-config-modal", "is_open"),
        Input("close-api-config-btn", "n_clicks"),
        State("api-config-modal", "is_open"),
        prevent_initial_call=True,
    )
    def toggle_api_config_modal(close_clicks, is_open):
        if ctx.triggered_id == "close-api-config-btn":
            return False
        return is_open

    for api_config in api_configs:
        api_id = api_config["id"]

        @app.callback(
            Output(f"api-input-{api_id}", "type"),
            Output(f"api-toggle-icon-{api_id}", "className"),
            Input(f"api-toggle-{api_id}", "n_clicks"),
            State(f"api-input-{api_id}", "type"),
            prevent_initial_call=True,
        )
        def toggle_password_visibility(n_clicks, current_type, _api_id=api_id):
            if not n_clicks:
                raise PreventUpdate
            visible = current_type == "password"
            return (
                "text" if visible else "password",
                "fas fa-eye-slash" if visible else "fas fa-eye",
            )

    @app.callback(
        [
            *[Output(f"api-input-{api_id}", "value") for api_id in api_ids],
            Output("api-alpaca-paper", "value"),
            *[Output(f"api-status-{api_id}", "color") for api_id in api_ids],
            *[Output(f"api-status-{api_id}", "title") for api_id in api_ids],
            Output("integration-execution-broker", "value"),
            Output("integration-market-data-provider", "value"),
            Output("env-file-status", "children"),
        ],
        Input("api-config-modal", "is_open"),
        Input("api-keys-store", "data"),
    )
    def load_integration_status(is_open, revision):
        vault = get_integration_vault()
        vaulted = vault.statuses(config_keys) if vault is not None else {}
        colors = []
        titles = []
        vault_count = 0
        env_count = 0
        for api, config_key in zip(api_configs, config_keys):
            status = vaulted.get(config_key)
            if status is not None and status.configured:
                source = "Encrypted vault"
                vault_count += 1
            elif _is_real_env_value(os.getenv(api["env_var"])):
                source = "Environment"
                env_count += 1
            else:
                source = "Not configured"
            colors.append("success" if source != "Not configured" else "outline-secondary")
            titles.append(source)

        if vault is None:
            summary = dbc.Alert(
                "Encrypted storage is unavailable. Set INTEGRATION_VAULT_KEY and DATABASE_URL; environment credentials remain usable.",
                color="warning",
                className="py-2 mb-0",
            )
        else:
            summary = dbc.Alert(
                [
                    html.I(className="fas fa-lock me-2"),
                    f"Encrypted vault ready: {vault_count} stored, {env_count} environment fallback.",
                ],
                color="success",
                className="py-2 mb-0",
            )
        config = get_config() or {}
        return tuple("" for _ in api_ids) + (
            _paper_value(),
            *colors,
            *titles,
            config.get("execution_broker", "alpaca"),
            config.get("research_market_data_provider", "alpaca"),
            summary,
        )

    @app.callback(
        Output("api-keys-store", "data"),
        Output("api-save-status", "children"),
        Input("save-api-keys-btn", "n_clicks"),
        Input("clear-api-keys-btn", "n_clicks"),
        [
            *[State(f"api-input-{api_id}", "value") for api_id in api_ids],
            State("api-alpaca-paper", "value"),
            State("integration-execution-broker", "value"),
            State("integration-market-data-provider", "value"),
        ],
        prevent_initial_call=True,
    )
    def mutate_integrations(save_clicks, clear_clicks, *values):
        vault = get_integration_vault()
        revision = {"revision": datetime.now(timezone.utc).isoformat()}
        if vault is None:
            return revision, dbc.Alert(
                "Encrypted storage is not configured. No credentials were saved.",
                color="danger",
                className="py-2",
            )

        if ctx.triggered_id == "clear-api-keys-btn":
            removed = vault.delete_many(
                config_keys + ["alpaca_use_paper"], actor="webui"
            )
            message = (
                f"Disconnected {removed} stored integration value(s). "
                "Environment fallbacks were not changed."
            )
        elif ctx.triggered_id == "save-api-keys-btn":
            secret_values = values[: len(api_ids)]
            paper = bool(values[len(api_ids)])
            execution_broker = values[len(api_ids) + 1] or "alpaca"
            market_data_provider = values[len(api_ids) + 2] or "alpaca"
            updates = {
                config_key: value
                for config_key, value in zip(config_keys, secret_values)
                if str(value or "").strip()
            }
            updates["alpaca_use_paper"] = "true" if paper else "false"
            updates["execution_broker"] = execution_broker
            updates["research_market_data_provider"] = market_data_provider
            changed = vault.set_many(updates, actor="webui")
            set_config(
                {
                    "execution_broker": execution_broker,
                    "research_market_data_provider": market_data_provider,
                }
            )
            message = f"Saved {changed} encrypted integration value(s)."
        else:
            raise PreventUpdate

        return revision, dbc.Alert(message, color="success", className="py-2")

    @app.callback(
        Output("integration-health-results", "children"),
        Input("test-broker-connection-btn", "n_clicks"),
        State("integration-execution-broker", "value"),
        prevent_initial_call=True,
    )
    def test_broker_connection(n_clicks, broker):
        if not n_clicks:
            raise PreventUpdate
        from tradingagents.broker.preflight import certify_broker_runtime
        from tradingagents.broker.registry import get_execution_broker_runtime

        try:
            runtime = get_execution_broker_runtime(
                {**(get_config() or {}), "execution_broker": broker or "alpaca"}
            )
            report = certify_broker_runtime(
                runtime,
                symbol="AAPL",
                require_paper=False,
            )
            rows = [
                html.Div(
                    [
                        html.I(
                            className=(
                                "fas fa-check-circle text-success me-2"
                                if check.status.value == "pass"
                                else "fas fa-exclamation-circle text-warning me-2"
                                if check.status.value == "warn"
                                else "fas fa-times-circle text-danger me-2"
                            )
                        ),
                        check.message,
                    ],
                    className="small mb-1",
                )
                for check in report.checks
            ]
            return dbc.Alert(
                rows,
                color="success" if report.ready else "danger",
                className="py-2 mb-0",
            )
        except Exception as exc:
            return dbc.Alert(f"Connection test failed: {exc}", color="danger")
