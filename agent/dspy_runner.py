import asyncio
import json
from typing import Any
from .db.dspy_memory import init_dspy_db, DSPyRepository, ShadowTrade

# Flag to ensure DB is initialized only once
_DB_INITIALIZED = False

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
    # NOTE: We do NOT fetch new data. We use what the legacy agent fetched.
    market_data = state.get("market_data_snapshot", {}) 
    # TODO: In Phase D, ensure main.py populates 'market_data_snapshot'
    
    # --- EXECUTION ---
    # --- EXECUTION ---
    try:
        from .dspy.modules import ShadowTrader
        from .telegram import notify_shadow_trade_opened
        import dspy
        from .config import get_config
        
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
            from .dspy.simulator import ShadowSimulator
            current_price = market_data.get("close", 0)
            coin = market_data.get("coin", "BTC") # Default if missing
            if current_price > 0:
                await ShadowSimulator.update_open_trades(current_price, coin)
        except Exception as sim_error:
            print(f"[Shadow Mode] Simulation Error: {sim_error}")

        # --- INFERENCE STEP ---
        
        # Extract inputs (Mocking custom inputs for now as legacy agent doesn't have them yet)
        if not market_data:
             print("[Shadow Mode] Market data empty, skipping inference.")
             return
             
        inputs = {
            "market_structure": str(market_data.get("candles_1h", "Neutral structure")),
            "risk_environment": str(market_data.get("market_context", "Normal")),
            "social_sentiment": 50.0, # Placeholder default
            "whale_activity": "Normal flow", # Placeholder default
            "macro_context": "No major events" # Placeholder default
        }
        
        # Run Inference with Assertions
        with dspy.settings.context(assertions=True):
             prediction = trader(**inputs)
        
        signal = prediction.plan
        
        print(f"[Shadow Mode] RESULT: {signal.signal} ({signal.confidence:.0%}) - {signal.coin}")
        
        trade_record = ShadowTrade(
            coin=signal.coin,
            signal=signal.signal,
            confidence=signal.confidence,
            entry_price=signal.entry_price if signal.entry_price else market_data.get("close", 0),
            size_usd=1000.0, # Default paper size
            leverage=20,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            market_context_hash=str(hash(str(inputs))),
            full_prompt_trace=json.dumps(prediction.plan.model_dump())
        )
        
        DSPyRepository.save_trade(trade_record)
        print(f"[Shadow Mode] Saved trade to memory (ID: {trade_record.id})")
        
        # --- NOTIFICATION ---
        await notify_shadow_trade_opened(
            coin=trade_record.coin,
            signal=trade_record.signal,
            confidence=trade_record.confidence,
            entry_price=trade_record.entry_price,
            stop_loss=trade_record.stop_loss,
            take_profit=trade_record.take_profit
        )
        
    except Exception as e:
        print(f"[Shadow Mode] EXECUTION ERROR: {e}")
        import traceback
        traceback.print_exc()
