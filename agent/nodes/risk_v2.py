"""
Risk Manager Node (Refactored v2)

No tool calls - receives context from Analyst and makes decisions.
Uses memory_context passed from analyst for learning-aware sizing.
"""

import json
from typing import Any
from langchain_core.messages import HumanMessage, SystemMessage

from agent.config.llm_factory import get_risk_llm
from agent.utils.prompts import get_risk_prompt
from agent.db.async_logger import async_logger
from agent.config.config import get_config
from agent.models.schemas import RiskDecision


async def risk_node(state: dict[str, Any], tools: list) -> dict[str, Any]:
    """
    Risk Manager node (v2) - No tool calls.
    
    Receives analyst_signal and memory_context from Analyst.
    Makes sizing/leverage decisions without additional API calls.
    """
    cfg = get_config()
    target_coin = cfg.focus_coins[0]
    
    analyst_signal = state.get("analyst_signal") or {}
    memory_context = state.get("memory_context") or {}
    account_state = state.get("account_state") or {}
    
    signal_type = analyst_signal.get("signal", "HOLD")
    confidence = analyst_signal.get("confidence", 0)
    reasoning = analyst_signal.get("reasoning", "No reasoning provided")
    
    print(f"\n[Risk v2] Evaluating signal: {signal_type} ({confidence:.0%})")
    
    # Quick HOLD check - STRICT for ALL accounts
    # Require 60%+ confidence to trade (fees eat small wins)
    min_confidence = 0.6
    
    if signal_type == "HOLD" or confidence < min_confidence:
        print(f"[Risk v2] Decision: NO_TRADE (signal={signal_type}, conf={confidence:.0%}, min_required={min_confidence:.0%})")
        return {
            **state,
            "risk_decision": {
                "approved": False,
                "action": "NO_TRADE",
                "reason": f"Signal is {signal_type} with {confidence:.0%} confidence (need {min_confidence:.0%}+)"
            }
        }
    
    # Get account info from state
    equity = float(account_state.get("equity", 0))
    margin_usage = float(account_state.get("margin_usage_pct", 0))
    
    # Get learning insights
    learning = memory_context.get("learning", {}) if memory_context else {}
    performance = memory_context.get("performance", {}) if memory_context else {}
    
    # Build risk context
    risk_context = f"""
## SIGNAL TO EVALUATE
Signal: {signal_type} {target_coin}
Confidence: {confidence:.0%}
Reasoning: {reasoning[:300]}...

## ACCOUNT STATE
Equity: ${equity:.2f}
Margin Used: {margin_usage:.1f}%

## LEARNING INSIGHTS
{format_learning(learning)}

## PERFORMANCE (24h)
Win Rate: {performance.get('win_rate', 0):.1f}%
PnL: ${performance.get('total_pnl_usd', 0):.2f}
"""

    # Determine mode and sizing
    if equity < 50.0:
        # LADDER MODE - AGGRESSIVE
        leverage = 40  # MAX leverage for BTC
        
        # For micro accounts (<$10), enforce minimum $100 position
        if equity < 10.0:
            min_position = 100.0  # Minimum $100 notional
            margin_needed = min_position / leverage  # $2 margin for $100 position
            position_size = min_position
        else:
            # Use 90% of equity with max leverage
            position_size = equity * leverage * 0.9
        
        mode_prompt = f"""
*** LADDER MODE (Equity ${equity:.2f}) - ULTRA AGGRESSIVE ***
LEVERAGE: {leverage}x (MAX)
POSITION SIZE: ${position_size:.2f} notional
MARGIN USED: ${position_size/leverage:.2f}

CRITICAL: For micro accounts, we MUST use large positions to overcome fees.
- Minimum position: $100 notional
- This means using ${position_size/leverage:.2f} margin with {leverage}x leverage
- Output size_usd as ${position_size/leverage:.2f} (the margin amount)

Output your decision as JSON.
"""
    else:
        # Normal mode
        max_position = equity * cfg.max_position_pct
        mode_prompt = f"""
Normal mode. Max position: ${max_position:.2f}
Be conservative with sizing.
"""

    system_prompt = get_risk_prompt() + mode_prompt
    
    query = f"""{risk_context}

Based on the above, provide your risk decision as JSON:
```json
{{
  "approved": true or false,
  "action": "OPEN_LONG" or "OPEN_SHORT" or "NO_TRADE",
  "size_usd": float,
  "leverage": int (1-40),
  "stop_loss": float (price where trade is invalid),
  "take_profit": float (target price),
  "invalidation_conditions": ["List conditions that would invalidate this trade", "e.g. Price closes below $90,000 on 4H"],
  "reason": "brief explanation"
}}
```

IMPORTANT: Output ONLY the JSON."""

    llm = get_risk_llm()
    
    try:
        response = await llm.ainvoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=query)
        ])
        
        decision = _parse_decision(response.content)
        
        # Verbose output
        print(f"\n{'='*60}")
        print(f"[Risk v2] DECISION: {decision.get('action', 'UNKNOWN')}")
        if decision.get("approved"):
            print(f"[Risk v2] SIZE: ${decision.get('size_usd', 0):.2f} @ {decision.get('leverage', 1)}x")
        print(f"[Risk v2] REASON: {decision.get('reason', 'No reason')[:150]}")
        print(f"{'='*60}\n")
        
        async_logger.log(
            action_type="RISK_DECISION",
            node_name="risk_v2",
            output=json.dumps(decision),
            reasoning=decision.get("reason", "")
        )
        
        return {
            **state,
            "risk_decision": decision,
            "risk_response": response
        }
        
    except Exception as e:
        print(f"[Risk v2] Error: {e}")
        return {
            **state,
            "risk_decision": {
                "approved": False,
                "action": "NO_TRADE",
                "reason": f"Risk error: {e}"
            }
        }


def format_learning(learning: dict) -> str:
    """Format learning insights for prompt."""
    if not learning or learning.get("sample_size", 0) < 3:
        return "Insufficient trade history for learning."
    
    return f"""Long Win Rate: {learning.get('long_win_rate', 0):.0f}%
Short Win Rate: {learning.get('short_win_rate', 0):.0f}%
Recommendation: {learning.get('recommendation', 'N/A')}"""


def _parse_decision(content: str) -> dict:
    """Extract and VALIDATE JSON decision using Pydantic model."""
    try:
        # Extract JSON
        if "```json" in content:
            json_str = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            json_str = content.split("```")[1].split("```")[0].strip()
        elif "{" in content:
            start = content.find("{")
            end = content.rfind("}") + 1
            json_str = content[start:end]
        else:
            print("[Risk v2] PARSE ERROR: No JSON found")
            return {"approved": False, "action": "NO_TRADE", "reason": "No JSON found in response", "size_usd": 0.0, "leverage": 1}
        
        raw_data = json.loads(json_str)
        
        # Normalize fields for Pydantic
        # Map "decision" -> "action" (common LLM alias)
        if "decision" in raw_data and "action" not in raw_data:
            raw_data["action"] = raw_data["decision"]
            
        # Map "reasoning" -> "reason"
        if "reasoning" in raw_data and "reason" not in raw_data:
            raw_data["reason"] = raw_data["reasoning"]
            
        # 1. Pydantic Validation
        try:
            validated_decision = RiskDecision(**raw_data)
            return validated_decision.model_dump()
        except Exception as validation_err:
            print(f"[Risk v2] VALIDATION FAILED: {validation_err}")
            # Fail safe
            return {
                "approved": False, 
                "action": "NO_TRADE", 
                "reason": f"Schema validation failed: {str(validation_err)[:100]}",
                "size_usd": 0.0,
                "leverage": 1
            }
            
    except Exception as e:
        print(f"[Risk v2] ERROR: {e}")
        return {"approved": False, "action": "NO_TRADE", "reason": f"Parse error: {e}", "size_usd": 0.0, "leverage": 1}

