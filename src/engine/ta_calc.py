"""
Technical indicator calculator using pandas-ta.
Computes RSI, MACD, EMA, Bollinger Bands, ATR and volume MA from OHLCV klines.
"""
from datetime import datetime, timezone
from typing import Optional

import pandas as pd
import pandas_ta as ta


_MIN_CANDLES = 52  # minimum candles needed for meaningful indicators


def calc_indicators(klines: list[dict]) -> dict:
    """
    Compute technical indicators from OHLCV candle list (oldest-first).
    Returns a flat dict of values for the latest completed candle.
    Returns empty dict if data is insufficient.
    """
    if len(klines) < _MIN_CANDLES:
        return {}

    df = pd.DataFrame(klines)
    for col in ("open", "high", "low", "close", "vol"):
        df[col] = df[col].astype(float)

    # RSI(14)
    df["rsi"] = ta.rsi(df["close"], length=14)

    # MACD(12,26,9)
    macd = ta.macd(df["close"], fast=12, slow=26, signal=9)
    df["macd"] = macd["MACD_12_26_9"]
    df["macd_sig"] = macd["MACDs_12_26_9"]
    df["macd_hist"] = macd["MACDh_12_26_9"]

    # EMA
    df["ema9"] = ta.ema(df["close"], length=9)
    df["ema21"] = ta.ema(df["close"], length=21)
    df["ema50"] = ta.ema(df["close"], length=50)

    # Bollinger Bands(20, 2) — detect column names dynamically (name varies by pandas-ta version)
    bb = ta.bbands(df["close"], length=20, std=2)
    if bb is not None and not bb.empty:
        _bbu = next((c for c in bb.columns if c.startswith("BBU_")), None)
        _bbm = next((c for c in bb.columns if c.startswith("BBM_")), None)
        _bbl = next((c for c in bb.columns if c.startswith("BBL_")), None)
        df["bb_upper"] = bb[_bbu] if _bbu else float("nan")
        df["bb_mid"] = bb[_bbm] if _bbm else float("nan")
        df["bb_lower"] = bb[_bbl] if _bbl else float("nan")
    else:
        df["bb_upper"] = float("nan")
        df["bb_mid"] = float("nan")
        df["bb_lower"] = float("nan")

    # ATR(14)
    df["atr"] = ta.atr(df["high"], df["low"], df["close"], length=14)

    # Volume SMA(20)
    df["vol_ma20"] = ta.sma(df["vol"], length=20)

    last = df.iloc[-1]
    prev = df.iloc[-2]

    def _f(v) -> float | None:
        return round(float(v), 6) if pd.notna(v) else None

    # MACD crossover detection
    macd_cross = None
    if pd.notna(prev["macd"]) and pd.notna(prev["macd_sig"]):
        if prev["macd"] < prev["macd_sig"] and last["macd"] > last["macd_sig"]:
            macd_cross = "golden"
        elif prev["macd"] > prev["macd_sig"] and last["macd"] < last["macd_sig"]:
            macd_cross = "death"

    # EMA trend
    ema_trend = None
    if pd.notna(last["ema9"]) and pd.notna(last["ema21"]):
        ema_trend = "bullish" if last["ema9"] > last["ema21"] else "bearish"

    # BB position
    bb_pos = None
    if pd.notna(last["bb_upper"]) and pd.notna(last["bb_lower"]):
        if last["close"] > last["bb_upper"]:
            bb_pos = "above_upper"
        elif last["close"] < last["bb_lower"]:
            bb_pos = "below_lower"
        else:
            bb_pos = "inside"

    # Volume ratio vs MA
    vol_ratio = None
    if pd.notna(last["vol_ma20"]) and float(last["vol_ma20"]) > 0:
        vol_ratio = round(float(last["vol"]) / float(last["vol_ma20"]), 2)

    return {
        "close": float(last["close"]),
        "rsi": _f(last["rsi"]),
        "macd": _f(last["macd"]),
        "macd_signal": _f(last["macd_sig"]),
        "macd_hist": _f(last["macd_hist"]),
        "macd_cross": macd_cross,
        "ema9": _f(last["ema9"]),
        "ema21": _f(last["ema21"]),
        "ema50": _f(last["ema50"]),
        "ema_trend": ema_trend,
        "bb_upper": _f(last["bb_upper"]),
        "bb_mid": _f(last["bb_mid"]),
        "bb_lower": _f(last["bb_lower"]),
        "bb_position": bb_pos,
        "atr": _f(last["atr"]),
        "vol": float(last["vol"]),
        "vol_ma20": _f(last["vol_ma20"]),
        "vol_ratio": vol_ratio,
    }


def format_klines_for_ai(klines: list[dict], timeframe: str, n: int = 20) -> str:
    """
    Format the last N candles as a compact readable string for AI context.
    klines: OHLCV list oldest-first, each dict has 'ts', 'open', 'high', 'low', 'close', 'vol'.
    """
    if not klines:
        return f"【{timeframe} K线】无数据"
    recent = klines[-n:]
    lines = [f"【{timeframe} K线 近{len(recent)}根 (旧→新)】"]
    for k in recent:
        ts = k.get("ts", 0)
        if ts:
            dt = datetime.fromtimestamp(int(ts) / 1000, tz=timezone.utc).strftime("%m-%d %H:%M")
        else:
            dt = "??:??"
        o = round(float(k.get("open", 0)), 1)
        h = round(float(k.get("high", 0)), 1)
        lo = round(float(k.get("low", 0)), 1)
        c = round(float(k.get("close", 0)), 1)
        v = round(float(k.get("vol", 0)), 2)
        lines.append(f"  {dt} O:{o} H:{h} L:{lo} C:{c} V:{v}")
    return "\n".join(lines)


def format_multi_tf_klines(
    klines_by_tf: dict[str, list[dict]],
    n: int = 20,
    counts: Optional[dict[str, int]] = None,
) -> str:
    """Format K-lines for multiple timeframes into a single string.
    counts: per-TF override dict {tf: candle_count}; falls back to n if not provided.
    """
    parts = []
    for tf, klines in klines_by_tf.items():
        if klines:
            c = counts.get(tf, n) if counts else n
            parts.append(format_klines_for_ai(klines, tf, c))
    return "\n\n".join(parts)


def build_multi_tf_summary(klines_by_tf: dict[str, list[dict]]) -> str:
    """
    Build a combined TA summary for multiple timeframes (ordered as provided).
    Timeframes with insufficient data are skipped.
    """
    parts = []
    for tf, klines in klines_by_tf.items():
        if klines:
            parts.append(build_ta_summary(klines, tf))
    return "\n\n".join(parts)


def build_ta_summary(klines: list[dict], timeframe: str) -> str:
    """
    Build a human-readable TA summary for injection into AI prompt.
    klines: OHLCV list oldest-first for the primary timeframe.
    """
    ind = calc_indicators(klines)
    if not ind:
        return f"技术指标数据不足（需至少 {_MIN_CANDLES} 根 {timeframe} K 线）"

    lines = [f"【{timeframe} 技术指标】 当前价格: {ind['close']}"]

    if ind["rsi"] is not None:
        rsi_note = "超买" if ind["rsi"] > 70 else ("超卖" if ind["rsi"] < 30 else "中性")
        lines.append(f"RSI(14): {ind['rsi']} [{rsi_note}]")

    if ind["macd"] is not None:
        cross_note = ""
        if ind["macd_cross"] == "golden":
            cross_note = " ⚡ 金叉"
        elif ind["macd_cross"] == "death":
            cross_note = " ⚡ 死叉"
        lines.append(
            f"MACD: {ind['macd']} / 信号线: {ind['macd_signal']} / 柱状: {ind['macd_hist']}{cross_note}"
        )

    if ind["ema9"] is not None:
        trend_cn = "多头排列" if ind["ema_trend"] == "bullish" else "空头排列"
        lines.append(
            f"EMA9: {ind['ema9']} / EMA21: {ind['ema21']} / EMA50: {ind['ema50']} [{trend_cn}]"
        )

    if ind["bb_upper"] is not None:
        bb_pos_cn = {
            "above_upper": "突破上轨（可能超买）",
            "below_lower": "跌破下轨（可能超卖）",
            "inside": "轨道内",
        }.get(ind["bb_position"], "")
        lines.append(
            f"布林带: 上轨 {ind['bb_upper']} / 中轨 {ind['bb_mid']} / 下轨 {ind['bb_lower']} [{bb_pos_cn}]"
        )

    if ind["atr"] is not None:
        lines.append(f"ATR(14): {ind['atr']}")

    if ind["vol_ratio"] is not None:
        vol_note = "放量" if ind["vol_ratio"] > 1.5 else ("缩量" if ind["vol_ratio"] < 0.7 else "正常")
        lines.append(f"成交量: 均量的 {ind['vol_ratio']}x [{vol_note}]")

    return "\n".join(lines)
