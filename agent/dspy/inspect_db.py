from agent.db.dspy_memory import get_dspy_session, ShadowTrade, OptimizationExample
from sqlmodel import select

def inspect_db():
    try:
        with get_dspy_session() as session:
            trades = session.exec(select(ShadowTrade)).all()
            opt_examples = session.exec(select(OptimizationExample)).all()
            
            print(f"ShadowTrade Count: {len(trades)}")
            print(f"OptimizationExample Count: {len(opt_examples)}")
            
            if len(trades) > 0:
                print("\nSample Trade:")
                t = trades[0]
                print(f"  ID: {t.id}")
                print(f"  Coin: {t.coin}")
                print(f"  Signal: {t.signal}")
                print(f"  PnL: {t.pnl_usd}")
                print(f"  Trace exists: {bool(t.full_prompt_trace)}")
                if t.full_prompt_trace:
                    import json
                    try:
                        trace = json.loads(t.full_prompt_trace)
                        print(f"  Trace Keys: {list(trace.keys())}")
                        # Print first level if it's a dict
                        if isinstance(trace, dict):
                             for k, v in trace.items():
                                 if isinstance(v, dict):
                                     print(f"    {k}: {list(v.keys())}")
                    except:
                        print("  Trace is not valid JSON")
                
            if len(opt_examples) > 0:
                print("\nSample Optimization Example:")
                o = opt_examples[0]
                print(f"  Score: {o.score}")
                
    except Exception as e:
        print(f"Error inspecting DB: {e}")

if __name__ == "__main__":
    inspect_db()
