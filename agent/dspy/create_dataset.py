import json
from sqlmodel import select
from agent.db.dspy_memory import get_dspy_session, ShadowTrade, OptimizationExample

def create_dataset():
    """
    Convert profitable ShadowTrades into OptimizationExamples for MIPROv2.
    """
    with get_dspy_session() as session:
        # Fetch all closed trades with PnL
        trades = session.exec(select(ShadowTrade).where(ShadowTrade.pnl_usd != None)).all()
        print(f"Found {len(trades)} closed trades.")
        
        examples = []
        skipped = 0
        
        for t in trades:
            # simple filter: profitable trades only
            if (t.pnl_usd or 0) > 0:
                try:
                    trace = json.loads(t.full_prompt_trace)
                    
                    # Extract input/output from the stored trace
                    # Assuming trace structure matches DSPy's dump
                    # We need to map this to the Signature fields
                    
                    if isinstance(trace, dict) and "inputs" in trace:
                        # NEW FORMAT
                        input_market = trace["inputs"].get("market_structure")
                        input_risk = trace["inputs"].get("risk_environment")
                        output_plan = trace.get("output")
                    else:
                        # OLD FORMAT (Try legacy fallback or skip)
                        input_market = trace.get("market_structure") or trace.get("kwargs", {}).get("market_structure")
                        input_risk = trace.get("risk_env") or trace.get("kwargs", {}).get("risk_env")
                        output_plan = trace.get("decision_json") or trace.get("response", {}).get("decision_json")
                        # If still failing, check if it's just the output directly
                        if not output_plan and "signal" in trace:
                             output_plan = trace # The whole trace was the output plan
                    
                    if input_market and output_plan:
                         ex = OptimizationExample(
                             input_market_structure=input_market,
                             input_risk_env=input_risk or "N/A",
                             gold_plan_json=output_plan,
                             score=t.pnl_usd
                         )
                         examples.append(ex)
                    else:
                        skipped += 1
                        
                except Exception as e:
                    print(f"Error parsing trade {t.id}: {e}")
                    skipped += 1
        
        print(f"Created {len(examples)} examples. Skipped {skipped}.")
        
        # Clear old examples to avoid duplicates?
        # session.exec(delete(OptimizationExample)) # Safer to append? No, let's clear for fresh start
        # For now, just add them
        
        for ex in examples:
            session.add(ex)
        
        session.commit()
        print("Dataset saved to DB.")

if __name__ == "__main__":
    create_dataset()
