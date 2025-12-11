from datetime import datetime
from typing import Optional, List
from sqlmodel import SQLModel, Field, create_engine, Session, select
import json
import os

# --- MODELS ---

class ShadowTrade(SQLModel, table=True):
    """
    Records paper trades executed by the DSPy Shadow Agent.
    Used for PnL tracking and Optimization feedback.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    
    coin: str
    signal: str  # LONG, SHORT, HOLD
    confidence: float
    
    # Execution
    entry_price: float
    size_usd: float
    leverage: int
    
    # Target (For Simulation)
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    
    # Outcome (Updated later)
    exit_price: Optional[float] = None
    pnl_usd: Optional[float] = None
    pnl_percent: Optional[float] = None
    max_drawdown: Optional[float] = None
    duration_minutes: Optional[float] = None
    
    # Data Context (For Optimization)
    market_context_hash: str # Hash of input data to avoid duplication
    full_prompt_trace: str = Field(..., description="JSON dump of DSPy trace")

class OptimizationExample(SQLModel, table=True):
    """
    High-quality examples filtered from ShadowTrades for MIPROv2 training.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    
    # DSPy Example Format
    input_market_structure: str
    input_risk_env: str
    
    # The 'Label' (Successful Plan)
    gold_plan_json: str
    
    score: float # PnL Score

# --- DATABASE ENGINE ---

# Distinct database file using env var for Docker persistence
default_db_file = "dspy_memory.db"
unique_db_url = os.getenv("DSPY_DATABASE_URL", f"sqlite:///{default_db_file}")

engine = create_engine(unique_db_url)

def init_dspy_db():
    SQLModel.metadata.create_all(engine)

def get_dspy_session():
    return Session(engine)

# --- REPOSITORY ---

class DSPyRepository:
    @staticmethod
    def save_trade(trade: ShadowTrade):
        with get_dspy_session() as session:
            session.add(trade)
            session.commit()
            session.refresh(trade)
            return trade

    @staticmethod
    def update_outcome(trade_id: int, exit_price: float, pnl: float, duration: float):
        with get_dspy_session() as session:
            trade = session.get(ShadowTrade, trade_id)
            if trade:
                trade.exit_price = exit_price
                trade.pnl_usd = pnl
                trade.duration_minutes = duration
                session.add(trade)
                session.commit()
