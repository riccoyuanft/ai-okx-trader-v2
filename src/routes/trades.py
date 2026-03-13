from fastapi import APIRouter, Request, Depends, Query
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from src.auth.jwt import get_current_user
from src.db.supabase_client import get_trade_repo

router = APIRouter()
templates = Jinja2Templates(directory="src/templates")


def require_auth(request: Request) -> dict:
    return get_current_user(request)


@router.get("/trades", response_class=HTMLResponse)
async def trades_list(
    request: Request,
    user: dict = Depends(require_auth),
    page: int = Query(default=1, ge=1),
):
    limit = 20
    offset = (page - 1) * limit
    trade_repo = get_trade_repo()
    trades = await trade_repo.get_by_user(user["user_id"], limit=limit, offset=offset)

    total_pnl = sum(t["pnl_usdt"] or 0 for t in trades if t.get("pnl_usdt") is not None)
    closed = [t for t in trades if t.get("close_time")]
    win_count = sum(1 for t in closed if (t.get("pnl_usdt") or 0) > 0)
    win_rate = (win_count / len(closed) * 100) if closed else 0

    return templates.TemplateResponse("trades.html", {
        "request": request,
        "user": user,
        "trades": trades,
        "page": page,
        "has_more": len(trades) == limit,
        "total_pnl": round(total_pnl, 2),
        "win_rate": round(win_rate, 1),
        "closed_count": len(closed),
    })
