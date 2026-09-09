"""Tests for the panels that read live broker and persistence data.

These callbacks stand between an operator and their money: the positions
and orders tables, the liquidation confirmation, and the persisted decision
timeline. A failure in any of them has to render as a message rather than
an empty panel, and the liquidation path in particular must not act on a
click it cannot attribute to a symbol.
"""

from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest import mock

import dash

from conftest import dash_callback


def _app(register):
    app = dash.Dash(__name__, suppress_callback_exceptions=True)
    register(app)
    return app


class _triggered:
    """Both spellings of the callback context are read in these modules."""

    def __init__(self, prop_id):
        context = SimpleNamespace(triggered=[{"prop_id": prop_id}] if prop_id else [])
        self._patchers = [
            mock.patch.object(dash, "callback_context", context),
            mock.patch.object(dash, "ctx", context),
        ]

    def __enter__(self):
        for patcher in self._patchers:
            patcher.start()
        return self

    def __exit__(self, *exc):
        for patcher in self._patchers:
            patcher.stop()
        return False


class TradingCallbackFixture(unittest.TestCase):
    def setUp(self):
        from webui.callbacks.trading_callbacks import register_trading_callbacks

        self.app = _app(register_trading_callbacks)


class AccountTitleTests(TradingCallbackFixture):
    def _title(self, stored):
        return dash_callback(self.app, "alpaca-account-title.children")(stored)

    def test_paper_mode_is_named_in_the_title(self):
        """Nothing else on the panel distinguishes paper from live."""
        self.assertIn("Paper", self._title({"alpaca-paper": True}))

    def test_live_mode_is_named_in_the_title(self):
        self.assertIn("Live", self._title({"alpaca-paper": "false"}))

    def test_the_stored_flag_is_read_as_text_too(self):
        self.assertIn("Live", self._title({"alpaca-paper": "no"}))
        self.assertIn("Paper", self._title({"alpaca-paper": "yes"}))

    def test_without_a_stored_flag_the_configured_one_is_used(self):
        with mock.patch(
            "tradingagents.dataflows.config.get_alpaca_use_paper", lambda: "false"
        ):
            self.assertIn("Live", self._title(None))


class OrdersPagerTests(TradingCallbackFixture):
    def _page(self, clicks, prop_id='{"page":2,"type":"orders-page-btn"}.n_clicks'):
        from webui.callbacks import trading_callbacks

        with mock.patch.object(
            trading_callbacks,
            "ctx",
            SimpleNamespace(triggered=[{"prop_id": prop_id}]),
        ):
            return dash_callback(self.app, "orders-page-store.data")(clicks)

    def test_clicking_a_page_selects_it(self):
        self.assertEqual(self._page([0, 1]), 2)

    def test_a_prefixed_page_id_is_still_read(self):
        page = self._page(
            [1], prop_id='{"page":"orders-3","type":"orders-page-btn"}.n_clicks'
        )

        self.assertEqual(page, 3)

    def test_no_click_changes_nothing(self):
        self.assertIs(self._page([0, 0]), dash.no_update)
        self.assertIs(self._page(None), dash.no_update)

    def test_an_unparseable_button_id_changes_nothing(self):
        self.assertIs(self._page([1], prop_id="not-json.n_clicks"), dash.no_update)


class AlpacaTableTests(TradingCallbackFixture):
    def _render(self, page_data=None, error=None, orders_page=None):
        from webui.callbacks import trading_callbacks

        def get_page(page=1, page_size=None):
            if error:
                raise error
            return page_data or {"orders": [], "page": page, "total_pages": 1}

        with mock.patch.object(
            trading_callbacks.AlpacaUtils, "get_recent_orders_page", get_page
        ):
            with mock.patch.object(
                trading_callbacks, "render_positions_table", lambda: "positions"
            ):
                with mock.patch.object(
                    trading_callbacks, "render_account_summary", lambda: "summary"
                ):
                    return dash_callback(
                        self.app, "positions-table-container.children"
                    )(0, 0, 0, orders_page)

    def test_the_panels_render_together(self):
        positions, orders, pagination, summary = self._render()

        self.assertEqual(positions, "positions")
        self.assertEqual(summary, "summary")
        self.assertIsNotNone(orders)
        self.assertIsNotNone(pagination)

    def test_the_selected_page_is_requested(self):
        from webui.callbacks import trading_callbacks

        seen = {}

        def get_page(page=1, page_size=None):
            seen["page"] = page
            return {"orders": [], "page": page, "total_pages": 1}

        with mock.patch.object(
            trading_callbacks.AlpacaUtils, "get_recent_orders_page", get_page
        ):
            with mock.patch.object(
                trading_callbacks, "render_positions_table", lambda: "positions"
            ):
                with mock.patch.object(
                    trading_callbacks, "render_account_summary", lambda: "summary"
                ):
                    dash_callback(self.app, "positions-table-container.children")(
                        0, 0, 0, 3
                    )

        self.assertEqual(seen["page"], 3)

    def test_a_failing_orders_fetch_renders_an_error_not_a_blank_table(self):
        _positions, orders, pagination, _summary = self._render(
            error=RuntimeError("credentials rejected")
        )

        self.assertIn("credentials rejected", str(orders))
        self.assertIsNotNone(pagination)


class LiquidationTests(TradingCallbackFixture):
    def _confirm(self, clicks, prop_id='{"index":"NVDA","type":"liquidate-btn"}.n_clicks'):
        with _triggered(prop_id):
            return dash_callback(self.app, "liquidate-confirm.displayed")(clicks)

    def test_clicking_liquidate_asks_for_confirmation_by_symbol(self):
        displayed, message = self._confirm([1])

        self.assertTrue(displayed)
        self.assertIn("NVDA", message)
        self.assertIn("cannot be undone", message)

    def test_no_click_asks_nothing(self):
        self.assertEqual(self._confirm([0, 0]), (False, ""))

    def test_a_click_that_cannot_be_attributed_to_a_symbol_asks_nothing(self):
        """Better to do nothing than to confirm liquidating the wrong name."""
        self.assertEqual(self._confirm([1], prop_id="not-json.n_clicks"), (False, ""))
        self.assertEqual(
            self._confirm([1], prop_id='{"type":"liquidate-btn"}.n_clicks'), (False, "")
        )

    def _liquidate(self, submit, message, result=None, error=None):
        def close_position(symbol):
            if error:
                raise error
            return dict(result or {}, symbol=symbol)

        with mock.patch(
            "tradingagents.dataflows.alpaca_utils.AlpacaUtils.close_position",
            close_position,
        ):
            return dash_callback(self.app, "liquidation-status.children")(
                submit, message
            )

    MESSAGE = "Are you sure you want to liquidate your entire position in NVDA?"

    def test_a_confirmed_liquidation_reports_the_order(self):
        rendered = str(
            self._liquidate(1, self.MESSAGE, {"success": True, "order_id": "abc123"})
        )

        self.assertIn("Successfully liquidated", rendered)
        self.assertIn("abc123", rendered)

    def test_a_refused_liquidation_reports_the_reason(self):
        rendered = str(
            self._liquidate(1, self.MESSAGE, {"success": False, "error": "market closed"})
        )

        self.assertIn("Failed to liquidate", rendered)
        self.assertIn("market closed", rendered)

    def test_a_failing_liquidation_reports_the_exception(self):
        rendered = str(
            self._liquidate(1, self.MESSAGE, error=RuntimeError("broker unreachable"))
        )

        self.assertIn("broker unreachable", rendered)

    def test_an_unconfirmed_dialog_does_nothing(self):
        self.assertEqual(self._liquidate(None, self.MESSAGE), "")

    def test_an_unreadable_message_does_not_liquidate_something_arbitrary(self):
        rendered = str(self._liquidate(1, "malformed"))

        self.assertIn("Error during liquidation", rendered)


class StorageCallbackFixture(unittest.TestCase):
    def setUp(self):
        from webui.callbacks.storage_callbacks import register_storage_callbacks

        self.app = _app(register_storage_callbacks)


SAVE_FIELDS = 37


class SettingsSaveTests(StorageCallbackFixture):
    def _save(self, current=None, **overrides):
        from webui.callbacks import storage_callbacks

        values = [None] * SAVE_FIELDS
        values[0] = overrides.pop("ticker_symbols", "NVDA")
        values[6] = overrides.pop("research_depth", "Medium")
        with mock.patch.object(
            storage_callbacks, "ctx", SimpleNamespace(triggered=[{"prop_id": "x"}])
        ):
            return dash_callback(self.app, "settings-store.data")(*values, current)

    def test_the_typed_symbols_are_normalized_before_storage(self):
        saved = self._save(ticker_symbols=" nvda , aapl ")

        self.assertEqual(saved["ticker_input"], "NVDA, AAPL")

    def test_a_list_of_symbols_is_accepted_too(self):
        saved = self._save(ticker_symbols=["nvda", " aapl "])

        self.assertEqual(saved["ticker_input"], "NVDA, AAPL")

    def test_unset_toggles_are_stored_as_false_rather_than_null(self):
        saved = self._save()

        self.assertIs(saved["loop_enabled"], False)
        self.assertIs(saved["market_hour_enabled"], False)
        self.assertIs(saved["quick_store"], False)

    def test_parallel_tool_calls_default_to_on(self):
        saved = self._save()

        self.assertIs(saved["quick_parallel_tool_calls"], True)
        self.assertIs(saved["deep_parallel_tool_calls"], True)

    def test_the_reasoning_defaults_differ_between_the_two_roles(self):
        saved = self._save()

        self.assertEqual(saved["quick_reasoning_effort"], "low")
        self.assertEqual(saved["deep_reasoning_effort"], "medium")

    def test_an_unchanged_settings_set_is_not_rewritten(self):
        """Rewriting it would feed the store back into its own input."""
        first = self._save()

        self.assertIs(self._save(current=first), first)

    def test_a_changed_setting_is_written(self):
        first = self._save(research_depth="Medium")

        second = self._save(research_depth="Deep", current=first)

        self.assertEqual(second["research_depth"], "Deep")

    def test_an_untriggered_call_leaves_the_store_alone(self):
        from webui.callbacks import storage_callbacks

        values = [None] * SAVE_FIELDS
        with mock.patch.object(
            storage_callbacks, "ctx", SimpleNamespace(triggered=[])
        ):
            saved = dash_callback(self.app, "settings-store.data")(
                *values, {"ticker_input": "KEPT"}
            )

        self.assertEqual(saved["ticker_input"], "KEPT")

    def test_an_untriggered_call_with_no_store_yields_the_defaults(self):
        from webui.callbacks import storage_callbacks
        from webui.utils.storage import get_default_settings

        values = [None] * SAVE_FIELDS
        with mock.patch.object(
            storage_callbacks, "ctx", SimpleNamespace(triggered=[])
        ):
            saved = dash_callback(self.app, "settings-store.data")(*values, None)

        self.assertEqual(saved, get_default_settings())


class SettingsLoadTests(StorageCallbackFixture):
    def _load(self, stored):
        return dash_callback(self.app, "ticker-input.value")(stored)

    def test_stored_settings_are_restored(self):
        restored = self._load({"ticker_input": "AAPL", "research_depth": "Deep"})

        self.assertEqual(restored[0], "AAPL")
        self.assertEqual(restored[6], "Deep")

    def test_an_empty_store_falls_back_to_the_defaults(self):
        from webui.utils.storage import get_default_settings

        restored = self._load(None)
        defaults = get_default_settings()

        self.assertEqual(restored[0], defaults["ticker_input"])

    def test_a_partial_store_fills_the_gaps_from_the_defaults(self):
        from webui.utils.storage import get_default_settings

        restored = self._load({"ticker_input": "AAPL"})

        self.assertEqual(restored[0], "AAPL")
        self.assertEqual(restored[6], get_default_settings()["research_depth"])

    def test_every_bound_component_gets_a_value(self):
        self.assertEqual(len(self._load(None)), 36)


def _summary(**overrides):
    fields = {
        "symbol": "NVDA",
        "status": "filled",
        "broker": "alpaca",
        "order_count": 2,
        "filled_quantity": 10.0,
        "error": None,
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


def _event(category="fill", label="Filled 10 @ 120", details=None):
    from datetime import datetime, timezone

    return SimpleNamespace(
        category=category,
        label=label,
        occurred_at=datetime(2026, 9, 9, 14, 30, tzinfo=timezone.utc),
        details=details if details is not None else {"price": 120.0},
    )


class DecisionExplorerFixture(unittest.TestCase):
    def setUp(self):
        from webui.callbacks.decision_explorer_callbacks import (
            register_decision_explorer_callbacks,
        )

        self.app = _app(register_decision_explorer_callbacks)

    def _runtime(self, repository=None):
        from webui.callbacks import decision_explorer_callbacks

        if repository is None:
            runtime = SimpleNamespace(unit_of_work_factory=None)
        else:
            uow = mock.MagicMock()
            uow.__enter__ = lambda _self: SimpleNamespace(
                decision_explorer=repository
            )
            uow.__exit__ = lambda *a: False
            runtime = SimpleNamespace(unit_of_work_factory=lambda: uow)

        patcher = mock.patch.object(
            decision_explorer_callbacks, "get_persistence_runtime", lambda: runtime
        )
        patcher.start()
        self.addCleanup(patcher.stop)


class DecisionListTests(DecisionExplorerFixture):
    def _decision(self, decision_id="d1", symbol="NVDA", status="filled"):
        from datetime import datetime

        return SimpleNamespace(
            decision_id=decision_id,
            symbol=symbol,
            status=status,
            created_at=datetime(2026, 9, 9, 14, 30),
        )

    def _list(self, decisions, selected=None, **kwargs):
        repository = mock.MagicMock()
        repository.list_decisions.return_value = list(decisions)
        self._runtime(repository)
        result = dash_callback(self.app, "decision-explorer-selection.options")(
            0, 0, kwargs.get("symbol"), kwargs.get("status"), selected
        )
        return result, repository

    def test_a_decision_is_offered_with_its_symbol_and_state(self):
        (options, _value), _repo = self._list([self._decision()])

        self.assertIn("NVDA", options[0]["label"])
        self.assertIn("FILLED", options[0]["label"])
        self.assertEqual(options[0]["value"], "d1")

    def test_the_first_decision_is_selected_by_default(self):
        (_options, value), _repo = self._list([self._decision(), self._decision("d2")])

        self.assertEqual(value, "d1")

    def test_an_existing_selection_survives_a_refresh(self):
        (_options, value), _repo = self._list(
            [self._decision(), self._decision("d2")], selected="d2"
        )

        self.assertEqual(value, "d2")

    def test_a_selection_that_no_longer_exists_falls_back_to_the_first(self):
        (_options, value), _repo = self._list([self._decision()], selected="gone")

        self.assertEqual(value, "d1")

    def test_the_symbol_and_status_filters_reach_the_query(self):
        _result, repository = self._list([], symbol="NVDA", status="filled")

        repository.list_decisions.assert_called_once_with(
            limit=100, symbol="NVDA", status="filled"
        )

    def test_blank_filters_are_sent_as_no_filter(self):
        _result, repository = self._list([], symbol="", status="")

        repository.list_decisions.assert_called_once_with(
            limit=100, symbol=None, status=None
        )

    def test_no_decisions_offers_nothing(self):
        (options, value), _repo = self._list([])

        self.assertEqual(options, [])
        self.assertIsNone(value)

    def test_without_postgres_the_list_is_empty_rather_than_broken(self):
        self._runtime(None)

        options, value = dash_callback(
            self.app, "decision-explorer-selection.options"
        )(0, 0, None, None, None)

        self.assertEqual((options, value), ([], None))


class DecisionDetailTests(DecisionExplorerFixture):
    def _detail(self, summary=None, timeline=(), error=None):
        repository = mock.MagicMock()
        if error:
            repository.get_decision.side_effect = error
        else:
            repository.get_decision.return_value = SimpleNamespace(
                summary=summary or _summary(), timeline=list(timeline)
            )
        self._runtime(repository)
        return str(
            dash_callback(self.app, "decision-explorer-detail.children")("d1")
        )

    def test_the_summary_fields_are_rendered(self):
        rendered = self._detail()

        self.assertIn("NVDA", rendered)
        self.assertIn("FILLED", rendered)
        self.assertIn("alpaca", rendered)

    def test_a_missing_broker_renders_as_a_dash(self):
        rendered = self._detail(_summary(broker=None))

        self.assertIn("'-'", rendered)

    def test_an_errored_decision_shows_its_error(self):
        rendered = self._detail(_summary(error="insufficient buying power"))

        self.assertIn("insufficient buying power", rendered)

    def test_a_timeline_event_is_rendered_with_its_time_and_details(self):
        rendered = self._detail(timeline=[_event()])

        self.assertIn("2026-09-09 14:30:00 UTC", rendered)
        self.assertIn("Filled 10 @ 120", rendered)
        self.assertIn("price: 120.0", rendered)

    def test_each_event_category_gets_a_badge(self):
        for category in ("fill", "outcome", "order", "lifecycle", "something-else"):
            rendered = self._detail(timeline=[_event(category=category)])

            self.assertIn(category.upper(), rendered)

    def test_nested_and_empty_details_are_left_out(self):
        rendered = self._detail(
            timeline=[_event(details={"nested": {"a": 1}, "none": None, "kept": 5})]
        )

        self.assertIn("kept: 5", rendered)
        self.assertNotIn("nested", rendered)
        self.assertNotIn("none:", rendered)

    def test_nothing_selected_says_so(self):
        self._runtime(mock.MagicMock())

        rendered = str(
            dash_callback(self.app, "decision-explorer-detail.children")(None)
        )

        self.assertIn("No persisted decisions found", rendered)

    def test_without_postgres_the_requirement_is_stated(self):
        self._runtime(None)

        rendered = str(
            dash_callback(self.app, "decision-explorer-detail.children")("d1")
        )

        self.assertIn("requires PostgreSQL", rendered)

    def test_a_failing_lookup_renders_the_reason(self):
        rendered = self._detail(error=RuntimeError("connection reset"))

        self.assertIn("Unable to load decision", rendered)
        self.assertIn("connection reset", rendered)


if __name__ == "__main__":
    unittest.main()
