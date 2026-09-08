import unittest

from tradingagents.graph.signal_processing import SignalProcessor


class FailingLLM:
    def invoke(self, _messages):
        raise AssertionError("LLM should not be called for deterministic final proposals")


class SignalProcessorTests(unittest.TestCase):
    def test_extracts_executable_action_without_llm(self):
        processor = SignalProcessor(FailingLLM())

        self.assertEqual(
            processor.process_signal("Advisory Rating: Overweight\nFINAL TRANSACTION PROPOSAL: **BUY**"),
            "BUY",
        )
        self.assertEqual(
            processor.process_signal("FINAL TRANSACTION PROPOSAL: **SHORT**"),
            "SHORT",
        )

    def test_unparseable_final_decision_requires_review(self):
        processor = SignalProcessor(FailingLLM())

        self.assertEqual(processor.process_signal("Ambiguous prose only."), "REVIEW")
        self.assertEqual(processor.process_signal("The buyer is still holding."), "REVIEW")


if __name__ == "__main__":
    unittest.main()
