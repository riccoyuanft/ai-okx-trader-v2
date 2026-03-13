from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class UserBase(BaseModel):
    unique_id: str
    okx_testnet: bool = True


class UserCreate(UserBase):
    okx_api_key: str
    okx_secret_key: str
    okx_passphrase: str


class UserOut(UserBase):
    id: str
    is_active: bool
    created_at: datetime
    last_login: Optional[datetime] = None


class LoginForm(BaseModel):
    unique_id: str
    totp_token: str


class SetupForm(BaseModel):
    unique_id: str
    okx_api_key: str
    okx_secret_key: str
    okx_passphrase: str
    okx_testnet: bool = True


class TOTPConfirmForm(BaseModel):
    session_id: str
    totp_token: str
