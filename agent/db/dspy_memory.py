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
    reasoning: Optional[str] = None  # DSPy explanation for the decision
    
    # Execution
    entry_price: float
    size_usd: float
    leverage: int
    account_equity: Optional[float] = None  # Shadow equity at time of trade
    
    # Target (For Simulation)
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    
    # Outcome (Updated later)
    exit_price: Optional[float] = None
    pnl_usd: Optional[float] = None
    pnl_percent: Optional[float] = None
    fees_usd: Optional[float] = None  # Simulated trading fees
    slippage_usd: Optional[float] = None  # Simulated slippage
    max_drawdown: Optional[float] = None
    duration_minutes: Optional[float] = None
    
    # Data Context (For Optimization)
    market_context_hash: str # Hash of input data to avoid duplication
    full_prompt_trace: str = Field(..., description="JSON dump of DSPy trace")


class ShadowAccountState(SQLModel, table=True):
    """
    Persistent virtual account state for the Shadow Agent.
    Starts with real exchange equity, then diverges based on shadow trade performance.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    
    # Account State
    initial_equity: float  # Starting equity (from real exchange)
    current_equity: float  # Virtual equity after P&L
    
    # Cumulative Tracking
    total_pnl: float = 0.0
    total_fees: float = 0.0
    total_slippage: float = 0.0
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0


class ShadowStats(SQLModel):
    """Cumulative statistics for Shadow Mode performance (not a table)."""
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    cumulative_pnl: float = 0.0
    total_fees: float = 0.0
    win_rate: float = 0.0
    avg_pnl_per_trade: float = 0.0
    current_equity: float = 0.0
    initial_equity: float = 0.0
    equity_change_pct: float = 0.0


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

# Slippage simulation rate (0.01% = 1 basis point per side)
SLIPPAGE_RATE = 0.0001

class DSPyRepository:
    @staticmethod
    def save_trade(trade: ShadowTrade):
        with get_dspy_session() as session:
            session.add(trade)
            session.commit()
            session.refresh(trade)
            return trade

    @staticmethod
    def update_outcome(trade_id: int, exit_price: float, pnl: float, fees: float, slippage: float, duration: float):
        with get_dspy_session() as session:
            trade = session.get(ShadowTrade, trade_id)
            if trade:
                trade.exit_price = exit_price
                trade.pnl_usd = pnl
                trade.fees_usd = fees
                trade.slippage_usd = slippage
                trade.duration_minutes = duration
                session.add(trade)
                session.commit()

    @staticmethod
    def get_or_create_account(initial_equity: float) -> ShadowAccountState:
        """Get existing shadow account or create new one with initial equity."""
        with get_dspy_session() as session:
            account = session.exec(select(ShadowAccountState)).first()
            if account:
                return account
            
            # Create new account
            account = ShadowAccountState(
                initial_equity=initial_equity,
                current_equity=initial_equity
            )
            session.add(account)
            session.commit()
            session.refresh(account)
            print(f"[Shadow Mode] Initialized account with ${initial_equity:.2f}")
            return account

    @staticmethod
    def get_shadow_equity() -> float:
        """Get current shadow equity (0 if not initialized)."""
        with get_dspy_session() as session:
            account = session.exec(select(ShadowAccountState)).first()
            return account.current_equity if account else 0.0

    @staticmethod
    def update_account_after_trade(pnl: float, fees: float, slippage: float, is_winner: bool):
        """Update shadow account state after a trade closes."""
        with get_dspy_session() as session:
            account = session.exec(select(ShadowAccountState)).first()
            if not account:
                return
            
            net_pnl = pnl - fees - slippage
            account.current_equity += net_pnl
            account.total_pnl += pnl
            account.total_fees += fees
            account.total_slippage += slippage
            account.total_trades += 1
            if is_winner:
                account.winning_trades += 1
            else:
                account.losing_trades += 1
            account.updated_at = datetime.utcnow()
            
            session.add(account)
            session.commit()
            print(f"[Shadow Mode] Account updated: ${account.current_equity:.2f} (Net: ${net_pnl:+.2f})")

    @staticmethod
    def get_cumulative_stats() -> ShadowStats:
        """Calculate cumulative performance stats for Shadow Mode."""
        with get_dspy_session() as session:
            account = session.exec(select(ShadowAccountState)).first()
            
            if not account:
                return ShadowStats()
            
            win_rate = (account.winning_trades / account.total_trades * 100) if account.total_trades > 0 else 0.0
            avg_pnl = (account.total_pnl / account.total_trades) if account.total_trades > 0 else 0.0
            equity_change = ((account.current_equity / account.initial_equity) - 1) * 100 if account.initial_equity > 0 else 0.0
            
            return ShadowStats(
                total_trades=account.total_trades,
                winning_trades=account.winning_trades,
                losing_trades=account.losing_trades,
                cumulative_pnl=round(account.total_pnl - account.total_fees - account.total_slippage, 2),
                total_fees=round(account.total_fees + account.total_slippage, 2),
                win_rate=round(win_rate, 1),
                avg_pnl_per_trade=round(avg_pnl, 2),
                current_equity=round(account.current_equity, 2),
                initial_equity=round(account.initial_equity, 2),
                equity_change_pct=round(equity_change, 1)
            )

