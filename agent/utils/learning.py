"""
Learning Module

Learns from actual trade history fetched via MCP.
No seed patterns - all learning is from real account data.
"""

import json
from datetime import datetime
from typing import Optional


async def fetch_trade_history(tools: list) -> list:
    """
    Fetch trade history from the account via MCP.
    Returns list of fills (executed trades).
    """
    tool_map = {t.name: t for t in tools}
    fills_tool = tool_map.get("get_user_fills")
    
    if not fills_tool:
        print("[Learning] get_user_fills tool not found")
        return []
    
    try:
        result = await fills_tool.ainvoke({})
        if isinstance(result, str):
            result = json.loads(result)
        return result if isinstance(result, list) else []
    except Exception as e:
        print(f"[Learning] Error fetching fills: {e}")
        return []


def analyze_trade_performance(fills: list) -> dict:
    """
    Analyze trade history to extract performance insights.
    Groups trades and calculates win rates, avg PnL, etc.
    """
    if not fills:
        return {"total_trades": 0, "insights": []}
    
    # Group fills by closed PnL calculation
    # Each fill has: coin, side, px, sz, time, closedPnl, etc.
    trades = []
    for fill in fills:
        try:
            # Handle string fills (JSON from MCP)
            if isinstance(fill, str):
                fill = json.loads(fill)
            
            if not isinstance(fill, dict):
                continue
                
            closed_pnl = float(fill.get("closedPnl", 0))
            if closed_pnl != 0:  # Only count trades that closed
                trades.append({
                    "coin": fill.get("coin", "UNKNOWN"),
                    "side": fill.get("side", "UNKNOWN"),
                    "px": float(fill.get("px", 0)),
                    "sz": float(fill.get("sz", 0)),
                    "pnl": closed_pnl,
                    "time": fill.get("time", 0)
                })
        except (ValueError, TypeError):
            continue
    
    if not trades:
        return {"total_trades": 0, "insights": []}
    
    # Calculate stats
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] < 0]
    
    total_pnl = sum(t["pnl"] for t in trades)
    win_rate = (len(wins) / len(trades)) * 100 if trades else 0
    avg_win = sum(t["pnl"] for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t["pnl"] for t in losses) / len(losses) if losses else 0
    
    # Analyze by side (LONG vs SHORT performance)
    longs = [t for t in trades if t["side"] == "B"]
    shorts = [t for t in trades if t["side"] == "A"]
    
    long_wins = len([t for t in longs if t["pnl"] > 0])
    short_wins = len([t for t in shorts if t["pnl"] > 0])
    
    long_wr = (long_wins / len(longs) * 100) if longs else 0
    short_wr = (short_wins / len(shorts) * 100) if shorts else 0
    
    # Generate insights
    insights = []
    
    if len(longs) >= 3 and len(shorts) >= 3:
        if long_wr > short_wr + 20:
            insights.append(f"LONG trades outperform (WR: {long_wr:.0f}% vs SHORT: {short_wr:.0f}%). FAVOR LONGS.")
        elif short_wr > long_wr + 20:
            insights.append(f"SHORT trades outperform (WR: {short_wr:.0f}% vs LONG: {long_wr:.0f}%). FAVOR SHORTS.")
    
    if avg_win > 0 and avg_loss != 0:
        rr_ratio = abs(avg_win / avg_loss)
        if rr_ratio < 1:
            insights.append(f"Risk/Reward poor ({rr_ratio:.1f}:1). Widen TPs or tighten SLs.")
        elif rr_ratio > 2:
            insights.append(f"Good R:R ({rr_ratio:.1f}:1). Keep letting winners run.")
    
    if win_rate < 40:
        insights.append(f"Win rate low ({win_rate:.0f}%). Be more selective with entries.")
    elif win_rate > 60:
        insights.append(f"Win rate strong ({win_rate:.0f}%). Current strategy working.")
    
    return {
        "total_trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": win_rate,
        "total_pnl": total_pnl,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "long_wr": long_wr,
        "short_wr": short_wr,
        "insights": insights
    }


def format_learning_insights(analysis: dict) -> str:
    """
    Format learning analysis for LLM prompt injection.
    """
    if analysis.get("total_trades", 0) == 0:
        return "## Learning Insights\nNo closed trades yet. Building performance history..."
    
    lines = [
        "## Learning Insights (From Your Trade History)",
        f"**Performance**: {analysis['wins']}W / {analysis['losses']}L ({analysis['win_rate']:.0f}% WR)",
        f"**Total PnL**: ${analysis['total_pnl']:.2f}",
        f"**Avg Win**: ${analysis['avg_win']:.2f} | **Avg Loss**: ${analysis['avg_loss']:.2f}",
        f"**By Side**: LONG {analysis['long_wr']:.0f}% WR | SHORT {analysis['short_wr']:.0f}% WR",
        ""
    ]
    
    if analysis.get("insights"):
        lines.append("**Recommendations:**")
        for insight in analysis["insights"]:
            lines.append(f"- {insight}")
    
    return "\n".join(lines)


async def get_learning_context(tools: list) -> str:
    """
    Main entry point: Fetch trade history and generate learning context.
    Call this in analyst_v2 to inject learning into prompt.
    """
    fills = await fetch_trade_history(tools)
    analysis = analyze_trade_performance(fills)
    return format_learning_insights(analysis)


# Legacy compatibility - no-op for startup
def init_learning():
    """Initialize learning system - no longer seeds patterns."""
    print("[Learning] System initialized (learning from real trades)")
