# TradingAgents/graph/signal_processing.py

from langchain_openai import ChatOpenAI
REVIEW = "REVIEW"


class SignalProcessor:
    """Processes trading signals to extract actionable decisions."""

    def __init__(self, quick_thinking_llm: ChatOpenAI):
        """Initialize with an LLM for processing."""
        self.quick_thinking_llm = quick_thinking_llm

    def process_signal(self, full_signal: str) -> str:
        """
        Process a full trading signal to extract the core decision.

        Args:
            full_signal: Complete trading signal text

        Returns:
            Extracted decision (BUY, SELL, or HOLD)
        """
        # First try deterministic extraction to avoid unnecessary LLM calls
        content = full_signal.upper()

        # Check for trading-mode keywords first (LONG / SHORT / NEUTRAL)
        for action in ["LONG", "SHORT", "NEUTRAL"]:
            pattern = f"FINAL TRANSACTION PROPOSAL: **{action}**"
            if pattern in content:
                return action

        # Check for investment-mode keywords (BUY / SELL / HOLD)
        for action in ["BUY", "SELL", "HOLD"]:
            pattern = f"FINAL TRANSACTION PROPOSAL: **{action}**"
            if pattern in content:
                return action

        # A malformed final decision is not a neutral market opinion. Surface a
        # non-executable sentinel instead of asking another model to guess.
        return REVIEW
