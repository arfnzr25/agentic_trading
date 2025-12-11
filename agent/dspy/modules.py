import dspy
from .signatures import StrategicAnalysis
from ..models.schemas import TradeSignal

# Handle dspy.Suggest import (varies by version)
try:
    from dspy.primitives.assertions import Suggest
except ImportError:
    try:
        from dspy import Suggest
    except ImportError:
        def Suggest(*args, **kwargs): pass # No-op if missing


class ShadowTrader(dspy.Module):
    def __init__(self):
        super().__init__()
        # Use Predict since TradeSignal already includes a 'reasoning' field
        self.analyze = dspy.Predict(StrategicAnalysis)
        
    def forward(self, market_structure: str, risk_environment: str, social_sentiment: float, whale_activity: str, macro_context: str):
        # 1. Generate Prediction
        pred = self.analyze(
            market_structure=market_structure,
            risk_environment=risk_environment,
            social_sentiment=social_sentiment,
            whale_activity=whale_activity,
            macro_context=macro_context
        )
        
        # Ensure strict Pydantic type (Defensive coding for LLM variance)
        if hasattr(pred, 'plan') and not isinstance(pred.plan, TradeSignal):
            try:
                import json
                import ast
                
                val = pred.plan
                dict_val = {}
                
                if isinstance(val, dict):
                    dict_val = val
                elif isinstance(val, str):
                    try:
                        dict_val = json.loads(val)
                    except:
                        try:
                            dict_val = ast.literal_eval(val)
                        except:
                            print(f"[Shadow Mode] Failed to parse: {val}")
                            
                if dict_val:
                    pred.plan = TradeSignal(**dict_val)
                    
            except Exception as e:
                print(f"[Shadow Mode] Schema Conversion Error: {e}")
                
        # --- USER CUSTOMIZATION: LOGIC ASSERTIONS ---

        # These rules guide the agent to self-correct if it violates them.
        
        # Rule 1: High Confidence Validation
        # If confidence is high (>60%), we MUST have specific entry/exit plans
        if pred.plan.confidence > 0.6:
            Suggest(
                pred.plan.entry_price is not None and pred.plan.entry_price > 0,
                "High confidence requires a specific numeric Entry Price."
            )
            Suggest(
                pred.plan.stop_loss is not None and pred.plan.stop_loss > 0,
                "High confidence trades MUST have a defined Stop Loss."
            )
            
        # Rule 2: Bear Trend Safety
        # If the risk environment indicates a Down Trend, discourage weak Longs
        if "BEAR" in risk_environment.upper() or "DOWN" in risk_environment.upper():
             Suggest(
                not (pred.plan.signal == "LONG" and pred.plan.confidence < 0.8),
                "In Bear Trends, avoid Longs unless conviction is extremely high (>80%). Consider HOLD or SHORT."
             )
             
        # Rule 3: Volatility Filter
        # If sentiment is extreme fear (<20) andvolatility is high, prefer HOLD
        if social_sentiment < 20 and "HIGH_VOLATILITY" in risk_environment:
            Suggest(
                pred.plan.signal == "HOLD",
                "Extreme Fear + High Volatility = unpredictable. Prefer HOLD until structure stabilizes."
            )
            
        return pred
