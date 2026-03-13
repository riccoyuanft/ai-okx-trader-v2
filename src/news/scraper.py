"""
BlockBeats (律动) news scraper.
P1: stub.
P4: implement three-tier strategy:
  1. WebSocket push (preferred) — check via browser devtools
  2. Hidden JSON API  — inspect XHR requests for /api/flash
  3. HTTP polling     — GET every 30s, parse with BeautifulSoup, dedup via Redis

Results are written to Redis list key "news:latest" (capped at 100 items).
UserEngine reads from this list on each decision cycle; no subscription needed.
"""
import asyncio
from loguru import logger


async def run_scraper() -> None:
    """
    Long-running coroutine. Start once from main.py lifespan.
    P4: implement.
    """
    logger.info("News scraper started [P4 stub — no-op]")
    while True:
        await asyncio.sleep(3600)
