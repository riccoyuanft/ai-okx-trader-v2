from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse, JSONResponse
from loguru import logger
import traceback

from src.db.redis_client import get_all_running_user_ids
from src.db.supabase_client import get_strategy_repo
from src.engine.manager import get_manager
from src.news.scraper import run_scraper

import asyncio


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("AI OKX Trader v2 starting up...")

    # Restore engines that were running before restart
    manager = get_manager()
    try:
        strategy_repo = get_strategy_repo()
        running_ids = await get_all_running_user_ids()
        for user_id in running_ids:
            strategy = await strategy_repo.get_active(user_id)
            if strategy:
                await manager.start_engine(user_id, strategy)
                logger.info(f"Auto-restored engine for user {user_id}")
            else:
                logger.warning(f"No active strategy for user {user_id}, skipping restore")
    except Exception as e:
        logger.warning(f"Engine restore skipped (external services unavailable): {e}")

    # Start news scraper (P4 stub — no-op until implemented)
    scraper_task = asyncio.create_task(run_scraper(), name="news_scraper")

    logger.info("Startup complete")
    yield

    logger.info("Shutting down...")
    scraper_task.cancel()
    await manager.shutdown_all()
    logger.info("Shutdown complete")


app = FastAPI(title="AI OKX Trader v2", lifespan=lifespan)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    try:
        response = await call_next(request)
        return response
    except Exception as e:
        logger.error(f"MIDDLEWARE EXCEPTION: {request.method} {request.url.path} -> {e}")
        logger.error(f"Traceback:\n{traceback.format_exc()}")
        raise


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"GLOBAL EXCEPTION HANDLER: {request.method} {request.url.path}")
    logger.error(f"Exception: {exc}")
    logger.error(f"Traceback:\n{traceback.format_exc()}")
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error", "error": str(exc)}
    )


app.mount("/static", StaticFiles(directory="src/static"), name="static")

from src.routes import auth, dashboard, strategies, trades, ws, account

app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(account.router)
app.include_router(strategies.router)
app.include_router(trades.router)
app.include_router(ws.router)


@app.get("/")
async def root():
    return RedirectResponse(url="/login")
