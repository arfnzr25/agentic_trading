import asyncio
import json
from typing import Any
from agent.db.dspy_memory import init_dspy_db, DSPyRepository, ShadowTrade

# Flag to ensure DB is initialized only once
_DB_INITIALIZED = False

# Fee rate for Hyperliquid (maker/taker average)
SIMULATED_FEE_RATE = 0.0003  # 0.03% per trade (entry + exit = ~0.06%)

async def run_shadow_cycle(state: dict[str, Any], tools: list):
    """
    Main entry point for the DSPy Shadow Mode.
    
    Args:
        state: COMPLETE immutable state copy from the live agent.
        tools: List of MCP tools (shared).
        
    This function runs in the background (fire-and-forget style) 
    so it does not block the live trading agent.
    """
    global _DB_INITIALIZED
    
    if not _DB_INITIALIZED:
        init_dspy_db()
        _DB_INITIALIZED = True
        print("[Shadow Mode] Database initialized")
        
    print("[Shadow Mode] Cycle started (Async)")
    
    # Extract Immutable Data needed for analysis
    market_data = state.get("market_data_snapshot", {}) 
    account_state = state.get("account_state", {})
    real_exchange_equity = float(account_state.get("equity", 0))
    
    # Initialize or get shadow account (uses real equity on first run, then diverges)
    shadow_account = DSPyRepository.get_or_create_account(real_exchange_equity)
    shadow_equity = shadow_account.current_equity
    
    # --- EXECUTION ---
    try:
        from agent.dspy.modules import ShadowTrader
        from agent.services.telegram import notify_shadow_trade_opened
        import dspy
        from agent.config.config import get_config
        
        # Initialize Module
        trader = ShadowTrader()
        cfg = get_config()
        
        # Configure LM (OpenRouter)
        try:
            if not dspy.settings.lm:
                 dspy.settings.configure(lm=dspy.LM(
                     model=f"openai/{cfg.analyst_model}",
                     api_key=cfg.openrouter_api_key,
                     api_base=cfg.openrouter_base_url
                 ))
        except:
             dspy.settings.configure(lm=dspy.LM(
                 model=f"openai/{cfg.analyst_model}",
                 api_key=cfg.openrouter_api_key,
                 api_base=cfg.openrouter_base_url
             ))

        # --- SIMULATION STEP ---
        # Check outcomes of previous trades based on current price
        try:
            from agent.dspy.simulator import ShadowSimulator
            current_price = market_data.get("close", 0)
            coin = market_data.get("coin", "BTC")
            if current_price > 0:
                await ShadowSimulator.update_open_trades(current_price, coin)
        except Exception as sim_error:
            print(f"[Shadow Mode] Simulation Error: {sim_error}")

        # --- INFERENCE STEP ---
        
        if not market_data:
             print("[Shadow Mode] Market data empty, skipping inference.")
             return
             
        # Context Injection: Get Shadow State
        from sqlmodel import select
        from agent.db.dspy_memory import get_dspy_session, ShadowTrade
        
        # 1. Get Open Positions details
        with get_dspy_session() as session:
            open_trades = session.exec(
                select(ShadowTrade).where(ShadowTrade.pnl_usd == None)
            ).all()
            
            # Format open positions for LLM
            if open_trades:
                pos_details = ", ".join([f"{t.coin} ({t.signal} @ ${t.entry_price:.2f})" for t in open_trades])
                open_context = f"OPEN POSITIONS ({len(open_trades)}): {pos_details}"
            else:
                open_context = "NO OPEN POSITIONS."
                
            # 2. Get Last Closed Trade
            last_trade = session.exec(
                select(ShadowTrade)
                .where(ShadowTrade.pnl_usd != None)
                .order_by(ShadowTrade.timestamp.desc())
            ).first()
            
            if last_trade:
                outcome = "WIN" if last_trade.pnl_usd > 0 else "LOSS"
                trade_history = f"LAST TRADE: {last_trade.coin} {last_trade.signal} -> {outcome} (${last_trade.pnl_usd:+.2f})"
            else:
                trade_history = "NO TRADE HISTORY."

        inputs = {
            "market_structure": str(market_data.get("candles_1h", "Neutral structure")),
            "risk_environment": str(market_data.get("market_context", "Normal")),
            "social_sentiment": 50.0,
            "whale_activity": "Normal flow",
            "macro_context": "No major events",
            "account_context": f"Shadow Equity: ${shadow_equity:.2f} | {open_context}",
            "last_trade_outcome": trade_history
        }
        
        # Run Inference with Assertions
        with dspy.settings.context(assertions=True):
             prediction = trader(**inputs)
        
        signal = prediction.plan
        
        # Extract reasoning from DSPy output
        reasoning = getattr(signal, 'reasoning', None) or "No reasoning provided"
        
        if signal.signal in ["CLOSE", "CUT_LOSS"]:
             print(f"[Shadow Mode] ACTION: Closing all {signal.coin} positions (Reason: {reasoning})")
             from agent.dspy.simulator import ShadowSimulator
             current_price = market_data.get("close", 0)
             if current_price > 0:
                 await ShadowSimulator.close_all_positions(signal.coin, current_price, reason=signal.signal)
             return  # Stop here, do not create a new trade record for "opening" a close
        
        # Calculate position size based on SHADOW equity (independent from exchange)
        leverage = 20
        size_usd = min(shadow_equity * 0.9 * leverage, 1000.0) if shadow_equity > 0 else 1000.0
        
        print(f"[Shadow Mode] RESULT: {signal.signal} ({signal.confidence:.0%}) - {signal.coin}")
        print(f"[Shadow Mode] Shadow Equity: ${shadow_equity:.2f} | Size: ${size_usd:.2f}")
        print(f"[Shadow Mode] Reasoning: {reasoning[:100]}...")
        
        if signal.signal == "HOLD":
             # Optional: Log HOLDs but don't notify or save trade
             return

        trade_record = ShadowTrade(
            coin=signal.coin,
            signal=signal.signal,
            confidence=signal.confidence,
            reasoning=reasoning,
            entry_price=signal.entry_price if signal.entry_price else market_data.get("close", 0),
            size_usd=size_usd,
            leverage=leverage,
            account_equity=shadow_equity,  # Use shadow equity, not exchange
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            market_context_hash=str(hash(str(inputs))),
            full_prompt_trace=json.dumps({
                "inputs": inputs,
                "output": prediction.plan.model_dump()
            })
        )
        
        DSPyRepository.save_trade(trade_record)
        print(f"[Shadow Mode] Saved trade to memory (ID: {trade_record.id})")
        
        # Get active positions count
        open_count = DSPyRepository.get_open_position_count()
        
        # --- NOTIFICATION WITH ALL TRACKABLE PARAMETERS ---
        await notify_shadow_trade_opened(
            coin=trade_record.coin,
            signal=trade_record.signal,
            confidence=trade_record.confidence,
            entry_price=trade_record.entry_price,
            stop_loss=trade_record.stop_loss,
            take_profit=trade_record.take_profit,
            reasoning=trade_record.reasoning,
            account_equity=trade_record.account_equity,
            open_position_count=open_count
        )
        
    except Exception as e:
        print(f"[Shadow Mode] EXECUTION ERROR: {e}")
        import traceback
        traceback.print_exc()

