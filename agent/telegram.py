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
    final_action: str
) -> str:
    """
    Format inference update for Telegram.
    """
    signal = analyst_signal.get("signal", "N/A")
    confidence = analyst_signal.get("confidence", 0)
    conf_pct = confidence * 100 if confidence <= 1 else confidence
    coin = analyst_signal.get("coin", "BTC")
    
    # Signal emoji
    signal_emoji = {
        "LONG": "🟢", "SHORT": "🔴", "HOLD": "🟡",
        "CLOSE": "⚫", "CUT_LOSS": "🔴", "SCALE_OUT": "🟠", "SCALE_IN": "🟢"
    }.get(signal, "⚪")
    
    # Risk decision
    risk_action = risk_decision.get("decision") or risk_decision.get("action", "N/A")
    action_emoji = {
        "APPROVE": "✅", "REJECT": "❌", "NO_TRADE": "⏸️",
        "CUT_LOSS": "🔴", "SCALE_OUT": "🟠"
    }.get(risk_action, "⚪")
    
    # Final action emoji
    final_emoji = "🎯" if final_action == "EXECUTED" else "⏸️"
    
    # Build message
    message = f"""📊 *Cycle #{cycle}*

💰 *Account*
Equity: `${equity:.2f}` | Margin: `{margin_pct:.1f}%`

{signal_emoji} *Analyst Signal: {signal}* ({conf_pct:.0f}%)"""
    
    # Add entry/SL/TP if available
    entry = analyst_signal.get("entry_price")
    sl = analyst_signal.get("stop_loss")
    tp = analyst_signal.get("take_profit")
    
    if entry or sl or tp:
        message += "\n"
        if entry:
            message += f"\nEntry: `${entry:,.2f}`"
        if sl:
            message += f" | SL: `${sl:,.2f}`"
        if tp:
            message += f" | TP: `${tp:,.2f}`"
    
    # Risk decision section
    message += f"""

{action_emoji} *Risk: {risk_action}*"""
    
    # Add sizing info if available
    size = risk_decision.get("size_usd")
    leverage = risk_decision.get("leverage")
    if size and leverage:
        message += f"\nSize: `${size:.2f}` @ `{leverage}x`"
    
    # Invalidation conditions if present
    invalidation = risk_decision.get("invalidation_conditions", [])
    if invalidation:
        message += "\n⚠️ *Invalidation:*"
        for cond in invalidation[:3]:  # Max 3 conditions
            message += f"\n  • {cond}"
    
    # Final action
    message += f"""

{final_emoji} *Final: {final_action}*"""
    
    # Full reasoning (up to 500 chars)
    reasoning = analyst_signal.get("reasoning", "")
    if reasoning:
        # Truncate at 500 chars but try to end at a sentence
        if len(reasoning) > 500:
            reasoning = reasoning[:500]
            last_period = reasoning.rfind(".")
            if last_period > 300:
                reasoning = reasoning[:last_period + 1]
            else:
                reasoning += "..."
        message += f"""

📝 *Reasoning:*
_{reasoning}_"""
    
    # Risk reasoning (up to 200 chars)
    risk_reason = risk_decision.get("reason") or risk_decision.get("reasoning", "")
    if risk_reason:
        risk_reason = risk_reason[:200] + "..." if len(risk_reason) > 200 else risk_reason
        message += f"""

🛡️ *Risk Notes:*
_{risk_reason}_"""
    
    return message


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
    final_action: str
):
    """Send inference update to Telegram."""
    if not is_enabled():
        return
    
    message = format_inference_update(
        cycle, equity, margin_pct,
        analyst_signal, risk_decision, final_action
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
    take_profit: Optional[float] = None
):
    """Send Shadow Mode trade open notification."""
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
        
    await send_message(start_msg)


async def notify_shadow_trade_closed(
    coin: str,
    signal: str,
    entry_price: float,
    exit_price: float,
    pnl_usd: float,
    pnl_pct: float,
    reason: str
):
    """Send Shadow Mode trade close notification."""
    if not is_enabled():
        return

    emoji = "👻"
    profit_emoji = "✅" if pnl_usd >= 0 else "❌"
    
    msg = f"""{emoji} *SHADOW TRADE CLOSED*
    
{profit_emoji} *{coin} {signal}*
Entry: `${entry_price:,.2f}` → Exit: `${exit_price:,.2f}`
PnL: `${pnl_usd:+.2f}` ({pnl_pct:+.1f}%)
Outcome: *{reason}*"""

    await send_message(msg)
