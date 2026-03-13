import asyncio
from datetime import datetime, timezone
from typing import Optional

from loguru import logger

from src.auth.crypto import decrypt
from src.db.redis_client import set_position, clear_position, set_ai_plan, clear_ai_plan
from src.db.supabase_client import get_user_repo, get_trade_repo
from src.engine.okx_client import OKXClient
from src.engine.ta_calc import build_multi_tf_summary, format_multi_tf_klines, _MIN_CANDLES as _TA_MIN_CANDLES
from src.engine.risk import RiskState, validate_pre_order, check_liquidation_proximity, record_trade_result
from src.ai.trading_agent import get_trading_decision
from src.engine.notifier import send_notification

# Credits deducted per AI decision call
_CREDIT_COST_TESTNET = 1  # 模拟盘
_CREDIT_COST_LIVE = 2     # 实盘

_TF_SECONDS = {
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1H": 3600,
    "4H": 14400,
}

# Multi-timeframe context strategy: entry TF + 2 higher TFs following trading conventions
_CONTEXT_TF_MAP: dict[str, list[str]] = {
    "1m":  ["1m",  "5m",  "15m"],
    "5m":  ["5m",  "15m", "1H"],
    "15m": ["15m", "1H",  "4H"],
    "30m": ["30m", "1H",  "4H"],
    "1H":  ["1H",  "4H"],
    "4H":  ["4H",  "1H"],
}
# Candle counts per TF: more candles for shorter TFs (covers ~8-16 hours each)
_TF_KLINE_COUNTS: dict[str, int] = {
    "1m":  120,
    "5m":  48,
    "15m": 32,
    "30m": 24,
    "1H":  16,
    "4H":  12,
}


class UserEngine:
    """
    Single-user trading engine. Runs as an asyncio.Task managed by UserEngineManager.
    Full pipeline: OKX data → TA → AI → risk validation → order execution.
    """

    def __init__(
        self,
        user_id: str,
        strategy: dict,
        log_queue: asyncio.Queue,
        log_buffer: Optional[list] = None,
        log_buffer_size: int = 200,
    ):
        self.user_id = user_id
        self.strategy = strategy
        self.log_queue = log_queue
        self._log_buffer = log_buffer if log_buffer is not None else []
        self._log_buffer_size = log_buffer_size
        self._running = False

        self._okx: Optional[OKXClient] = None
        self._risk_state = RiskState()

        self._current_trade_id: Optional[str] = None
        self._current_algo_id: Optional[str] = None
        self._current_stop_loss: Optional[float] = None

        self._notify_provider: str = "dingtalk"
        self._notify_webhook: Optional[str] = None

        self._ai_history: list[dict] = []  # per-user per-strategy conversation history
        self._manual_close_event: asyncio.Event = asyncio.Event()  # set by manager for user-initiated close
        self._is_testnet: bool = True  # set in _setup; determines credit cost
        self._unique_id: str = user_id[:8]  # human-readable label for server logs; updated in _setup

    # ── Logging ──────────────────────────────────────────────────────────────

    async def _log(self, msg: str) -> None:
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        line = f"[{ts}] {msg}\n"
        logger.info(f"[{self._unique_id}] {msg}")
        self._log_buffer.append(line)
        if len(self._log_buffer) > self._log_buffer_size:
            del self._log_buffer[0]
        try:
            self.log_queue.put_nowait(line)
        except asyncio.QueueFull:
            pass

    # ── Lifecycle ────────────────────────────────────────────────────────────

    async def _notify(self, title: str, content: str) -> None:
        """Fire-and-forget webhook notification."""
        if self._notify_webhook:
            await send_notification(self._notify_provider, self._notify_webhook, title, content)

    async def run(self) -> None:
        self._running = True
        strategy_name = self.strategy.get('name')
        symbol = self.strategy.get('symbol')
        await self._log(f"自动交易启动 | 策略: {strategy_name} | 标的: {symbol}")
        try:
            await self._setup()
            await asyncio.gather(
                self._decision_loop(),
                self._price_monitor(),
            )
        except asyncio.CancelledError:
            await self._log("自动交易已停止")
            raise
        except Exception as e:
            await self._log(f"自动交易异常退出: {e}")
            logger.exception(f"[{self._unique_id}] engine crash")
            await self._notify(
                f"⚠️ 引擎异常退出 [{symbol}]",
                f"策略: {strategy_name}\n错误: {e}"
            )
            raise
        finally:
            self._running = False

    async def _setup(self) -> None:
        """Decrypt credentials, create OKX client, set margin mode and leverage."""
        user_repo = get_user_repo()
        user = await user_repo.get_by_id(self.user_id)
        if not user:
            raise RuntimeError(f"User {self.user_id} not found in DB")

        testnet = bool(user.get("okx_testnet", True))
        mode = "模拟盘" if testnet else "实盘"

        if testnet:
            api_key_field, secret_field, pass_field = "okx_api_key", "okx_secret_key", "okx_passphrase"
        else:
            api_key_field, secret_field, pass_field = "okx_live_api_key", "okx_live_secret_key", "okx_live_passphrase"

        if not user.get(api_key_field):
            raise RuntimeError(f"用户 {self.user_id} 尚未配置{mode} API Key，请先在账户管理页面填写")

        okx_key = decrypt(user[api_key_field])
        okx_secret = decrypt(user[secret_field])
        okx_pass = decrypt(user[pass_field])

        logger.debug(f"[{user.get('unique_id', self.user_id[:8])}] mode={mode} api_key={okx_key[:8]}...")

        self._okx = OKXClient(okx_key, okx_secret, okx_pass, testnet)
        pos_mode = await self._okx.init_pos_mode()
        await self._log(f"OKX 客户端已初始化 ({mode}) | 仓位模式: {pos_mode}")

        self._is_testnet = testnet
        self._unique_id = user.get("unique_id") or self.user_id[:8]
        self._notify_provider = user.get("notify_provider") or "dingtalk"
        self._notify_webhook = user.get("notify_webhook") or None
        if self._notify_webhook:
            await self._log(f"通知已开启 ({self._notify_provider})")

        symbol = self.strategy["symbol"]
        leverage = int(self.strategy.get("default_leverage") or 10)
        try:
            await self._okx.set_leverage(symbol, leverage, "isolated")
            await self._log(f"逐仓保证金 + 杠杆 {leverage}x 设置成功")
        except Exception as e:
            await self._log(f"设置杠杆警告: {e}（继续运行）")

        # Restore open position state from DB so trade_id persists across restarts
        await self._restore_open_trade(symbol)

    async def _restore_open_trade(self, symbol: str) -> None:
        """On startup, check if an open position exists and link it to the open DB trade."""
        try:
            position = await self._okx.get_position(symbol)
            if not position:
                return
            trade_repo = get_trade_repo()
            open_trades = await trade_repo.get_open_by_user(self.user_id)
            # Find the open trade matching this symbol
            for trade in open_trades:
                if trade.get("symbol") == symbol:
                    self._current_trade_id = trade["id"]
                    self._current_stop_loss = float(trade.get("stop_loss") or 0) or None
                    await self._log(
                        f"恢复未平仓记录: {position['direction']}单 {position['qty']}张 "
                        f"开仓价:{position['entry_price']} 止损:{self._current_stop_loss}"
                    )
                    return
            # Position exists on OKX but no DB record — log warning
            await self._log(
                f"警告: OKX存在{position['direction']}仓位但数据库无对应记录，将追踪但不更新历史"
            )
        except Exception as e:
            await self._log(f"恢复持仓状态失败（继续运行）: {e}")

    # ── Decision loop ────────────────────────────────────────────────────────

    async def _decision_loop(self) -> None:
        timeframe = self.strategy.get("timeframe", "15m")
        interval = _TF_SECONDS.get(timeframe, 900)
        await self._log(f"决策循环启动 (周期: {timeframe}, 间隔: {interval}s)")

        # First tick immediately
        try:
            await self._tick()
        except Exception as e:
            await self._log(f"首次决策异常: {e}")

        while True:
            await asyncio.sleep(interval)
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                await self._log(f"决策异常: {e}")
                logger.exception(f"[{self._unique_id}] tick error")

    async def _tick(self) -> None:
        """One full decision cycle."""
        symbol = self.strategy["symbol"]
        timeframe = self.strategy.get("timeframe", "15m")

        # 1. Determine context TFs (strategy TF + higher TFs) and candle counts
        context_tfs = _CONTEXT_TF_MAP.get(timeframe, [timeframe, "1H"])
        # Display counts (what AI sees in raw K-line section)
        primary_display = _TF_KLINE_COUNTS.get(timeframe, 48)
        # Fetch counts: always >= _TA_MIN_CANDLES so TA indicators have enough data
        primary_fetch = max(primary_display, _TA_MIN_CANDLES)

        # Fetch primary TF + extra TFs concurrently
        klines = await self._okx.get_klines(symbol, timeframe, primary_fetch)
        current_price = klines[-1]["close"] if klines else 0

        extra_tfs = [tf for tf in context_tfs if tf != timeframe]
        extra_display = {tf: _TF_KLINE_COUNTS.get(tf, 16) for tf in extra_tfs}
        extra_fetch = {tf: max(cnt, _TA_MIN_CANDLES) for tf, cnt in extra_display.items()}
        extra_results = await asyncio.gather(
            *[self._okx.get_klines(symbol, tf, extra_fetch[tf]) for tf in extra_tfs],
            return_exceptions=True,
        )
        klines_by_tf: dict[str, list] = {timeframe: klines}
        for tf, res in zip(extra_tfs, extra_results):
            if not isinstance(res, Exception):
                klines_by_tf[tf] = res

        # 2. Build ordered TF dict and compute TA + raw K-line strings
        ordered = {tf: klines_by_tf[tf] for tf in context_tfs if tf in klines_by_tf}
        # display_counts: show only the requested candle count in K-line section
        display_counts = {timeframe: primary_display, **extra_display}
        ta_summary = build_multi_tf_summary(ordered)
        klines_section = format_multi_tf_klines(ordered, counts=display_counts)

        # 3. Funding rate
        try:
            funding = await self._okx.get_funding_rate(symbol)
        except Exception:
            funding = {"funding_rate": 0.0, "next_funding_time": 0}

        next_ts = funding.get("next_funding_time", 0)
        next_str = (
            datetime.fromtimestamp(next_ts / 1000, tz=timezone.utc).strftime("%H:%M UTC")
            if next_ts else "未知"
        )

        # 4. Account balance
        balance = await self._okx.get_account_balance()

        # 5. Current position
        position = await self._okx.get_position(symbol)
        if position:
            await set_position(self.user_id, {**position, "current_price": current_price})
        else:
            await clear_position(self.user_id)

        # 6. Recent trade history (last 5 closed trades for AI context)
        trade_repo = get_trade_repo()
        recent_trades = await trade_repo.get_by_user(self.user_id, limit=5)
        closed_trades = [t for t in recent_trades if t.get("close_time")]
        if closed_trades:
            await self._log(f"历史交易: 最近 {len(closed_trades)} 笔已平仓记录已传入 AI")

        # 7. News (if enabled)
        news_sentiment = None
        if self.strategy.get("enable_news_analysis"):
            from src.db.redis_client import get_latest_news
            items = await get_latest_news(10)
            if items:
                headlines = " | ".join(i.get("title", "") for i in items[:5])
                news_sentiment = {"summary": headlines}

        # 8. Credit check before AI call
        # Rule: never block AI when holding open position (position must always be monitored).
        # Credit errors (DB unavailable, etc.) are logged but never stop the engine.
        credit_cost = _CREDIT_COST_TESTNET if self._is_testnet else _CREDIT_COST_LIVE
        mode_label = "模拟盘" if self._is_testnet else "实盘"
        if position is None:
            # No position — enforce credit gate
            try:
                user_repo = get_user_repo()
                credit_ok, credit_reason = await user_repo.check_and_deduct_credits(
                    self.user_id, credit_cost,
                    note=f"AI决策: {symbol} {timeframe} ({mode_label})",
                    allow_negative=False,
                )
                if not credit_ok:
                    await self._log(f"[积分] AI决策跳过: {credit_reason}")
                    return
            except Exception as e:
                await self._log(f"[积分] 扣除失败，继续执行: {e}")
        else:
            # Holding position — always allow AI; deduct optimistically (allow debt)
            try:
                user_repo = get_user_repo()
                credit_ok, credit_reason = await user_repo.check_and_deduct_credits(
                    self.user_id, credit_cost,
                    note=f"AI决策: {symbol} {timeframe} ({mode_label})",
                    allow_negative=True,
                )
                if credit_ok:
                    remaining = int((await user_repo.get_by_id(self.user_id) or {}).get("credits_balance") or 0)
                    if remaining < 0:
                        await self._log(f"[积分] ⚠️ 已透支 ({remaining})，请尽快充值")
                elif credit_reason.startswith("订阅已到期") or credit_reason.startswith("未订阅"):
                    await self._log(f"[积分] {credit_reason}，因持仓继续监控")
            except Exception as e:
                await self._log(f"[积分] 扣除失败，继续执行: {e}")

        # 9. AI decision
        market_data = {
            "symbol": symbol,
            "timeframe": timeframe,
            "balance_usdt": balance.get("equity", 0),
            "available_usdt": balance.get("available", 0),
            "ta_summary": ta_summary,
            "klines_section": klines_section,
            "funding_rate": funding.get("funding_rate", 0),
            "next_funding_str": next_str,
        }
        await self._log(f"AI 决策中... 价格: {current_price}")

        decision, self._ai_history = await get_trading_decision(
            strategy=self.strategy,
            market_data=market_data,
            position_state=position,
            recent_trades=closed_trades,
            news_sentiment=news_sentiment,
            history=self._ai_history,
        )
        await self._log(f"[AI Prompt] 标的:{symbol} 余额:{balance.get('equity',0):.2f} 持仓:{position is not None} 历史:{len(closed_trades)}笔")
        await self._log(f"[AI Response] {decision.get('reason', '')} → action={decision.get('action','').upper()} lev={decision.get('leverage')}x pos={decision.get('position_pct')}% sl={decision.get('stop_loss')}")

        action = decision.get("action", "wait")
        reason = decision.get("reason", "")
        await self._log(f"AI决策: {action.upper()} — {reason}")

        # Persist AI plan so dashboard can display it
        await set_ai_plan(self.user_id, {
            "action": action,
            "stop_loss": decision.get("stop_loss"),
            "take_profit": decision.get("take_profit") or [],
            "leverage": decision.get("leverage"),
            "reason": reason,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })

        # 8. Execute
        if action == "wait":
            return

        if action == "close":
            if not position:
                await self._log("无持仓，无法平仓")
                return
            await self._close_position(position, reason="ai_close")
            return

        if action in ("long", "short"):
            if position:
                same_dir = (action == "long" and position["direction"] == "long") or \
                           (action == "short" and position["direction"] == "short")
                if same_dir:
                    await self._log("已持有同方向仓位，跳过开仓")
                    return
                # Reverse: close first
                await self._log("反向开仓，先平现有仓位")
                await self._close_position(position, reason="ai_close")

            # Risk validation
            ok, risk_reason = validate_pre_order(
                ai_decision=decision,
                account_balance=balance.get("equity", 0),
                strategy=self.strategy,
                risk_state=self._risk_state,
            )
            if not ok:
                await self._log(f"风控拒绝: {risk_reason}")
                return

            # Calculate contract qty
            leverage = int(decision.get("leverage") or self.strategy.get("default_leverage", 10))
            position_pct = float(decision.get("position_pct") or self.strategy.get("position_size_pct", 30))
            qty = await self._okx.calc_qty(
                symbol=symbol,
                balance_usdt=balance.get("available", 0),
                position_pct=position_pct,
                leverage=leverage,
                price=current_price,
            )
            if qty <= 0:
                await self._log("计算仓位为 0（余额不足或价格过高），跳过")
                return

            # Set leverage for this trade
            try:
                await self._okx.set_leverage(symbol, leverage, "isolated")
            except Exception as e:
                await self._log(f"设置杠杆失败: {e}，使用现有设置")

            # Place order
            order_side = "buy" if action == "long" else "sell"
            sl_price = decision.get("stop_loss")
            order_result = await self._okx.place_order(
                symbol=symbol,
                side=order_side,
                order_type="market",
                qty=qty,
                stop_loss=sl_price,
            )
            self._current_algo_id = order_result.get("algo_order_id")
            self._current_stop_loss = sl_price

            dir_cn = "多" if action == "long" else "空"
            sl_str = f" | 止损: {sl_price}" if sl_price else ""
            await self._log(
                f"开仓成功: {dir_cn}单 {qty}张 × {leverage}x{sl_str} | 订单: {order_result.get('order_id')}"
            )
            await self._notify(
                f"🟢 开仓 {dir_cn}单 [{symbol}]",
                f"开仓价: {current_price} | 数量: {qty}张 | 杠杆: {leverage}x{sl_str}\n理由: {reason}"
            )

            # Write trade record to Supabase
            trade_repo = get_trade_repo()
            trade = await trade_repo.create_open({
                "user_id": self.user_id,
                "strategy_id": self.strategy["id"],
                "symbol": symbol,
                "direction": action,
                "qty": qty,
                "entry_price": current_price,
                "leverage": leverage,
                "stop_loss": sl_price,
                "algo_order_id": self._current_algo_id,
                "ai_reasoning": decision.get("reason", ""),
                "open_time": datetime.now(timezone.utc).isoformat(),
            })
            self._current_trade_id = trade["id"]

    # ── Position close ───────────────────────────────────────────────────────

    async def _close_position(self, position: dict, reason: str = "manual") -> None:
        symbol = self.strategy["symbol"]

        # Cancel OKX stop-loss algo order
        if self._current_algo_id:
            try:
                await self._okx.cancel_algo_order(symbol, self._current_algo_id)
            except Exception:
                pass
            self._current_algo_id = None
            self._current_stop_loss = None

        # Fetch exit price before closing
        try:
            ticker = await self._okx.get_ticker(symbol)
            exit_price = ticker["last"]
        except Exception:
            exit_price = position.get("entry_price", 0)

        pos_side = position["direction"] if (self._okx and self._okx._pos_mode == "long_short_mode") else "net"
        await self._okx.close_position(symbol, pos_side=pos_side)
        dir_cn = "多" if position["direction"] == "long" else "空"
        pnl = position.get("unrealized_pnl", 0)
        pnl_sign = "+" if pnl >= 0 else ""
        await self._log(f"平仓: {dir_cn}单 | 原因: {reason} | 未实现盈亏: {pnl:+.2f} USDT")
        await self._notify(
            f"{'🔴' if pnl < 0 else '🟢'} 平仓 {dir_cn}单 [{symbol}]",
            f"开仓: {position.get('entry_price','?')} → 平仓: {exit_price}\n"
            f"未实现盈亏: {pnl_sign}{pnl:.2f} USDT | 原因: {reason}"
        )

        # Update Supabase trade record
        if self._current_trade_id:
            trade_repo = get_trade_repo()
            await trade_repo.update_close(self._current_trade_id, self.user_id, {
                "exit_price": exit_price,
                "close_time": datetime.now(timezone.utc).isoformat(),
                "pnl_usdt": pnl,
                "close_reason": reason,
            })
            self._risk_state = record_trade_result(self._risk_state, pnl)
            self._current_trade_id = None

        await clear_position(self.user_id)
        await clear_ai_plan(self.user_id)

    # ── Price monitor ────────────────────────────────────────────────────────

    async def _price_monitor(self) -> None:
        await self._log("价格监控启动")
        symbol = self.strategy["symbol"]

        while True:
            await asyncio.sleep(3)
            if not self._okx:
                continue
            try:
                # Check for user-initiated manual close request
                if self._manual_close_event.is_set():
                    self._manual_close_event.clear()
                    position = await self._okx.get_position(symbol)
                    if position:
                        await self._log("收到手动平仓指令，执行平仓...")
                        await self._close_position(position, reason="manual")
                    else:
                        await self._log("手动平仓：当前无持仓")
                    continue

                ticker = await self._okx.get_ticker(symbol)
                price = ticker["last"]

                position = await self._okx.get_position(symbol)
                if not position:
                    continue

                # Update Redis with live price
                await set_position(self.user_id, {**position, "current_price": price})

                # Liquidation proximity guard — threshold scales with actual leverage
                actual_leverage = position.get("leverage", 20)
                liq_guard_pct = float(self.strategy.get("liq_guard_pct") or 30)
                liq_threshold = round(liq_guard_pct / max(actual_leverage, 1), 2)
                if check_liquidation_proximity(
                    price, position.get("liquidation_price", 0), position["direction"],
                    threshold_pct=liq_threshold,
                ):
                    liq_price = position.get('liquidation_price', '?')
                    await self._log(
                        f"强平价警报！价格 {price} 距强平价 {liq_price} 不足 {liq_threshold}%，紧急平仓"
                    )
                    await self._notify(
                        f"🚨 强平警报 [{symbol}]",
                        f"当前价格: {price}\n强平价: {liq_price}\n已触发紧急平仓！"
                    )
                    await self._close_position(position, reason="liquidation_guard")

                # Software stop-loss backup
                if self._current_stop_loss and self._current_stop_loss > 0:
                    sl = self._current_stop_loss
                    breached = (
                        (position["direction"] == "long" and price <= sl) or
                        (position["direction"] == "short" and price >= sl)
                    )
                    if breached:
                        await self._log(
                            f"软件止损触发（备用）: 价格 {price} 已触及止损 {sl}"
                        )
                        await self._close_position(position, reason="sl")

            except asyncio.CancelledError:
                raise
            except Exception as e:
                await self._log(f"价格监控异常: {e}")
                logger.debug(f"[{self.user_id}] price monitor error: {e}")
