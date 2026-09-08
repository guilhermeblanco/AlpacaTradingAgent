from __future__ import annotations


def test_api_key_store_is_memory_only() -> None:
    from webui.utils.storage import create_api_keys_store_component

    store = create_api_keys_store_component()
    assert store.storage_type == "memory"
    assert store.data == {"revision": 0}


def test_legacy_browser_credential_store_is_purged() -> None:
    from pathlib import Path

    script = (
        Path(__file__).resolve().parents[1]
        / "webui"
        / "assets"
        / "purge_legacy_api_keys.js"
    ).read_text()
    assert 'localStorage.removeItem("api-keys-store")' in script


def test_integrations_modal_does_not_claim_browser_secret_storage() -> None:
    from webui.components.api_config_modal import create_api_config_modal

    rendered = str(create_api_config_modal())
    assert "browser's local storage" not in rendered
    assert "never returned to the browser" in rendered
    assert "Tradier Access Token" in rendered
    assert "Robinhood MCP Access Token" in rendered
    assert "leave blank to keep current" in rendered


def test_integration_callbacks_register_without_secret_store_outputs() -> None:
    import dash

    from webui.callbacks.api_config_callbacks import register_api_config_callbacks

    app = dash.Dash(__name__, suppress_callback_exceptions=True)
    register_api_config_callbacks(app)
    outputs = " ".join(app.callback_map)
    assert "api-config-modal.is_open" in outputs
    assert "api-save-status.children" in outputs
    assert "api-input-openai.value" in outputs
