"""Tests for the Robinhood MCP adapter.

Robinhood is reached over a streamable-HTTP MCP endpoint rather than a
normal REST client, so this adapter owns the JSON-RPC framing, the
session handshake, and the several response shapes the tools return. Every
snapshot the agents read from a Robinhood account passes through it.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from tradingagents.broker.robinhood import (
    RobinhoodMCPClient,
    RobinhoodSnapshotProvider,
    _rows,
    load_robinhood_access_token,
)


def _response(body, *, status=200, headers=None):
    return SimpleNamespace(
        status_code=status,
        text=body if isinstance(body, str) else json.dumps(body),
        headers=headers or {},
    )


class TokenLoadingTests(unittest.TestCase):
    def test_an_explicit_token_is_used(self):
        self.assertEqual(load_robinhood_access_token(access_token="abc"), "abc")

    def test_a_token_file_is_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "token.json"
            path.write_text(json.dumps({"access_token": "from-file"}), encoding="utf-8")

            self.assertEqual(
                load_robinhood_access_token(token_path=str(path)), "from-file"
            )

    def test_no_token_anywhere_is_refused(self):
        with self.assertRaises(ValueError):
            load_robinhood_access_token()

    def test_a_token_file_without_a_token_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "token.json"
            path.write_text(json.dumps({"other": "value"}), encoding="utf-8")

            with self.assertRaises(ValueError):
                load_robinhood_access_token(token_path=str(path))


class RowExtractionTests(unittest.TestCase):
    """MCP tools return their rows under several different keys."""

    def test_a_bare_list_is_returned(self):
        self.assertEqual(_rows([{"a": 1}], "results"), [{"a": 1}])

    def test_rows_are_found_under_a_named_key(self):
        self.assertEqual(_rows({"results": [{"a": 1}]}, "results"), [{"a": 1}])

    def test_the_first_matching_key_wins(self):
        payload = {"positions": [{"a": 1}], "results": [{"b": 2}]}

        self.assertEqual(_rows(payload, "positions", "results"), [{"a": 1}])

    def test_non_dict_entries_are_dropped(self):
        self.assertEqual(_rows([{"a": 1}, "junk", None], "results"), [{"a": 1}])

    def test_an_unrecognized_shape_yields_nothing(self):
        self.assertEqual(_rows({"unexpected": 1}, "results"), [])
        self.assertEqual(_rows(None, "results"), [])


class ClientTests(unittest.TestCase):
    def test_a_token_is_required(self):
        with self.assertRaises(ValueError):
            RobinhoodMCPClient(access_token="")

    def test_request_ids_increment(self):
        client = RobinhoodMCPClient(access_token="t", transport=lambda *a, **k: None)

        self.assertEqual([client._id(), client._id()], [1, 2])

    def test_a_plain_json_body_is_decoded(self):
        decoded = RobinhoodMCPClient._decode(_response({"result": {"ok": True}}))

        self.assertEqual(decoded, {"result": {"ok": True}})

    def test_an_empty_body_decodes_to_nothing(self):
        self.assertEqual(RobinhoodMCPClient._decode(_response("")), {})

    def test_a_server_sent_event_stream_is_decoded(self):
        body = 'event: message\ndata: {"result": {"ok": true}}\n\n'

        decoded = RobinhoodMCPClient._decode(_response(body))

        self.assertEqual(decoded, {"result": {"ok": True}})

    def test_the_last_event_wins_in_a_stream(self):
        body = 'event: message\ndata: {"n": 1}\nevent: message\ndata: {"n": 2}\n'

        self.assertEqual(RobinhoodMCPClient._decode(_response(body)), {"n": 2})

    def test_a_non_object_body_is_refused(self):
        with self.assertRaises(RuntimeError):
            RobinhoodMCPClient._decode(_response("[1, 2, 3]"))

    def test_an_http_error_is_reported_with_its_status(self):
        client = RobinhoodMCPClient(
            access_token="t",
            transport=lambda *a, **k: _response("denied", status=403),
        )

        with self.assertRaises(RuntimeError) as raised:
            client._post({})

        self.assertIn("403", str(raised.exception))

    def test_the_session_id_is_captured_and_resent(self):
        seen = []

        def transport(url, json=None, headers=None, timeout=None):
            seen.append(headers.get("Mcp-Session-Id"))
            return _response({}, headers={"Mcp-Session-Id": "session-1"})

        client = RobinhoodMCPClient(access_token="t", transport=transport)
        client._post({})
        client._post({})

        self.assertEqual(seen, [None, "session-1"])
        self.assertEqual(client.session_id, "session-1")

    def test_the_token_is_sent_as_a_bearer_credential(self):
        captured = {}

        def transport(url, json=None, headers=None, timeout=None):
            captured.update(headers)
            return _response({})

        RobinhoodMCPClient(access_token="secret", transport=transport)._post({})

        self.assertEqual(captured["Authorization"], "Bearer secret")


class ToolCallTests(unittest.TestCase):
    def _client(self, responses):
        queue = list(responses)

        def transport(url, json=None, headers=None, timeout=None):
            return _response(queue.pop(0), headers={"Mcp-Session-Id": "s"})

        return RobinhoodMCPClient(access_token="t", transport=transport)

    def test_the_handshake_runs_before_the_first_tool_call(self):
        client = self._client([{}, {}, {"result": {"structuredContent": {"data": [1]}}}])

        self.assertEqual(client.call_tool("get_accounts"), [1])

    def test_a_json_rpc_error_is_raised(self):
        client = self._client([{}, {}, {"error": {"message": "unauthorized"}}])

        with self.assertRaises(RuntimeError) as raised:
            client.call_tool("get_accounts")

        self.assertIn("unauthorized", str(raised.exception))

    def test_a_tool_level_error_is_raised(self):
        client = self._client([{}, {}, {"result": {"isError": True}}])

        with self.assertRaises(RuntimeError):
            client.call_tool("get_accounts")

    def test_structured_content_without_a_data_key_is_returned_whole(self):
        client = self._client(
            [{}, {}, {"result": {"structuredContent": {"accounts": [1]}}}]
        )

        self.assertEqual(client.call_tool("get_accounts"), {"accounts": [1]})

    def test_text_content_is_parsed_as_json(self):
        client = self._client(
            [
                {},
                {},
                {"result": {"content": [{"text": json.dumps({"data": [{"a": 1}]})}]}},
            ]
        )

        self.assertEqual(client.call_tool("get_accounts"), [{"a": 1}])

    def test_a_result_with_neither_shape_is_returned_as_is(self):
        client = self._client([{}, {}, {"result": {"raw": 1}}])

        self.assertEqual(client.call_tool("get_accounts"), {"raw": 1})


class SnapshotProviderTests(unittest.TestCase):
    ACCOUNTS = {
        "accounts": [
            {"account_number": "111", "state": "inactive", "agentic_allowed": True},
            {"account_number": "222", "state": "active", "agentic_allowed": True},
        ]
    }

    def _provider(self, responses, **kwargs):
        client = mock.MagicMock()
        client.call_tool.side_effect = lambda name, args=None: responses[name]
        return RobinhoodSnapshotProvider(client, **kwargs), client

    def test_an_agentic_account_is_selected(self):
        provider, _client = self._provider({"get_accounts": self.ACCOUNTS})

        self.assertEqual(provider._account()["account_number"], "222")

    def test_an_explicit_account_number_is_honoured(self):
        provider, _client = self._provider(
            {"get_accounts": self.ACCOUNTS}, account_number="111"
        )

        self.assertEqual(provider._account()["account_number"], "111")

    def test_no_eligible_account_is_refused(self):
        provider, _client = self._provider(
            {"get_accounts": {"accounts": [{"account_number": "1", "state": "closed"}]}}
        )

        with self.assertRaises(RuntimeError):
            provider._account()

    def test_a_quote_is_normalized(self):
        provider, _client = self._provider(
            {
                "get_equity_quotes": {
                    "results": [
                        {"bid_price": 99.0, "ask_price": 101.0, "last_trade_price": 100.0}
                    ]
                }
            }
        )

        quote = provider.get_quote_snapshot("nvda")

        self.assertEqual(quote.bid_price, 99.0)
        self.assertEqual(quote.ask_price, 101.0)
        self.assertEqual(quote.last_price, 100.0)

    def test_alternate_quote_field_names_are_accepted(self):
        provider, _client = self._provider(
            {"get_equity_quotes": {"quotes": [{"bid": 99.0, "ask": 101.0, "price": 100.0}]}}
        )

        quote = provider.get_quote_snapshot("NVDA")

        self.assertEqual(quote.bid_price, 99.0)

    def test_a_missing_quote_is_refused(self):
        provider, _client = self._provider({"get_equity_quotes": {"results": []}})

        with self.assertRaises(RuntimeError):
            provider.get_quote_snapshot("NVDA")

    def test_a_portfolio_snapshot_is_normalized(self):
        provider, _client = self._provider(
            {
                "get_accounts": self.ACCOUNTS,
                "get_portfolio": {
                    "total_value": 100_000.0,
                    "cash": 25_000.0,
                    "buying_power": {"buying_power": 50_000.0},
                },
                "get_equity_positions": {
                    "positions": [
                        {
                            "symbol": "NVDA",
                            "quantity": "10",
                            "price": "120.0",
                            "average_price": "100.0",
                        }
                    ]
                },
            }
        )

        snapshot = provider.get_portfolio_snapshot()

        self.assertEqual(snapshot.broker, "robinhood")
        self.assertEqual(snapshot.account.equity, 100_000.0)
        self.assertEqual(snapshot.account.buying_power, 50_000.0)
        self.assertEqual(len(snapshot.positions), 1)
        self.assertEqual(snapshot.positions[0].symbol, "NVDA")
        self.assertEqual(snapshot.positions[0].market_value, 1_200.0)

    def test_a_flat_buying_power_field_is_accepted(self):
        provider, _client = self._provider(
            {
                "get_accounts": self.ACCOUNTS,
                "get_portfolio": {"equity": 1.0, "cash": 1.0, "buying_power": 42.0},
                "get_equity_positions": {"positions": []},
            }
        )

        self.assertEqual(provider.get_portfolio_snapshot().account.buying_power, 42.0)

    def test_an_empty_account_snapshots_cleanly(self):
        provider, _client = self._provider(
            {
                "get_accounts": self.ACCOUNTS,
                "get_portfolio": {},
                "get_equity_positions": {"positions": []},
            }
        )

        snapshot = provider.get_portfolio_snapshot()

        self.assertEqual(snapshot.positions, [])
        self.assertEqual(snapshot.account.equity, 0.0)


if __name__ == "__main__":
    unittest.main()
