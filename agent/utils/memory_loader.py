"""
Memory Loader Module

Unified pre-loading of all memory context from SQLite.
Runs at the start of each inference cycle to minimize DB calls.
"""

from datetime import datetime, timedelta
from typing import Optional
from .db import get_session
from .db.repository import (
    TradeRepository,
    InferenceLogRepository,
    MarketMemoryRepository
)
from .db.models import Trade
from sqlmodel import select


def preload_memory(coin: str) -> dict:
    """
    Load all memory context in a single DB session.
    
    Returns:
        dict with keys: daily_bias, performance, last_thought, active_trades, learning
    """
    today_str = datetime.utcnow().strftime("%Y-%m-%d")
    
    with get_session() as session:
        # 1. Daily Market Bias (cached)
        daily_bias = MarketMemoryRepository.get_today(session, coin, today_str)
        
        # 2. Performance Metrics (24h)
        perf_metrics = TradeRepository.get_performance_metrics(session, coin, hours=24)
        
        # 3. Last Cycle Reasoning (Thought Continuity)
        last_logs = InferenceLogRepository.get_recent(session, limit=1)
        last_thought = last_logs[0] if last_logs else None
        
        # 4. Active Trades (Position Memory)
        stmt = select(Trade).where(Trade.coin == coin).where(Trade.closed_at == None)
        active_trades = list(session.exec(stmt).all())
        
        # 5. Learning Insights (Pattern Analysis)
        learning = _analyze_patterns(session, coin)
    
    return {
        "daily_bias": daily_bias,
        "performance": perf_metrics,
        "last_thought": last_thought,
        "active_trades": active_trades,
        "learning": learning
    }


def _analyze_patterns(session, coin: str, limit: int = 30) -> dict:
    """
    Analyze closed trades to extract learning patterns.
    """
    # Get recent closed trades
    trades = TradeRepository.get_closed_trades(session, coin, limit=limit)
    
    if not trades:
        return {
            "sample_size": 0,
            "overall_win_rate": 0.0,
            "best_direction": "UNKNOWN",
            "recommendation": "Insufficient data for learning."
        }
    
    # Calculate metrics
    wins = [t for t in trades if (t.pnl_usd or 0) > 0]
    longs = [t for t in trades if t.direction == "LONG"]
    shorts = [t for t in trades if t.direction == "SHORT"]
    
    long_wins = len([t for t in longs if (t.pnl_usd or 0) > 0])
    short_wins = len([t for t in shorts if (t.pnl_usd or 0) > 0])
    
    long_wr = (long_wins / len(longs) * 100) if longs else 0
    short_wr = (short_wins / len(shorts) * 100) if shorts else 0
    
    best_direction = "LONG" if long_wr > short_wr else "SHORT"
    
    # Generate recommendation
    if len(trades) < 5:
        recommendation = "Insufficient trades for reliable patterns."
    elif long_wr > 60 and len(longs) >= 5:
        recommendation = f"LONG positions performing well ({long_wr:.0f}% WR). Favor bullish setups."
    elif short_wr > 60 and len(shorts) >= 5:
        recommendation = f"SHORT positions performing well ({short_wr:.0f}% WR). Favor bearish setups."
    else:
        recommendation = "No strong directional edge detected. Prioritize high-confluence setups."
    
    return {
        "sample_size": len(trades),
        "overall_win_rate": len(wins) / len(trades) * 100,
        "long_win_rate": long_wr,
        "short_win_rate": short_wr,
        "best_direction": best_direction,
        "recommendation": recommendation
    }


def format_memory_context(memory: dict) -> str:
    """
    Format memory into a string for LLM injection.
    """
    parts = []
    
    # Daily Bias
    if memory["daily_bias"]:
        parts.append(f"""## 🧠 DAILY BIAS (Cached)
- Bias: {memory['daily_bias'].market_bias}
- Volatility: {memory['daily_bias'].volatility_score}/100
""")
    
    # Performance
    perf = memory["performance"]
    parts.append(f"""## 📊 TODAY'S PERFORMANCE
- Win Rate: {perf['win_rate']:.1f}%
- PnL: ${perf['total_pnl_usd']:.2f}
- Trades: {perf['total_trades']}
""")
    
    # Last Thought
    if memory["last_thought"]:
        thought = memory["last_thought"].analyst_reasoning or ""
        if len(thought) > 20:
            parts.append(f"""## 💭 LAST CYCLE THOUGHT
"{thought[:250]}..."
""")
    
    # Active Trades
    if memory["active_trades"]:
        trades_str = "\n".join([
            f"- {t.direction} {t.coin} @ {t.entry_price}: {t.reasoning[:80]}..."
            for t in memory["active_trades"]
        ])
        parts.append(f"""## 📈 ACTIVE POSITIONS (Your Thesis)
{trades_str}
""")
    
    # Learning
    learning = memory["learning"]
    if learning["sample_size"] >= 3:
        parts.append(f"""## 🎓 LEARNED PATTERNS ({learning['sample_size']} trades)
- Long WR: {learning['long_win_rate']:.0f}%
- Short WR: {learning['short_win_rate']:.0f}%
- Recommendation: {learning['recommendation']}
""")
    
    return "\n".join(parts)
