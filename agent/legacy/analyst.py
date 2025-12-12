"""
Market Analyst Node

Analyzes market conditions and generates trade signals.
Uses a manual tool execution loop for OpenRouter compatibility.
"""

import json
import time
from typing import Any
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage, ToolMessage

from agent.config.llm_factory import get_analyst_llm
from agent.utils.prompts import get_analyst_prompt, build_system_context
from agent.db.async_logger import async_logger
from agent.db import get_session, MarketMemoryRepository, TradeRepository, InferenceLogRepository
from agent.db.models import Trade, MarketMemory
from sqlmodel import select
from langchain_core.tools import tool
from datetime import datetime
from agent.config.config import get_config

@tool
def save_daily_bias(coin: str, analysis: str, bias: str, volatility_score: float) -> str:
    """
    Save the Daily Chart analysis to memory explanation.
    Args:
        coin: "BTC"
        analysis: Summary of 1D chart (Trend, Key Levels).
        bias: "BULLISH", "BEARISH", or "NEUTRAL".
        volatility_score: 0-100 (High score = Expect ranges to break).
    """
    try:
        today_str = datetime.utcnow().strftime("%Y-%m-%d")
        with get_session() as session:
            existing = MarketMemoryRepository.get_today(session, coin, today_str)
            if existing:
                return f"Memory for {today_str} already exists. Skipping."
            
            mem = MarketMemory(
                coin=coin,
                date=today_str,
                analysis=analysis,
                market_bias=bias,
                volatility_score=volatility_score
            )
            MarketMemoryRepository.create(session, mem)
        return f"Daily Bias for {today_str} Saved Successfully."
    except Exception as e:
        return f"Error saving memory: {e}"


async def execute_tool_call(tool_call: dict, tools: list) -> str:
    """Execute a single tool call and return the result."""
    tool_name = tool_call.get("name")
    tool_args = tool_call.get("args", {})
    
    # Handle local storage tool
    if tool_name == "save_daily_bias":
        # Execute locally
        return save_daily_bias.invoke(tool_args)

    # Find the tool in the provided list
    selected_tool = next((t for t in tools if t.name == tool_name), None)
    if not selected_tool:
        return f"Error: Tool {tool_name} not found."
    
    try:
        # Execute async
        result = await selected_tool.ainvoke(tool_args)
        return result
    except Exception as e:
        return f"Error: {str(e)}"


async def analyst_node(state: dict[str, Any], tools: list) -> dict[str, Any]:
    """
    Market Analyst node for LangGraph.
    
    Analyzes market conditions and proposes trade signals.
    Uses bind_tools() with a manual execution loop for OpenRouter compatibility.
    
    Args:
        state: Current graph state with account_state, exit_plans, etc.
        tools: MCP tools available for analysis
        
    Returns:
        Updated state with analyst_signal
    """
    llm = get_analyst_llm()
    cfg = get_config()
    target_coin = cfg.focus_coins[0]
    
    # Fetch active trade context (Episodic Memory)
    active_trade_context = ""
    open_symbols = state.get("account_state", {}).get("open_symbols", [])
    
    if open_symbols:
        try:
            with get_session() as session:
                # Find active trades for these symbols
                statement = select(Trade).where(Trade.coin.in_(open_symbols)).where(Trade.closed_at == None)
                active_trades = session.exec(statement).all()
                
                if active_trades:
                    memory_blocks = []
                    for trade in active_trades:
                        memory_blocks.append(f"Pos: {trade.direction} {trade.coin} | Entry: {trade.entry_price} | THESIS: '{trade.reasoning}'")
                    
                    active_trade_context = "## ACTIVE TRADE THESIS (MEMORY)\n" + "\n".join(memory_blocks)
        except Exception as e:
            print(f"[Analyst] Failed to fetch trade memory for {open_symbols}: {e}")
            import traceback
            traceback.print_exc()

    context = build_system_context(
        account_state=state.get("account_state", {}),
        active_exit_plans=state.get("exit_plans_context", "No active exit plans."),
        tool_list=[t.name for t in tools],
        active_trade_context=active_trade_context
    )
    
    
    # Check for LADDER MODE (Micro-Account)
    account_equity = float(state.get("account_state", {}).get("equity", 0))
    mode_prompt = ""
    if account_equity < 50.0:
        mode_prompt = """
*** LADDER MODE ACTIVE (Equity < $50) ***
You are in LADDER MODE. Your goal is AGGRESSIVE GROWTH.
- Ignore small invalidation risks.
- TARGET SIZE: "ALL IN" (100% Margin).
- LEVERAGE: MAX (50x).
- BIAS: DIRECTIONAL MOMENTUM. Do not hedge.
- Signal LONG if ANY bullish structure exists.
- Do NOT output "HOLD" unless the market is crashing.
- Your target is to double the account.
"""

    # --- PERFORMANCE MEMORY (ADAPTIVE LEARNING) ---
    perf_context = ""
    with get_session() as session:
        metrics = TradeRepository.get_performance_metrics(session, target_coin, hours=24)
        
    win_rate = metrics["win_rate"]
    pnl = metrics["total_pnl_usd"]
    
    # Adaptive Instructions based on Performance
    if win_rate < 40 and metrics["total_trades"] > 3:
        perf_context = f"""
*** PERFORMANCE ALERT: WIN RATE LOW ({metrics['win_rate']:.1f}%) ***
You are performing poorly on {target_coin} today.
- MODE: DEFENSIVE.
- REQUIREMENT: Only take A+ setups. Confidence must be > 0.8.
- REDUCE FREQUENCY: Do not force trades.
"""
    elif pnl > 50:
        perf_context = f"""
*** PERFORMANCE ALERT: WINNING STREAK (+${pnl:.2f}) ***
You are trading well.
- MODE: AGGRESSIVE.
- AUTHORITY: Trust your instinct. Trend is your friend.
"""

    # --- THOUGHT CONTINUITY (SHORT-TERM MEMORY) ---
    thought_context = ""
    with get_session() as session:
        last_logs = InferenceLogRepository.get_recent(session, limit=1)
        if last_logs:
            last_thought = last_logs[0].analyst_reasoning or "No previous thought."
            if len(last_thought) > 10:
                thought_context = f"""
## 💭 PREVIOUS CYCLE THOUGHT (Short-Term Memory)
"Last time I thought: {last_thought[:300]}..."
Use this to maintain continuity. If you were waiting for a condition, check if it occured.
"""

    system_prompt = get_analyst_prompt() + "\n" + mode_prompt + "\n" + perf_context + "\n" + thought_context + "\n" + context
    
    
    # --- MARKET MEMORY CHECK ---
    today_str = datetime.utcnow().strftime("%Y-%m-%d")
    daily_memory = None
    with get_session() as session:
        daily_memory = MarketMemoryRepository.get_today(session, target_coin, today_str)

    # Bind tools (Local + Remote)
    all_tools = tools + [save_daily_bias]
    llm_with_tools = llm.bind_tools(all_tools)
    
    # Calculate timestamps
    current_ms = int(time.time() * 1000)
    start_ms_5m = current_ms - (50 * 5 * 60 * 1000)
    start_ms_4h = current_ms - (50 * 4 * 60 * 60 * 1000)
    start_ms_1d = current_ms - (30 * 24 * 60 * 60 * 1000)
    
    # Build Query based on Memory
    if daily_memory:
        # CACHE HIT: Inject memory, skip 1D fetch
        print(f"[Analyst] Daily Memory HIT for {today_str} ({daily_memory.market_bias})")
        
        memory_block = f"""
## 🧠 DAILY MARKET MEMORY (CACHED {today_str})
BIAS: {daily_memory.market_bias}
VOLATILITY SCORE: {daily_memory.volatility_score}/100
ANALYSIS: {daily_memory.analysis}
--> INSTRUCTION: SKIP 1D Chart Analysis. Use this cached context for Macro Bias.
"""
        query = f"""Perform a HIGH FUNCTIONING SCALPER analysis on {target_coin} (5m Chart).

*** IMPORTANT: START YOUR RESPONSE WITH A TOOL CALL. DO NOT PREAMBLE. ***

{memory_block}

STEPS:
1. Call get_market_context('{target_coin}') -> CRITICAL: Check Funding, OI, Premium.
2. Call get_candles for {target_coin} (Interval: '5m', Start: {start_ms_5m}, End: {current_ms}) -> IMMEDIATE STRUCTURE.
3. Call get_candles for {target_coin} (Interval: '4h', Start: {start_ms_4h}, End: {current_ms}) -> INTERMEDIATE TREND.
4. (Daily Bias Cached) -> Proceed to Signal Generation.
5. Check get_account_health.

ANALYSIS PRIORITY:
- If Cached Bias is Bullish, look for 5m Long entries.
- If Cached Bias is Bearish, fade 5m pumps.
"""
    else:
        # CACHE MISS: Fetch 1D and Save
        print(f"[Analyst] Daily Memory MISS for {today_str}. Fetching new data.")
        
        query = f"""Perform a HIGH FUNCTIONING SCALPER analysis on {target_coin} (5m Chart).

*** IMPORTANT: START YOUR RESPONSE WITH A TOOL CALL. DO NOT PREAMBLE. ***

STEPS:
1. Call get_market_context('{target_coin}') -> CRITICAL: Check Funding, OI, Premium.
2. Call get_candles for {target_coin} (Interval: '5m', Start: {start_ms_5m}, End: {current_ms}) -> IMMEDIATE STRUCTURE.
3. Call get_candles for {target_coin} (Interval: '4h', Start: {start_ms_4h}, End: {current_ms}) -> INTERMEDIATE TREND.
4. Call get_candles for {target_coin} (Interval: '1d', Start: {start_ms_1d}, End: {current_ms}) -> MACRO BIAS.
5. *** ACTIONS REQUIRED ***: 
   - Analyze the 1D Chart.
   - CALL `save_daily_bias(coin='{target_coin}', analysis='...', bias='...', volatility_score=...)`.
6. Check get_account_health.

ANALYSIS PRIORITY:
- Establish the Daily Bias first, SAVE IT, then trade the 5m chart logic.
"""

    query += """
After gathering data, provide your analysis as JSON:
```json
{
  "signal": "LONG" or "SHORT" or "HOLD",
  "coin": "{target_coin}",
  "confidence": 0.0 to 1.0,
  "reasoning": "detailed explanation of your analysis",
  "entry_price": float or null,
  "stop_loss": float or null,
  "take_profit": float or null
}
```"""

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=query)
    ]
    
    # Tool execution loop (max 15 iterations)
    max_iterations = 15
    tools_called = []
    full_reasoning = [query]
    
    try:
        for i in range(max_iterations):
            # Check for "Final Lap" warning
            if i == max_iterations - 2:
                 messages.append(HumanMessage(content="SYSTEM ALERT: Critical step limit reached. You must OUTPUT the final JSON signal now. Do not call more tools."))
            # Get LLM response
            response = await llm_with_tools.ainvoke(messages)
            messages.append(response)
            
            print(f"[Analyst] Iteration {i+1}/{max_iterations}")
            
            # Log to DB (Async)
            async_logger.log(
                action_type="LLM_THOUGHT",
                node_name="analyst",
                output=response.content[:1000] if response.content else "",
                reasoning=f"Iteration {i+1}/{max_iterations}"
            )
            
            # Log the content if any
            if response.content:
                full_reasoning.append(response.content)
            
            # Check for tool calls
            if hasattr(response, 'tool_calls') and response.tool_calls:
                for tool_call in response.tool_calls:
                    tool_name = tool_call.get("name", "unknown")
                    tools_called.append(tool_name)
                    print(f"  Tool Call: {tool_name}")
                
                    # Execute the tool
                    result = await execute_tool_call(tool_call, tools)
                    print(f"    Result: {str(result)[:100]}...")
                    
                    # Log tool usage (Async)
                    async_logger.log(
                        action_type="TOOL_CALL",
                        node_name="analyst",
                        tool_name=tool_name,
                        output=f"Tool: {tool_name}\nResult: {str(result)[:500]}",
                        reasoning=f"Iteration {i+1}"
                    )
                    
                    # Add tool result as message
                    tool_msg = ToolMessage(
                        content=result[:4000],  # Truncate large results
                        tool_call_id=tool_call.get("id", str(i))
                    )
                    messages.append(tool_msg)
                    full_reasoning.append(f"[Tool: {tool_name}] {result[:500]}...")
            else:
                # No tool calls - check if it's a valid final response (JSON)
                is_json_response = "```json" in response.content or '"signal":' in response.content
                
                if is_json_response:
                     print(f"[Analyst] Reasoning: {response.content[:200]}...")
                     break
                else:
                    # It's just talking/planning without tools or JSON. Reject it.
                    if i < max_iterations - 1:
                        print("[Analyst] Thinking... (Text-only response detected, nudging for tools)")
                        # Check if we should nudge for specific tools or just general action
                        if "get_market_context" not in tools_called:
                             messages.append(HumanMessage(content=f"SYSTEM ALERT: You output text but NO tool calls. You MUST call `get_market_context('{target_coin}')` now. Do not describe the plan, EXECUTE IT."))
                        else:
                             messages.append(HumanMessage(content="SYSTEM ALERT: You output text but NO tool calls. If you have enough data, output the JSON signal. If not, call the next tool."))
                        continue
                    else:
                        break
        
        # Log the interaction (Async)
        async_logger.log(
            action_type="LLM_RESPONSE",
            node_name="analyst",
            output=f"Tools called: {tools_called}",
            reasoning="\n---\n".join(full_reasoning)
        )
        
        # Parse the final response for JSON signal
        signal = None
        final_content = response.content if response.content else ""
        
        if final_content:
            # Try multiple JSON extraction strategies
            json_str = None
            
            # Strategy 1: Extract from ```json code block
            if "```json" in final_content:
                try:
                    json_str = final_content.split("```json")[1].split("```")[0].strip()
                except IndexError:
                    pass
            
            # Strategy 2: Extract from generic code block
            if json_str is None and "```" in final_content:
                try:
                    json_str = final_content.split("```")[1].split("```")[0].strip()
                except IndexError:
                    pass
            
            # Strategy 3: Find JSON object with "signal" key (handles nested braces)
            if json_str is None:
                import re
                # Match JSON object containing "signal" - allow nested braces
                pattern = r'\{[^{}]*"signal"[^{}]*(?:\{[^{}]*\}[^{}]*)*\}'
                match = re.search(pattern, final_content, re.DOTALL)
                if match:
                    json_str = match.group()
            
            # Strategy 4: Try to find any JSON-like structure
            if json_str is None:
                import re
                # Find the largest JSON object
                matches = re.findall(r'\{[^{}]+\}', final_content, re.DOTALL)
                for m in matches:
                    if '"signal"' in m:
                        json_str = m
                        break
            
            # Try to parse the extracted JSON
            if json_str:
                try:
                    signal = json.loads(json_str)
                except json.JSONDecodeError:
                    # Try to fix common JSON issues
                    try:
                        # Fix missing quotes around values
                        fixed = json_str.replace("'", '"')
                        signal = json.loads(fixed)
                    except json.JSONDecodeError:
                        pass
        
        # If no structured signal, create from content
        if signal is None:
            signal = {
                "signal": "HOLD",
                "coin": None,
                "confidence": 0.0,
                "coins_analyzed": tools_called,
                "reasoning": final_content if final_content else "No analysis produced"
            }
        
        # Ensure required fields
        signal.setdefault("reasoning", "Analysis completed")
        signal.setdefault("coins_analyzed", tools_called)
        
        return {
            **state,
            "analyst_signal": signal,
            "analyst_response": response
        }
        
    except Exception as e:
        # Log error
        # Log error (Async)
        async_logger.log(
            action_type="ERROR",
            node_name="analyst",
            output=str(e),
            error=str(e)
        )
        
        return {
            **state,
            "analyst_signal": {
                "signal": "HOLD",
                "coin": None,
                "confidence": 0.0,
                "reasoning": f"Analysis error: {str(e)}"
            },
            "analyst_error": str(e)
        }


