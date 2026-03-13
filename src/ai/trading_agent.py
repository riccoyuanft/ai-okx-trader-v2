"""
Trading decision AI agent.
Builds prompt from market state, calls OpenAI-compatible endpoint, parses JSON response.

Decision output schema:
  {
    "action": "long" | "short" | "close" | "wait",
    "leverage": int,
    "position_pct": float,   # 0-100
    "stop_loss": float,
    "take_profit": [float, ...],
    "reason": str            # Chinese, ≤60 chars
  }
"""
import json
import re
from typing import Optional

from loguru import logger
from openai import AsyncOpenAI


_SYSTEM_TEMPLATE = """\
你是一名专业的加密货币合约交易 AI，负责根据技术分析数据和用户定义的交易策略做出买卖决策。

【用户交易策略】
{nl_strategy}

【限制条件】
- 最大杠杆上限: {max_leverage}x（AI 可在此范围内自由选择）
- 最大仓位上限: {max_position_pct}%（基于账户权益）
- 保证金模式: 逐仓（固定，不可更改）
- 每笔交易必须提供止损价格

【响应格式】
仅以如下 JSON 格式回复，不要添加任何其他文字或 markdown：
{{
  "action": "long" | "short" | "close" | "wait",
  "leverage": <整数，1 到 {max_leverage}>,
  "position_pct": <0 到 {max_position_pct} 的数字，表示账户余额百分比>,
  "stop_loss": <止损触发价格，浮点数；action 为 wait/close 时可为 0>,
  "take_profit": [<止盈价格列表，可为空数组>],
  "reason": "<中文决策理由，不超过 60 字>"
}}"""

_USER_TEMPLATE = """\
【标的】{symbol}
【当前持仓】{position_desc}
【账户余额】{balance:.2f} USDT（可用: {available:.2f} USDT）

{klines_section}{ta_summary}

【资金费率】{funding_rate:.4%}（下次收取: {next_funding_str}）
{trade_history_section}{news_section}
请根据以上数据和你的交易策略做出决策。"""


def _build_position_desc(position: Optional[dict]) -> str:
    if not position:
        return "无持仓"
    dir_cn = "多" if position["direction"] == "long" else "空"
    pnl = position.get("unrealized_pnl", 0)
    pnl_sign = "+" if pnl >= 0 else ""
    return (
        f"{dir_cn}单 {position['qty']}张 "
        f"@ 均价 {position['entry_price']} "
        f"| 杠杆 {position['leverage']}x "
        f"| 未实现盈亏 {pnl_sign}{pnl:.2f} USDT"
        f"| 强平价 {position.get('liquidation_price', 0)}"
    )


def _extract_json(text: str) -> dict:
    """Extract JSON object from AI response, tolerating markdown fences."""
    text = text.strip()
    # Strip markdown code fences if present
    match = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", text)
    if match:
        text = match.group(1)
    return json.loads(text)


def _format_trade_history(trades: list) -> str:
    """Format recent closed trades into a readable summary for AI context."""
    if not trades:
        return ""
    lines = ["【近期交易记录】"]
    for t in trades[:5]:
        direction = "多" if t.get("direction") == "long" else "空"
        pnl = t.get("pnl_usdt")
        pnl_str = f"{'+' if pnl and pnl >= 0 else ''}{pnl:.2f} USDT" if pnl is not None else "未知"
        open_price = t.get("entry_price", "?")
        close_price = t.get("exit_price", "?")
        close_reason = t.get("close_reason", "")
        lines.append(f"  - {direction}单 开{open_price}→平{close_price} | 盈亏:{pnl_str} | 原因:{close_reason}")
    return "\n".join(lines) + "\n"


_MAX_HISTORY_PAIRS = 10   # max user+assistant pairs to keep


def _truncate_history(history: list[dict]) -> list[dict]:
    """
    Smart history truncation: keep all non-wait decisions + last 3 waits.
    Ensures total pairs <= _MAX_HISTORY_PAIRS.
    """
    if len(history) <= _MAX_HISTORY_PAIRS * 2:
        return history
    key_pairs: list[tuple] = []
    wait_pairs: list[tuple] = []
    for i in range(0, len(history) - 1, 2):
        user_msg = history[i]
        asst_msg = history[i + 1]
        try:
            d = json.loads(asst_msg["content"])
            action = d.get("action", "wait")
        except Exception:
            action = "unknown"
        if action in ("long", "short", "close"):
            key_pairs.append((i, user_msg, asst_msg))
        else:
            wait_pairs.append((i, user_msg, asst_msg))
    kept = key_pairs + wait_pairs[-3:]
    kept.sort(key=lambda x: x[0])
    result = []
    for _, u, a in kept:
        result.append(u)
        result.append(a)
    return result[-_MAX_HISTORY_PAIRS * 2:]


async def get_trading_decision(
    strategy: dict,
    market_data: dict,
    position_state: Optional[dict],
    recent_trades: Optional[list] = None,
    news_sentiment: Optional[dict] = None,
    history: Optional[list] = None,
) -> tuple[dict, list]:
    """
    Build prompt, call AI endpoint, parse decision.
    Returns (decision_dict, updated_history).

    Args:
        strategy: strategy row from DB
        market_data: {symbol, timeframe, balance_usdt, available_usdt, ta_summary,
                      klines_section, funding_rate, next_funding_str}
        position_state: current OKX position dict or None
        history: per-user per-strategy conversation history list (mutated and returned)
        news_sentiment: optional dict with 'summary' key
    """
    from src.config.settings import get_settings
    settings = get_settings()

    history = list(history) if history else []

    max_leverage = strategy.get("max_leverage", 20)
    max_position_pct = strategy.get("max_position_pct", 50)
    nl_strategy = strategy.get("nl_strategy") or "无特定策略，请根据技术指标综合判断。"

    system_prompt = _SYSTEM_TEMPLATE.format(
        nl_strategy=nl_strategy,
        max_leverage=max_leverage,
        max_position_pct=max_position_pct,
    )

    news_section = ""
    if news_sentiment and news_sentiment.get("summary"):
        news_section = f"【消息面摘要】{news_sentiment['summary']}\n"

    trade_history_section = _format_trade_history(recent_trades or [])

    klines_raw = market_data.get("klines_section", "")
    klines_section = klines_raw + "\n\n" if klines_raw else ""

    user_prompt = _USER_TEMPLATE.format(
        symbol=market_data.get("symbol", "?"),
        position_desc=_build_position_desc(position_state),
        balance=market_data.get("balance_usdt", 0),
        available=market_data.get("available_usdt", 0),
        klines_section=klines_section,
        ta_summary=market_data.get("ta_summary", "技术指标数据缺失"),
        funding_rate=market_data.get("funding_rate", 0),
        next_funding_str=market_data.get("next_funding_str", "未知"),
        trade_history_section=trade_history_section,
        news_section=news_section,
    )

    # Compact prompt for history storage (strip raw K-lines — TA summary retained)
    history_user_prompt = _USER_TEMPLATE.format(
        symbol=market_data.get("symbol", "?"),
        position_desc=_build_position_desc(position_state),
        balance=market_data.get("balance_usdt", 0),
        available=market_data.get("available_usdt", 0),
        klines_section="",
        ta_summary=market_data.get("ta_summary", "技术指标数据缺失"),
        funding_rate=market_data.get("funding_rate", 0),
        next_funding_str=market_data.get("next_funding_str", "未知"),
        trade_history_section=trade_history_section,
        news_section=news_section,
    )

    # Select AI provider from system settings
    ai_provider = strategy.get("ai_provider", "openai").lower()
    if ai_provider == "qwen":
        api_key = settings.qwen_api_key
        base_url = settings.qwen_base_url
        model = settings.qwen_model
    elif ai_provider == "doubao":
        api_key = settings.doubao_api_key
        base_url = settings.doubao_base_url
        model = settings.doubao_model
    else:  # default to openai
        api_key = settings.openai_api_key
        base_url = settings.openai_base_url
        model = settings.openai_model

    client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=60.0)
    logger.info(f"[AI] provider={ai_provider} model={model} history_pairs={len(history)//2}")
    logger.info(f"[AI] ===== PROMPT =====\n{user_prompt}\n[AI] ===== END PROMPT =====")

    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(history)
    messages.append({"role": "user", "content": user_prompt})

    response = await client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=0.1,
        max_tokens=600,
        timeout=30,
    )

    raw = response.choices[0].message.content or ""
    logger.info(f"[AI] ===== RESPONSE =====\n{raw}\n[AI] ===== END RESPONSE =====")

    try:
        decision = _extract_json(raw)
    except Exception as e:
        logger.warning(f"[AI] JSON parse failed ({e}), raw: {raw[:200]}")
        fail_decision = {
            "action": "wait", "leverage": strategy.get("default_leverage", 10),
            "position_pct": 0, "stop_loss": None, "take_profit": [], "reason": "AI响应解析失败",
        }
        return fail_decision, history

    action = str(decision.get("action", "wait")).lower()
    if action not in ("long", "short", "close", "wait"):
        action = "wait"

    leverage = min(int(decision.get("leverage") or strategy.get("default_leverage", 10)), max_leverage)
    position_pct = min(float(decision.get("position_pct") or strategy.get("position_size_pct", 30)), max_position_pct)
    stop_loss_raw = decision.get("stop_loss")
    stop_loss = float(stop_loss_raw) if stop_loss_raw else None
    take_profit = [float(p) for p in (decision.get("take_profit") or []) if p]

    result = {
        "action": action,
        "leverage": leverage,
        "position_pct": position_pct,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "reason": str(decision.get("reason", ""))[:100],
    }

    # Update conversation history (compact prompt — no raw K-lines to save tokens)
    history.append({"role": "user", "content": history_user_prompt})
    history.append({"role": "assistant", "content": json.dumps(result, ensure_ascii=False)})
    history = _truncate_history(history)

    return result, history
