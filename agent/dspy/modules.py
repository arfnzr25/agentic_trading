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
        
    def forward(self, market_structure: str, risk_environment: str, social_sentiment: float, 
                whale_activity: str, macro_context: str, account_context: str, last_trade_outcome: str):
        # 1. Generate Prediction
        pred = self.analyze(
            market_structure=market_structure,
            risk_environment=risk_environment,
            social_sentiment=social_sentiment,
            whale_activity=whale_activity,
            macro_context=macro_context,
            account_context=account_context,
            last_trade_outcome=last_trade_outcome
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
        
        # Rule 1: Confidence Validation (Loosened for Shadow Mode)
        # If confidence is > 50%, we should have a plan.
        if pred.plan.confidence > 0.5:
            Suggest(
                pred.plan.entry_price is not None and pred.plan.entry_price > 0,
                "Confidence > 50% implies a setup found. Define Entry Price."
            )
            Suggest(
                pred.plan.stop_loss is not None,
                "Trades must have a Stop Loss."
            )
            
        # Rule 2: Bear Trend Safety (Moderated)
        # Allow counter-trend if conviction exists (>65%)
        if "BEAR" in risk_environment.upper() or "DOWN" in risk_environment.upper():
             Suggest(
                not (pred.plan.signal == "LONG" and pred.plan.confidence < 0.65),
                "Counter-trend Longs require higher conviction (>65%)."
             )
             
        # Rule 3: Volatility Filter (Removed strict HOLD enforcement)
        # Instead of forcing HOLD, suggest wider stops
        if social_sentiment < 20 and "HIGH_VOLATILITY" in risk_environment:
            Suggest(
                pred.plan.stop_loss is not None,
                "High volatility requires defined risk parameters (Stop Loss)."
            )
            
        return pred
