"""Database layer package."""

from agent.db.models import Trade, Signal, ExitPlan, Approval, AgentLog, InferenceLog, ALL_MODELS
from agent.db.engine import create_tables, get_session, get_async_session
from agent.db.repository import (
    TradeRepository,
    ExitPlanRepository,
    AgentLogRepository,
    InferenceLogRepository,
    InferenceLogRepository,
    ApprovalRepository,
    MarketMemoryRepository
)
from agent.db.dspy_memory import (
    init_dspy_db,
    get_dspy_session,
    ShadowTrade,
    ShadowAccountState,
    ShadowStats,
    OptimizationExample,
    DSPyRepository
)

__all__ = [
    # Models
    "Trade",
    "Signal", 
    "ExitPlan",
    "Approval",
    "AgentLog",
    "InferenceLog",
    "ALL_MODELS",
    # Engine
    "create_tables",
    "get_session",
    "get_async_session",
    # Repositories
    "TradeRepository",
    "ExitPlanRepository",
    "AgentLogRepository",
    "InferenceLogRepository",
    "InferenceLogRepository",
    "ApprovalRepository",
    "MarketMemoryRepository",
    # DSPy Memory
    "init_dspy_db",
    "get_dspy_session",
    "ShadowTrade",
    "ShadowAccountState",
    "ShadowStats",
    "OptimizationExample",
    "DSPyRepository"
]

