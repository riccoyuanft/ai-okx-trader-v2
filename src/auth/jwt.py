from datetime import datetime, timedelta, timezone
from typing import Optional
import jwt
from fastapi import Request, HTTPException, status
from src.config.settings import get_settings


def create_access_token(user_id: str, unique_id: str, okx_testnet: bool = True) -> str:
    settings = get_settings()
    expire = datetime.now(timezone.utc) + timedelta(hours=settings.jwt_expire_hours)
    payload = {
        "sub": user_id,
        "uid": unique_id,
        "tn": okx_testnet,
        "exp": expire,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_access_token(token: str) -> Optional[dict]:
    try:
        settings = get_settings()
        return jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


def get_current_user(request: Request) -> dict:
    token = request.cookies.get("access_token")
    if not token:
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": "/login"},
        )
    payload = decode_access_token(token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": "/login"},
        )
    return {
        "user_id": payload["sub"],
        "unique_id": payload["uid"],
        "okx_testnet": payload.get("tn", True),
    }
