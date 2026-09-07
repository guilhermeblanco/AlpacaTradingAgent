import threading
import time
import unittest

from tradingagents.orchestration import BatchOrchestrator, Candidate, ProviderPolicy


class BatchOrchestrationTests(unittest.TestCase):
    def test_preserves_candidate_order_and_retries(self):
        attempts = {}

        def handler(candidate):
            attempts[candidate.symbol] = attempts.get(candidate.symbol, 0) + 1
            if candidate.symbol == "AAPL" and attempts[candidate.symbol] == 1:
                raise RuntimeError("temporary")
            return candidate.score * 2

        rows = [Candidate(symbol="AAPL", score=8, source="scanner"),
                Candidate(symbol="MSFT", score=7, source="scanner")]
        results = BatchOrchestrator(
            max_workers=2, provider_policies={"openai": ProviderPolicy(max_retries=1)}
        ).run(rows, handler, provider="openai")
        self.assertEqual([row.candidate.symbol for row in results], ["AAPL", "MSFT"])
        self.assertTrue(all(row.success for row in results))
        self.assertEqual(results[0].attempts, 2)

    def test_enforces_provider_concurrency(self):
        active = 0
        peak = 0
        lock = threading.Lock()

        def handler(candidate):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.02)
            with lock:
                active -= 1
            return candidate.symbol

        rows = [Candidate(symbol=f"S{i}", score=i, source="test") for i in range(5)]
        BatchOrchestrator(
            max_workers=5, provider_policies={"llm": {"max_concurrency": 2}}
        ).run(rows, handler, provider="llm")
        self.assertEqual(peak, 2)

    def test_stop_cancels_new_batch(self):
        orchestrator = BatchOrchestrator()
        orchestrator.stop()
        rows = [Candidate(symbol="AAPL", score=1, source="test")]
        result = orchestrator.run(rows, lambda candidate: candidate.symbol, provider="llm")
        self.assertTrue(result[0].cancelled)


if __name__ == "__main__":
    unittest.main()
