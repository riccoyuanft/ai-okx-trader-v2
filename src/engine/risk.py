"""
Risk control rules — all hardcoded, cannot be disabled by users.

Rules (enforced in order):
  1. Margin mode: forced isolated — set on engine init via OKX API
  2. Stop-loss: OKX native algo order required on every open trade (primary)
     + software price monitor as backup
  3. Daily loss limit: cumulative daily PnL <= -max_daily_loss_pct → stop engine
  4. Consecutive loss cooldown: count >= max_consecutive_losses → 60-min pause
  5. Position size: single open <= max_position_pct of account balance
  6. Leverage: capped at strategy.max_leverage (AI value clipped, not rejected)
  7. Liquidation guard: distance to liq price < 1.5% of current price → force close (engine continues)
"""
import time
from dataclasses import dataclass, field
from typing import Optional

_COOLDOWN_SECONDS = 3600  # 60-minute cooldown after max consecutive losses


@dataclass
class RiskState:
    daily_pnl_usdt: float = 0.0
    consecutive_losses: int = 0
    cooldown_until: Optional[float] = None
    day_start_ts: float = field(default_factory=time.time)


def validate_pre_order(
    ai_decision: dict,
    account_balance: float,
    strategy: dict,
    risk_state: RiskState,
) -> tuple[bool, str]:
    """
    Validate AI decision against all risk rules before placing an order.
    Mutates ai_decision in-place to cap leverage/position_pct within limits.
    Returns (ok, reason).
    """
    now = time.time()

    # Reset day counter if new UTC day
    day_elapsed = now - risk_state.day_start_ts
    if day_elapsed >= 86400:
        risk_state.daily_pnl_usdt = 0.0
        risk_state.day_start_ts = now

    # 1. Check cooldown
    if risk_state.cooldown_until and now < risk_state.cooldown_until:
        remaining_min = int((risk_state.cooldown_until - now) / 60)
        return False, f"连续亏损冷却中，还有 {remaining_min} 分钟"

    # Clear expired cooldown
    if risk_state.cooldown_until and now >= risk_state.cooldown_until:
        risk_state.cooldown_until = None
        risk_state.consecutive_losses = 0

    # 2. Check daily loss limit
    max_daily_loss_pct = float(strategy.get("max_daily_loss_pct") or 5.0)
    if account_balance > 0 and risk_state.daily_pnl_usdt < 0:
        daily_loss_pct = abs(risk_state.daily_pnl_usdt) / account_balance * 100
        if daily_loss_pct >= max_daily_loss_pct:
            return False, f"日亏损已达 {daily_loss_pct:.1f}%，超过上限 {max_daily_loss_pct}%，今日停止交易"

    # 3. Check consecutive loss cooldown trigger
    max_consecutive = int(strategy.get("max_consecutive_losses") or 3)
    if risk_state.consecutive_losses >= max_consecutive:
        risk_state.cooldown_until = now + _COOLDOWN_SECONDS
        return False, f"连续亏损 {risk_state.consecutive_losses} 次，进入 60 分钟冷却"

    # 4. Cap leverage (mutate in-place rather than reject)
    max_leverage = int(strategy.get("max_leverage") or 20)
    if ai_decision.get("leverage", 1) > max_leverage:
        ai_decision["leverage"] = max_leverage

    # 5. Cap position size
    max_position_pct = float(strategy.get("max_position_pct") or 50.0)
    if ai_decision.get("position_pct", 0) > max_position_pct:
        ai_decision["position_pct"] = max_position_pct

    # 6. Stop-loss required for open orders
    action = ai_decision.get("action", "wait")
    if action in ("long", "short"):
        sl = ai_decision.get("stop_loss")
        if not sl or float(sl) <= 0:
            return False, "AI 未提供有效止损价格，拒绝开仓"

    return True, "OK"


def check_liquidation_proximity(
    current_price: float,
    liquidation_price: float,
    direction: str,
    threshold_pct: float = 1.5,
) -> bool:
    """
    Returns True if the position is dangerously close to liquidation.
    At 20x leverage liq is ~4.5% from entry; 1.5% threshold fires only as last resort.
    """
    if liquidation_price <= 0:
        return False
    if direction == "long":
        dist_pct = (current_price - liquidation_price) / current_price * 100
    else:
        dist_pct = (liquidation_price - current_price) / current_price * 100
    return dist_pct < threshold_pct


def record_trade_result(risk_state: RiskState, pnl_usdt: float) -> RiskState:
    """Update risk state after a trade closes."""
    risk_state.daily_pnl_usdt += pnl_usdt
    if pnl_usdt < 0:
        risk_state.consecutive_losses += 1
    else:
        risk_state.consecutive_losses = 0
    return risk_state
