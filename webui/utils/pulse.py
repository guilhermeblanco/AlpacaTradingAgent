"""Server-sent pulse: the UI is told when something changed.

Every panel used to poll. The fast interval ran at 1s while an analysis was
running, for every callback, in every open tab, whether or not anything had
happened — and a governor callback existed purely to switch it off again
afterwards.

The pulse inverts that. One long-lived connection per tab stays silent
while the machine is idle and emits the moment in-process state advances.
The polling intervals stay as a slow fallback for when a proxy eats the
stream.
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any, Iterator, Optional

#: How often the generator looks for a change. Cheap: an attribute read, no
#: HTTP round trip and no callback re-render.
POLL_SECONDS = 0.25

#: Emitted even when nothing changed, so a proxy that buffers or a client
#: that slept still learns the connection is alive.
HEARTBEAT_SECONDS = 20.0


class Pulse:
    """Tracks a revision that advances whenever the UI needs to redraw."""

    def __init__(self):
        self._lock = threading.Lock()
        self._revision = 0
        self._reason = "start"

    @property
    def revision(self) -> int:
        with self._lock:
            return self._revision

    def bump(self, reason: str = "") -> int:
        """Mark the UI stale. Safe to call from any thread."""
        with self._lock:
            self._revision += 1
            self._reason = reason or "update"
            return self._revision

    def snapshot(self, state) -> dict[str, Any]:
        """What the client is told: the revision and enough to render vitals."""
        with self._lock:
            revision, reason = self._revision, self._reason
        return {
            "revision": revision,
            "reason": reason,
            "analysis_running": bool(getattr(state, "analysis_running", False)),
            "analyzing_symbol": getattr(state, "analyzing_symbol", None),
            "tool_calls": int(getattr(state, "tool_calls_count", 0) or 0),
            "llm_calls": int(getattr(state, "llm_calls_count", 0) or 0),
            "at": time.time(),
        }


_PULSE = Pulse()


def get_pulse() -> Pulse:
    return _PULSE


def _drain(state) -> bool:
    """Consume the in-process staleness flag, advancing the revision.

    `app_state.needs_ui_update` is already set by everything that changes
    what a panel would draw, so the pulse reads it rather than asking every
    writer to learn a new call.
    """
    if getattr(state, "needs_ui_update", False):
        state.needs_ui_update = False
        _PULSE.bump("state")
        return True
    return False


def _format(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload)}\n\n"


def pulse_events(
    state,
    *,
    max_events: Optional[int] = None,
    poll_seconds: float = POLL_SECONDS,
    heartbeat_seconds: float = HEARTBEAT_SECONDS,
    sleep=time.sleep,
    clock=time.monotonic,
) -> Iterator[str]:
    """Yield server-sent events, one per change plus a periodic heartbeat.

    `max_events` bounds the stream so it can be exercised without a
    background thread; in the app it is None and the generator lives as
    long as the connection.
    """
    emitted = 0
    # The first event primes the client with current state rather than
    # leaving the panels blank until something happens.
    yield _format(_PULSE.snapshot(state))
    emitted += 1
    last_revision = _PULSE.revision
    last_emit = clock()

    while max_events is None or emitted < max_events:
        changed = _drain(state)
        revision = _PULSE.revision
        due = clock() - last_emit >= heartbeat_seconds
        if changed or revision != last_revision or due:
            yield _format(_PULSE.snapshot(state))
            emitted += 1
            last_revision = revision
            last_emit = clock()
            continue
        sleep(poll_seconds)


def register_pulse_route(server, state):
    """Mount the stream on the Flask server behind the Dash app."""
    from flask import Response

    @server.route("/stream/pulse")
    def stream_pulse():  # pragma: no cover - exercised through the generator
        return Response(
            pulse_events(state),
            mimetype="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    return stream_pulse
