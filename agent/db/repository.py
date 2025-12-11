"""
Database Repository

CRUD operations for trades, signals, exit plans, and logs.
"""

from datetime import datetime, timedelta
from typing import Optional
from sqlmodel import Session, select, func
from sqlalchemy import desc

from .models import Trade, Signal, ExitPlan, Approval, AgentLog, MarketMemory, InferenceLog
from .engine import get_session


class TradeRepository:
    """CRUD operations for trades."""
    
    @staticmethod
    def create(session: Session, trade: Trade) -> Trade:
        session.add(trade)
        session.commit()
        session.refresh(trade)
        return trade
    
    @staticmethod
    def get_by_id(session: Session, trade_id: int) -> Optional[Trade]:
        return session.get(Trade, trade_id)
    
    @staticmethod
    def get_open_trades(session: Session) -> list[Trade]:
        """Get all trades that haven't been closed."""
        statement = select(Trade).where(Trade.closed_at == None)
        return list(session.exec(statement).all())
    
    @staticmethod
    def get_recent(session: Session, limit: int = 50) -> list[Trade]:
        """Get recent trades ordered by open time."""
        statement = select(Trade).order_by(desc(Trade.opened_at)).limit(limit)
        return list(session.exec(statement).all())

    @staticmethod
    def get_performance_metrics(session: Session, coin: Optional[str] = None, hours: int = 24) -> dict:
        """Calculate performance metrics for the given timeframe."""
        start_time = datetime.utcnow() - timedelta(hours=hours)
        
        # Base query for closed trades in time window
        query = select(Trade).where(Trade.closed_at >= start_time).where(Trade.closed_at != None)
        
        if coin:
            query = query.where(Trade.coin == coin)
            
        trades = session.exec(query).all()
        
        if not trades:
            return {
                "win_rate": 0.0,
                "total_pnl_usd": 0.0,
                "total_trades": 0,
                "wins": 0,
                "losses": 0
            }
            
        total_pnl = sum(t.pnl_usd for t in trades if t.pnl_usd is not None)
        wins = sum(1 for t in trades if t.pnl_pct is not None and t.pnl_pct > 0)
        total_trades = len(trades)
        
        return {
            "win_rate": (wins / total_trades) * 100 if total_trades > 0 else 0.0,
            "total_pnl_usd": total_pnl,
            "total_trades": total_trades,
            "wins": wins,
            "losses": total_trades - wins
        }
    
    @staticmethod
    def get_closed_trades(
        session: Session,
        coin: Optional[str] = None,
        limit: int = 50
    ) -> list[Trade]:
        """Get closed trades, optionally filtered by coin."""
        statement = select(Trade).where(Trade.closed_at != None).order_by(desc(Trade.closed_at))
        if coin:
            statement = statement.where(Trade.coin == coin)
        statement = statement.limit(limit)
        return list(session.exec(statement).all())
    
    @staticmethod
    def close_trade(
        session: Session,
        trade_id: int,
        exit_price: float,
        close_reason: str
    ) -> Optional[Trade]:
        """Close a trade and calculate PnL."""
        trade = session.get(Trade, trade_id)
        if trade is None:
            return None
        
        trade.closed_at = datetime.utcnow()
        trade.exit_price = exit_price
        trade.close_reason = close_reason
        
        # Calculate PnL
        if trade.direction == "LONG":
            trade.pnl_pct = (exit_price - trade.entry_price) / trade.entry_price
        else:
            trade.pnl_pct = (trade.entry_price - exit_price) / trade.entry_price
        
        trade.pnl_usd = trade.size_usd * trade.pnl_pct * trade.leverage
        
        session.add(trade)
        session.commit()
        session.refresh(trade)
        return trade


class ExitPlanRepository:
    """CRUD operations for exit plans."""
    
    @staticmethod
    def create(session: Session, exit_plan: ExitPlan) -> ExitPlan:
        session.add(exit_plan)
        session.commit()
        session.refresh(exit_plan)
        return exit_plan
    
    @staticmethod
    def get_active_plans(session: Session) -> list[ExitPlan]:
        """Get all active exit plans."""
        statement = select(ExitPlan).where(ExitPlan.status == "ACTIVE")
        return list(session.exec(statement).all())
    
    @staticmethod
    def get_by_trade_id(session: Session, trade_id: int) -> Optional[ExitPlan]:
        statement = select(ExitPlan).where(ExitPlan.trade_id == trade_id)
        return session.exec(statement).first()
    
    @staticmethod
    def invalidate(
        session: Session,
        exit_plan_id: int,
        reason: str
    ) -> Optional[ExitPlan]:
        """Mark exit plan as invalidated."""
        plan = session.get(ExitPlan, exit_plan_id)
        if plan is None:
            return None
        
        plan.status = "INVALIDATED"
        plan.triggered_at = datetime.utcnow()
        plan.triggered_reason = reason
        
        session.add(plan)
        session.commit()
        session.refresh(plan)
        return plan
    
    @staticmethod
    def format_for_context(plans: list[ExitPlan]) -> str:
        """Format exit plans for injection into system prompt."""
        if not plans:
            return "No active exit plans."
        
        lines = ["## Active Exit Plans\n"]
        for plan in plans:
            # Get associated trade
            trade = plan.trade
            if not trade:
                continue
            
            lines.append(f"### {trade.coin} {trade.direction} @ ${trade.entry_price:,.2f}")
            lines.append(f"- TP: ${plan.take_profit_price:,.2f} (+{plan.take_profit_pct*100:.1f}%)")
            lines.append(f"- SL: ${plan.stop_loss_price:,.2f} (-{plan.stop_loss_pct*100:.1f}%)")
            lines.append("- Invalidation Conditions:")
            for i, cond in enumerate(plan.invalidation_conditions, 1):
                lines.append(f"  {i}. ❌ {cond}")
            lines.append("")
        
        return "\n".join(lines)


class AgentLogRepository:
    """CRUD operations for agent logs."""
    
    @staticmethod
    def log(
        session: Session,
        action_type: str,
        output: str,
        node_name: Optional[str] = None,
        tool_name: Optional[str] = None,
        input_args: Optional[str] = None,
        reasoning: Optional[str] = None,
        tokens_used: Optional[int] = None,
        latency_ms: Optional[int] = None,
        error: Optional[str] = None
    ) -> AgentLog:
        """Create a log entry."""
        log = AgentLog(
            action_type=action_type,
            node_name=node_name,
            tool_name=tool_name,
            input_args=input_args[:1000] if input_args else None,  # Truncate
            output=output[:5000],  # Truncate
            reasoning=reasoning,  # Full reasoning - not truncated
            tokens_used=tokens_used,
            latency_ms=latency_ms,
            error=error
        )
        session.add(log)
        session.commit()
        session.refresh(log)
        return log
    
    @staticmethod
    def get_recent(
        session: Session,
        limit: int = 100,
        action_type: Optional[str] = None
    ) -> list[AgentLog]:
        """Get recent logs."""
        statement = select(AgentLog).order_by(desc(AgentLog.timestamp))
        if action_type:
            statement = statement.where(AgentLog.action_type == action_type)
        statement = statement.limit(limit)
        return list(session.exec(statement).all())


class InferenceLogRepository:
    """CRUD operations for inference logs."""
    
    @staticmethod
    def create(
        session: Session,
        cycle_number: Optional[int] = None,
        analyst_model: Optional[str] = None,
        risk_model: Optional[str] = None,
        analyst_signal: Optional[str] = None,
        analyst_reasoning: Optional[str] = None,
        analyst_tool_calls: Optional[str] = None,
        risk_decision: Optional[str] = None,
        risk_reasoning: Optional[str] = None,
        risk_tool_calls: Optional[str] = None,
        final_action: Optional[str] = None,
        final_reasoning: Optional[str] = None,
        account_equity: Optional[float] = None,
        account_margin_pct: Optional[float] = None,
        active_positions: Optional[int] = None,
        trade_id: Optional[int] = None
    ) -> InferenceLog:
        """Create an inference log entry."""
        
        log = InferenceLog(
            cycle_number=cycle_number,
            analyst_model=analyst_model,
            risk_model=risk_model,
            analyst_signal=analyst_signal,
            analyst_reasoning=analyst_reasoning,
            analyst_tool_calls=analyst_tool_calls,
            risk_decision=risk_decision,
            risk_reasoning=risk_reasoning,
            risk_tool_calls=risk_tool_calls,
            final_action=final_action,
            final_reasoning=final_reasoning,
            account_equity=account_equity,
            account_margin_pct=account_margin_pct,
            active_positions=active_positions,
            trade_id=trade_id
        )
        session.add(log)
        session.commit()
        session.refresh(log)
        return log
    
    @staticmethod
    def get_recent(session: Session, limit: int = 50):
        """Get recent inference logs."""
        statement = select(InferenceLog).order_by(desc(InferenceLog.timestamp)).limit(limit)
        return list(session.exec(statement).all())


class ApprovalRepository:
    """CRUD operations for approval requests."""
    
    @staticmethod
    def create(session: Session, approval: Approval) -> Approval:
        session.add(approval)
        session.commit()
        session.refresh(approval)
        return approval
    
    @staticmethod
    def get_pending(session: Session) -> list[Approval]:
        """Get all pending approval requests."""
        statement = select(Approval).where(Approval.status == "PENDING")
        return list(session.exec(statement).all())
    
    @staticmethod
    def respond(
        session: Session,
        approval_id: int,
        status: str,
        responder: str
    ) -> Optional[Approval]:
        """Record approval response."""
        approval = session.get(Approval, approval_id)
        if approval is None:
            return None
        
        approval.status = status
        approval.responded_at = datetime.utcnow()
        approval.responder = responder
        
        session.add(approval)
        session.commit()
        session.refresh(approval)
        return approval


class MarketMemoryRepository:
    """CRUD operations for market memory."""
    
    @staticmethod
    def create(session: Session, memory: MarketMemory) -> MarketMemory:
        session.add(memory)
        session.commit()
        session.refresh(memory)
        return memory
    
    @staticmethod
    def get_today(session: Session, coin: str, date: str) -> Optional[MarketMemory]:
        """Get memory for a specific coin and date."""
        statement = select(MarketMemory).where(MarketMemory.coin == coin).where(MarketMemory.date == date)
        return session.exec(statement).first()
