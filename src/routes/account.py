from fastapi import APIRouter, Request, Form, Depends, Query
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from src.auth.jwt import get_current_user, create_access_token
from src.auth.totp import verify_token
from src.auth.crypto import encrypt, decrypt
from src.db.supabase_client import get_user_repo
from src.db.redis_client import is_engine_running

router = APIRouter()
templates = Jinja2Templates(directory="src/templates")


def require_auth(request: Request) -> dict:
    return get_current_user(request)


@router.get("/account", response_class=HTMLResponse)
async def account_page(request: Request, user: dict = Depends(require_auth)):
    user_repo = get_user_repo()
    user_data = await user_repo.get_by_id(user["user_id"])
    credit_info = await user_repo.get_credit_info(user["user_id"])
    engine_running = await is_engine_running(user["user_id"])

    response = templates.TemplateResponse("account.html", {
        "request": request,
        "user": user,
        "user_data": user_data,
        "credit_info": credit_info,
        "engine_running": engine_running,
    })
    # Sync JWT if DB mode differs (e.g. stale JWT without 'tn' field)
    db_testnet = bool(user_data.get("okx_testnet", True)) if user_data else True
    if user.get("okx_testnet") != db_testnet:
        new_token = create_access_token(user["user_id"], user["unique_id"], okx_testnet=db_testnet)
        response.set_cookie("access_token", new_token, httponly=True, samesite="lax")
    return response


async def _verify_totp_or_error(request, user, user_data, totp_token, templates):
    """Returns error response if TOTP invalid, else None."""
    totp_secret = decrypt(user_data["totp_secret"])
    if not verify_token(totp_secret, totp_token):
        return templates.TemplateResponse(
            "account.html",
            {"request": request, "user": user, "user_data": user_data, "error": "验证码错误或已过期"},
            status_code=400,
        )
    return None


@router.post("/account/update-testnet")
async def update_testnet_credentials(
    request: Request,
    user: dict = Depends(require_auth),
    okx_api_key: str = Form(...),
    okx_secret_key: str = Form(...),
    okx_passphrase: str = Form(...),
    totp_token: str = Form(...),
):
    user_repo = get_user_repo()
    user_data = await user_repo.get_by_id(user["user_id"])
    if not user_data:
        return templates.TemplateResponse("account.html", {"request": request, "user": user, "error": "用户不存在"}, status_code=400)

    err = await _verify_totp_or_error(request, user, user_data, totp_token, templates)
    if err:
        return err

    if await is_engine_running(user["user_id"]):
        credit_info = await user_repo.get_credit_info(user["user_id"])
        return templates.TemplateResponse(
            "account.html",
            {"request": request, "user": user, "user_data": user_data,
             "credit_info": credit_info, "engine_running": True,
             "error": "自动交易运行中，请先停止引擎再修改 API 凭证"},
            status_code=400,
        )

    await user_repo.update_testnet_credentials(
        user_id=user["user_id"],
        okx_api_key_enc=encrypt(okx_api_key),
        okx_secret_key_enc=encrypt(okx_secret_key),
        okx_passphrase_enc=encrypt(okx_passphrase),
    )
    return RedirectResponse(url="/account?success=testnet_updated", status_code=302)


@router.post("/account/update-live")
async def update_live_credentials(
    request: Request,
    user: dict = Depends(require_auth),
    okx_live_api_key: str = Form(...),
    okx_live_secret_key: str = Form(...),
    okx_live_passphrase: str = Form(...),
    totp_token: str = Form(...),
):
    user_repo = get_user_repo()
    user_data = await user_repo.get_by_id(user["user_id"])
    if not user_data:
        return templates.TemplateResponse("account.html", {"request": request, "user": user, "error": "用户不存在"}, status_code=400)

    err = await _verify_totp_or_error(request, user, user_data, totp_token, templates)
    if err:
        return err

    if await is_engine_running(user["user_id"]):
        credit_info = await user_repo.get_credit_info(user["user_id"])
        return templates.TemplateResponse(
            "account.html",
            {"request": request, "user": user, "user_data": user_data,
             "credit_info": credit_info, "engine_running": True,
             "error": "自动交易运行中，请先停止引擎再修改 API 凭证"},
            status_code=400,
        )

    await user_repo.update_live_credentials(
        user_id=user["user_id"],
        okx_live_api_key_enc=encrypt(okx_live_api_key),
        okx_live_secret_key_enc=encrypt(okx_live_secret_key),
        okx_live_passphrase_enc=encrypt(okx_live_passphrase),
    )
    return RedirectResponse(url="/account?success=live_updated", status_code=302)


@router.post("/account/switch-mode")
async def switch_mode(
    request: Request,
    user: dict = Depends(require_auth),
    mode: str = Form(...),
    totp_token: str = Form(...),
):
    user_repo = get_user_repo()
    user_data = await user_repo.get_by_id(user["user_id"])
    if not user_data:
        return templates.TemplateResponse("account.html", {"request": request, "user": user, "error": "用户不存在"}, status_code=400)

    err = await _verify_totp_or_error(request, user, user_data, totp_token, templates)
    if err:
        return err

    # Guard: cannot switch mode while engine is running
    if await is_engine_running(user["user_id"]):
        credit_info = await user_repo.get_credit_info(user["user_id"])
        return templates.TemplateResponse(
            "account.html",
            {"request": request, "user": user, "user_data": user_data,
             "credit_info": credit_info, "engine_running": True,
             "error": "自动交易运行中，请先停止引擎再切换模式"},
            status_code=400,
        )

    testnet = (mode == "testnet")
    # Guard: cannot switch to live if live credentials not set
    if not testnet and not user_data.get("okx_live_api_key"):
        credit_info = await user_repo.get_credit_info(user["user_id"])
        return templates.TemplateResponse(
            "account.html",
            {"request": request, "user": user, "user_data": user_data,
             "credit_info": credit_info, "engine_running": False,
             "error": "请先填写实盘 API Key 再切换到实盘模式"},
            status_code=400,
        )

    await user_repo.switch_okx_mode(user_id=user["user_id"], testnet=testnet)
    # Re-issue JWT so the new mode badge propagates to all pages immediately
    new_token = create_access_token(user["user_id"], user["unique_id"], okx_testnet=testnet)
    mode_label = "testnet" if testnet else "live"
    response = RedirectResponse(url=f"/account?success=mode_{mode_label}", status_code=302)
    response.set_cookie("access_token", new_token, httponly=True, samesite="lax")
    return response


@router.get("/api/credits/history")
async def credits_history(
    request: Request,
    user: dict = Depends(require_auth),
    offset: int = Query(default=0, ge=0),
):
    """Return credit info + paginated transactions (20 per page)."""
    user_repo = get_user_repo()
    data = await user_repo.get_credit_info(user["user_id"], limit=20, offset=offset)
    return JSONResponse(data)


@router.post("/account/update-notify")
async def update_notify_config(
    request: Request,
    user: dict = Depends(require_auth),
    notify_provider: str = Form("dingtalk"),
    notify_webhook: str = Form(""),
):
    user_repo = get_user_repo()
    await user_repo.update_notify_config(
        user_id=user["user_id"],
        notify_provider=notify_provider,
        notify_webhook=notify_webhook.strip(),
    )
    return RedirectResponse(url="/account?success=notify_updated", status_code=302)
