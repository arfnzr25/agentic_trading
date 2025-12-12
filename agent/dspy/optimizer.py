import dspy
import json
import os
from dspy.teleprompt import MIPROv2
from sqlmodel import select
from agent.db.dspy_memory import get_dspy_session, OptimizationExample
from agent.dspy.modules import ShadowTrader
from agent.config.config import get_config

# --- CONFIGURATION ---
MIN_EXAMPLES = 10  # Require at least this many examples to run

def load_dataset():
    """Load optimization examples from DB and convert to DSPy Examples."""
    with get_dspy_session() as session:
        opt_data = session.exec(select(OptimizationExample)).all()
        
    dataset = []
    for d in opt_data:
        # Create DSPy Example
        # Start with Inputs
        ex = dspy.Example(
            market_structure=d.input_market_structure,
            risk_environment=d.input_risk_env,
            social_sentiment=50.0, # Default/Placeholder if missing
            whale_activity="Normal flow",
            macro_context="No major events",
            account_context="Optimized Context", # Placeholder
            last_trade_outcome="N/A"
        )
        
        # Add labels (Gold Plan)
        # We need to stash the gold plan for the metric to check against
        try:
             gold_json = json.loads(d.gold_plan_json)
             ex = ex.with_inputs(
                 "market_structure", "risk_environment", "social_sentiment", 
                 "whale_activity", "macro_context", "account_context", "last_trade_outcome"
             )
             # Attach the gold label (raw dict) for the metric
             ex.gold_signal = gold_json.get("signal")
             ex.gold_plan = gold_json
             dataset.append(ex)
        except:
            continue
            
    print(f"Loaded {len(dataset)} examples from DB.")
    return dataset

def trading_metric(gold, pred, trace=None):
    """
    Custom metric for Shadow Trader.
    Score 1.0 if Signal matches Gold Signal AND Schema is valid.
    Score 0.0 otherwise.
    """
    try:
        # Check if prediction is a valid ShadowPlan object (from signature)
        # DSPy returned object should have .plan which is the Pydantic model
        if not hasattr(pred, "plan"):
            return 0.0
            
        pred_signal = pred.plan.signal
        gold_signal = gold.gold_signal
        
        # strict signal match
        if pred_signal == gold_signal:
            return 1.0
            
        return 0.0
        
    except Exception as e:
        return 0.0

def run_optimization():
    print("Initializing Optimization...")
    cfg = get_config()
    
    # Configure LM
    lm = dspy.LM(
         model=f"openai/{cfg.analyst_model}",
         api_key=cfg.openrouter_api_key,
         api_base=cfg.openrouter_base_url
    )
    dspy.settings.configure(lm=lm)
    
    # Load Data
    trainset = load_dataset()
    if len(trainset) < MIN_EXAMPLES:
        print(f"Not enough examples to run optimization (Found {len(trainset)}, Need {MIN_EXAMPLES}).")
        print("Please let the Shadow Agent run longer to collect data.")
        return

    # Define Program
    program = ShadowTrader()
    
    # MIPROv2 Optimizer
    print(f"Starting MIPROv2 with {len(trainset)} examples...")
    teleprompter = MIPROv2(
        metric=trading_metric,
        auto="light", # 'light' is faster, 'medium' or 'heavy' for better results
    )
    
    # Compile
    optimized_program = teleprompter.compile(
        program,
        trainset=trainset,
        requires_permission_to_run=False,
        max_bootstrapped_demos=3,
        max_labeled_demos=3,
    )
    
    # Save
    save_path = "agent/dspy/optimized_shadow_trader.json"
    optimized_program.save(save_path)
    print(f"Optimization Complete! Saved to {save_path}")

if __name__ == "__main__":
    run_optimization()
