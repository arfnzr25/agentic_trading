"""
Risk Manager Node

Validates trades against risk rules and manages exit plans.
Uses a manual tool execution loop for OpenRouter compatibility.
"""

import json
from typing import Any
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

from agent.config.llm_factory import get_risk_llm
from agent.utils.prompts import get_risk_prompt, build_system_context
from agent.db import get_session, AgentLogRepository, ExitPlanRepository, MarketMemoryRepository, TradeRepository
from agent.db.async_logger import async_logger
from datetime import datetime
from agent.config.config import get_config


async def execute_tool_call(tool_call: dict, tools: list) -> str:
    """Execute a single tool call and return the result."""
    tool_name = tool_call.get("name")
    tool_args = tool_call.get("args", {})
    
    for tool in tools:
        if tool.name == tool_name:
            try:
                result = await tool.ainvoke(tool_args)
                return json.dumps(result) if not isinstance(result, str) else result
            except Exception as e:
                return f"Error calling {tool_name}: {str(e)}"
    
    return f"Tool {tool_name} not found"


async def risk_node(state: dict[str, Any], tools: list) -> dict[str, Any]:
    """
    Risk Manager node for LangGraph.
    
    Validates proposed trades and manages exit plans.
    Uses bind_tools() with a manual execution loop for OpenRouter compatibility.
    
    Args:
        state: Current graph state with analyst_signal, account_state, etc.
        tools: MCP tools available for risk assessment
        
    Returns:
        Updated state with risk_decision
    """
    cfg = get_config()
    target_coin = cfg.focus_coins[0]
    llm = get_risk_llm()
    
    analyst_signal = state.get("analyst_signal") or {}
    
    # Build context
    # Fetch Daily Memory (Macro Context)
    today_str = datetime.utcnow().strftime("%Y-%m-%d")
    memory_context = ""
    with get_session() as session:
        daily_memory = MarketMemoryRepository.get_today(session, target_coin, today_str)
        if daily_memory:
             memory_context = f"""
## 🧠 DAILY MACRO BIAS (Shared Context)
BIAS: {daily_memory.market_bias}
VOLATILITY: {daily_memory.volatility_score}/100
GUIDANCE: Adjust risk tolerance. High volatility = Wider stops allowed.
"""

    # --- PERFORMANCE MEMORY (ADAPTIVE RISK) ---
    perf_context = ""
    with get_session() as session:
        metrics = TradeRepository.get_performance_metrics(session, target_coin, hours=24)
        
    pnl_24h = metrics["total_pnl_usd"]
    
    if pnl_24h < -20.0:
        perf_context = f"""
## ⚠️ PERFORMANCE ALERT: DRAWDOWN (-${abs(pnl_24h):.2f})
Risk Protocol: DEFENSIVE
- Max Leverage: REDUCED (Max 20x).
- Approval: REQUIRE STRUCTURAL CONFIRMATION.
- Rejection: Reject weak setups aggressively.
"""
    elif pnl_24h > 50.0:
        perf_context = f"""
## 🚀 PERFORMANCE ALERT: PROFITABLE (+${pnl_24h:.2f})
Risk Protocol: GROWTH
- Max Leverage: ALLOW MAX.
- Tolerance: Standard deviations allowed.
"""

    context = build_system_context(
        account_state=state.get("account_state", {}),
        active_exit_plans=state.get("exit_plans_context", "No active exit plans."),
        tool_list=[t.name for t in tools]
    ) + memory_context + perf_context
    
    system_prompt = get_risk_prompt() + "\n" + context
    
    # Bind tools to the LLM
    llm_with_tools = llm.bind_tools(tools)
    
    # Build query based on whether there's a trade signal
    signal_type = analyst_signal.get("signal", "HOLD")
    coin = analyst_signal.get("coin")
    
    if signal_type == "HOLD" or not coin:
        query = f"""No new trade signal from analyst. Perform a risk check.

STEPS:
1. Call get_account_health to check current portfolio status
2. Call get_open_orders to see any pending orders

Active Exit Plans:
{state.get("exit_plans_context", "No active exit plans.")}

RESPOND with a JSON decision:
```json
{{
  "decision": "NO_TRADE",
  "account_health": "summary of account status",
  "margin_available": percentage as decimal,
  "exit_plans_status": "summary of exit plan checks",
  "reasoning": "detailed explanation of current risk status"
}}
```"""
    else:
        # Check Account Size for Ladder Mode
        account_state = state.get("account_state", {})
        equity = float(account_state.get("equity", 0))
        
        if equity < 50.0:
             # LADDER MODE PARAMETERS - FORCE MAX SIZING
             max_position_usd = equity * 50 * 0.9  # Equity × Max Leverage × Safety
             risk_params = f"""
- MODE: **LADDER CHALLENGE (Micro-Account) - MAXIMUM AGGRESSION**
- REQUIRED POSITION SIZE: ${max_position_usd:.2f} (Equity × 50x × 0.9)
- Max position: 100% of portfolio (ALL-IN MANDATORY)
- Max drawdown: 100% (Liquidation is the only stop)
- Max concurrent positions: 1
- Leverage: 50x (REQUIRED)
- Auto-approve: YES

CRITICAL: You MUST set adjusted_size_usd to ${max_position_usd:.2f}. Do NOT use smaller sizes.
"""
        else:
             # STANDARD PARAMETERS
             risk_params = f"""
- Max position: {cfg.risk.max_position_pct * 100}% of portfolio
- Max drawdown: {cfg.risk.max_drawdown_pct * 100}%
- Max concurrent positions: {cfg.risk.max_concurrent_positions}
- Prefer max leverage: {cfg.risk.prefer_max_leverage}
- Auto-approve threshold: ${cfg.risk.auto_approve_usd}
"""

        query = f"""Validate this proposed trade from the Market Analyst:

PROPOSED TRADE:
{json.dumps(analyst_signal, indent=2)}

RISK PARAMETERS:{risk_params}

STEPS:
1. Call get_account_health to check current equity and margin
2. Call get_open_orders to count existing positions
3. Validate against risk parameters
4. If approved, create an exit plan with stop-loss and take-profit

RESPOND with a JSON decision:
```json
{{
  "decision": "APPROVE" or "REJECT" or "CUT_LOSS" or "RESCUE_DCA",
  "adjusted_size_usd": float,
  "leverage": int,
  "exit_plan": {{
    "stop_loss_pct": float,
    "take_profit_pct": float,
    "invalidation_conditions": ["condition 1"]
  }},
  "reasoning": "detailed explanation",
  "rescue_plan": "If underwater, specify plan: 'CUT NOW', 'DCA @ price', or 'HEDGE'"
}}
```"""

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=query)
    ]
    
    # Tool execution loop (max 5 iterations)
    max_iterations = 5
    tools_called = []
    full_reasoning = [query]
    
    try:
        for i in range(max_iterations):
            # Get LLM response
            response = await llm_with_tools.ainvoke(messages)
            messages.append(response)
            
            print(f"[Risk] Iteration {i+1}/{max_iterations}")
            
            # Log to DB (Async)
            async_logger.log(
                action_type="LLM_THOUGHT",
                node_name="risk",
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
                        node_name="risk",
                        output=f"Tool: {tool_name}\nResult: {str(result)[:500]}",
                        reasoning=f"Iteration {i+1}"
                    )
                    
                    # Add tool result as message
                    tool_msg = ToolMessage(
                        content=result[:4000],
                        tool_call_id=tool_call.get("id", str(i))
                    )
                    messages.append(tool_msg)
                    full_reasoning.append(f"[Tool: {tool_name}] {result[:500]}...")
            else:
                # No more tool calls - we have the final response
                if response.content:
                     print(f"[Risk] Reasoning: {response.content[:200]}...")
                break
        
        # Log the interaction (Async)
        async_logger.log(
            action_type="LLM_RESPONSE",
            node_name="risk",
            output=f"Tools called: {tools_called}",
            reasoning="\n---\n".join(full_reasoning)
        )
        
        # Parse the final response for JSON decision
        decision = None
        final_content = response.content if response.content else ""
        
        if final_content:
            try:
                if "```json" in final_content:
                    json_str = final_content.split("```json")[1].split("```")[0]
                    decision = json.loads(json_str.strip())
                elif "```" in final_content:
                    json_str = final_content.split("```")[1].split("```")[0]
                    decision = json.loads(json_str.strip())
                else:
                    import re
                    json_match = re.search(r'\{[^{}]*"decision"[^{}]*\}', final_content, re.DOTALL)
                    if json_match:
                        decision = json.loads(json_match.group())
            except json.JSONDecodeError:
                pass
        
        # If no structured decision, create from content
        if decision is None:
            decision = {
                "decision": "REJECT" if signal_type != "HOLD" else "NO_TRADE",
                "reasoning": final_content if final_content else "No risk assessment produced"
            }
        
        # Ensure required fields
        decision.setdefault("reasoning", "Risk assessment completed")
        
        return {
            **state,
            "risk_decision": decision,
            "risk_response": response
        }
        
    except Exception as e:
        # Log error (Async)
        async_logger.log(
            action_type="ERROR",
            node_name="risk",
            output=str(e),
            error=str(e)
        )
        
        return {
            **state,
            "risk_decision": {
                "decision": "REJECT",
                "reasoning": f"Risk assessment error: {str(e)}"
            },
            "risk_error": str(e)
        }


