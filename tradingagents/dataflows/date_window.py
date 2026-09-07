"""Shared point-in-time date-window rules for externally dated content."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


def to_utc(value: datetime) -> datetime:
    """Return an aware UTC datetime, treating naive source times as UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def in_window(
    published_at: datetime | None,
    start_at: datetime,
    end_at: datetime,
    *,
    now: datetime | None = None,
) -> bool:
    """Check the half-open interval ``[start, midnight-after-end)``.

    Undated content is excluded from historical windows because its availability
    cannot be proven. It remains usable for a window that reaches the present.
    """
    start = to_utc(start_at)
    end_exclusive = to_utc(end_at) + timedelta(days=1)
    if published_at is not None:
        published = to_utc(published_at)
        return start <= published < end_exclusive
    current = to_utc(now or datetime.now(timezone.utc))
    return end_exclusive >= current - timedelta(days=1)
