from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from typing import Optional

from src.auth.jwt import get_current_user
from src.auth.crypto import encrypt, decrypt
from src.auth.totp import verify_token
from src.db.supabase_client import get_strategy_repo, get_user_repo
from src.db.redis_client import is_engine_running

router = APIRouter()
templates = Jinja2Templates(directory="src/templates")


def require_auth(request: Request) -> dict:
    return get_current_user(request)


@router.get("/strategies", response_class=HTMLResponse)
async def strategies_list(request: Request, user: dict = Depends(require_auth)):
    strategy_repo = get_strategy_repo()
    strategies = await strategy_repo.get_all_by_user(user["user_id"])
    return templates.TemplateResponse("strategies.html", {
        "request": request,
        "user": user,
        "strategies": strategies,
    })


@router.get("/strategies/new", response_class=HTMLResponse)
async def strategy_new_page(request: Request, user: dict = Depends(require_auth)):
    return templates.TemplateResponse("strategy_edit.html", {
        "request": request,
        "user": user,
        "strategy": None,
        "action": "/strategies/new",
    })


@router.post("/strategies/new")
async def strategy_create(
    request: Request,
    user: dict = Depends(require_auth),
    name: str = Form(...),
    symbol: str = Form(default="BTC-USDT-SWAP"),
    timeframe: str = Form(default="15m"),
    nl_strategy: Optional[str] = Form(default=None),
    default_leverage: int = Form(default=10),
    max_leverage: int = Form(default=20),
    position_size_pct: float = Form(default=30.0),
    ai_provider: str = Form(default="qwen"),
    ai_api_key: Optional[str] = Form(default=None),
    ai_base_url: Optional[str] = Form(default=None),
    ai_model: Optional[str] = Form(default=None),
    max_daily_loss_pct: float = Form(default=5.0),
    max_consecutive_losses: int = Form(default=3),
    max_position_pct: float = Form(default=50.0),
    enable_news_analysis: bool = Form(default=False),
):
    strategy_repo = get_strategy_repo()
    data = {
        "user_id": user["user_id"],
        "name": name,
        "symbol": symbol,
        "timeframe": timeframe,
        "nl_strategy": nl_strategy,
        "default_leverage": default_leverage,
        "max_leverage": max_leverage,
        "position_size_pct": position_size_pct,
        "ai_provider": ai_provider,
        "ai_api_key": encrypt(ai_api_key) if ai_api_key else None,
        "ai_base_url": ai_base_url,
        "ai_model": ai_model,
        "max_daily_loss_pct": max_daily_loss_pct,
        "max_consecutive_losses": max_consecutive_losses,
        "max_position_pct": max_position_pct,
        "stop_on_breach": True,
        "enable_news_analysis": enable_news_analysis,
        "is_active": False,
    }
    await strategy_repo.create(data)
    return RedirectResponse(url="/strategies", status_code=302)


@router.get("/strategies/{strategy_id}/edit", response_class=HTMLResponse)
async def strategy_edit_page(request: Request, strategy_id: str, user: dict = Depends(require_auth)):
    strategy_repo = get_strategy_repo()
    strategy = await strategy_repo.get_by_id(strategy_id, user["user_id"])
    if not strategy:
        return RedirectResponse(url="/strategies", status_code=302)
    engine_running = await is_engine_running(user["user_id"])
    return templates.TemplateResponse("strategy_edit.html", {
        "request": request,
        "user": user,
        "strategy": strategy,
        "action": f"/strategies/{strategy_id}/edit",
        "engine_running": engine_running,
    })


@router.post("/strategies/{strategy_id}/edit")
async def strategy_update(
    request: Request,
    strategy_id: str,
    user: dict = Depends(require_auth),
    name: str = Form(...),
    symbol: str = Form(default="BTC-USDT-SWAP"),
    timeframe: str = Form(default="15m"),
    nl_strategy: Optional[str] = Form(default=None),
    default_leverage: int = Form(default=10),
    max_leverage: int = Form(default=20),
    position_size_pct: float = Form(default=30.0),
    ai_provider: str = Form(default="qwen"),
    ai_api_key: Optional[str] = Form(default=None),
    ai_base_url: Optional[str] = Form(default=None),
    ai_model: Optional[str] = Form(default=None),
    max_daily_loss_pct: float = Form(default=5.0),
    max_consecutive_losses: int = Form(default=3),
    max_position_pct: float = Form(default=50.0),
    enable_news_analysis: bool = Form(default=False),
    totp_token: str = Form(...),
):
    user_repo = get_user_repo()
    user_data = await user_repo.get_by_id(user["user_id"])
    if not user_data:
        return RedirectResponse(url=f"/strategies/{strategy_id}/edit?error=auth_failed", status_code=302)
    totp_secret = decrypt(user_data["totp_secret"])
    if not verify_token(totp_secret, totp_token):
        return RedirectResponse(url=f"/strategies/{strategy_id}/edit?error=totp_failed", status_code=302)

    strategy_repo = get_strategy_repo()
    strategy = await strategy_repo.get_by_id(strategy_id, user["user_id"])
    if strategy and strategy.get("is_active") and await is_engine_running(user["user_id"]):
        return RedirectResponse(url=f"/strategies/{strategy_id}/edit?error=engine_running", status_code=302)

    data = {
        "name": name,
        "symbol": symbol,
        "timeframe": timeframe,
        "nl_strategy": nl_strategy,
        "default_leverage": default_leverage,
        "max_leverage": max_leverage,
        "position_size_pct": position_size_pct,
        "ai_provider": ai_provider,
        "ai_base_url": ai_base_url,
        "ai_model": ai_model,
        "max_daily_loss_pct": max_daily_loss_pct,
        "max_consecutive_losses": max_consecutive_losses,
        "max_position_pct": max_position_pct,
        "enable_news_analysis": enable_news_analysis,
    }
    if ai_api_key:
        data["ai_api_key"] = encrypt(ai_api_key)
    await strategy_repo.update(strategy_id, user["user_id"], data)
    return RedirectResponse(url="/strategies", status_code=302)


@router.post("/strategies/{strategy_id}/activate")
async def strategy_activate(
    request: Request,
    strategy_id: str,
    user: dict = Depends(require_auth),
    totp_token: str = Form(...),
):
    if await is_engine_running(user["user_id"]):
        return RedirectResponse(url="/strategies?error=engine_running", status_code=302)
    user_repo = get_user_repo()
    user_data = await user_repo.get_by_id(user["user_id"])
    if not user_data:
        return RedirectResponse(url="/strategies?error=auth_failed", status_code=302)
    totp_secret = decrypt(user_data["totp_secret"])
    if not verify_token(totp_secret, totp_token):
        return RedirectResponse(url="/strategies?error=totp_failed", status_code=302)
    strategy_repo = get_strategy_repo()
    await strategy_repo.activate(strategy_id, user["user_id"])
    return RedirectResponse(url="/strategies", status_code=302)


@router.post("/strategies/{strategy_id}/delete")
async def strategy_delete(request: Request, strategy_id: str, user: dict = Depends(require_auth)):
    strategy_repo = get_strategy_repo()
    await strategy_repo.delete(strategy_id, user["user_id"])
    return RedirectResponse(url="/strategies", status_code=302)
