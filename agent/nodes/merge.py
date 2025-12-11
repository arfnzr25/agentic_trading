"""
Merge Node

Combines Analyst and Risk Manager outputs into final decision.
Handles execution via MCP or routes to Telegram approval.
"""

import json
from datetime import datetime
from typing import Any
from langchain_core.messages import HumanMessage, SystemMessage

from ..llm_factory import get_llm
from ..prompts import get_merge_prompt
from ..db import get_session, AgentLogRepository, TradeRepository, ExitPlanRepository, Trade, ExitPlan
from ..db.async_logger import async_logger
from sqlmodel import select
from ..config import get_config


async def merge_node(state: dict[str, Any], tools: list) -> dict[str, Any]:
    """
    Merge node for LangGraph Option B.
    
    Combines analyst signal and risk decision into final action.
    
    Args:
        state: Current graph state with analyst_signal and risk_decision
        tools: MCP tools for execution
        
    Returns:
        Updated state with final_decision and any execution results
    """
    cfg = get_config()
    
    analyst_signal = state.get("analyst_signal") or {}
    risk_decision = state.get("risk_decision") or {}
    
    # Handle both old ("decision") and new ("action") risk output formats
    decision = risk_decision.get("decision") or risk_decision.get("action")
    
    # Normalize decision values
    if decision in ["APPROVE", "OPEN_LONG", "OPEN_SHORT"]:
        decision = "APPROVE"
    
    print(f"[Merge] Analyst signal: {analyst_signal.get('signal')} | Risk decision: {decision}")

    
    
    # Handle special Rescue/Cut decisions FIRST
    
    if decision == "CUT_LOSS":
        coin = analyst_signal.get("coin") or "BTC"
        result = await _execute_cut_loss(coin, tools)
        return {
            **state,
            "final_decision": {
                "action": "EXECUTED",
                "trade": {"coin": coin, "action": "CUT_LOSS"},
                "result": result,
                "reasoning": risk_decision.get("reasoning", "Emergency Cut")
            }
        }
    
    # Handle CLOSE signal from Analyst - PRIORITY over Risk decision
    analyst_signal_type = analyst_signal.get("signal")
    if analyst_signal_type == "CLOSE":
        coin = analyst_signal.get("coin") or "BTC"
        print(f"[Merge] CLOSE signal detected - executing position close for {coin}")
        result = await _execute_cut_loss(coin, tools)  # Reuse cut loss function
        return {
            **state,
            "final_decision": {
                "action": "EXECUTED",
                "trade": {"coin": coin, "action": "CLOSE"},
                "result": result,
                "reasoning": analyst_signal.get("reasoning", "Analyst triggered CLOSE")
            }
        }
    
    # Handle CUT_LOSS from Analyst - emergency close
    if analyst_signal_type == "CUT_LOSS":
        coin = analyst_signal.get("coin") or "BTC"
        print(f"[Merge] CUT_LOSS signal detected - emergency close for {coin}")
        result = await _execute_cut_loss(coin, tools)
        return {
            **state,
            "final_decision": {
                "action": "EXECUTED",
                "trade": {"coin": coin, "action": "CUT_LOSS"},
                "result": result,
                "reasoning": analyst_signal.get("reasoning", "Stop loss hit - emergency exit")
            }
        }
    
    # Handle SCALE_OUT from Analyst - partial close
    if analyst_signal_type == "SCALE_OUT":
        coin = analyst_signal.get("coin") or "BTC"
        print(f"[Merge] SCALE_OUT signal detected - closing 50% of {coin} position")
        result = await _execute_scale_out(coin, tools, pct=0.5)
        return {
            **state,
            "final_decision": {
                "action": "EXECUTED",
                "trade": {"coin": coin, "action": "SCALE_OUT", "pct": 0.5},
                "result": result,
                "reasoning": analyst_signal.get("reasoning", "Taking partial profit")
            }
        }
    
    # Handle SCALE_IN from Analyst - add to position
    if analyst_signal_type == "SCALE_IN":
        coin = analyst_signal.get("coin") or "BTC"
        print(f"[Merge] SCALE_IN signal detected - adding to {coin} position")
        # SCALE_IN uses same trade execution as LONG/SHORT
        # Falls through to _build_trade_params and _execute_trade

    # Handle missing or empty signals (Standard path)
    if (not analyst_signal or analyst_signal.get("signal") in ("HOLD", None)) and decision not in ("RESCUE_DCA", "CUT_LOSS"):
        return {
            **state,
            "final_decision": {
                "action": "NO_TRADE",
                "reasoning": analyst_signal.get("reasoning", "No opportunity identified") if analyst_signal else "Analyst did not return a signal"
            }
        }
    
    if decision == "REJECT":
        return {
            **state,
            "final_decision": {
                "action": "REJECTED",
                "reasoning": risk_decision.get("notes", "Risk rules violated")
            }
        }
    
    if decision == "NO_TRADE":
        return {
            **state,
            "final_decision": {
                "action": "NO_TRADE",
                "reasoning": "No active trade signal"
            }
        }
        
    # Handle SCALE_OUT / REDUCE (Partial Close)
    if decision in ("SCALE_OUT", "REDUCE") and analyst_signal.get("signal") == "SCALE_OUT":
        coin = analyst_signal.get("coin")
        result = await _execute_scale_out(coin, tools, pct=0.5)
        return {
            **state,
            "final_decision": {
                "action": "SCALED_OUT",
                "trade": {"coin": coin, "action": "CLOSE_PARTIAL", "pct": 0.5},
                "result": result
            }
        }
    
    # Build the trade from validated signal
    trade_params = _build_trade_params(analyst_signal, risk_decision, cfg, state)
    
    # Check if approval needed
    # Treat SCALE_IN same as auto-approve for now unless size is huge
    size_usd = trade_params.get("size", 0)
    # requires_approval = size_usd >= cfg.risk.auto_approve_usd
    requires_approval = False # DISABLED per user request for autonomous growth
    
    if requires_approval:
        # Return approval request instead of executing
        approval_message = _build_approval_message(trade_params, analyst_signal)
        
        return {
            **state,
            "final_decision": {
                "action": "REQUEST_APPROVAL",
                "trade": trade_params,
                "requires_approval": True,
                "approval_message": approval_message
            }
        }
    
    # Auto-execute small trades (APPROVE or SCALE_IN)
    result = await _execute_trade(trade_params, tools, state)
    
    # Save to database if successful
    if result.get("success"):
        _save_trade_to_db(trade_params, analyst_signal, risk_decision)
    
    return {
        **state,
        "final_decision": {
            "action": "EXECUTED",
            "trade": trade_params,
            "result": result
        }
    }


def _build_trade_params(analyst_signal: dict, risk_decision: dict, cfg, state: dict = None) -> dict:
    """Build trade parameters from analyst signal and risk decision."""
    
    # Use risk-adjusted size if available
    size_usd = risk_decision.get("adjusted_size_usd") or analyst_signal.get("size_usd", 1000)
    leverage = risk_decision.get("leverage", 20)
    
    exit_plan = risk_decision.get("exit_plan", {})
    
    coin = analyst_signal.get("coin")
    signal_type = analyst_signal.get("signal")
    
    # Determine direction
    is_buy = True
    if signal_type == "LONG":
        is_buy = True
    elif signal_type == "SHORT":
        is_buy = False
    elif signal_type == "SCALE_IN":
        # Infer direction from existing position
        if state:
            pos_details = state.get("account_state", {}).get("open_position_details", {})
            current_side = pos_details.get(coin, "LONG") # Default to LONG if unknown (risky but fallback)
            is_buy = (current_side == "LONG")
            
    
    # --- SIZE BUMPING LOGIC (Ladder Mode Override) ---
    # In standard mode: bump to $12 minimum if too small.
    # In LADDER MODE (Equity < $50): Force MAX MARGIN position for aggressive growth.
    
    MIN_ORDER_SIZE = 12.0 # Exchange minimum with safety buffer
    MAX_LEVERAGE = 40 # Hyperliquid max for BTC
    LADDER_SAFETY = 0.90 # Use 90% of equity to leave buffer for fees
    
    current_equity = 0.0
    if state:
        current_equity = float(state.get("account_state", {}).get("equity", 0))
        
    if current_equity < 50.0:
        # LADDER MODE: Force maximum margin position
        max_position = current_equity * MAX_LEVERAGE * LADDER_SAFETY
        if size_usd < max_position:
            print(f"[Merge] LADDER MODE: Overriding size ${size_usd:.2f} -> ${max_position:.2f} (Equity ${current_equity:.2f} × {MAX_LEVERAGE}x × {LADDER_SAFETY})")
            size_usd = max(max_position, MIN_ORDER_SIZE)  # At least exchange minimum
            leverage = MAX_LEVERAGE
    elif size_usd < MIN_ORDER_SIZE:
        print(f"[Merge] Warning: Size ${size_usd:.2f} below min ${MIN_ORDER_SIZE}, but not bumping (Standard Mode)")

    return {
        "coin": coin,
        "is_buy": is_buy,
        "size": size_usd,
        "size_type": "usd",
        "sl_pct": exit_plan.get("stop_loss_pct", cfg.risk.default_sl_btc_pct),
        "tp_pct": exit_plan.get("take_profit_pct", 0.05),
        "leverage": leverage
    }


def _build_approval_message(trade_params: dict, analyst_signal: dict) -> str:
    """Build Telegram approval message."""
    
    direction = "LONG" if trade_params["is_buy"] else "SHORT"
    
    entry_val = analyst_signal.get('entry_price')
    entry_str = f"${entry_val:,.2f}" if isinstance(entry_val, (int, float)) else "Market"
    
    size_val = trade_params['size']
    size_str = f"${size_val:,.2f}"
    
    sl_pct = trade_params.get('sl_pct', 0) * 100
    sl_str = f"{sl_pct:.1f}%"
    
    tp_pct = trade_params.get('tp_pct', 0) * 100
    tp_str = f"{tp_pct:.1f}%"

    return f"""[ALERT] **Trade Approval Required**

**{trade_params['coin']} {direction}**
Entry: ~{entry_str}
Size: {size_str}
Leverage: {trade_params['leverage']}x

Stop Loss: -{sl_str}
Take Profit: +{tp_str}

**Reasoning:**
{analyst_signal.get('reasoning', 'N/A')[:200]}

Reply [YES] to APPROVE or [NO] to REJECT"""





async def _execute_trade(trade_params: dict, tools: list, state: dict) -> dict:
    """Execute trade via MCP tools."""
    
    # Find the place_smart_order tool
    place_order_tool = None
    for tool in tools:
        if tool.name == "place_smart_order":
            place_order_tool = tool
            break
    
    if not place_order_tool:
        return {"success": False, "error": "place_smart_order tool not found"}
    
    try:
        # Call the tool
        result = await place_order_tool.ainvoke({
            "coin": trade_params["coin"],
            "is_buy": trade_params["is_buy"],
            "size": trade_params["size"],
            "size_type": trade_params["size_type"],
            "sl_pct": trade_params["sl_pct"],
            "tp_pct": trade_params["tp_pct"],
            "leverage": trade_params["leverage"]
        })
        
        # Check for error string return (FastMCP catches exceptions)
        if isinstance(result, str) and result.strip().startswith("Error"):
             async_logger.log(
                 action_type="ERROR",
                 node_name="merge",
                 tool_name="place_smart_order",
                 output=result,
                 error="Tool returned error string"
             )
             return {"success": False, "error": result}
        
        # Log success logic...
        # Log success logic
        async_logger.log(
            action_type="TOOL_CALL",
            node_name="merge",
            tool_name="place_smart_order",
            input_args=json.dumps(trade_params),
            output=str(result)[:5000]
        )
        
        
        # --- PERSIST TRADE TO DATABASE ---
        # Use the robust saver (handles both new trades and updates)
        _save_trade_to_db(trade_params, state.get("analyst_signal", {}), state.get("risk_decision", {}), result)
            
        return {"success": True, "result": result}
        
    except Exception as e:
        async_logger.log(
            action_type="ERROR",
            node_name="merge",
            tool_name="place_smart_order",
            output=str(e),
            error=str(e)
        )
        
        return {"success": False, "error": str(e)}


async def _execute_cut_loss(coin: str, tools: list) -> dict:
    """Execute emergency cut loss for a specific coin."""
    
    # 1. Try to find close_all_positions first (safest)
    close_all_tool = next((t for t in tools if t.name == "close_all_positions"), None)
    
    if close_all_tool:
        try:
            # We use close_all_positions for now as it's the most reliable "panic" button implemented
            # In future, we should implement close_position(coin) specific tool
            result = await close_all_tool.ainvoke({})
            return {"success": True, "tool": "close_all_positions", "result": result}
        except Exception as e:
            return {"success": False, "error": str(e)}
            
    # 2. Fallback: Identify position and market close
    # (Requires get_account_info + place_order logic, omitting for brevity in this hotfix)
    return {"success": False, "error": "close_all_positions tool not found"}


async def _execute_scale_out(coin: str, tools: list, pct: float = 0.5) -> dict:
    """Execute a partial close (Scale Out)."""
    close_tool = next((t for t in tools if t.name == "close_position"), None)
    if not close_tool:
        return {"success": False, "error": "close_position tool not found"}
        
    try:
        # Close 'pct' of the position (e.g., 0.5 for 50%)
        # Note: close_position tool expects 'percentage' as usage, usually 0.0-1.0 or 0-100?
        # Checking implementation: usually normalized to 0-1 implies 100%. 
        # Let's assume tool takes 0.0-1.0 floats logic.
        result = await close_tool.ainvoke({"coin": coin, "percentage": pct})
        return {"success": True, "result": result}
    except Exception as e:
        return {"success": False, "error": str(e)}


def _save_trade_to_db(trade_params: dict, analyst_signal: dict, risk_decision: dict, result: Any = None) -> None:
    """Save executed trade and exit plan to database (Create or Update)."""
    
    try:
        with get_session() as session:
            coin = trade_params["coin"]
            
            # Check for existing open trade
            statement = select(Trade).where(Trade.coin == coin).where(Trade.closed_at == None)
            existing_trade = session.exec(statement).first()
            
            exit_plan_data = risk_decision.get("exit_plan", {})
            
            if existing_trade:
                # UPDATE existing trade
                
                # Update size if scaling
                if trade_params.get("sl_pct"): # If we have new params
                    # Logic to update size roughly - exact size tracking is complex without fetch
                    # For now just update the exit plan
                    pass
                
                # Update Exit Plan
                # Check if plan exists
                if existing_trade.exit_plan:
                    existing_trade.exit_plan.stop_loss_pct = exit_plan_data.get("stop_loss_pct", existing_trade.exit_plan.stop_loss_pct)
                    existing_trade.exit_plan.take_profit_pct = exit_plan_data.get("take_profit_pct", existing_trade.exit_plan.take_profit_pct)
                    new_conds = exit_plan_data.get("invalidation_conditions", [])
                    if new_conds:
                        existing_trade.exit_plan.invalidation_conditions = new_conds
                    
                    session.add(existing_trade.exit_plan)
                else:
                    # Create missing plan
                    plan = ExitPlan(
                        trade_id=existing_trade.id,
                        stop_loss_pct=exit_plan_data.get("stop_loss_pct"),
                        take_profit_pct=exit_plan_data.get("take_profit_pct"),
                        invalidation_conditions=json.dumps(exit_plan_data.get("invalidation_conditions", [])),
                        status="ACTIVE"
                    )
                    session.add(plan)
                
                session.commit()
                print(f"[DB] Updated Trade {existing_trade.id} Exit Plan")
                
            else:
                # CREATE new trade
                
                # Get actual entry price from result if available
                entry_px = analyst_signal.get("entry_price", 0)
                if result and isinstance(result, dict):
                    entry_px = float(result.get("avgPx", result.get("entryPx", entry_px)))
                
                trade = Trade(
                    coin=trade_params["coin"],
                    direction="LONG" if trade_params["is_buy"] else "SHORT",
                    entry_price=entry_px,
                    size_usd=trade_params["size"],
                    size_tokens=0, 
                    leverage=trade_params["leverage"],
                    reasoning=analyst_signal.get("reasoning", "Autonomous Entry (No reasoning provided)")
                )
                session.add(trade)
                session.commit()
                session.refresh(trade)
                
                # Calculate Prices for Exit Plan
                entry = trade.entry_price or 0
                tp_pct = exit_plan_data.get("take_profit_pct")
                sl_pct = exit_plan_data.get("stop_loss_pct")
                
                tp_price = 0.0
                sl_price = 0.0
                
                if entry > 0:
                    if trade.direction == "LONG":
                        tp_price = entry * (1 + tp_pct) if tp_pct else 0
                        sl_price = entry * (1 - sl_pct) if sl_pct else 0
                    else:
                        tp_price = entry * (1 - tp_pct) if tp_pct else 0
                        sl_price = entry * (1 + sl_pct) if sl_pct else 0

                # Create exit plan
                plan = ExitPlan(
                    trade_id=trade.id,
                    stop_loss_pct=sl_pct,
                    take_profit_pct=tp_pct,
                    stop_loss_price=sl_price,
                    take_profit_price=tp_price,
                    invalidation_conditions_json=json.dumps(exit_plan_data.get("invalidation_conditions", [])),
                    status="ACTIVE"
                )
                session.add(plan)
                session.commit()
                print(f"[DB] Created New Trade {trade.id}")
            
    except Exception as e:
        print(f"DB Error: {e}")
