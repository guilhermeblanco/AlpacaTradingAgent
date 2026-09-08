from datetime import datetime, timezone
from types import SimpleNamespace

from tradingagents.dataflows import googlenews_utils, macro_utils
from tradingagents.dataflows.date_window import in_window


def test_half_open_window_excludes_next_midnight_and_undated_history():
    start = datetime(2026, 5, 1)
    end = datetime(2026, 5, 9)

    assert in_window(datetime(2026, 5, 9, 23, 59), start, end)
    assert not in_window(datetime(2026, 5, 10), start, end)
    assert not in_window(
        None,
        start,
        end,
        now=datetime(2026, 9, 1, tzinfo=timezone.utc),
    )


def test_google_news_filters_feed_entries_to_requested_window(monkeypatch):
    requested_urls = []
    entries = [
        SimpleNamespace(
            title="In range - Source",
            link="https://example.test/in",
            published="Sat, 09 May 2026 23:59:00 GMT",
            summary="In range",
        ),
        SimpleNamespace(
            title="Future - Source",
            link="https://example.test/future",
            published="Sun, 10 May 2026 00:00:00 GMT",
            summary="Future",
        ),
    ]
    monkeypatch.setattr(
        googlenews_utils,
        "feedparser",
        SimpleNamespace(
            parse=lambda url: (
                requested_urls.append(url) or SimpleNamespace(entries=entries)
            )
        ),
    )

    result = googlenews_utils._getNewsDataRSS(
        "AAPL", "2026-05-01", "2026-05-09"
    )

    assert [item["title"] for item in result] == ["In range"]
    assert "before%3A2026-05-10" in requested_urls[0]


def test_fred_request_pins_realtime_vintage(monkeypatch):
    captured = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"observations": []}

    def fake_get(url, *, params, timeout):
        captured.update({"url": url, "params": params, "timeout": timeout})
        return Response()

    monkeypatch.setattr(macro_utils, "get_fred_api_key", lambda: "key")
    monkeypatch.setattr(macro_utils.requests, "get", fake_get)

    macro_utils.get_fred_data("GDP", "2020-01-01", "2020-03-31")

    assert captured["params"]["realtime_start"] == "2020-03-31"
    assert captured["params"]["realtime_end"] == "2020-03-31"
    assert captured["timeout"] == 30
