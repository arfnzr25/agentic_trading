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
            # Attempt 1: Send with specified parse_mode (Markdown)
            async with session.post(url, json=payload, timeout=10) as resp:
                if resp.status == 200:
                    return True
                
                # Capture error details for debugging
                err_text = await resp.text()
                
                if resp.status == 400 and parse_mode:
                    # Retry as Plain Text if Markdown failed
                    print(f"[Telegram] Formatting error (400). Retrying as plain text... Error: {err_text}")
                    
                    # Remove parse_mode entirely instead of setting to None
                    payload.pop("parse_mode", None)
                    
                    async with session.post(url, json=payload, timeout=10) as retry_resp:
                        if retry_resp.status == 200:
                            return True
                        else:
                            retry_err = await retry_resp.text()
                            print(f"[Telegram] Retry failed: {retry_resp.status} | Response: {retry_err}")
                            return False
                else:
                    print(f"[Telegram] Failed to send: {resp.status} | Response: {err_text}")
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
    phase1 = metadata.get("phase1_time_ms", 0)
    total_time = metadata.get("total_time_ms", 0)
    current_close = metadata.get("current_close", 0)
    position_direction = metadata.get("position_direction")
    entry_price = metadata.get("entry_price")
    
    # Analyst Info
    signal = analyst_signal.get("signal", "HOLD")
    confidence = analyst_signal.get("confidence", 0)
    conf_pct = confidence * 100 if confidence <= 1 else confidence
    reasoning = analyst_signal.get("reasoning", "No reasoning provided")
    
    # Build the message components
    
    # 1. Header
    msg = f"⏱️ *Cycle #{cycle}*\n"
    
    # 2. Market Data
    msg += f"💰 *BTC Price:* `${current_close:,.2f}`\n"
    
    # Mode Display
    mode = metadata.get("trade_mode", "UNKNOWN")
    if mode == "SNIPER": mode_icon = "🎯"
    elif mode == "SCALPING": mode_icon = "⚡"
    else: mode_icon = "🤔"
    
    msg += f"{mode_icon} *Mode:* `{mode}`\n"
    msg += f"🧠 *Context Injection:* {'✅ Ready' if phase1 > 0 else '⚠️ Empty'}\n"
    
    # 3. Position Info (if exists)
    if position_direction:
         entry_str = f"${entry_price:,.2f}" if entry_price else "Unknown"
         tp_val = metadata.get("take_profit")
         sl_val = metadata.get("stop_loss")
         
         # Extra Details
         size_val = metadata.get("position_size", 0)
         liq_val = metadata.get("liquidation_price")
         margin_val = metadata.get("margin_used", 0)
         
         tp_str = f"${tp_val:,.2f}" if tp_val else "None"
         sl_str = f"${sl_val:,.2f}" if sl_val else "None"
         liq_str = f"${liq_val:,.2f}" if liq_val else "None"
         
         emoji = "🟢" if position_direction == "LONG" else "🔴"
         msg += f"\n{emoji} *OPEN POSITION ({position_direction})*\n"
         msg += f"Entry: `{entry_str}` | Size: `{size_val}`\n"
         msg += f"TP: `{tp_str}` | SL: `{sl_str}`\n"
         msg += f"Liq: `{liq_str}` | Margin: `${margin_val:.2f}`\n"
    
    # 4. Analysis
    conf_emoji = "🔥" if conf_pct > 70 else "🤔"
    if signal == "HOLD": conf_emoji = "✋"
    if signal == "CLOSE": conf_emoji = "🚪"
    
    msg += f"\n{conf_emoji} *SIGNAL:* `{signal}` ({conf_pct:.0f}%)\n"
    
    # Expanded Reasoning
    msg += f"\n📝 *Reasoning:*\n{reasoning}\n"
    
    # Footer
    msg += f"\n_⏱️ Analysis Time: {total_time}ms | Eq: ${equity:.0f}_"
    
    return msg


def format_trade_executed(
    coin: str,
    direction: str,
    size_usd: float,
    leverage: int,
    entry_price: float,
    stop_loss: Optional[float] = None,
    take_profit: Optional[float] = None,
    order_type: str = "ENTRY"
) -> str:
    """
    Format trade execution notification.
    order_type: "ENTRY", "SCALE_IN", "SCALE_OUT", "CUT_LOSS"
    """
    emoji = "🟢" if direction == "LONG" else "🔴"
    
    header = "🎯 *TRADE EXECUTED*"
    if order_type == "SCALE_IN": header = "⚖️ *SCALE IN*"
    if order_type == "SCALE_OUT": header = "📉 *SCALE OUT*"
    if order_type == "CUT_LOSS": header = "🚨 *CUT LOSS*"
    
    message = f"""{header}

{emoji} *{coin} {direction}*
Price: `${entry_price:,.2f}`
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
    open_position_count: int = 0,
    metadata: Optional[dict] = None
):
    """Send inference update to Telegram."""
    if not is_enabled():
        return
    
    message = format_inference_update(
        cycle, equity, margin_pct,
        analyst_signal, risk_decision, final_action,
        open_position_count,
        metadata
    )
    await send_message(message)


async def notify_trade_executed(
    coin: str,
    direction: str,
    size_usd: float,
    leverage: int,
    entry_price: float,
    stop_loss: Optional[float] = None,
    take_profit: Optional[float] = None,
    order_type: str = "ENTRY"
):
    """Send trade execution notification.
       order_type: ENTRY, SCALE_IN, SCALE_OUT, CUT_LOSS
    """
    if not is_enabled():
        return
    
    message = format_trade_executed(
        coin, direction, size_usd, leverage,
        entry_price, stop_loss, take_profit,
        order_type
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
    
    message = format_shadow_trade_opened(
        coin, signal, confidence, entry_price,
        stop_loss, take_profit, reasoning,
        account_equity, open_position_count
    )
    await send_message(message)

# Helper for consistency if needed, but existing impl was inline.
# I will keep the inline impl from the previous read to avoid breaking naming if not defined.
# WAIT, I did not define `format_shadow_trade_opened` helper in previous read, it was inline.
# I should REVERT the notify_shadow_trade_opened to INLINE implementation to be safe, 
# or split it out. I'll stick to the previous inline impl to minimize risk.

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
