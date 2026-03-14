from datetime import datetime, timezone
from typing import Optional
from supabase import create_client, Client
import httpx
from src.config.settings import get_settings
from functools import lru_cache


@lru_cache
def get_supabase() -> Client:
    settings = get_settings()
    try:
        client = create_client(settings.supabase_url, settings.supabase_key)
        # Replace PostgREST httpx session to bypass Windows system proxy.
        # The default session has trust_env=True which picks up HTTPS_PROXY from
        # the Windows registry/env and can cause SSL EOF errors through proxy tunnels.
        pg = client.postgrest
        if hasattr(pg, "session") and isinstance(pg.session, httpx.Client):
            old = pg.session
            pg.session = httpx.Client(
                base_url=old.base_url,
                headers=dict(old.headers),
                timeout=old.timeout,
                trust_env=False,
            )
        return client
    except Exception as e:
        raise RuntimeError(f"Failed to create Supabase client: {e}. Please check SUPABASE_URL and SUPABASE_KEY in .env") from e


class UserRepo:
    def __init__(self, db: Client):
        self.db = db

    async def get_by_id(self, user_id: str) -> Optional[dict]:
        res = self.db.table("users").select("*").eq("id", user_id).limit(1).execute()
        return res.data[0] if res.data else None

    async def get_by_unique_id(self, unique_id: str) -> Optional[dict]:
        res = self.db.table("users").select("*").eq("unique_id", unique_id).limit(1).execute()
        return res.data[0] if res.data else None

    async def create(self, unique_id: str, totp_secret_enc: str,
                     okx_api_key_enc: str, okx_secret_key_enc: str,
                     okx_passphrase_enc: str, okx_testnet: bool = True) -> dict:
        res = self.db.table("users").insert({
            "unique_id": unique_id,
            "totp_secret": totp_secret_enc,
            "okx_api_key": okx_api_key_enc,
            "okx_secret_key": okx_secret_key_enc,
            "okx_passphrase": okx_passphrase_enc,
            "okx_testnet": okx_testnet,
        }).execute()
        return res.data[0]

    async def update_last_login(self, user_id: str) -> None:
        from datetime import datetime, timezone
        self.db.table("users").update({"last_login": datetime.now(timezone.utc).isoformat()}).eq("id", user_id).execute()

    async def update_totp(self, user_id: str, totp_secret_enc: str) -> None:
        self.db.table("users").update({"totp_secret": totp_secret_enc}).eq("id", user_id).execute()

    async def update_testnet_credentials(
        self, user_id: str, okx_api_key_enc: str, okx_secret_key_enc: str,
        okx_passphrase_enc: str
    ) -> None:
        """Update testnet (模拟盘) OKX credentials."""
        self.db.table("users").update({
            "okx_api_key": okx_api_key_enc,
            "okx_secret_key": okx_secret_key_enc,
            "okx_passphrase": okx_passphrase_enc,
        }).eq("id", user_id).execute()

    async def update_live_credentials(
        self, user_id: str, okx_live_api_key_enc: str, okx_live_secret_key_enc: str,
        okx_live_passphrase_enc: str
    ) -> None:
        """Update live (实盘) OKX credentials."""
        self.db.table("users").update({
            "okx_live_api_key": okx_live_api_key_enc,
            "okx_live_secret_key": okx_live_secret_key_enc,
            "okx_live_passphrase": okx_live_passphrase_enc,
        }).eq("id", user_id).execute()

    async def switch_okx_mode(self, user_id: str, testnet: bool) -> None:
        """Switch active trading mode between testnet and live."""
        self.db.table("users").update({"okx_testnet": testnet}).eq("id", user_id).execute()

    async def set_engine_running(self, user_id: str, running: bool) -> None:
        """Persist engine on/off state so it survives server restarts."""
        self.db.table("users").update({"engine_running": running}).eq("id", user_id).execute()

    async def get_all_engine_running(self) -> list[dict]:
        """Return all users with engine_running=True (used on server startup)."""
        res = self.db.table("users").select("id").eq("engine_running", True).execute()
        return res.data or []

    async def update_notify_config(
        self, user_id: str, notify_provider: str, notify_webhook: str
    ) -> None:
        """Update webhook notification configuration."""
        self.db.table("users").update({
            "notify_provider": notify_provider,
            "notify_webhook": notify_webhook,
        }).eq("id", user_id).execute()

    async def check_and_deduct_credits(
        self, user_id: str, amount: int, note: str, allow_negative: bool = False
    ) -> tuple[bool, str]:
        """
        Check plan validity and credit balance, then deduct.
        Returns (True, "") on success or (False, reason) if blocked.
        allow_negative=True: skip balance check (used when holding an open position).
        Each user engine runs single-threaded so no concurrent write risk per user.
        """
        user = await self.get_by_id(user_id)
        if not user:
            return False, "用户不存在"

        # Check plan expiry (always enforced, even with open position)
        expires_at = user.get("plan_expires_at")
        if expires_at:
            expires = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
            if expires < datetime.now(timezone.utc):
                return False, "订阅已到期，请续费"
        else:
            return False, "未订阅，请先购买计划"

        balance = int(user.get("credits_balance") or 0)
        if balance < amount and not allow_negative:
            return False, f"积分不足（需{amount}，剩{balance}）"

        new_balance = balance - amount
        self.db.table("users").update({"credits_balance": new_balance}).eq("id", user_id).execute()

        # Audit log — fire-and-forget, never block the engine on log failure
        try:
            self.db.table("credit_transactions").insert({
                "user_id": user_id,
                "amount": -amount,
                "balance_after": new_balance,
                "note": note,
            }).execute()
        except Exception:
            pass

        return True, ""

    async def get_credit_info(self, user_id: str, limit: int = 20, offset: int = 0) -> dict:
        """Return credits_balance, plan_expires_at and paginated transactions."""
        user = await self.get_by_id(user_id)
        balance = int((user or {}).get("credits_balance") or 0)
        expires_at = (user or {}).get("plan_expires_at")
        try:
            res = (
                self.db.table("credit_transactions")
                .select("amount,balance_after,note,created_at")
                .eq("user_id", user_id)
                .order("created_at", desc=True)
                .range(offset, offset + limit - 1)
                .execute()
            )
            transactions = res.data or []
        except Exception:
            transactions = []
        return {
            "credits_balance": balance,
            "plan_expires_at": str(expires_at) if expires_at else None,
            "transactions": transactions,
            "has_more": len(transactions) == limit,
            "offset": offset,
        }


class StrategyRepo:
    def __init__(self, db: Client):
        self.db = db

    async def get_all_by_user(self, user_id: str) -> list[dict]:
        res = self.db.table("strategies").select("*").eq("user_id", user_id).order("created_at", desc=True).execute()
        return res.data or []

    async def get_by_id(self, strategy_id: str, user_id: str) -> Optional[dict]:
        res = self.db.table("strategies").select("*").eq("id", strategy_id).eq("user_id", user_id).limit(1).execute()
        return res.data[0] if res.data else None

    async def get_active(self, user_id: str) -> Optional[dict]:
        res = self.db.table("strategies").select("*").eq("user_id", user_id).eq("is_active", True).limit(1).execute()
        return res.data[0] if res.data else None

    async def create(self, data: dict) -> dict:
        res = self.db.table("strategies").insert(data).execute()
        return res.data[0]

    async def update(self, strategy_id: str, user_id: str, data: dict) -> dict:
        from datetime import datetime, timezone
        data["updated_at"] = datetime.now(timezone.utc).isoformat()
        res = self.db.table("strategies").update(data).eq("id", strategy_id).eq("user_id", user_id).execute()
        return res.data[0]

    async def deactivate_all(self, user_id: str) -> None:
        self.db.table("strategies").update({"is_active": False}).eq("user_id", user_id).execute()

    async def activate(self, strategy_id: str, user_id: str) -> None:
        await self.deactivate_all(user_id)
        self.db.table("strategies").update({"is_active": True}).eq("id", strategy_id).eq("user_id", user_id).execute()

    async def delete(self, strategy_id: str, user_id: str) -> None:
        self.db.table("strategies").delete().eq("id", strategy_id).eq("user_id", user_id).execute()


class TradeRepo:
    def __init__(self, db: Client):
        self.db = db

    async def create_open(self, data: dict) -> dict:
        res = self.db.table("trade_logs").insert(data).execute()
        return res.data[0]

    async def update_close(self, trade_id: str, user_id: str, close_data: dict) -> None:
        self.db.table("trade_logs").update(close_data).eq("id", trade_id).eq("user_id", user_id).execute()

    async def update_stop_loss(self, trade_id: str, user_id: str, stop_loss: float) -> None:
        self.db.table("trade_logs").update({"stop_loss": stop_loss}).eq("id", trade_id).eq("user_id", user_id).execute()

    async def get_by_user(
        self, user_id: str, limit: int = 50, offset: int = 0,
        is_testnet: Optional[bool] = None,
    ) -> list[dict]:
        q = (
            self.db.table("trade_logs")
            .select("*")
            .eq("user_id", user_id)
            .order("open_time", desc=True)
        )
        if is_testnet is not None:
            q = q.eq("is_testnet", is_testnet)
        res = q.range(offset, offset + limit - 1).execute()
        return res.data or []

    async def get_open_by_user(self, user_id: str) -> list[dict]:
        res = (
            self.db.table("trade_logs")
            .select("*")
            .eq("user_id", user_id)
            .is_("close_time", "null")
            .execute()
        )
        return res.data or []


def get_user_repo() -> UserRepo:
    return UserRepo(get_supabase())


def get_strategy_repo() -> StrategyRepo:
    return StrategyRepo(get_supabase())


def get_trade_repo() -> TradeRepo:
    return TradeRepo(get_supabase())
