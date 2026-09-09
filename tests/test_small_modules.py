"""Tests for the small seams other tests route around.

None of these is complicated; each is a place where a wrong answer is
quiet. A lazy import that stops being lazy pulls the Dash app — and an
Alpaca account fetch — into an unrelated import. A snapshot adapter that
swallows a broker failure lets execution read an outage as a flat account.
A registry that picks the wrong provider silently changes where every price
comes from.
"""

from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest import mock

from tradingagents.broker.alpaca_snapshot import AlpacaSnapshotProvider
from tradingagents.dataflows import utils as dataflow_utils
from tradingagents.llm_clients.base_client import BaseLLMClient, normalize_content
from tradingagents.marketdata import registry as marketdata_registry
from tradingagents.persistence.runtime import build_persistence_runtime


class WebuiLazyImportTests(unittest.TestCase):
    def test_run_app_is_reachable_from_the_package(self):
        import webui

        self.assertTrue(callable(webui.run_app))

    def test_an_unknown_attribute_is_an_attribute_error(self):
        import webui

        with self.assertRaises(AttributeError):
            webui.no_such_thing

    def test_importing_a_submodule_does_not_build_the_dash_app(self):
        """Building it fetches Alpaca account data at import time, so this
        is checked in a clean interpreter — another test in this process may
        already have imported the app for its own reasons."""
        import subprocess
        import sys

        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys, webui.utils.prompt_capture;"
                " print('webui.app_dash' in sys.modules)",
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "False")


def _position(**overrides):
    fields = {
        "symbol": "NVDA",
        "qty": "10",
        "market_value": "1200.0",
        "avg_entry_price": "100.0",
        "current_price": "120.0",
        "unrealized_pl": "200.0",
        "unrealized_intraday_pl": "25.0",
        "asset_class": "us_equity",
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


def _account(**overrides):
    fields = {
        "equity": "100000.0",
        "last_equity": "99000.0",
        "cash": "25000.0",
        "buying_power": "50000.0",
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


class AlpacaSnapshotTests(unittest.TestCase):
    def _provider(self, *, account=None, positions=(), error=None):
        client = mock.MagicMock()
        if error:
            client.get_account.side_effect = error
            client.get_all_positions.side_effect = error
        else:
            client.get_account.return_value = account or _account()
            client.get_all_positions.return_value = list(positions)

        provider = AlpacaSnapshotProvider()
        provider._client = lambda: client
        return provider

    def test_the_account_balances_are_converted_to_numbers(self):
        snapshot = self._provider().get_portfolio_snapshot()

        self.assertEqual(snapshot.broker, "alpaca")
        self.assertEqual(snapshot.account.equity, 100_000.0)
        self.assertEqual(snapshot.account.last_equity, 99_000.0)
        self.assertEqual(snapshot.account.cash, 25_000.0)
        self.assertEqual(snapshot.account.buying_power, 50_000.0)

    def test_a_position_is_converted_field_by_field(self):
        snapshot = self._provider(positions=[_position()]).get_portfolio_snapshot()
        position = snapshot.positions[0]

        self.assertEqual(position.symbol, "NVDA")
        self.assertEqual(position.quantity, 10.0)
        self.assertEqual(position.market_value, 1_200.0)
        self.assertEqual(position.average_entry_price, 100.0)
        self.assertEqual(position.unrealized_pl, 200.0)
        self.assertEqual(position.asset_class, "us_equity")

    def test_a_flat_account_snapshots_with_no_positions(self):
        self.assertEqual(self._provider().get_portfolio_snapshot().positions, [])

    def test_a_broker_failure_propagates(self):
        """Execution must never read an outage as a flat account."""
        provider = self._provider(error=RuntimeError("credentials rejected"))

        with self.assertRaises(RuntimeError):
            provider.get_portfolio_snapshot()

    def test_a_quote_is_normalized(self):
        with mock.patch(
            "tradingagents.dataflows.alpaca_utils.AlpacaUtils.get_latest_quote",
            lambda symbol: {"bid_price": 99.0, "ask_price": 101.0},
        ):
            quote = AlpacaSnapshotProvider().get_quote_snapshot("NVDA")

        self.assertEqual((quote.bid_price, quote.ask_price), (99.0, 101.0))

    def test_a_missing_quote_field_reads_as_unknown_not_zero(self):
        with mock.patch(
            "tradingagents.dataflows.alpaca_utils.AlpacaUtils.get_latest_quote",
            lambda symbol: {},
        ):
            quote = AlpacaSnapshotProvider().get_quote_snapshot("NVDA")

        self.assertIsNone(quote.bid_price)
        self.assertIsNone(quote.last_price)

    def test_a_last_price_is_taken_from_either_spelling(self):
        for key in ("last_price", "price"):
            with mock.patch(
                "tradingagents.dataflows.alpaca_utils.AlpacaUtils.get_latest_quote",
                lambda symbol, _k=key: {_k: 100.0},
            ):
                quote = AlpacaSnapshotProvider().get_quote_snapshot("NVDA")

            self.assertEqual(quote.last_price, 100.0, key)


class MarketDataRegistryTests(unittest.TestCase):
    def test_both_providers_are_registered(self):
        registry = marketdata_registry.default_market_data_registry()

        self.assertIsNotNone(registry.create("alpaca", {}))

    def test_tradier_needs_both_halves_of_its_credential(self):
        with mock.patch(
            "tradingagents.dataflows.config.get_api_key", lambda *a: ""
        ):
            registry = marketdata_registry.default_market_data_registry()

            with self.assertRaises(ValueError):
                registry.create("tradier", {})

    def test_tradier_is_built_from_its_credentials(self):
        with mock.patch(
            "tradingagents.dataflows.config.get_api_key", lambda *a: "value"
        ):
            registry = marketdata_registry.default_market_data_registry()
            provider = registry.create("tradier", {"tradier_use_sandbox": "true"})

        self.assertIsNotNone(provider)

    def test_the_sandbox_flag_is_read_from_config_then_environment(self):
        with mock.patch(
            "tradingagents.dataflows.config.get_api_key", lambda *a: "value"
        ):
            registry = marketdata_registry.default_market_data_registry()
            with mock.patch.dict(os.environ, {"TRADIER_USE_SANDBOX": "false"}):
                provider = registry.create("tradier", {})

        self.assertFalse(provider.client.sandbox)

    def test_alpaca_is_the_default_provider(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("RESEARCH_MARKET_DATA_PROVIDER", None)

            provider = marketdata_registry.get_research_market_data_provider({})

        self.assertEqual(provider.name, "alpaca")

    def test_the_provider_can_be_chosen_by_configuration(self):
        with mock.patch(
            "tradingagents.dataflows.config.get_api_key", lambda *a: "value"
        ):
            provider = marketdata_registry.get_research_market_data_provider(
                {"research_market_data_provider": "tradier"}
            )

        self.assertEqual(provider.name, "tradier")

    def test_the_environment_can_choose_the_provider(self):
        with mock.patch.dict(
            os.environ, {"RESEARCH_MARKET_DATA_PROVIDER": "alpaca"}
        ):
            provider = marketdata_registry.get_research_market_data_provider({})

        self.assertEqual(provider.name, "alpaca")


class PersistenceRuntimeTests(unittest.TestCase):
    def test_the_default_backend_needs_no_database(self):
        runtime = build_persistence_runtime({})

        self.assertEqual(runtime.backend, "local")
        self.assertIsNone(runtime.unit_of_work_factory)

    def test_closing_a_local_runtime_does_nothing(self):
        build_persistence_runtime({"persistence_backend": "local"}).close()

    def test_an_unknown_backend_is_refused(self):
        with self.assertRaises(ValueError):
            build_persistence_runtime({"persistence_backend": "sqlite"})

    def test_postgres_builds_a_unit_of_work_factory(self):
        import tradingagents.persistence.runtime as runtime_module

        engine = mock.MagicMock()
        with mock.patch.object(
            runtime_module, "create_database_engine", lambda settings: engine
        ):
            with mock.patch.object(
                runtime_module, "create_session_factory", lambda e: "factory"
            ):
                runtime = build_persistence_runtime(
                    {
                        "persistence_backend": "postgres",
                        "database_url": "postgresql://localhost/test",
                    }
                )

        self.assertEqual(runtime.backend, "postgres")
        self.assertIsNotNone(runtime.unit_of_work_factory)

    def test_closing_a_postgres_runtime_disposes_the_engine(self):
        import tradingagents.persistence.runtime as runtime_module

        engine = mock.MagicMock()
        with mock.patch.object(
            runtime_module, "create_database_engine", lambda settings: engine
        ):
            with mock.patch.object(
                runtime_module, "create_session_factory", lambda e: "factory"
            ):
                runtime = build_persistence_runtime(
                    {
                        "persistence_backend": "postgres",
                        "database_url": "postgresql://localhost/test",
                    }
                )

        runtime.close()

        engine.dispose.assert_called_once()

    def test_a_blank_url_falls_back_to_the_environment(self):
        import tradingagents.persistence.runtime as runtime_module

        seen = {}

        def from_env():
            seen["used"] = True
            return runtime_module.DatabaseSettings(url="postgresql://env/test")

        with mock.patch.object(
            runtime_module.DatabaseSettings, "from_env", staticmethod(from_env)
        ):
            with mock.patch.object(
                runtime_module, "create_database_engine", lambda settings: mock.MagicMock()
            ):
                with mock.patch.object(
                    runtime_module, "create_session_factory", lambda e: "factory"
                ):
                    build_persistence_runtime({"persistence_backend": "postgres"})

        self.assertTrue(seen["used"])


class LlmContentNormalizationTests(unittest.TestCase):
    def test_a_plain_string_is_left_alone(self):
        response = SimpleNamespace(content="the answer")

        self.assertEqual(normalize_content(response).content, "the answer")

    def test_structured_text_blocks_are_joined(self):
        response = SimpleNamespace(
            content=[{"type": "text", "text": "first"}, {"type": "text", "text": "second"}]
        )

        self.assertEqual(normalize_content(response).content, "first\nsecond")

    def test_non_text_blocks_are_dropped(self):
        response = SimpleNamespace(
            content=[{"type": "thinking", "text": "hmm"}, {"type": "text", "text": "answer"}]
        )

        self.assertEqual(normalize_content(response).content, "answer")

    def test_bare_strings_in_the_block_list_are_kept(self):
        response = SimpleNamespace(content=["first", {"type": "text", "text": "second"}])

        self.assertEqual(normalize_content(response).content, "first\nsecond")

    def test_empty_blocks_do_not_leave_blank_lines(self):
        response = SimpleNamespace(
            content=[{"type": "text", "text": ""}, {"type": "text", "text": "answer"}]
        )

        self.assertEqual(normalize_content(response).content, "answer")

    def test_a_response_without_content_passes_through(self):
        response = SimpleNamespace()

        self.assertIs(normalize_content(response), response)


class FakeClient(BaseLLMClient):
    provider = None

    def __init__(self, *args, valid=True, **kwargs):
        super().__init__(*args, **kwargs)
        self._valid = valid

    def get_llm(self):
        return "llm"

    def validate_model(self):
        return self._valid


class BaseClientTests(unittest.TestCase):
    def test_the_constructor_records_the_model_and_endpoint(self):
        client = FakeClient("gpt-5.4", base_url="https://x", timeout=30)

        self.assertEqual(client.model, "gpt-5.4")
        self.assertEqual(client.base_url, "https://x")
        self.assertEqual(client.kwargs, {"timeout": 30})

    def test_the_provider_name_comes_from_the_attribute_when_set(self):
        client = FakeClient("m")
        client.provider = "openrouter"

        self.assertEqual(client.get_provider_name(), "openrouter")

    def test_the_provider_name_otherwise_comes_from_the_class(self):
        self.assertEqual(FakeClient("m").get_provider_name(), "fake")

    def test_a_known_model_produces_no_warning(self):
        import warnings

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            FakeClient("m", valid=True).warn_if_unknown_model()

        self.assertEqual(caught, [])

    def test_an_unknown_model_warns_but_does_not_stop_the_run(self):
        import warnings

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            FakeClient("made-up", valid=False).warn_if_unknown_model()

        self.assertEqual(len(caught), 1)
        self.assertIn("made-up", str(caught[0].message))


class AzureClientTests(unittest.TestCase):
    def _client(self, **kwargs):
        from tradingagents.llm_clients.azure_client import AzureOpenAIClient

        return AzureOpenAIClient("gpt-5.4", **kwargs)

    def test_a_missing_api_key_is_refused_by_name(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ValueError) as raised:
                self._client(base_url="https://x").get_llm()

        self.assertIn("AZURE_OPENAI_API_KEY", str(raised.exception))

    def test_a_missing_endpoint_is_refused_by_name(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ValueError) as raised:
                self._client(api_key="k").get_llm()

        self.assertIn("AZURE_OPENAI_ENDPOINT", str(raised.exception))

    def test_the_deployment_defaults_to_the_model_name(self):
        from tradingagents.llm_clients import azure_client

        captured = {}

        with mock.patch.object(
            azure_client, "NormalizedAzureChatOpenAI", lambda **kw: captured.update(kw)
        ):
            with mock.patch.dict(os.environ, {}, clear=True):
                self._client(api_key="k", base_url="https://x").get_llm()

        self.assertEqual(captured["azure_deployment"], "gpt-5.4")
        self.assertEqual(captured["azure_endpoint"], "https://x")

    def test_optional_settings_are_forwarded_only_when_given(self):
        from tradingagents.llm_clients import azure_client

        captured = {}

        with mock.patch.object(
            azure_client, "NormalizedAzureChatOpenAI", lambda **kw: captured.update(kw)
        ):
            with mock.patch.dict(os.environ, {}, clear=True):
                self._client(
                    api_key="k", base_url="https://x", timeout=30
                ).get_llm()

        self.assertEqual(captured["timeout"], 30)
        self.assertNotIn("max_retries", captured)

    def test_any_model_id_is_accepted(self):
        """Azure deployments are named by the operator, not by a catalog."""
        self.assertTrue(self._client().validate_model())


class DataflowUtilTests(unittest.TestCase):
    def test_an_ordinary_ticker_is_path_safe_as_is(self):
        self.assertEqual(dataflow_utils.safe_ticker_component("BRK.B"), "BRK.B")

    def test_a_pair_separator_becomes_an_underscore(self):
        self.assertEqual(dataflow_utils.safe_ticker_component("BTC/USD"), "BTC_USD")

    def test_an_empty_ticker_is_refused(self):
        for value in ("", None, 123):
            with self.assertRaises(ValueError):
                dataflow_utils.safe_ticker_component(value)

    def test_surrounding_whitespace_is_refused(self):
        with self.assertRaises(ValueError):
            dataflow_utils.safe_ticker_component(" NVDA")

    def test_an_overlong_ticker_is_refused(self):
        with self.assertRaises(ValueError):
            dataflow_utils.safe_ticker_component("A" * 65)

    def test_path_traversal_is_refused(self):
        for value in ("..", "../etc", "a\\b", "a b"):
            with self.assertRaises(ValueError):
                dataflow_utils.safe_ticker_component(value)

    def test_a_ticker_that_is_only_punctuation_is_refused(self):
        with self.assertRaises(ValueError):
            dataflow_utils.safe_ticker_component("/")

    def test_disallowed_characters_are_refused(self):
        with self.assertRaises(ValueError):
            dataflow_utils.safe_ticker_component("NV$DA")

    def test_saving_is_a_no_op_without_a_path(self):
        import pandas as pd

        dataflow_utils.save_output(pd.DataFrame({"a": [1]}), "tag", None)

    def test_saving_writes_a_csv_when_given_a_path(self):
        import tempfile
        from pathlib import Path

        import pandas as pd

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.csv"
            dataflow_utils.save_output(pd.DataFrame({"a": [1]}), "tag", str(path))

            self.assertTrue(path.exists())

    def test_the_current_date_is_iso_formatted(self):
        import re

        self.assertRegex(dataflow_utils.get_current_date(), r"^\d{4}-\d{2}-\d{2}$")

    def test_a_weekday_is_its_own_next_weekday(self):
        from datetime import datetime

        friday = datetime(2026, 9, 11)

        self.assertEqual(dataflow_utils.get_next_weekday(friday), friday)

    def test_a_weekend_rolls_forward_to_monday(self):
        from datetime import datetime

        for weekend_day in ("2026-09-12", "2026-09-13"):
            rolled = dataflow_utils.get_next_weekday(weekend_day)

            self.assertEqual(rolled.weekday(), 0, weekend_day)

    def test_a_date_string_is_accepted(self):
        rolled = dataflow_utils.get_next_weekday("2026-09-11")

        self.assertEqual(rolled.strftime("%Y-%m-%d"), "2026-09-11")

    def test_decorating_a_class_wraps_every_method(self):
        def shout(func):
            def wrapper(*args, **kwargs):
                return str(func(*args, **kwargs)).upper()

            return wrapper

        @dataflow_utils.decorate_all_methods(shout)
        class Speaker:
            def hello():
                return "hello"

            def goodbye():
                return "goodbye"

        self.assertEqual(Speaker.hello(), "HELLO")
        self.assertEqual(Speaker.goodbye(), "GOODBYE")


class IntegrationVaultCliTests(unittest.TestCase):
    def test_generate_key_prints_a_usable_fernet_key(self):
        import io
        import contextlib

        from cryptography.fernet import Fernet
        from tradingagents.integrations.__main__ import main

        out = io.StringIO()
        with mock.patch("sys.argv", ["vault", "generate-key"]):
            with contextlib.redirect_stdout(out):
                code = main()

        self.assertEqual(code, 0)
        Fernet(out.getvalue().strip().encode("ascii"))

    def test_an_unknown_command_is_refused(self):
        from tradingagents.integrations.__main__ import main

        with mock.patch("sys.argv", ["vault", "explode"]):
            with self.assertRaises(SystemExit):
                main()


if __name__ == "__main__":
    unittest.main()
