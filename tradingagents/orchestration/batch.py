from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from pydantic import BaseModel, Field


class Candidate(BaseModel):
    symbol: str
    asset_class: str = "equity"
    score: float
    discovered_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    source: str
    provenance: dict[str, Any] = Field(default_factory=dict)


class ProviderPolicy(BaseModel):
    max_concurrency: int = Field(default=2, ge=1)
    min_interval_seconds: float = Field(default=0.0, ge=0)
    max_retries: int = Field(default=1, ge=0, le=10)


class BatchResult(BaseModel):
    candidate: Candidate
    success: bool
    value: Any = None
    error: Optional[str] = None
    attempts: int = 0
    cancelled: bool = False


class _ProviderGate:
    def __init__(self, policy: ProviderPolicy):
        self.policy = policy
        self.semaphore = threading.BoundedSemaphore(policy.max_concurrency)
        self.lock = threading.Lock()
        self.last_started = 0.0

    def enter(self, stop: threading.Event) -> bool:
        while not stop.is_set():
            if self.semaphore.acquire(timeout=0.1):
                with self.lock:
                    delay = self.policy.min_interval_seconds - (time.monotonic() - self.last_started)
                    if delay > 0 and stop.wait(delay):
                        self.semaphore.release()
                        return False
                    self.last_started = time.monotonic()
                return True
        return False

    def leave(self) -> None:
        self.semaphore.release()


class BatchOrchestrator:
    """Runs candidate analysis with bounded global and per-provider concurrency."""

    def __init__(
        self,
        *,
        max_workers: int = 4,
        provider_policies: Optional[dict[str, ProviderPolicy | dict]] = None,
    ):
        self.max_workers = max(1, int(max_workers))
        self._policies = {
            name: policy if isinstance(policy, ProviderPolicy) else ProviderPolicy.model_validate(policy)
            for name, policy in (provider_policies or {}).items()
        }
        self._gates: dict[str, _ProviderGate] = {}
        self._stop = threading.Event()
        self._pause = threading.Event()
        self._pause.set()

    def pause(self) -> None:
        self._pause.clear()

    def resume(self) -> None:
        self._pause.set()

    def stop(self) -> None:
        self._stop.set()
        self._pause.set()

    def reset(self) -> None:
        self._stop.clear()
        self._pause.set()

    def _gate(self, provider: str) -> _ProviderGate:
        if provider not in self._gates:
            self._gates[provider] = _ProviderGate(self._policies.get(provider, ProviderPolicy()))
        return self._gates[provider]

    def run(
        self,
        candidates: list[Candidate],
        handler: Callable[[Candidate], Any],
        *,
        provider: str,
    ) -> list[BatchResult]:
        gate = self._gate(provider)
        policy = gate.policy

        def work(candidate: Candidate) -> BatchResult:
            attempts = 0
            while attempts <= policy.max_retries:
                while not self._pause.wait(timeout=0.1):
                    if self._stop.is_set():
                        return BatchResult(candidate=candidate, success=False, attempts=attempts, cancelled=True)
                if self._stop.is_set() or not gate.enter(self._stop):
                    return BatchResult(candidate=candidate, success=False, attempts=attempts, cancelled=True)
                attempts += 1
                try:
                    return BatchResult(candidate=candidate, success=True, value=handler(candidate), attempts=attempts)
                except Exception as exc:
                    if attempts > policy.max_retries:
                        return BatchResult(candidate=candidate, success=False, error=str(exc), attempts=attempts)
                finally:
                    gate.leave()
            raise AssertionError("unreachable")

        if self._stop.is_set():
            return [BatchResult(candidate=row, success=False, cancelled=True) for row in candidates]
        results: dict[str, BatchResult] = {}
        with ThreadPoolExecutor(max_workers=self.max_workers, thread_name_prefix="analysis") as pool:
            futures = {pool.submit(work, candidate): candidate for candidate in candidates}
            for future in as_completed(futures):
                result = future.result()
                results[result.candidate.symbol] = result
        return [results[candidate.symbol] for candidate in candidates]
