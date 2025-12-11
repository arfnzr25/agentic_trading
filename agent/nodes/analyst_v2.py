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

from ..llm_factory import get_analyst_llm
from ..prompts import get_analyst_prompt, build_system_context
from ..db.async_logger import async_logger
from ..db import get_session, MarketMemoryRepository
from ..db.models import MarketMemory, Trade
from ..db.repository import InferenceLogRepository
from ..config import get_config
from ..memory_loader import preload_memory, format_memory_context
from ..data_fetcher import fetch_analyst_data, calculate_timestamps, summarize_candles
from ..learning import get_learning_context
from datetime import datetime
from sqlmodel import select
from ..models.schemas import TradeSignal


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
    data = await fetch_analyst_data(tools, target_coin, timestamps)
    
    # Fetch trade history learning (also async)
    learning_context = await get_learning_context(tools)
    
    phase2_time = (time.time() - phase2_start) * 1000
    print(f"[Analyst v2] Phase 2 (Fetch + Learning): {phase2_time:.0f}ms")
    
    # Debug: check what we got
    candles_5m_raw = data.get("candles_5m", "")
    candles_1h_raw = data.get("candles_1h", "")
    candles_4h_raw = data.get("candles_4h", "")
    candles_1d_raw = data.get("candles_1d", "")
    
    # Summarize candle data (show more candles for better analysis)
    candles_5m_summary = summarize_candles(candles_5m_raw, max_candles=15)
    candles_1h_summary = summarize_candles(candles_1h_raw, max_candles=24)
    candles_4h_summary = summarize_candles(candles_4h_raw, max_candles=12)
    candles_1d_summary = summarize_candles(candles_1d_raw, max_candles=7)
    
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
   
4. DEFAULT ACTION = HOLD (let the trade work)
   - Premature closes eat account with fees
   - Patience with valid thesis = profits

Your trade thesis is in THOUGHT CONTINUITY below - REVIEW IT before deciding.
"""
    elif account_equity < 50.0:
        mode_prompt = """
*** LADDER MODE (Equity < $50) - SEEKING ENTRY ***
- Confidence threshold: 60%+ required for entry
- Sizing: MAX (90% equity × 40x)
- PATIENCE: Wait for clear setups, one good trade > many small trades eaten by fees
- Favor: Momentum plays, aligned HTF"""
    else:
        mode_prompt = """
*** STANDARD MODE - SEEKING ENTRY ***
- Confidence threshold: 60%+ required for entry
- Favor: Well-defined structure with clear invalidation"""
    
    # Build structured data context
    data_context = f"""
## MULTI-TIMEFRAME STRUCTURE (Macro → Micro)

### 1D (Daily Trend - Big Picture)
{candles_1d_summary}

### 4H (Swing Trend)
{candles_4h_summary}

### 1H (Intraday Trend)
{candles_1h_summary}

### 5M (Entry Timing)
{candles_5m_summary}

### Market Microstructure
{data.get("market_context", "N/A")}

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
4. {"Position Management: Should we HOLD, add to position, or CLOSE?" if has_open_position else "Entry: Only if aligned + structure confirmed"}

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
