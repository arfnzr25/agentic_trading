"""
Telegram Notification Module

Sends inference updates to Telegram for monitoring.
"""

import os
import asyncio
import ssl
import aiohttp
from typing import Optional

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


def is_enabled() -> bool:
    """Check if Telegram notifications are enabled."""
    return bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)


def _get_ssl_context():
    """Create SSL context (workaround for Python 3.14 SSL issues)."""
    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE
    return ssl_context


async def send_message(text: str, parse_mode: str = "Markdown") -> bool:
    """
    Send a message to Telegram.
    
    Args:
        text: Message text (supports Markdown)
        parse_mode: "Markdown" or "HTML"
    
    Returns:
        True if sent successfully, False otherwise
    """
    if not is_enabled():
        return False
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True
    }
    
    try:
        connector = aiohttp.TCPConnector(ssl=_get_ssl_context())
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.post(url, json=payload, timeout=10) as resp:
                if resp.status == 200:
                    return True
                else:
                    print(f"[Telegram] Failed to send: {resp.status}")
                    return False
    except Exception as e:
        print(f"[Telegram] Error sending message: {e}")
        return False


def format_inference_update(
    cycle: int,
    equity: float,
    margin_pct: float,
    analyst_signal: dict,
    risk_decision: dict,
    final_action: str,
    open_position_count: int = 0,
    metadata: Optional[dict] = None
) -> str:
    """
    Format inference update to resemble detailed system logs (as requested).
    """
    metadata = metadata or {}
    
    # Extract Metadata
    mode = metadata.get("mode", "UNKNOWN_MODE")
    phase1 = metadata.get("phase1_time_ms", 0)
    phase2 = metadata.get("phase2_time_ms", 0)
    phase3 = metadata.get("phase3_time_ms", 0)
    total_time = metadata.get("total_time_ms", 0)
    current_close = metadata.get("current_close", 0)
    position_direction = metadata.get("position_direction")
    entry_price = metadata.get("entry_price")
    
    # Analyst Info
    signal = analyst_signal.get("signal", "HOLD")
    confidence = analyst_signal.get("confidence", 0)
    conf_pct = confidence * 100 if confidence <= 1 else confidence
    reasoning = analyst_signal.get("reasoning", "No reasoning provided")
    
    # Truncate reasoning for display if super long (Telegram limit ~4096)
    if len(reasoning) > 800:
        reasoning = reasoning[:800] + "..."
    
    # Build the message components
    
    # 1. Header & Account
    msg = f"""agent-1  | --- Cycle #{cycle} ---
agent-1  | 
agent-1  | [Starting inference cycle...]
agent-1  |  Account: Equity ${equity:,.2f}, Margin {margin_pct:.2f}%
agent-1  | [Cycle] Using analyst_v2 (3-phase)"""

    # 2. Position Info (if exists)
    if position_direction:
         entry_str = f"${entry_price:,.2f}" if entry_price else "Unknown"
         msg += f"\n               [Analyst V2] Position : {position_direction} | Entry Price : {entry_str}"
    
    # 3. Mode & Phases
    msg += f"""
agent-1  | [Analyst v2] Starting analysis for BTC | Mode: {mode}
agent-1  | [Analyst v2] Phase 1 (Memory): {phase1}ms
                [AnalystV2] Context Injection : Successful
agent-1  | [Analyst v2] Phase 2 (Fetch + Learning): {phase2}ms
agent-1  | [Analyst v2] Current BTC price: ${current_close:,.2f}
agent-1  | [Analyst v2] Timeframes: 5m/1h/4h/1d loaded
agent-1  | [Analyst v2] Phase 3 (LLM): {phase3}ms
agent-1  | 
agent-1  | ============================================================
agent-1  | [Analyst v2] SIGNAL: {signal} ({conf_pct:.0f}% confidence)
agent-1  | [Analyst v2] REASONING: {reasoning}
agent-1  | [Analyst v2] TOTAL TIME: {total_time}ms
agent-1  | ==========================================================
"""

    # Wrap in code block for monospaced look (or strictly follow logs)
    # User asked for "Fix the telegram notification to follow this format"
    # To make it look like logs in Telegram, code block is best.
    
    return f"```\n{msg}\n```"


def format_trade_executed(
    coin: str,
    direction: str,
    size_usd: float,
    leverage: int,
    entry_price: float,
    stop_loss: Optional[float] = None,
    take_profit: Optional[float] = None
) -> str:
    """
    Format trade execution notification.
    """
    emoji = "🟢" if direction == "LONG" else "🔴"
    
    message = f"""🎯 *TRADE EXECUTED*

{emoji} *{coin} {direction}*
Entry: `${entry_price:,.2f}`
Size: `${size_usd:.2f}` @ `{leverage}x`"""
    
    if stop_loss:
        message += f"\nSL: `${stop_loss:,.2f}`"
    if take_profit:
        message += f"\nTP: `${take_profit:,.2f}`"
    
    return message


def format_trade_closed(
    coin: str,
    direction: str,
    entry_price: float,
    exit_price: float,
    pnl_usd: float,
    pnl_pct: float,
    reason: str = "Manual"
) -> str:
    """
    Format trade close notification.
    """
    profit_emoji = "🟢" if pnl_usd >= 0 else "🔴"
    
    message = f"""📍 *POSITION CLOSED*

{profit_emoji} *{coin} {direction}*
Entry: `${entry_price:,.2f}` → Exit: `${exit_price:,.2f}`
PnL: `${pnl_usd:+.2f}` ({pnl_pct:+.1f}%)
Reason: _{reason}_"""
    
    return message


async def notify_inference(
    cycle: int,
    equity: float,
    margin_pct: float,
    analyst_signal: dict,
    risk_decision: dict,
    final_action: str,
    open_position_count: int = 0
):
    """Send inference update to Telegram."""
    if not is_enabled():
        return
    
    message = format_inference_update(
        cycle, equity, margin_pct,
        analyst_signal, risk_decision, final_action,
        open_position_count
    )
    await send_message(message)


async def notify_trade_executed(
    coin: str,
    direction: str,
    size_usd: float,
    leverage: int,
    entry_price: float,
    stop_loss: Optional[float] = None,
    take_profit: Optional[float] = None
):
    """Send trade execution notification."""
    if not is_enabled():
        return
    
    message = format_trade_executed(
        coin, direction, size_usd, leverage,
        entry_price, stop_loss, take_profit
    )
    await send_message(message)


async def notify_trade_closed(
    coin: str,
    direction: str,
    entry_price: float,
    exit_price: float,
    pnl_usd: float,
    pnl_pct: float,
    reason: str = "Manual"
):
    """Send trade close notification."""
    if not is_enabled():
        return
    
    message = format_trade_closed(
        coin, direction, entry_price, exit_price,
        pnl_usd, pnl_pct, reason
    )
    await send_message(message)


async def notify_startup(mode: str, equity: float):
    """Send startup notification."""
    if not is_enabled():
        return
    
    message = f"""🤖 *Agent Started*

Mode: `{mode}`
Equity: `${equity:.2f}`
Status: ✅ Running"""
    
    await send_message(message)


async def notify_error(error: str):
    """Send error notification."""
    if not is_enabled():
        return
    
    message = f"""⚠️ *Agent Error*

```
{error[:500]}
```"""
    
    await send_message(message)


# --- SHADOW MODE NOTIFICATIONS ---

async def notify_shadow_trade_opened(
    coin: str,
    signal: str,
    confidence: float,
    entry_price: float,
    stop_loss: Optional[float] = None,
    take_profit: Optional[float] = None,
    reasoning: Optional[str] = None,
    account_equity: Optional[float] = None,
    open_position_count: Optional[int] = None
):
    """Send Shadow Mode trade open notification with full reasoning and stats."""
    if not is_enabled():
        return

    emoji = "👻" # Ghost for Shadow Mode
    action_emoji = "🟢" if signal == "LONG" else "🔴"
    
    start_msg = f"""{emoji} *SHADOW TRADE OPENED*
    
{action_emoji} *{coin} {signal}* ({confidence*100:.0f}%)
Entry: `${entry_price:,.2f}`"""

    if stop_loss:
        start_msg += f"\nSL: `${stop_loss:,.2f}`"
    if take_profit:
        start_msg += f"\nTP: `${take_profit:,.2f}`"
    
    # State section
    if account_equity or open_position_count is not None:
        start_msg += "\n\n📊 *State:*"
        if account_equity:
            start_msg += f"\nEquity: `${account_equity:,.2f}`"
        if open_position_count is not None:
            start_msg += f"\nOpen positions: `{open_position_count}`"

    if reasoning:
        # Full reasoning display (Telegram limit is 4096, so we are safe unless massive)
        start_msg += f"\n\n📝 *Reasoning:*\n_{reasoning}_"
        
    await send_message(start_msg)


async def notify_shadow_trade_closed(
    coin: str,
    signal: str,
    entry_price: float,
    exit_price: float,
    pnl_usd: float,
    pnl_pct: float,
    fees_usd: float,
    reason: str,
    cumulative_pnl: Optional[float] = None,
    win_rate: Optional[float] = None
):
    """Send Shadow Mode trade close notification with fees and cumulative stats."""
    if not is_enabled():
        return

    emoji = "👻"
    profit_emoji = "✅" if pnl_usd >= 0 else "❌"
    
    net_pnl = pnl_usd - fees_usd
    
    msg = f"""{emoji} *SHADOW TRADE CLOSED*
    
{profit_emoji} *{coin} {signal}*
Entry: `${entry_price:,.2f}` → Exit: `${exit_price:,.2f}`
Gross PnL: `${pnl_usd:+.2f}` ({pnl_pct:+.1f}%)
Fees: `-${fees_usd:.2f}`
Net PnL: `${net_pnl:+.2f}`
Outcome: *{reason}*"""

    if cumulative_pnl is not None:
        msg += f"\n\n📊 *Cumulative:* `${cumulative_pnl:+.2f}`"
        if win_rate is not None:
            msg += f" | Win Rate: `{win_rate:.1f}%`"

    await send_message(msg)

