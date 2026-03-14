"""
OKX API client wrapper.
Uses python-okx SDK (sync) wrapped in asyncio.to_thread for non-blocking calls.
Credentials are passed in decrypted; never stored in plaintext beyond this object's lifetime.
"""
import asyncio
import math
from typing import Optional

from loguru import logger

import okx.Trade as Trade
import okx.Account as Account
import okx.MarketData as MarketData
import okx.PublicData as PublicData


async def test_okx_credentials(
    api_key: str, secret_key: str, passphrase: str, testnet: bool = True
) -> tuple[bool, str]:
    """
    Verify OKX API credentials by calling get_account_balance.
    Returns (True, "") on success or (False, error_message) on failure.
    Runs in a thread to avoid blocking the event loop.
    """
    flag = "1" if testnet else "0"
    try:
        account_api = Account.AccountAPI(api_key, secret_key, passphrase, False, flag)
        result = await asyncio.to_thread(account_api.get_account_balance)
        if result.get("code") == "0":
            return True, ""
        msg = result.get("msg") or result.get("data", [{}])[0].get("sMsg", "未知错误")
        return False, f"OKX API 返回错误: {msg}"
    except Exception as e:
        return False, f"连接 OKX 失败: {e}"


class OKXClient:
    """
    Per-user REST client. Uses asyncio.to_thread to wrap sync python-okx SDK.
    """

    def __init__(self, api_key: str, secret_key: str, passphrase: str, testnet: bool = True):
        self.api_key = api_key
        self.secret_key = secret_key
        self.passphrase = passphrase
        self.testnet = testnet
        flag = "1" if testnet else "0"

        self._trade = Trade.TradeAPI(api_key, secret_key, passphrase, False, flag)
        self._account = Account.AccountAPI(api_key, secret_key, passphrase, False, flag)
        self._market = MarketData.MarketAPI(flag=flag)
        self._public = PublicData.PublicAPI(flag=flag)
        self._pos_mode: str = "net_mode"  # fetched in init_pos_mode()

    # ── Account config ────────────────────────────────────────────────────────

    async def init_pos_mode(self) -> str:
        """Fetch account config, validate account level, cache position mode."""
        result = await asyncio.to_thread(self._account.get_account_config)
        logger.debug(f"[OKX] get_account_config raw: {result}")
        if result.get("code") == "0" and result.get("data"):
            cfg = result["data"][0]
            self._pos_mode = cfg.get("posMode", "net_mode")
            acct_lv = cfg.get("acctLv", "1")
            _ACCT_LV_NAMES = {"1": "简单模式", "2": "单币种保证金", "3": "多币种保证金", "4": "组合保证金"}
            lv_name = _ACCT_LV_NAMES.get(acct_lv, f"未知({acct_lv})")
            logger.info(f"[OKX] acctLv={acct_lv}({lv_name}) posMode={self._pos_mode}")
            if acct_lv == "1":
                raise RuntimeError(
                    f"OKX账户当前为【简单模式】，不支持合约逐仓交易。\n"
                    f"请登录OKX{'模拟盘' if self.testnet else ''}尝试交易并切换到【合约模式】。"
                )
        else:
            logger.warning(f"[OKX] get_account_config failed: {result}, defaulting to net_mode")
        logger.info(f"[OKX] position mode = {self._pos_mode}")
        return self._pos_mode

    # ── Market data ──────────────────────────────────────────────────────────

    async def get_klines(self, symbol: str, bar: str, limit: int = 100) -> list:
        """Return OHLCV list oldest-first. Each item: {ts, open, high, low, close, vol}"""
        result = await asyncio.to_thread(
            self._market.get_candlesticks,
            instId=symbol,
            bar=bar,
            limit=str(limit),
        )
        if result.get("code") != "0":
            raise RuntimeError(f"get_klines failed: {result.get('msg', result)}")
        rows = result["data"]
        rows.reverse()  # API returns newest-first; reverse to oldest-first
        return [
            {
                "ts": int(row[0]),
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
                "vol": float(row[5]),
            }
            for row in rows
        ]

    async def get_ticker(self, symbol: str) -> dict:
        result = await asyncio.to_thread(self._market.get_ticker, instId=symbol)
        if result.get("code") != "0":
            raise RuntimeError(f"get_ticker failed: {result.get('msg', result)}")
        d = result["data"][0]
        return {
            "last": float(d["last"]),
            "bid": float(d["bidPx"]),
            "ask": float(d["askPx"]),
        }

    async def get_funding_rate(self, symbol: str) -> dict:
        result = await asyncio.to_thread(self._public.get_funding_rate, instId=symbol)
        if result.get("code") != "0":
            raise RuntimeError(f"get_funding_rate failed: {result.get('msg', result)}")
        d = result["data"][0]
        return {
            "funding_rate": float(d["fundingRate"]),
            "next_funding_time": int(d["nextFundingTime"]),
        }

    async def get_instrument_info(self, symbol: str) -> dict:
        result = await asyncio.to_thread(
            self._public.get_instruments, instType="SWAP", instId=symbol
        )
        if result.get("code") != "0":
            raise RuntimeError(f"get_instrument_info failed: {result.get('msg', result)}")
        inst = result["data"][0]
        return {
            "ct_val": float(inst["ctVal"]),
            "min_sz": float(inst["minSz"]),
            "lot_sz": float(inst["lotSz"]),
            "tick_sz": float(inst["tickSz"]),
        }

    # ── Account ──────────────────────────────────────────────────────────────

    async def get_account_balance(self) -> dict:
        result = await asyncio.to_thread(self._account.get_account_balance)
        if result.get("code") != "0":
            raise RuntimeError(f"get_account_balance failed: {result.get('msg', result)}")
        
        from loguru import logger
        
        if not result.get("data"):
            return {"equity": 0.0, "available": 0.0}
        
        details = result["data"][0].get("details", [])
        for item in details:
            if item["ccy"] == "USDT":
                equity = float(item.get("eq") or 0)
                available = float(item.get("availEq") or item.get("availBal") or 0)
                logger.debug(f"[OKX] USDT balance found: equity={equity}, available={available}")
                return {
                    "equity": equity,
                    "available": available,
                }
        
        logger.warning(f"[OKX] No USDT found in details: {details}")
        return {"equity": 0.0, "available": 0.0}

    async def get_position(self, symbol: str) -> Optional[dict]:
        result = await asyncio.to_thread(self._account.get_positions, instId=symbol)
        if result.get("code") != "0":
            raise RuntimeError(f"get_position failed: {result.get('msg', result)}")
        data = result.get("data", [])
        if not data:
            return None

        if self._pos_mode == "long_short_mode":
            # In long_short_mode, pos is always positive; direction comes from posSide.
            # OKX may return both long and short entries — find the one with non-zero qty.
            for pos in data:
                qty = float(pos.get("pos") or 0)
                if qty > 0:
                    pos_side = pos.get("posSide", "long")
                    direction = pos_side  # "long" or "short"
                    logger.debug(f"[OKX] get_position long_short_mode: posSide={pos_side} qty={qty}")
                    return {
                        "symbol": symbol,
                        "direction": direction,
                        "qty": qty,
                        "entry_price": float(pos.get("avgPx") or 0),
                        "liquidation_price": float(pos.get("liqPx") or 0),
                        "unrealized_pnl": float(pos.get("upl") or 0),
                        "leverage": int(float(pos.get("lever") or 1)),
                        "margin": float(pos.get("imr") or 0),
                    }
            return None
        else:
            # In net_mode, qty sign determines direction (positive=long, negative=short)
            pos = data[0]
            qty = float(pos.get("pos") or 0)
            if qty == 0:
                return None
            return {
                "symbol": symbol,
                "direction": "long" if qty > 0 else "short",
                "qty": abs(qty),
                "entry_price": float(pos.get("avgPx") or 0),
                "liquidation_price": float(pos.get("liqPx") or 0),
                "unrealized_pnl": float(pos.get("upl") or 0),
                "leverage": int(float(pos.get("lever") or 1)),
                "margin": float(pos.get("imr") or 0),
            }

    async def set_leverage(self, symbol: str, leverage: int, margin_mode: str = "isolated") -> None:
        if self._pos_mode == "long_short_mode":
            for pos_side in ("long", "short"):
                result = await asyncio.to_thread(
                    self._account.set_leverage,
                    instId=symbol,
                    lever=str(leverage),
                    mgnMode=margin_mode,
                    posSide=pos_side,
                )
                logger.debug(f"[OKX] set_leverage posSide={pos_side} result: {result}")
                if result.get("code") != "0":
                    raise RuntimeError(f"set_leverage({pos_side}) failed: {result.get('msg')} | full={result}")
        else:
            result = await asyncio.to_thread(
                self._account.set_leverage,
                instId=symbol,
                lever=str(leverage),
                mgnMode=margin_mode,
                posSide="net",
            )
            logger.debug(f"[OKX] set_leverage posSide=net result: {result}")
            if result.get("code") != "0":
                raise RuntimeError(f"set_leverage failed: {result.get('msg')} | full={result}")

    async def set_margin_mode(self, symbol: str, mode: str = "isolated") -> None:
        await self.set_leverage(symbol, 1, mode)

    # ── Order management ─────────────────────────────────────────────────────

    async def calc_qty(
        self, symbol: str, balance_usdt: float, position_pct: float, leverage: int, price: float
    ) -> float:
        """Calculate order size in contracts, rounded down to lot size."""
        info = await self.get_instrument_info(symbol)
        ct_val = info["ct_val"]
        min_sz = info["min_sz"]
        lot_sz = info["lot_sz"]

        notional = balance_usdt * (position_pct / 100) * leverage
        qty_raw = notional / (price * ct_val)

        # For SWAP/Futures, sz must be an integer number of contracts
        qty = math.floor(qty_raw / lot_sz) * lot_sz
        if lot_sz >= 1:
            qty = float(int(qty))  # force integer contracts
        else:
            lot_decimals = len(str(lot_sz).rstrip("0").split(".")[-1]) if "." in str(lot_sz) else 0
            qty = round(qty, lot_decimals)

        logger.debug(
            f"[OKX] calc_qty: balance={balance_usdt} pct={position_pct}% lev={leverage}x "
            f"price={price} ctVal={ct_val} lotSz={lot_sz} minSz={min_sz} → qty={qty}"
        )
        return qty if qty >= min_sz else 0

    async def place_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        qty: float,
        price: Optional[float] = None,
        stop_loss: Optional[float] = None,
        take_profits: Optional[list] = None,
    ) -> dict:
        """
        Place market/limit order.
        side: 'buy' (open long) | 'sell' (open short)
        Returns {'order_id': str, 'algo_order_id': str|None}
        """
        if self._pos_mode == "long_short_mode":
            pos_side = "long" if side == "buy" else "short"
        else:
            pos_side = "net"

        params: dict = {
            "instId": symbol,
            "tdMode": "isolated",
            "side": side,
            "posSide": pos_side,
            "ordType": order_type,
            "sz": str(int(qty) if qty == int(qty) else qty),
        }
        if price and order_type == "limit":
            params["px"] = str(price)

        logger.info(f"[OKX] place_order params: {params} | pos_mode={self._pos_mode}")
        result = await asyncio.to_thread(self._trade.place_order, **params)
        logger.info(f"[OKX] place_order response: {result}")
        if result.get("code") != "0":
            per_order_msg = ""
            if result.get("data"):
                per_order_msg = " | ".join(
                    f"sCode={d.get('sCode')} sMsg={d.get('sMsg')}" for d in result["data"]
                )
            raise RuntimeError(
                f"place_order failed: {result.get('msg')} | {per_order_msg} | params={params}"
            )
        order_id = result["data"][0]["ordId"]

        algo_id = None
        if stop_loss is not None:
            algo_id = await self._place_stop_loss(symbol, side, qty, stop_loss)

        return {"order_id": order_id, "algo_order_id": algo_id}

    async def _place_stop_loss(
        self, symbol: str, entry_side: str, qty: float, sl_price: float
    ) -> Optional[str]:
        """Place conditional stop-loss algo order (non-fatal if it fails)."""
        close_side = "sell" if entry_side == "buy" else "buy"
        if self._pos_mode == "long_short_mode":
            pos_side = "long" if entry_side == "buy" else "short"
        else:
            pos_side = "net"
        sz_str = str(int(qty) if qty == int(qty) else qty)
        algo_params = dict(
            instId=symbol,
            tdMode="isolated",
            side=close_side,
            posSide=pos_side,
            ordType="conditional",
            sz=sz_str,
            slTriggerPx=str(sl_price),
            slOrdPx="-1",
            slTriggerPxType="last",
        )
        try:
            logger.info(f"[OKX] place_algo_order (stop-loss) params: {algo_params}")
            result = await asyncio.to_thread(self._trade.place_algo_order, **algo_params)
            logger.info(f"[OKX] place_algo_order response: {result}")
            if result.get("code") == "0":
                return result["data"][0].get("algoId")
            else:
                logger.warning(f"[OKX] place_algo_order failed: {result.get('msg')} | full={result}")
        except Exception as e:
            logger.warning(f"[OKX] place_algo_order exception: {e}")
        return None

    async def close_position(self, symbol: str, pos_side: str = "net") -> dict:
        result = await asyncio.to_thread(
            self._trade.close_positions,
            instId=symbol,
            mgnMode="isolated",
            posSide=pos_side,
        )
        if result.get("code") != "0":
            raise RuntimeError(f"close_position failed: {result.get('msg', result)}")
        return result["data"][0] if result.get("data") else {}

    async def cancel_algo_order(self, symbol: str, algo_order_id: str) -> None:
        await asyncio.to_thread(
            self._trade.cancel_algo_order,
            [{"instId": symbol, "algoId": algo_order_id}],
        )
