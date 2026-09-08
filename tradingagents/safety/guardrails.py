"""Deterministic production safety layer.

Every guard here is plain arithmetic over injected state — zero LLM calls,
zero imports from the agent stack — so it cannot be talked out of a halt by
a model and keeps working when providers misbehave. Three stages:

- pre-trade checks: per-order notional cap, per-symbol concentration cap
- circuit breakers: daily loss halt, drawdown-from-high-water-mark halt,
  consecutive-rejection halt (a data/connectivity glitch signal)
- kill switch: shared PostgreSQL state for coordinated deployments plus a
  host-local emergency flag file

A daily LLM token budget rides along: run logs already count tokens, the
guard accumulates them per day and can refuse to start new analyses.
"""

from __future__ import annotations

import json
import math
import os
import threading
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from .state import SafetyStateStore

DEFAULT_SAFETY_CONFIG: Dict[str, Any] = {
    "safety_enabled": True,
    # Pre-trade checks
    "max_trade_notional_usd": 25_000.0,  # 0 = uncapped
    "max_symbol_concentration_pct": 25.0,  # of account equity; 0 = uncapped
    # Circuit breakers
    "daily_loss_halt_pct": 10.0,  # halt when equity drops this % vs yesterday
    "max_drawdown_halt_pct": 15.0,  # halt when equity drops this % vs high-water mark
    "max_consecutive_rejections": 5,  # halt after this many broker rejections in a row
    # LLM cost cap
    "daily_llm_token_budget": 0,  # tokens/day across all runs; 0 = unlimited
}

_SAFETY_HOME = Path(os.path.expanduser("~")) / ".tradingagents" / "safety"


@dataclass
class SafetyVerdict:
    allowed: bool
    reasons: List[str] = field(default_factory=list)
    checks: Dict[str, dict] = field(default_factory=dict)


def _today(when: Optional[str] = None) -> str:
    return when or date.today().isoformat()


def _finite_float(value) -> Optional[float]:
    """Parse broker-supplied numbers defensively.

    Outage artifacts show up here as NaN/inf equity or literal HTML error
    pages in numeric fields. Anything non-finite or unparseable reads as
    'unavailable' (None): a NaN that reaches a comparison silently passes
    every breaker, and a NaN stored as the high-water mark disables the
    drawdown breaker permanently.
    """
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


class SafetyGuard:
    """Thread-safe guard backed by local files or shared PostgreSQL state.

    Persisted state: equity high-water mark, current rejection streak, and
    per-day LLM token counts. A local kill-switch file remains available so a
    human or host monitor can engage an emergency halt without database access.
    """

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        state_path: Optional[Path] = None,
        kill_switch_path: Optional[Path] = None,
        state_store: Optional["SafetyStateStore"] = None,
    ):
        merged = dict(DEFAULT_SAFETY_CONFIG)
        for key in merged:
            if config and config.get(key) is not None:
                merged[key] = config[key]
        self.config = merged
        self.state_path = Path(state_path or (_SAFETY_HOME / "state.json"))
        self.kill_switch_path = Path(
            kill_switch_path or (_SAFETY_HOME / "KILL_SWITCH")
        )
        self.state_store = state_store
        self._lock = threading.RLock()
        self._state = self._load_state()

    # ----- state persistence -------------------------------------------------

    def _load_state(self) -> Dict[str, Any]:
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                return raw
        except (OSError, ValueError):
            pass
        return {"high_water_mark": None, "consecutive_rejections": 0, "llm_tokens": {}}

    def _save_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._state, indent=2), encoding="utf-8")
        os.replace(tmp, self.state_path)

    # ----- kill switch --------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return bool(self.config.get("safety_enabled", True))

    def kill_switch_active(self) -> bool:
        if self.state_store is not None:
            active, _ = self.state_store.kill_switch()
            return active or self.kill_switch_path.exists()
        return self.kill_switch_path.exists()

    def kill_switch_reason(self) -> str:
        if self.state_store is not None:
            active, reason = self.state_store.kill_switch()
            if active:
                return reason
        try:
            return self.kill_switch_path.read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    def engage_kill_switch(self, reason: str = "manual halt") -> None:
        stamp = datetime.now(timezone.utc).isoformat()
        persisted_reason = f"{reason} (engaged {stamp})"
        if self.state_store is not None:
            self.state_store.set_kill_switch(True, persisted_reason)
        else:
            self.kill_switch_path.parent.mkdir(parents=True, exist_ok=True)
            self.kill_switch_path.write_text(persisted_reason, encoding="utf-8")
        # Ops alert; imported lazily and failure-isolated so the safety
        # layer keeps zero hard dependencies.
        try:
            from tradingagents.alerts import notify_kill_switch

            notify_kill_switch(reason)
        except Exception:
            pass

    def release_kill_switch(self) -> None:
        if self.state_store is not None:
            self.state_store.set_kill_switch(False)
        try:
            self.kill_switch_path.unlink()
        except FileNotFoundError:
            pass

    # ----- order/rejection tracking -------------------------------------------

    def record_order_result(self, success: bool) -> None:
        if self.state_store is not None:
            self.state_store.record_order_result(success)
            return
        with self._lock:
            if success:
                self._state["consecutive_rejections"] = 0
            else:
                self._state["consecutive_rejections"] = (
                    int(self._state.get("consecutive_rejections", 0)) + 1
                )
            self._save_state()

    def consecutive_rejections(self) -> int:
        if self.state_store is not None:
            return self.state_store.consecutive_rejections()
        with self._lock:
            return int(self._state.get("consecutive_rejections", 0))

    # ----- LLM token budget ----------------------------------------------------

    def record_llm_tokens(self, tokens: int, when: Optional[str] = None) -> None:
        if not tokens:
            return
        day = _today(when)
        if self.state_store is not None:
            self.state_store.record_llm_tokens(date.fromisoformat(day), int(tokens))
            return
        with self._lock:
            counts = self._state.setdefault("llm_tokens", {})
            counts[day] = int(counts.get(day, 0)) + int(tokens)
            # Keep the map from growing unbounded.
            for old_day in sorted(counts)[:-31]:
                del counts[old_day]
            self._save_state()

    def llm_tokens_used(self, when: Optional[str] = None) -> int:
        day = _today(when)
        if self.state_store is not None:
            return self.state_store.llm_tokens_used(date.fromisoformat(day))
        with self._lock:
            return int(self._state.get("llm_tokens", {}).get(day, 0))

    def check_llm_budget(self, when: Optional[str] = None) -> SafetyVerdict:
        budget = float(self.config.get("daily_llm_token_budget", 0) or 0)
        used = self.llm_tokens_used(when)
        if not self.enabled or budget <= 0 or used < budget:
            return SafetyVerdict(
                allowed=True,
                checks={"llm_budget": {"status": "pass", "used": used, "budget": budget}},
            )
        return SafetyVerdict(
            allowed=False,
            reasons=[
                f"Daily LLM token budget exhausted: {used:,.0f} of {budget:,.0f} tokens used."
            ],
            checks={"llm_budget": {"status": "fail", "used": used, "budget": budget}},
        )

    # ----- the main pre-order gate ----------------------------------------------

    def check_order(
        self,
        symbol: str,
        notional: float,
        account: Optional[Dict[str, float]] = None,
        position_value: Optional[float] = None,
        risk_reducing: bool = False,
    ) -> SafetyVerdict:
        """Deterministic gate every outbound order must pass.

        `account` carries {"equity", "last_equity"} when available; checks
        that need missing data are reported as skipped rather than guessed.
        Risk-reducing exits bypass exposure and circuit-breaker checks so a
        loss halt cannot trap an existing position. The explicit kill switch
        still blocks all broker order flow.
        """
        if not self.enabled:
            return SafetyVerdict(
                allowed=True, checks={"safety": {"status": "skipped", "detail": "disabled"}}
            )

        reasons: List[str] = []
        checks: Dict[str, dict] = {}
        equity = _finite_float(account.get("equity")) if account else None
        last_equity = _finite_float(account.get("last_equity")) if account else None
        if last_equity == 0.0:
            last_equity = None  # a 0 baseline can't produce a meaningful change %

        # Kill switch dominates everything.
        if self.kill_switch_active():
            reason = self.kill_switch_reason() or "engaged"
            reasons.append(f"Kill switch is engaged: {reason}")
            checks["kill_switch"] = {"status": "fail", "detail": reason}
        else:
            checks["kill_switch"] = {"status": "pass"}

        if risk_reducing:
            for name in (
                "trade_notional",
                "concentration",
                "daily_loss",
                "drawdown",
                "rejection_streak",
            ):
                checks[name] = {
                    "status": "skipped",
                    "detail": "risk-reducing exit",
                }
            return SafetyVerdict(
                allowed=not reasons,
                reasons=reasons,
                checks=checks,
            )

        # Pre-trade: per-order notional cap.
        notional_value = _finite_float(notional) or 0.0
        cap = float(self.config.get("max_trade_notional_usd", 0) or 0)
        if cap > 0 and notional_value > cap:
            reasons.append(
                f"Order notional ${notional_value:,.2f} exceeds max_trade_notional_usd ${cap:,.2f}."
            )
            checks["trade_notional"] = {"status": "fail", "notional": notional_value, "cap": cap}
        else:
            checks["trade_notional"] = {"status": "pass", "notional": notional_value, "cap": cap}

        # Pre-trade: per-symbol concentration cap.
        conc_pct = float(self.config.get("max_symbol_concentration_pct", 0) or 0)
        if conc_pct > 0:
            if equity:
                exposure = (_finite_float(position_value) or 0.0) + notional_value
                limit = equity * conc_pct / 100.0
                if exposure > limit:
                    reasons.append(
                        f"{symbol} exposure ${exposure:,.2f} would exceed "
                        f"{conc_pct:g}% of equity (${limit:,.2f})."
                    )
                    checks["concentration"] = {
                        "status": "fail",
                        "exposure": exposure,
                        "limit": limit,
                    }
                else:
                    checks["concentration"] = {
                        "status": "pass",
                        "exposure": exposure,
                        "limit": limit,
                    }
            else:
                checks["concentration"] = {
                    "status": "skipped",
                    "detail": "account equity unavailable",
                }
        else:
            checks["concentration"] = {"status": "pass", "detail": "uncapped"}

        # Circuit breaker: daily loss.
        halt_pct = float(self.config.get("daily_loss_halt_pct", 0) or 0)
        if halt_pct > 0 and equity and last_equity:
            change_pct = (equity - last_equity) / last_equity * 100.0
            if change_pct <= -halt_pct:
                reasons.append(
                    f"Daily loss circuit breaker: equity is {change_pct:+.2f}% vs "
                    f"yesterday (halt at -{halt_pct:g}%)."
                )
                checks["daily_loss"] = {"status": "fail", "change_pct": change_pct}
            else:
                checks["daily_loss"] = {"status": "pass", "change_pct": change_pct}
        else:
            checks["daily_loss"] = {
                "status": "skipped" if halt_pct > 0 else "pass",
                "detail": "account equity unavailable" if halt_pct > 0 else "uncapped",
            }

        # Circuit breaker: drawdown from persisted high-water mark.
        dd_pct = float(self.config.get("max_drawdown_halt_pct", 0) or 0)
        if equity:
            if self.state_store is not None:
                hwm = self.state_store.update_high_water_mark(equity)
            else:
                with self._lock:
                    hwm = self._state.get("high_water_mark")
                    if hwm is None or equity > float(hwm):
                        self._state["high_water_mark"] = equity
                        self._save_state()
                        hwm = equity
            hwm = float(hwm)
            if dd_pct > 0 and hwm > 0:
                drawdown_pct = (hwm - equity) / hwm * 100.0
                if drawdown_pct >= dd_pct:
                    reasons.append(
                        f"Drawdown circuit breaker: equity is {drawdown_pct:.2f}% below the "
                        f"${hwm:,.2f} high-water mark (halt at {dd_pct:g}%)."
                    )
                    checks["drawdown"] = {"status": "fail", "drawdown_pct": drawdown_pct}
                else:
                    checks["drawdown"] = {"status": "pass", "drawdown_pct": drawdown_pct}
            else:
                checks["drawdown"] = {"status": "pass", "detail": "uncapped"}
        else:
            checks["drawdown"] = {
                "status": "skipped" if dd_pct > 0 else "pass",
                "detail": "account equity unavailable" if dd_pct > 0 else "uncapped",
            }

        # Circuit breaker: consecutive broker rejections (data-glitch signal).
        max_rejects = int(self.config.get("max_consecutive_rejections", 0) or 0)
        streak = self.consecutive_rejections()
        if max_rejects > 0 and streak >= max_rejects:
            reasons.append(
                f"{streak} consecutive orders were rejected (halt at {max_rejects}); "
                "possible data or connectivity problem."
            )
            checks["rejection_streak"] = {"status": "fail", "streak": streak}
        else:
            checks["rejection_streak"] = {"status": "pass", "streak": streak}

        return SafetyVerdict(allowed=not reasons, reasons=reasons, checks=checks)

    # ----- status for dashboards ---------------------------------------------

    def status(self, account: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
        """Green/red snapshot of every guard for the WebUI panel."""
        order_view = self.check_order("_status_", 0.0, account=account)
        budget_view = self.check_llm_budget()
        guards: Dict[str, dict] = {}
        for name, check in {**order_view.checks, **budget_view.checks}.items():
            guards[name] = {
                "ok": check.get("status") != "fail",
                "status": check.get("status"),
                "detail": {k: v for k, v in check.items() if k != "status"},
            }
        return {
            "enabled": self.enabled,
            "kill_switch_active": self.kill_switch_active(),
            "kill_switch_reason": self.kill_switch_reason(),
            "reasons": list(order_view.reasons) + list(budget_view.reasons),
            "guards": guards,
            "config": dict(self.config),
        }


# ----- process-wide singleton ---------------------------------------------------

_GUARD: Optional[SafetyGuard] = None
_GUARD_LOCK = threading.Lock()


def get_safety_guard() -> SafetyGuard:
    """Process-wide guard configured from the runtime config (lazily)."""
    global _GUARD
    with _GUARD_LOCK:
        if _GUARD is None:
            config: Dict[str, Any] = {}
            try:
                # Imported lazily: the safety layer must stay importable even
                # if the dataflows stack (and its dependencies) are not.
                from tradingagents.dataflows.config import get_config

                config = dict(get_config() or {})
            except Exception:
                config = {
                    "persistence_backend": os.getenv("PERSISTENCE_BACKEND", "local"),
                    "database_url": os.getenv("DATABASE_URL"),
                    "safety_state_scope": os.getenv("SAFETY_STATE_SCOPE"),
                }
            if str(config.get("persistence_backend") or "local").lower() == "postgres":
                from .state import build_postgres_safety_store

                _GUARD = SafetyGuard(
                    config=config, state_store=build_postgres_safety_store(config)
                )
            else:
                _GUARD = SafetyGuard(config=config)
        return _GUARD


def reset_safety_guard() -> None:
    global _GUARD
    with _GUARD_LOCK:
        if _GUARD is not None and _GUARD.state_store is not None:
            _GUARD.state_store.close()
        _GUARD = None
