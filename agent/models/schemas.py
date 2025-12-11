from typing import Optional, Literal, List
from pydantic import BaseModel, Field, field_validator

class TradeSignal(BaseModel):
    """
    Standardized trading signal structure used by both Legacy and DSPy agents.
    Ensures strict type compliance before passing to Risk Management.
    """
    coin: str = Field(..., description="The symbol/pair to trade (e.g. BTC, ETH)")
    signal: Literal["LONG", "SHORT", "HOLD", "CLOSE", "CUT_LOSS", "SCALE_OUT", "SCALE_IN"] = Field(..., description="Action to take")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Conviction score (0.0 to 1.0)")
    
    # Execution Parameters
    entry_price: Optional[float] = Field(None, description="Target entry price (if limit order) or current price")
    stop_loss: Optional[float] = Field(None, description="Hard invalidation price level")
    take_profit: Optional[float] = Field(None, description="Target exit price level")
    
    # Context
    reasoning: str = Field(..., min_length=10, description="Detailed Chain-of-Thought reasoning for the decision")
    timeframe: str = Field("1H", description="Primary timeframe analysis is based on")

    @field_validator('reasoning')
    def reasoning_must_be_detailed(cls, v):
        if len(v.split()) < 5:
            # Simple check for at least 5 words to avoid "Buy now" type low quality outputs
            raise ValueError("Reasoning must be descriptive")
        return v
    
    class Config:
        extra = 'ignore' 

class RiskDecision(BaseModel):
    """
    Standardized risk management decision.
    """
    approved: bool = Field(..., description="Whether the trade is approved for execution")
    action: Literal["OPEN_LONG", "OPEN_SHORT", "NO_TRADE", "CLOSE_LONG", "CLOSE_SHORT", "HOLD"] = Field(..., description="Final action to execute")
    size_usd: float = Field(..., ge=0.0, description="Position size in USD margin")
    leverage: int = Field(..., ge=1, le=50, description="Leverage multiplier")
    
    # Risk parameters
    stop_loss: Optional[float] = Field(None, description="Hard stop loss price")
    take_profit: Optional[float] = Field(None, description="Target price")
    invalidation_conditions: List[str] = Field(default_factory=list, description="List of conditions that invalidate the trade")
    
    reason: str = Field(..., description="Explanation for the risk decision")

    class Config:
        extra = 'ignore'
