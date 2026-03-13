"""
News sentiment AI agent.
P1: stub.
P4: implement — takes raw news items from Redis, returns structured sentiment.

Output:
  {
    "sentiment": "bullish" | "bearish" | "neutral",
    "confidence": float,
    "affects": [str],
    "summary": str,
    "urgency": "high" | "medium" | "low"
  }
"""
from typing import Optional


async def analyze_news(
    news_items: list[dict],
    symbol: str,
    strategy: dict,
) -> Optional[dict]:
    """
    Summarize recent news items and return structured sentiment.
    P4: implement.
    """
    raise NotImplementedError("P4")
