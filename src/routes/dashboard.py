from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from src.auth.jwt import get_current_user
from src.db.supabase_client import get_strategy_repo, get_user_repo
from src.db.redis_client import is_engine_running, get_position, get_ai_plan

router = APIRouter()
templates = Jinja2Templates(directory="src/templates")


def require_auth(request: Request) -> dict:
    return get_current_user(request)


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, user: dict = Depends(require_auth)):
    strategy_repo = get_strategy_repo()
    active_strategy = await strategy_repo.get_active(user["user_id"])
    running = await is_engine_running(user["user_id"])
    position = await get_position(user["user_id"])
    ai_plan = await get_ai_plan(user["user_id"]) if position else None

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "user": user,
        "active_strategy": active_strategy,
        "engine_running": running,
        "position": position,
        "ai_plan": ai_plan,
    })


@router.post("/engine/start")
async def engine_start(request: Request, user: dict = Depends(require_auth)):
    from src.engine.manager import get_manager
    strategy_repo = get_strategy_repo()
    active_strategy = await strategy_repo.get_active(user["user_id"])

    if not active_strategy:
        return RedirectResponse(url="/dashboard?error=no_strategy", status_code=302)

    # Persist running state to DB (users table) so engine auto-restores after server restart
    user_repo = get_user_repo()
    await user_repo.set_engine_running(user["user_id"], True)

    manager = get_manager()
    await manager.start_engine(user["user_id"], active_strategy)
    return RedirectResponse(url="/dashboard", status_code=302)


@router.post("/engine/stop")
async def engine_stop(request: Request, user: dict = Depends(require_auth)):
    position = await get_position(user["user_id"])
    if position and position.get("direction"):
        return RedirectResponse(url="/dashboard?error=has_position", status_code=302)

    from src.engine.manager import get_manager
    user_repo = get_user_repo()
    manager = get_manager()

    await manager.stop_engine(user["user_id"])

    # Persist stopped state to DB so engine does NOT restart after server restart
    await user_repo.set_engine_running(user["user_id"], False)

    return RedirectResponse(url="/dashboard", status_code=302)


@router.get("/api/position")
async def api_position(request: Request, user: dict = Depends(require_auth)):
    """Return current Redis position + AI plan as JSON for live dashboard polling."""
    position = await get_position(user["user_id"])
    ai_plan = await get_ai_plan(user["user_id"]) if position else None
    return JSONResponse({"position": position or {}, "ai_plan": ai_plan or {}})


@router.get("/api/ticker")
async def api_ticker(request: Request, user: dict = Depends(require_auth)):
    """Return the current market price for the active strategy symbol.
    Uses engine's OKX client if running, otherwise falls back to public market data."""
    import asyncio
    import okx.MarketData as MarketData
    from src.engine.manager import get_manager

    strategy_repo = get_strategy_repo()
    active_strategy = await strategy_repo.get_active(user["user_id"])
    if not active_strategy:
        return JSONResponse({"price": None, "symbol": None})

    symbol = active_strategy.get("symbol")
    manager = get_manager()
    engine = manager._engines.get(user["user_id"])

    price = None
    if engine and getattr(engine, "_okx", None):
        try:
            ticker = await engine._okx.get_ticker(symbol)
            price = ticker.get("last")
        except Exception:
            pass

    if price is None:
        try:
            mkt = MarketData.MarketAPI("", "", "", False, "0")
            result = await asyncio.to_thread(mkt.get_ticker, instId=symbol)
            if result.get("code") == "0" and result.get("data"):
                price = float(result["data"][0]["last"])
        except Exception:
            pass

    return JSONResponse({"price": price, "symbol": symbol})


@router.post("/position/close")
async def position_close(request: Request, user: dict = Depends(require_auth)):
    """Manually close current position. Engine continues running after close."""
    from src.engine.manager import get_manager
    manager = get_manager()
    requested = manager.request_manual_close(user["user_id"])
    if not requested:
        return RedirectResponse(url="/dashboard?error=no_engine", status_code=302)
    return RedirectResponse(url="/dashboard?info=close_requested", status_code=302)
