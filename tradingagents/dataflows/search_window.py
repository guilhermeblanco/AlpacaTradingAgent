"""Whether a live web search may be used for a given analysis date.

The dated sources bound themselves: Finnhub takes `from`/`to`, SimFin filters
on publish date, Google News takes `after:`/`before:`, Reddit filters its
window. The hosted web-search tool takes none of that — the date range only
reaches it as prose in the prompt, which is a request, not a constraint.

For an analysis of today that does not matter: today is the cutoff. For an
analysis of a past date it matters a great deal, because the model can
surface anything published since, and a decision built on it is not the
decision the original run could have made.

So live search is allowed only when the requested date is current. For any
earlier date the tools compose from the bounded sources instead. This is
day-granular, like every other window in this codebase: it does not model
the hour of day a decision was made.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

#: Trade dates are chosen against the US market calendar, so "today" is too.
MARKET_TIMEZONE = ZoneInfo("America/New_York")

#: Set true to compose from dated sources even for a current-date run —
#: useful when reproducing a run and you want the sources pinned.
FORCE_CONFIG_KEY = "require_point_in_time_web_search"

HISTORICAL_NOTICE = (
    "Composed from date-bounded sources because this analysis is for a past "
    "date. Live web search cannot be constrained to a historical window, so "
    "it would be able to see information the original run could not."
)

FORCED_NOTICE = (
    "Composed from date-bounded sources because point-in-time sourcing is "
    f"required ({FORCE_CONFIG_KEY})."
)


def market_today(now: Optional[datetime] = None) -> date:
    now = now or datetime.now(MARKET_TIMEZONE)
    if now.tzinfo is None:
        return now.date()
    return now.astimezone(MARKET_TIMEZONE).date()


def parse_analysis_date(curr_date: Any) -> Optional[date]:
    """The requested date, or None when it cannot be read."""
    if isinstance(curr_date, datetime):
        return curr_date.date()
    if isinstance(curr_date, date):
        return curr_date
    try:
        return datetime.strptime(str(curr_date).strip(), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def is_historical(curr_date: Any, *, now: Optional[datetime] = None) -> bool:
    """True when the requested date is earlier than the current market date.

    An unreadable date is treated as historical: refusing to guess is the
    safe direction, since the cost is a bounded answer rather than a
    contaminated one.
    """
    parsed = parse_analysis_date(curr_date)
    if parsed is None:
        return True
    return parsed < market_today(now)


def live_search_allowed(
    curr_date: Any,
    *,
    config: Optional[dict] = None,
    now: Optional[datetime] = None,
) -> bool:
    """Whether the hosted web-search tool may be used for this date."""
    if _forced(config):
        return False
    return not is_historical(curr_date, now=now)


def bounded_reason(
    curr_date: Any,
    *,
    config: Optional[dict] = None,
    now: Optional[datetime] = None,
) -> str:
    """Why a tool composed from dated sources instead of searching."""
    if _forced(config):
        return FORCED_NOTICE
    if is_historical(curr_date, now=now):
        return HISTORICAL_NOTICE
    return ""


def _forced(config: Optional[dict]) -> bool:
    if config is None:
        try:
            from .config import get_config

            config = get_config() or {}
        except Exception:
            config = {}
    value = config.get(FORCE_CONFIG_KEY, False)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)
