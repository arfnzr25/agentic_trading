"""
Market Analyst Node (Refactored v2)

Uses 3-phase approach:
1. Memory Pre-load (SQL)
2. Parallel Data Fetch (asyncio.gather)
3. Single LLM Analysis Call
"""

import json
import time
from typing import Any
from langchain_core.messages import HumanMessage, SystemMessage

from agent.config.llm_factory import get_analyst_llm
from agent.utils.prompts import get_analyst_prompt, build_system_context
from agent.db.async_logger import async_logger
from agent.db import get_session, MarketMemoryRepository
from agent.db.models import MarketMemory, Trade
from agent.db.repository import InferenceLogRepository
from agent.config.config import get_config
from agent.utils.memory_loader import preload_memory, format_memory_context
from agent.services.data_fetcher import fetch_analyst_data, calculate_timestamps, summarize_candles
from agent.utils.learning import get_learning_context
from datetime import datetime
from sqlmodel import select
from agent.models.schemas import TradeSignal
from agent.utils import chart_tools


async def analyst_node(state: dict[str, Any], tools: list) -> dict[str, Any]:
    """
    Market Analyst node (v2) - 3-Phase Architecture.
    
    Phase 1: Pre-load memory from SQL (~10ms)
    Phase 2: Parallel fetch market data (~5s)
    Phase 3: Single LLM call for analysis (~5s)
    
    Total target: ~15s (down from 60-100s)
    """
    start_time = time.time()
    cfg = get_config()
    target_coin = cfg.focus_coins[0]
    
    # Check for open positions (determines analysis mode)
    account_state = state.get("account_state", {})
    open_positions = account_state.get("open_symbols", [])
    has_open_position = target_coin in open_positions
    position_direction = account_state.get("open_position_details", {}).get(target_coin, None)
    
    # Extract raw position details (for entry price fallback)
    raw_positions = account_state.get("raw_positions", {})
    position = raw_positions.get(target_coin, {})
    
    # Extract Open Orders for TP/SL fallback
    open_orders = account_state.get("open_orders", [])
    if isinstance(open_orders, list):
        coin_orders = [o for o in open_orders if isinstance(o, dict) and o.get("coin") == target_coin]
    else:
        coin_orders = []
    
    # Simple heuristics for TP/SL from Open Orders
    exchange_tp = None
    exchange_sl = None
    
    if has_open_position and position:
        entry_px = float(position.get("entryPx", 0))
        is_long = float(position.get("szi", 0)) > 0
        
        for o in coin_orders:
            # Assuming Reduce-Only orders are TP/SL
            if o.get("reduceOnly", False):
                # Price can be limitPx or triggerPx
                px = float(o.get("limitPx", 0))
                if px == 0: px = float(o.get("triggerPx", 0))
                if px == 0: continue
                
                # Logic: SL is usually a trigger order, TP is usually a limit
                # If trigger exists, likely SL.
                if o.get("triggerCondition") and o.get("triggerCondition") != "N/A":
                     exchange_sl = px
                else:
                    # Limit order: check price relative to entry
                    if is_long:
                        if px > entry_px: exchange_tp = px
                        elif px < entry_px: exchange_sl = px # Limit stop?
                    else: # Short
                        if px < entry_px: exchange_tp = px
                        elif px > entry_px: exchange_sl = px

    mode_str = f"MANAGING {position_direction}" if has_open_position else "SEEKING ENTRY"
    print(f"[Analyst v2] Starting analysis for {target_coin} | Mode: {mode_str}")
    
    # ===== THOUGHT CONTINUITY: Last conclusion + Trade thesis =====
    last_conclusion = ""
    trade_thesis = ""
    
    with get_session() as session:
        # Fetch last analyst conclusion
        recent_logs = InferenceLogRepository.get_recent(session, limit=1)
        if recent_logs:
            last_log = recent_logs[0]
            last_signal = last_log.analyst_signal or "N/A"
            last_reasoning = (last_log.analyst_reasoning or "")[:200]
            last_conclusion = f"LAST CYCLE: Signal={last_signal}, Reasoning: {last_reasoning}..."
        
        # If managing a position, fetch the original trade thesis
        if has_open_position:
            active_trade = session.exec(
                select(Trade)
                .where(Trade.coin == target_coin)
                .where(Trade.closed_at == None)
                .order_by(Trade.opened_at.desc())
            ).first()
            
            if active_trade:
                trade_thesis = f"ORIGINAL THESIS: {active_trade.reasoning[:300]}..."
                print(f"[Analyst v2] Trade thesis loaded: {active_trade.reasoning[:50]}...")
    
    # ===== PHASE 1: MEMORY PRE-LOAD =====
    phase1_start = time.time()
    memory = preload_memory(target_coin)
    memory_context = format_memory_context(memory)
    phase1_time = (time.time() - phase1_start) * 1000
    print(f"[Analyst v2] Phase 1 (Memory): {phase1_time:.0f}ms")
    
    # ===== PHASE 2: PARALLEL DATA FETCH =====
    phase2_start = time.time()
    timestamps = calculate_timestamps()
    
    # Create tasks for data and learning
    task_data = fetch_analyst_data(tools, target_coin, timestamps, state.get("account_address", ""))
    task_learning = get_learning_context(tools)
    
    # News Fetch (Daily/Volatility Trigger)
    # Run in thread to avoid blocking loop (requests is synchronous)
    from agent.services import news_fetcher
    import asyncio
    task_news = asyncio.to_thread(news_fetcher.fetch_macro_context, query="Summarize top 3 crypto market drivers today")
    
    # Execute all
    data, learning_context, macro_context = await asyncio.gather(task_data, task_learning, task_news)
    
    phase2_time = (time.time() - phase2_start) * 1000
    print(f"[Analyst v2] Phase 2 (Fetch + Learning + News): {phase2_time:.0f}ms")
    

    
    # Debug: check what we got
    candles_5m_raw = data.get("candles_5m", "")
    candles_1m_raw = data.get("candles_1m", "") # NEW
    candles_1h_raw = data.get("candles_1h", "")
    candles_4h_raw = data.get("candles_4h", "")
    candles_1d_raw = data.get("candles_1d", "")
    
    # Helper to clean candles
    def _clean_candles(raw_data) -> list:
        try:
            import json as _json
            if isinstance(raw_data, list):
                # Check if it's a list of TextContent objects
                cleaned = []
                for c in raw_data:
                    if isinstance(c, dict):
                         if "text" in c:
                             try: cleaned.append(_json.loads(c["text"]))
                             except: continue
                         else:
                             cleaned.append(c)
                return cleaned
            elif isinstance(raw_data, str):
                if raw_data.startswith('['):
                    return _json.loads(raw_data)
        except Exception:
             return []
        return []

    # Process Timeframes with TA-Lib
    # Process Timeframes with TA-Lib
    c5m_list = _clean_candles(candles_5m_raw)
    c1m_list = _clean_candles(candles_1m_raw) # NEW
    c1h_list = _clean_candles(candles_1h_raw)
    c4h_list = _clean_candles(candles_4h_raw)
    c1d_list = _clean_candles(candles_1d_raw)
    
    # Calculate Indicators & Format Strings
    # 5M (Scalp Context)
    ind_5m = chart_tools.calculate_indicators(c5m_list, interval="5m")
    candles_5m_summary = chart_tools.format_context_string(target_coin, "5m", ind_5m, c5m_list)

    # 1M (Scalp Momentum)
    # Simple formatting for 1M to keep context light (avoiding full indicators if not critical)
    # Just need price action and Volume? Let's use standard tool for consistency.
    ind_1m = chart_tools.calculate_indicators(c1m_list, interval="1m")
    candles_1m_summary = chart_tools.format_context_string(target_coin, "1m", ind_1m, c1m_list)
    
    # 1H (Intraday Context)
    ind_1h = chart_tools.calculate_indicators(c1h_list, interval="1h")
    candles_1h_summary = chart_tools.format_context_string(target_coin, "1h", ind_1h, c1h_list)
    
    # 4H (Swing Context)
    ind_4h = chart_tools.calculate_indicators(c4h_list, interval="4h")
    candles_4h_summary = chart_tools.format_context_string(target_coin, "4h", ind_4h, c4h_list)
    
    # 1D (Macro Context)
    ind_1d = chart_tools.calculate_indicators(c1d_list, interval="1d")
    candles_1d_summary = chart_tools.format_context_string(target_coin, "1d", ind_1d, c1d_list)

    # Shadow Mode Data Extraction (using the robust list)
    current_close = 0.0
    if c5m_list:
        current_close = float(c5m_list[-1].get("c", 0))

    # Process Recent Fills (Trade History)
    user_fills_raw = data.get("user_fills", [])
    user_fills_summary = "No recent trades found."
    
    try:
        if isinstance(user_fills_raw, list):
            # Handle potential TextContent wrappers
            cleaned_fills = _clean_candles(user_fills_raw) # Re-use cleaner as it handles list of dicts/text
            
            # Filter for this coin
            relevant_fills = [f for f in cleaned_fills if isinstance(f, dict) and f.get("coin") == target_coin]
            # HL API 'side' is 'B' or 'A'. 'px', 'sz', 'time' (ms)
            
            if relevant_fills:
                # Sort by time desc
                relevant_fills.sort(key=lambda x: x.get("time", 0), reverse=True)
                
                fill_lines = []
                for f in relevant_fills[:5]:
                    side = "BUY" if f.get("side") == "B" else "SELL"
                    px = float(f.get("px", 0))
                    sz = float(f.get("sz", 0))
                    ts = f.get("time", 0)
                    
                    # Time formatting
                    try:
                        dt = (time.time() * 1000 - ts) / 1000
                        if dt < 3600: t_str = f"{dt/60:.0f}m ago"
                        elif dt < 86400: t_str = f"{dt/3600:.1f}h ago"
                        else: t_str = f"{dt/86400:.1f}d ago"
                    except: t_str = "Unknown time"
                    
                    # PnL & Type
                    pnl = float(f.get("closedPnl", 0))
                    pnl_str = f" | PnL: ${pnl:+.2f}" if pnl != 0 else ""
                    
                    # Order Type (heuristic based on 'crossing')
                    is_taker = f.get("crossing", True) 
                    type_str = "Market" if is_taker else "Limit"
                    
                    fill_lines.append(f"- {side} ${px:,.2f} ({sz}) | {type_str}{pnl_str} | {t_str}")
                
                user_fills_summary = "\n".join(fill_lines)
    except Exception as e:
        user_fills_summary = f"Error parsing fills: {e}"

    # ===== PHASE 2.5: SNIPER MODE GATEKEEPING & NERVOUS WATCHMAN =====
    # Extract Trends via Regimes (VWAP-based, slower)
    trend_1h = ind_1h.get("regime", "NEUTRAL")
    trend_5m = ind_5m.get("regime", "NEUTRAL")
    
    # Primary Confluence: Regime Agreement (VWAP-based)
    regime_confluence = (trend_1h == trend_5m) and (trend_1h != "NEUTRAL")
    
    # Alternative Confluence: EMA Cross Agreement (faster, catches momentum)
    ema_1h = ind_1h.get("ema_cross", "NEUTRAL")
    ema_5m = ind_5m.get("ema_cross", "NEUTRAL")
    ema_confluence = (ema_1h == ema_5m) and (ema_1h != "NEUTRAL")
    
    # Combined: Either confluence qualifies (still disciplined, but responsive)
    confluence = regime_confluence or ema_confluence
    
    confluence_source = "REGIME" if regime_confluence else ("EMA" if ema_confluence else "NONE")
    
    # --- PHASE 10: SCALPING MODE WITH COOLDOWN ---
    if not confluence and not has_open_position:
        # Check Cooldown
        cooldown_hours = 4
        is_cooldown = False
        last_loss_time = 0
        
        # Check user fills for recent losses
        # user_fills_raw is available from earlier scope
        if isinstance(user_fills_raw, list):
             # Sort desc by time just in case
             sorted_fills = sorted(user_fills_raw, key=lambda x: x.get("time", 0), reverse=True)
             for f in sorted_fills:
                 pnl = float(f.get("closedPnl", 0))
                 if pnl < 0:
                     last_loss_time = f.get("time", 0)
                     # Check if within window
                     if (time.time() * 1000 - last_loss_time) < (cooldown_hours * 3600 * 1000):
                         is_cooldown = True
                     break # Found most recent loss
                     
        if is_cooldown:
            print(f"[Analyst v2] 🧊 Scalping Cooldown Active (Last loss {((time.time()*1000-last_loss_time)/3600000):.1f}h ago). Ignoring micro-structure.")
        else:
            # Check Scalping Confluence
            # Logic: 5m Regime aligns with 1m EMA Cross
            trend_1m_ema = ind_1m.get("ema_cross", "NEUTRAL")
            
            # Require 5m Trend + 1m Momentum alignment
            scalp_confluence = (trend_5m == trend_1m_ema) and (trend_5m != "NEUTRAL")
            
            if scalp_confluence:
                confluence = True
                confluence_source = "SCALPING (5m/1m)"
                print(f"[Analyst v2] ⚡ Scalping Opportunity: 5m {trend_5m} + 1m {trend_1m_ema}")

    print(f"\n[Analyst v2] 💰 Current {target_coin} price: ${current_close:,.2f}")

    if has_open_position:
        # --- THE NERVOUS WATCHMAN (Active Management) ---
        # 1. Thesis Validation: If confluence breaks, exit.
        if not confluence:
             print("[Analyst v2] 🔴 NERVOUS WATCHMAN: Confluence broken on open position. Signaling CLOSE.")
             # Construct artificial signal to bypass LLM cost and enforce discipline
             return {
                 "analyst_signal": {
                     "signal": "CLOSE",
                     "confidence": 1.0,
                     "reasoning": f"Nervous Watchman Triggered: 1H Trend ({trend_1h}) and 5m Trend ({trend_5m}) have diverged. Sniper Thesis Invalidated.",
                     "coin": target_coin
                 },
                 "analyst_response": None,
                 "analyst_metadata": {
                     "mode": mode_str,
                     "confluence": False,
                     "current_close": current_close,
                     "phase1_time_ms": phase1_time,
                     "total_time_ms": phase1_time + phase2_time
                 }
             }
        else:
             print("[Analyst v2] 🟢 Nervous Watchman: Confluence holds. Proceeding to Analysis.")
    else:
        # --- SNIPER ENTRY GATEKEEPING ---
        # If no confluence, do not even ask LLM.
        if cfg.risk.require_confluence and not confluence:
            print("[Analyst v2] 🛑 Sniper Gatekeeper: No confluence. Returning HOLD.")
            return {
                 "analyst_signal": {
                     "signal": "HOLD",
                     "confidence": 1.0,
                     "reasoning": f"Sniper Gatekeeper: Market is chopping (1H={trend_1h}, 5m={trend_5m}). Waiting for Setup.",
                     "coin": target_coin
                 },
                 "analyst_response": None,
                 "analyst_metadata": {
                     "mode": mode_str,
                     "confluence": False,
                     "current_close": current_close,
                     "phase1_time_ms": phase1_time,
                     "total_time_ms": phase1_time + phase2_time
                 }
            }

    # ===== PHASE 3: SINGLE LLM ANALYSIS =====

    # Add derived fields
    data["coin"] = target_coin
    data["close"] = current_close
    # Price logged earlier in Phase 2.5
    print(f"[Analyst v2] Context generated: {ind_5m.get('regime', 'N/A')}")
    
    # Show brief summary
    print(f"[Analyst v2] Timeframes: 5m/1h/4h/1d loaded")
    
    # ===== PHASE 3: SINGLE LLM ANALYSIS =====
    phase3_start = time.time()
    
    # Build comprehensive prompt with all data
    account_equity = float(state.get("account_state", {}).get("equity", 0))
    
    # Mode selection - DIFFERENT LOGIC for managing vs seeking
    mode_prompt = ""
    if has_open_position:
        # MANAGING POSITION - Evaluate thesis validity, NOT current confidence
        mode_prompt = f"""
*** MANAGING {position_direction} POSITION - PROTECT THE TRADE ***
You have an OPEN {position_direction} position. Your job is to MANAGE it, not re-evaluate entry.

CRITICAL RULES FOR POSITION MANAGEMENT:
1. EVALUATE THESIS VALIDITY - Is the original trade thesis STILL VALID?
   - If thesis is valid but confidence dropped = HOLD (normal volatility)
   - If thesis is INVALIDATED (structure broke against you) = CLOSE or CUT_LOSS
   
2. DO NOT CLOSE just because current confidence is low!
   - Low confidence on a NEW trade = don't enter
   - Low confidence on EXISTING trade = evaluate thesis, not confidence
   
3. CLOSE/CUT_LOSS ONLY when:
   - Price broke structure that invalidates thesis
   - Key level loss confirmed (not just tested)
   - Stop loss hit
   
4. PROFIT TAKING IS PRIORITY:
   - If price hit your Target/Resistance -> CLOSE or SCALE_OUT.
   - Do not hold endlessly. "Valid thesis" ends when the move completes.
   - UNREALIZED GAINS ARE NOT YOURS. Secure them.

5. DEFAULT ACTION:
   - If developing: HOLD
   - If target hit: CLOSE/SCALE_OUT
   - If thesis failed: CUT_LOSS

Your trade thesis is in THOUGHT CONTINUITY below - REVIEW IT before deciding.
"""
    elif account_equity < 50.0:
        mode_prompt = """
*** LADDER MODE (Equity < $50) - AGGRESSIVE RECOVERY ***
- Confidence threshold: 50% required for entry (Calculated Risk)
- Sizing: MAX (90% equity × 40x)
- AGGRESSIVENESS: High. You cannot afford to wait for "perfect" setups that never come.
- Favor: Momentum plays, scalp setups, quick flips."""
    else:
        mode_prompt = """
*** STANDARD MODE - GROWTH FOCUSED ***
- Confidence threshold: 55%+ required for entry
- ACTION BIAS: Prefer ACTING over HOLDING if Edge > Fees.
- "Neutral" does NOT mean HOLD. If short-term structure is clear, take it.
- Do not fear small losses. Fear missing the move."""
    
    # Build structured data context
    data_context = f"""
## MULTI-TIMEFRAME STRUCTURE (Macro → Micro)

### MACRO CONTEXT (News)
{macro_context}

### 1D (Daily Trend - Big Picture)
{candles_1d_summary}

### 4H (Swing Trend)
{candles_4h_summary}

### 1H (Intraday Trend)
{candles_1h_summary}

### 5M (Entry Timing)
### 5M (Entry Timing)
{candles_5m_summary}

### 1M (Scalp Momentum)
{candles_1m_summary}

### Market Microstructure
{data.get("market_context", "N/A")}

### RECENT TRADING ACTIVITY (Context)
{user_fills_summary}

### Account
{data.get("account_health", "N/A")}

{memory_context}

{learning_context}

## THOUGHT CONTINUITY
{last_conclusion if last_conclusion else "First cycle - no prior context."}
{trade_thesis if trade_thesis else ""}
"""
    
    # Combine system prompt
    system_prompt = get_analyst_prompt() + "\n" + mode_prompt
    
    query = f"""## DECISION CHECKLIST FOR {target_coin}

### Current Mode: {"MANAGING " + position_direction + " POSITION" if has_open_position else "NO POSITION - SEEKING ENTRY"}

{data_context}

ANALYZE:
1. HTF Alignment: Is 4H and 5M trend aligned?
2. Structure: HH/HL = Bullish, LH/LL = Bearish, Mixed = Choppy
3. Funding: Extreme = fade, Neutral = follow structure
4. {"Position Management: Should we HOLD, add to position, or CLOSE?" if has_open_position else "Entry: Do we have >55% confidence for a move > 0.3%?"}

OUTPUT JSON:
```json
{{
  "signal": "LONG" | "SHORT" | "HOLD" | "CLOSE",
  "coin": "{target_coin}",  
  "confidence": 0.0-1.0,
  "reasoning": "Brief: [HTF trend] + [5M structure] + [funding context]",
  "entry_price": float or null,
  "stop_loss": float (below structure),
  "take_profit": float (2-3R target)
}}
```"""

    # Single LLM call (no tool binding needed - data already fetched)
    llm = get_analyst_llm()
    
    try:
        response = await llm.ainvoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=query)
        ])
        
        phase3_time = (time.time() - phase3_start) * 1000
        print(f"[Analyst v2] Phase 3 (LLM): {phase3_time:.0f}ms")
        
        # Log to DB
        async_logger.log(
            action_type="LLM_RESPONSE",
            node_name="analyst_v2",
            output=response.content[:1000] if response.content else "",
            reasoning=f"3-phase analysis complete"
        )
        
        # Parse JSON signal
        signal = _parse_signal(response.content, target_coin)
        
        total_time = (time.time() - start_time) * 1000
        
        # Verbose output for user
        print(f"\n{'='*60}")
        print(f"[Analyst v2] SIGNAL: {signal.get('signal', 'UNKNOWN')} ({signal.get('confidence', 0):.0%} confidence)")
        print(f"[Analyst v2] REASONING: {signal.get('reasoning', 'No reasoning')[:200]}...")
        if signal.get('entry_price'):
            print(f"[Analyst v2] Entry: ${signal.get('entry_price'):.2f} | SL: ${signal.get('stop_loss', 0):.2f} | TP: ${signal.get('take_profit', 0):.2f}")
        print(f"[Analyst v2] TOTAL TIME: {total_time:.0f}ms")
        print(f"{'='*60}\n")
        
        return {
            **state,
            "analyst_signal": signal,
            "analyst_reasoning": signal.get("reasoning", response.content),
            "tools_called": ["get_market_context", "get_candles", "get_account_health"],
            "memory_context": memory,  # Pass to Risk node
            "market_data_snapshot": data, # EXPOSE TO SHADOW RUNNER
            "analyst_metadata": {
                 "mode": mode_str,
                 "phase1_time_ms": int(phase1_time),
                 "phase2_time_ms": int(phase2_time),
                 "phase3_time_ms": int(phase3_time),
                 "total_time_ms": int(total_time),
                 "current_close": current_close,
                 "position_direction": position_direction,
                 "entry_price": active_trade.entry_price if (has_open_position and active_trade) else (float(position.get("entryPx", 0)) if has_open_position else None),
                 "stop_loss": active_trade.stop_loss if (has_open_position and active_trade) else exchange_sl,
                 "take_profit": active_trade.take_profit if (has_open_position and active_trade) else exchange_tp,
                 "position_size": float(position.get("szi", 0)) if has_open_position else 0,
                 "liquidation_price": float(position.get("liquidationPx", 0)) if has_open_position and position.get("liquidationPx") else None,
                 "margin_used": float(position.get("marginUsed", 0)) if has_open_position else 0,
                 "confluence": confluence  # Passed for Risk Node Sizing
            }
        }
        
    except Exception as e:
        print(f"[Analyst v2] Error: {e}")
        async_logger.log(
            action_type="ERROR",
            node_name="analyst_v2",
            output=str(e),
            error=str(e)
        )
        
        return {
            **state,
            "analyst_signal": {"signal": "HOLD", "coin": target_coin, "reasoning": f"Analysis error: {e}"},
            "analyst_reasoning": f"Error: {e}",
            "tools_called": [],
        }


def _parse_signal(content: str, coin: str) -> dict:
    """Extract and VALIDATE JSON signal using Pydantic shared model."""
    try:
        # Try to find JSON block
        if "```json" in content:
            json_str = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            json_str = content.split("```")[1].split("```")[0].strip()
        elif "{" in content:
            start = content.find("{")
            end = content.rfind("}") + 1
            json_str = content[start:end]
        else:
            print("[Analyst v2] PARSE ERROR: No JSON found in response")
            return {"signal": "HOLD", "coin": coin, "reasoning": "Could not parse response: No JSON found", "confidence": 0.0}
        
        # 1. Basic Parse
        raw_data = json.loads(json_str)
        raw_data["coin"] = raw_data.get("coin", coin)
        
        # 2. Pydantic Validation (Strict Type Checking)
        try:
            validated_signal = TradeSignal(**raw_data)
            return validated_signal.model_dump()
        except Exception as validation_err:
            print(f"[Analyst v2] VALIDATION FAILED: {validation_err}")
            # Fallback to safe HOLD if schema is violated
            return {
                "signal": "HOLD", 
                "coin": coin, 
                "confidence": 0.0,
                "reasoning": f"Schema validation failed: {str(validation_err)[:100]}..."
            }
        
    except Exception as e:
        print(f"[Analyst v2] JSON PARSE ERROR: {e}")
        return {"signal": "HOLD", "coin": coin, "reasoning": f"Parse error: {e}", "confidence": 0.0}
