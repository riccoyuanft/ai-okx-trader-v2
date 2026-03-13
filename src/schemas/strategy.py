from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime


class StrategyBase(BaseModel):
    name: str
    symbol: str = "BTC-USDT-SWAP"
    timeframe: str = "15m"
    nl_strategy: Optional[str] = None

    default_leverage: int = Field(default=10, ge=1, le=125)
    max_leverage: int = Field(default=20, ge=1, le=125)
    position_size_pct: float = Field(default=30.0, ge=1.0, le=100.0)

    ai_provider: str = "qwen"
    ai_api_key: Optional[str] = None
    ai_base_url: Optional[str] = None
    ai_model: Optional[str] = None

    max_daily_loss_pct: float = Field(default=5.0, ge=0.1, le=100.0)
    max_consecutive_losses: int = Field(default=3, ge=1, le=20)
    max_position_pct: float = Field(default=50.0, ge=1.0, le=100.0)
    stop_on_breach: bool = True

    enable_news_analysis: bool = False


class StrategyCreate(StrategyBase):
    pass


class StrategyUpdate(StrategyBase):
    pass


class StrategyOut(StrategyBase):
    id: str
    user_id: str
    is_active: bool
    created_at: datetime
    updated_at: datetime
