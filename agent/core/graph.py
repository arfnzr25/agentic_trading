"""
LangGraph Workflow - Option B (Parallel Merge)

Analyst and Risk Manager run in parallel, then merge.
"""

import asyncio
import os
from typing import Any, TypedDict
from langgraph.graph import StateGraph, END
from langchain_mcp_adapters.client import MultiServerMCPClient

from agent.config.config import get_config
from agent.nodes.merge import merge_node
from agent.nodes.analyst_v2 import analyst_node
from agent.nodes.risk_v2 import risk_node
from agent.db import get_session, ExitPlanRepository

# Legacy v1 toggles removed - v2 is now standard


class AgentState(TypedDict, total=False):
    """State schema for the trading agent graph."""
    
    # Context injected at start of each cycle
    account_state: dict
    exit_plans_context: str
    
    # Analyst output
    analyst_signal: dict
    analyst_response: Any
    analyst_error: str
    analyst_metadata: dict  # Holds timings, mode, etc.
    
    # Risk output
    risk_decision: dict
    risk_response: Any
    risk_error: str
    
    # Final decision
    final_decision: dict
    
    # MCP tools reference
    tools: list
    
    # Shadow Mode Data
    market_data_snapshot: dict
    memory_context: str


async def create_agent_graph(mcp_client: MultiServerMCPClient) -> StateGraph:
    """
    Create the LangGraph workflow for Option B (Parallel Merge).
    
    Flow:
        [Start] 
           ↓
        [Parallel]
           ├── analyst_node
           └── risk_node
           ↓
        [Merge Node]
           ↓
        [End]
    
    Args:
        mcp_client: Connected MCP client with tools
        
    Returns:
        Compiled StateGraph
    """
    
    # Get tools from MCP
    tools = mcp_client.get_tools()
    
    # Create wrapper functions that include tools
    async def analyst_wrapper(state: AgentState) -> AgentState:
        print("[Graph] Using analyst_v2 (3-phase)")
        return await analyst_node(state, tools)
    
    async def risk_wrapper(state: AgentState) -> AgentState:
        return await risk_node(state, tools)
    
    async def merge_wrapper(state: AgentState) -> AgentState:
        return await merge_node(state, tools)
    
    # Build the graph
    workflow = StateGraph(AgentState)
    
    # Add nodes
    workflow.add_node("analyst", analyst_wrapper)
    workflow.add_node("risk", risk_wrapper)
    workflow.add_node("merge", merge_wrapper)
    
    # Set entry point - both analyst and risk start together
    workflow.set_entry_point("analyst")
    
    # For true parallel execution, we'd use a fan-out pattern
    # But LangGraph's default is sequential, so we'll run them in sequence
    # and merge. For Option B parallel, we use asyncio.gather in the runner.
    
    # Simple sequential for now (will be parallelized in runner)
    workflow.add_edge("analyst", "risk")
    workflow.add_edge("risk", "merge")
    workflow.add_edge("merge", END)
    
    return workflow.compile()


async def run_sequential_cycle(mcp_client: MultiServerMCPClient, initial_state: AgentState, tools: list) -> dict:
    """
    Run a single inference cycle with SEQUENTIAL execution (Analyst -> Risk -> Merge).
    
    This ensures Risk Manager sees the actual Analyst signal before deciding.
    """
    import json
    from agent.db import get_session, InferenceLogRepository
    from agent.config.config import get_config
    
    cfg = get_config()
    
    # 1. Run Analyst (v2)
    print("[Cycle] Using analyst_v2 (3-phase)")
    analyst_result = await analyst_node(initial_state, tools)
    
    # 2. Update state with Analyst signal so Risk can see it
    intermediate_state = {
        **initial_state,
        "analyst_signal": analyst_result.get("analyst_signal"),
        "analyst_response": analyst_result.get("analyst_response"),
        "memory_context": analyst_result.get("memory_context"),  # Pass learning to Risk
        "market_data_snapshot": analyst_result.get("market_data_snapshot"), # Pass data to Shadow Runner
    }
    
    # 3. Run Risk (now seeing the signal)
    print("[Cycle] Using risk_v2 (no tool calls)")
    risk_result = await risk_node(intermediate_state, tools)
    
    # Merge the results
    merged_state = {
        **intermediate_state,
        "risk_decision": risk_result.get("risk_decision"),
        "risk_response": risk_result.get("risk_response")
    }
    
    # 4. Run Merge Node
    final_state = await merge_node(merged_state, tools)
    
    # Archive logic (rest remains similar)
    try:
        analyst_signal = analyst_result.get("analyst_signal") or {}
        risk_decision = risk_result.get("risk_decision") or {}
        final_decision = final_state.get("final_decision") or {}
        account_state = initial_state.get("account_state") or {}
        
        # Extract reasoning
        analyst_response = analyst_result.get("analyst_response")
        risk_response = risk_result.get("risk_response")
        
        analyst_reasoning = analyst_signal.get("reasoning", "")
        if analyst_response and hasattr(analyst_response, "content"):
            analyst_reasoning = analyst_response.content or analyst_reasoning
            
        risk_reasoning = risk_decision.get("notes", risk_decision.get("reason", ""))
        if risk_response and hasattr(risk_response, "content"):
            risk_reasoning = risk_response.content or risk_reasoning
        
        # Extract tool calls
        analyst_tool_calls = None
        risk_tool_calls = None
        if analyst_response and hasattr(analyst_response, "tool_calls"):
            analyst_tool_calls = json.dumps([str(tc) for tc in (analyst_response.tool_calls or [])])
        if risk_response and hasattr(risk_response, "tool_calls"):
            risk_tool_calls = json.dumps([str(tc) for tc in (risk_response.tool_calls or [])])
        
        with get_session() as session:
            InferenceLogRepository.create(
                session,
                analyst_model=cfg.analyst_model,
                risk_model=cfg.risk_model,
                analyst_signal=json.dumps(analyst_signal) if analyst_signal else None,
                analyst_reasoning=analyst_reasoning,
                analyst_tool_calls=analyst_tool_calls,
                risk_decision=json.dumps(risk_decision) if risk_decision else None,
                risk_reasoning=risk_reasoning,
                risk_tool_calls=risk_tool_calls,
                final_action=final_decision.get("action"),
                final_reasoning=final_decision.get("reasoning"),
                account_equity=account_state.get("equity"),
                account_margin_pct=account_state.get("margin_usage_pct")
            )
    except Exception as e:
        print(f"[WARN] Failed to archive inference: {e}")
    
    return final_state


def get_initial_state() -> AgentState:
    """Build initial state for a new inference cycle."""
    
    # Get account state via MCP (will be populated by caller)
    account_state = {}
    
    # Get active exit plans from DB
    with get_session() as session:
        active_plans = ExitPlanRepository.get_active_plans(session)
        exit_plans_context = ExitPlanRepository.format_for_context(active_plans)
    
    return AgentState(
        account_state=account_state,
        exit_plans_context=exit_plans_context
    )
