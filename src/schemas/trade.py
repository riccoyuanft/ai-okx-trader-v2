from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class TradeOpen(BaseModel):
    user_id: str
    strategy_id: str
    symbol: str
    direction: str
    leverage: int
    margin_mode: str = "isolated"
    entry_price: float
    qty: float
    open_time: datetime
    algo_order_id: Optional[str] = None
    ai_reasoning: Optional[str] = None
    news_context: Optional[str] = None


class TradeClose(BaseModel):
    exit_price: float
    pnl_usdt: float
    pnl_pct: float
    close_time: datetime
    close_reason: str


class TradeOut(BaseModel):
    id: str
    user_id: str
    strategy_id: str
    symbol: str
    direction: str
    leverage: int
    margin_mode: str
    entry_price: float
    exit_price: Optional[float] = None
    qty: float
    pnl_usdt: Optional[float] = None
    pnl_pct: Optional[float] = None
    open_time: datetime
    close_time: Optional[datetime] = None
    close_reason: Optional[str] = None
    algo_order_id: Optional[str] = None
    ai_reasoning: Optional[str] = None
    news_context: Optional[str] = None
