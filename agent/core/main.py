"""
AI Trading Agent - Main Entry Point

Runs the 3-minute inference loop connecting to the MCP server.
"""

import warnings
# Suppress Pydantic V2 migration warnings from libraries
warnings.filterwarnings("ignore", message=".*Pydantic V1 style.*")
warnings.filterwarnings("ignore", category=UserWarning, module="pydantic")


import asyncio
import sys
from datetime import datetime
from langchain_mcp_adapters.client import MultiServerMCPClient

from agent.config.config import get_config
from .graph import run_sequential_cycle, get_initial_state
from agent.db import create_tables, get_session, AgentLogRepository
from agent.db.async_logger import async_logger
from agent.utils.learning import init_learning
from .shadow_runner import run_shadow_cycle
from agent.services import telegram



async def get_account_state(tools: list) -> dict:
    """Fetch current account state via MCP."""
    
    # Find get_account_info tool (preferred over health for details)
    info_tool = next((t for t in tools if t.name == "get_account_info"), None)
    
    if not info_tool:
        return {"error": "get_account_info tool not found"}
    
    try:
        # Fetch raw state and open orders
        raw_state = await info_tool.ainvoke({})
        
        # Parallel fetch open orders
        open_orders = []
        orders_tool = next((t for t in tools if t.name == "get_open_orders"), None)
        if orders_tool:
             try:
                 raw_orders = await orders_tool.ainvoke({})
                 # Parse MCP wrapper
                 if isinstance(raw_orders, list) and len(raw_orders) > 0 and isinstance(raw_orders[0], dict) and "text" in raw_orders[0]:
                     try:
                         import json
                         open_orders = json.loads(raw_orders[0]["text"])
                     except: pass
                 elif isinstance(raw_orders, list):
                     # Validate elements are dicts
                     if all(isinstance(x, dict) for x in raw_orders):
                         open_orders = raw_orders
                     else:
                         print(f"[Main] Warning: open_orders contained non-dict items: {raw_orders}")
                         open_orders = []
             except Exception as oe:
                 print(f"[Main] Failed to fetch open orders: {oe}")
        
        # Parse MCP/LangChain wrapped content
        if isinstance(raw_state, list) and len(raw_state) > 0 and isinstance(raw_state[0], dict) and "text" in raw_state[0]:
             try:
                 import json
                 raw_state = json.loads(raw_state[0]["text"])
             except Exception:
                 pass
        elif isinstance(raw_state, str):
            import json
            raw_state = json.loads(raw_state)
            
        # Parse logic (mirrors test_cycle.py)
        margin_summary = raw_state.get("marginSummary", {})
        equity = float(margin_summary.get("accountValue", 0))
        margin_used = float(margin_summary.get("totalMarginUsed", 0))
        margin_usage_pct = (margin_used / equity * 100) if equity > 0 else 0
        
        # Parse positions
        positions = raw_state.get("assetPositions", [])
        active_positions = []
        for p in positions:
            pos = p.get("position", {})
            szi = float(pos.get("szi", 0))
            if szi != 0:
                coin = pos.get("coin")
                entry = float(pos.get("entryPx", 0))
                pnl = float(pos.get("unrealizedPnl", 0))
                side = "LONG" if szi > 0 else "SHORT"
                active_positions.append(f"{side} {coin} (Size: {szi}, Entry: {entry}, PnL: {pnl:.2f})")
        
        pos_str = "; ".join(active_positions) if active_positions else "None"
        
        # Calculate risk level
        risk_level = "LOW"
        if margin_usage_pct > 80: risk_level = "HIGH"
        elif margin_usage_pct > 50: risk_level = "MEDIUM"
        
        return {
            "equity": equity,
            "margin_used": margin_used,
            "margin_usage_pct": round(margin_usage_pct, 2),
            "positions": pos_str,
            "open_symbols": [p.split(" ")[1] for p in active_positions], # Extract coin names
            "open_position_details": {pos.get("coin"): "LONG" if float(pos.get("szi", 0)) > 0 else "SHORT" for p in raw_state.get("assetPositions", []) for pos in [p.get("position", {})] if float(pos.get("szi", 0)) != 0},
            "raw_positions": {pos.get("coin"): pos for p in raw_state.get("assetPositions", []) for pos in [p.get("position", {})] if float(pos.get("szi", 0)) != 0},
            "open_orders": open_orders,
            "risk_level": risk_level,
            "withdrawable": float(raw_state.get("withdrawable", 0))
        }
    except Exception as e:
        return {"error": str(e)}


async def run_inference_cycle(mcp_client: MultiServerMCPClient, tools: list, cycle_count: int) -> dict:
    """Run a single inference cycle."""
    
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Starting inference cycle...")
    
    # Build initial state
    state = get_initial_state()
    state["cycle_number"] = cycle_count  # Inject into state
    
    # Get current account state
    account_state = await get_account_state(tools)
    state["account_state"] = account_state
    
    print(f"  Account: Equity ${account_state.get('equity', 'N/A')}, "
          f"Margin {account_state.get('margin_usage_pct', 'N/A')}%")
    
    # Run the SEQUENTIAL cycle
    result = await run_sequential_cycle(mcp_client, state, tools)
    
    # Log result
    final_decision = result.get("final_decision", {})
    action = final_decision.get("action", "UNKNOWN")
    
    print(f"  Decision: {action}")
    if action == "EXECUTE":
        trade = final_decision.get("trade", {})
        print(f"  Trade: {trade.get('coin')} {'LONG' if trade.get('is_buy') else 'SHORT'} "
              f"${trade.get('size', 0):.2f}")
    elif action == "REQUEST_APPROVAL":
        print(f"  Awaiting Telegram approval...")
    
    with get_session() as session:
        AgentLogRepository.log(
            session,
            action_type="CYCLE_COMPLETE",
            output=str(final_decision)[:5000]
        )
    
    # Send Telegram notification (Inference)
    try:
        analyst_signal = result.get("analyst_signal", {})
        risk_decision = result.get("risk_decision", {})
        metadata = result.get("analyst_metadata", {})
        
        await telegram.notify_inference(
            cycle=state.get("cycle_number", 0),
            equity=account_state.get("equity", 0),
            margin_pct=account_state.get("margin_usage_pct", 0),
            analyst_signal=analyst_signal,
            risk_decision=risk_decision,
            final_action=action,
            open_position_count=len(account_state.get("open_symbols", [])),
            metadata=metadata
        )
        
        # Send Telegram notification (Trade Execution)
        if action == "EXECUTED":
            trade = final_decision.get("trade", {})
            trade_action = trade.get("action", "ENTRY") # Default to ENTRY
            
            # Map merge.py actions to Telegram types
            # merge.py uses: CUT_LOSS, CLOSE, SCALE_OUT, CLOSE_PARTIAL
            # telegram.py expects: ENTRY, SCALE_IN, SCALE_OUT, CUT_LOSS
            
            tg_type = "ENTRY"
            if trade_action in ["CUT_LOSS", "CLOSE"]: tg_type = "CUT_LOSS"
            elif trade_action in ["SCALE_OUT", "CLOSE_PARTIAL"]: tg_type = "SCALE_OUT"
            elif trade_action == "SCALE_IN": tg_type = "SCALE_IN"
            elif analyst_signal.get("signal") == "SCALE_IN": tg_type = "SCALE_IN"
            
            # Extract basic params
            entry_px = trade.get("entry_price") or account_state.get("market_price") or 0
            size_usd = trade.get("size", 0)
            leverage = trade.get("leverage", 1)
            
            # For CLOSE/CUT_LOSS, size/leverage might be ambiguous or full position
            # We will just show what we have.
            
            await telegram.notify_trade_executed(
                coin=trade.get("coin", "BTC"),
                direction="LONG" if trade.get("is_buy", True) else "SHORT",
                size_usd=float(size_usd),
                leverage=int(leverage),
                entry_price=float(entry_px),
                stop_loss=None, # Placeholder (TODO: Convert pct to price)
                take_profit=None, # Placeholder
                order_type=tg_type
            )
            
    except Exception as tg_err:
        print(f"[Telegram] Notification error: {tg_err}")
    
    # --- SHADOW MODE INJECTION (DISABLED) ---
    # Run DSPy Shadow Agent in background using the *exact same* data from this cycle
    # asyncio.create_task(run_shadow_cycle(result, tools))
    
    return result


async def main_loop():
    """Main trading loop - runs every 3 minutes."""
    
    cfg = get_config()
    
    print("=" * 60)
    print("  Hyperliquid AI Trading Agent")
    print("=" * 60)
    print(f"  MCP Server: {cfg.mcp_server_url}")
    print(f"  Analyst Model: {cfg.analyst_model}")
    print(f"  Risk Model: {cfg.risk_model}")
    print(f"  Inference Interval: {cfg.inference_interval_seconds}s (3 min)")
    print(f"  Max Position: {cfg.risk.max_position_pct * 100}%")
    print(f"  Max Drawdown: {cfg.risk.max_drawdown_pct * 100}%")
    print(f"  Auto-Approve Limit: ${cfg.risk.auto_approve_usd}")
    print("=" * 60)
    
    # Initialize database
    print("\n[INIT] Creating database tables...")
    create_tables()
    init_learning()  # Seed trade patterns if not exists
    
    # Connect to MCP server
    print(f"[INIT] Connecting to MCP server at {cfg.mcp_server_url}...")
    
    mcp_config = {
        "hyperliquid": {
            "url": cfg.mcp_server_url,
            "transport": "sse"
        }
    }
    
    # New API: no context manager
    mcp_client = MultiServerMCPClient(mcp_config)
    
    # Retry logic for initial connection
    tools = None
    while tools is None:
        try:
            tools = await mcp_client.get_tools()
            print(f"[INIT] Connected! {len(tools)} tools available.")
        except Exception as e:
            print(f"[INIT] Connection failed: {e}. Retrying in 5s...")
            await asyncio.sleep(5)
    
    # List some tools
    tool_names = [t.name for t in tools[:5]]
    print(f"[INIT] Tools: {', '.join(tool_names)}...")
    
    print("\n[RUNNING] Starting inference loop (Ctrl+C to stop)...")
    await async_logger.start()
    
    cycle_count = 0
    while True:
        try:
            cycle_count += 1
            print(f"\n--- Cycle #{cycle_count} ---")
            
            await run_inference_cycle(mcp_client, tools, cycle_count)
            
            # Wait for next cycle
            print(f"\n[WAIT] Sleeping {cfg.inference_interval_seconds}s until next cycle...")
            await asyncio.sleep(cfg.inference_interval_seconds)
            
        except KeyboardInterrupt:
            print("\n[STOP] Shutting down gracefully...")
            await async_logger.stop()
            break
        except Exception as e:
            print(f"\n[ERROR] Cycle failed: {e}")
            import traceback
            traceback.print_exc()
            # Log error but continue
            with get_session() as session:
                AgentLogRepository.log(
                    session,
                    action_type="ERROR",
                    output=str(e),
                    error=str(e)
                )
            # Wait before retry
            await asyncio.sleep(30)


def main():
    """Entry point."""
    try:
        asyncio.run(main_loop())
    except KeyboardInterrupt:
        print("\nGoodbye!")
        sys.exit(0)


if __name__ == "__main__":
    main()
