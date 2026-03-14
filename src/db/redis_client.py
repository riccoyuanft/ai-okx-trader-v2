import json
from typing import Optional
import redis.asyncio as aioredis
from src.config.settings import get_settings
from functools import lru_cache
from loguru import logger


@lru_cache
def get_redis() -> aioredis.Redis:
    settings = get_settings()
    logger.debug(f"Creating Redis client: host={settings.redis_host}, port={settings.redis_port}, db={settings.redis_db}")
    return aioredis.Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        db=settings.redis_db,
        decode_responses=True,
    )


# ── Engine state ────────────────────────────────────────────

async def set_engine_running(user_id: str) -> None:
    await get_redis().set(f"engine:{user_id}:running", "1")


async def clear_engine_running(user_id: str) -> None:
    await get_redis().delete(f"engine:{user_id}:running")


async def is_engine_running(user_id: str) -> bool:
    val = await get_redis().get(f"engine:{user_id}:running")
    return val == "1"


async def get_all_running_user_ids() -> list[str]:
    keys = await get_redis().keys("engine:*:running")
    return [k.split(":")[1] for k in keys]


# ── Position state ──────────────────────────────────────────

async def set_position(user_id: str, position: dict) -> None:
    await get_redis().set(f"engine:{user_id}:position", json.dumps(position))


async def get_position(user_id: str) -> Optional[dict]:
    val = await get_redis().get(f"engine:{user_id}:position")
    return json.loads(val) if val else None


async def clear_position(user_id: str) -> None:
    await get_redis().delete(f"engine:{user_id}:position")


# ── AI plan state (last decision for the open position) ──────

async def set_ai_plan(user_id: str, plan: dict) -> None:
    await get_redis().set(f"engine:{user_id}:ai_plan", json.dumps(plan))


async def get_ai_plan(user_id: str) -> Optional[dict]:
    val = await get_redis().get(f"engine:{user_id}:ai_plan")
    return json.loads(val) if val else None


async def clear_ai_plan(user_id: str) -> None:
    await get_redis().delete(f"engine:{user_id}:ai_plan")


# ── TOTP setup session (temporary, 10 min TTL) ─────────────

async def set_setup_session(session_id: str, data: dict, ttl: int = 600) -> None:
    try:
        logger.debug(f"[Redis] set_setup_session: session_id={session_id[:8]}...")
        await get_redis().setex(f"setup:{session_id}", ttl, json.dumps(data))
        logger.debug(f"[Redis] set_setup_session: SUCCESS")
    except Exception as e:
        logger.error(f"[Redis] set_setup_session FAILED: {e}")
        raise


async def get_setup_session(session_id: str) -> Optional[dict]:
    val = await get_redis().get(f"setup:{session_id}")
    return json.loads(val) if val else None


async def delete_setup_session(session_id: str) -> None:
    await get_redis().delete(f"setup:{session_id}")


# ── Account balance cache ────────────────────────────────────

async def set_balance(user_id: str, balance: dict) -> None:
    await get_redis().set(f"engine:{user_id}:balance", json.dumps(balance))


async def get_balance(user_id: str) -> Optional[dict]:
    val = await get_redis().get(f"engine:{user_id}:balance")
    return json.loads(val) if val else None


async def clear_balance(user_id: str) -> None:
    await get_redis().delete(f"engine:{user_id}:balance")


# ── News (latest N items from scraper) ─────────────────────

async def get_latest_news(count: int = 20) -> list[dict]:
    items = await get_redis().lrange("news:latest", 0, count - 1)
    return [json.loads(i) for i in items]
