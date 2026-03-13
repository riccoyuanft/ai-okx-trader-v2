import secrets
import traceback
from fastapi import APIRouter, Request, Form, Depends
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from loguru import logger

from src.auth.totp import generate_secret, verify_token, generate_qr_base64
from src.auth.jwt import create_access_token
from src.auth.crypto import encrypt, decrypt
from src.db.supabase_client import get_user_repo
from src.db.redis_client import set_setup_session, get_setup_session, delete_setup_session
from src.engine.okx_client import test_okx_credentials

router = APIRouter()
templates = Jinja2Templates(directory="src/templates")

logger.info("auth.py module loaded, router initialized")


@router.post("/test-post")
async def test_post(request: Request):
    logger.info("TEST POST ROUTE HIT")
    body = await request.body()
    logger.info(f"Body: {body[:200]}")
    return {"status": "ok"}


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})


@router.post("/login")
async def login(
    request: Request,
    unique_id: str = Form(...),
    totp_token: str = Form(...),
):
    user_repo = get_user_repo()
    user = await user_repo.get_by_unique_id(unique_id)

    if not user:
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "账户不存在，请先激活"},
            status_code=400,
        )

    totp_secret = decrypt(user["totp_secret"])
    if not verify_token(totp_secret, totp_token):
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "验证码错误或已过期"},
            status_code=400,
        )

    await user_repo.update_last_login(user["id"])
    token = create_access_token(user["id"], user["unique_id"], okx_testnet=bool(user.get("okx_testnet", True)))

    response = RedirectResponse(url="/dashboard", status_code=302)
    response.set_cookie("access_token", token, httponly=True, samesite="lax")
    return response


@router.get("/setup", response_class=HTMLResponse)
async def setup_page(request: Request):
    return templates.TemplateResponse("setup.html", {"request": request, "step": "form"})


@router.post("/setup/init")
async def setup_init(
    request: Request,
    unique_id: str = Form(...),
    okx_api_key: str = Form(...),
    okx_secret_key: str = Form(...),
    okx_passphrase: str = Form(...),
    okx_testnet: str = Form("true"),
):
    try:
        okx_testnet_bool = okx_testnet.lower() in ("true", "1", "on")
        logger.info(f"[setup/init] unique_id={unique_id} okx_testnet={okx_testnet_bool}")

        logger.debug("[setup/init] querying user repo...")
        user_repo = get_user_repo()
        existing = await user_repo.get_by_unique_id(unique_id)
        logger.debug(f"[setup/init] existing user: {existing}")
        if existing:
            return templates.TemplateResponse(
                "setup.html",
                {"request": request, "step": "form", "error": "该 ID 已被注册，请直接登录"},
                status_code=400,
            )

        # Validate OKX API credentials before proceeding
        logger.debug("[setup/init] testing OKX credentials...")
        ok, err_msg = await test_okx_credentials(
            okx_api_key, okx_secret_key, okx_passphrase, testnet=okx_testnet_bool
        )
        if not ok:
            return templates.TemplateResponse(
                "setup.html",
                {"request": request, "step": "form", "error": f"API Key 验证失败：{err_msg}"},
                status_code=400,
            )
        logger.info(f"[setup/init] OKX credentials verified (testnet={okx_testnet_bool})")

        logger.debug("[setup/init] generating TOTP secret...")
        totp_secret = generate_secret()
        logger.debug("[setup/init] generating QR code...")
        qr_base64 = generate_qr_base64(totp_secret, unique_id)

        session_id = secrets.token_urlsafe(32)
        logger.debug(f"[setup/init] saving setup session {session_id[:8]}...")
        await set_setup_session(session_id, {
            "unique_id": unique_id,
            "okx_api_key": okx_api_key,
            "okx_secret_key": okx_secret_key,
            "okx_passphrase": okx_passphrase,
            "okx_testnet": okx_testnet_bool,
            "totp_secret": totp_secret,
        })
        logger.info(f"[setup/init] success, redirecting to QR step")

        return templates.TemplateResponse("setup.html", {
            "request": request,
            "step": "qr",
            "session_id": session_id,
            "qr_base64": qr_base64,
            "unique_id": unique_id,
        })
    except Exception as e:
        logger.error(f"[setup/init] UNHANDLED ERROR: {e}\n{traceback.format_exc()}")
        raise


@router.post("/setup/confirm")
async def setup_confirm(
    request: Request,
    session_id: str = Form(...),
    totp_token: str = Form(...),
):
    session = await get_setup_session(session_id)
    if not session:
        return templates.TemplateResponse(
            "setup.html",
            {"request": request, "step": "form", "error": "会话已过期，请重新开始"},
            status_code=400,
        )

    if not verify_token(session["totp_secret"], totp_token):
        qr_base64 = generate_qr_base64(session["totp_secret"], session["unique_id"])
        return templates.TemplateResponse("setup.html", {
            "request": request,
            "step": "qr",
            "session_id": session_id,
            "qr_base64": qr_base64,
            "unique_id": session["unique_id"],
            "error": "验证码错误，请重试",
        }, status_code=400)

    user_repo = get_user_repo()
    user = await user_repo.create(
        unique_id=session["unique_id"],
        totp_secret_enc=encrypt(session["totp_secret"]),
        okx_api_key_enc=encrypt(session["okx_api_key"]),
        okx_secret_key_enc=encrypt(session["okx_secret_key"]),
        okx_passphrase_enc=encrypt(session["okx_passphrase"]),
        okx_testnet=session["okx_testnet"],
    )

    await delete_setup_session(session_id)

    token = create_access_token(user["id"], user["unique_id"], okx_testnet=session["okx_testnet"])
    response = RedirectResponse(url="/dashboard", status_code=302)
    response.set_cookie("access_token", token, httponly=True, samesite="lax")
    return response


@router.post("/logout")
async def logout():
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie("access_token")
    return response
