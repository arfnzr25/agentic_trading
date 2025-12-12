"""
AI Trading Agent Dashboard

Run with: streamlit run ui/dashboard.py
"""

import streamlit as st
import pandas as pd
from datetime import datetime
import json
import sys
import os
from zoneinfo import ZoneInfo

# Timezone helper
def to_local(dt: datetime) -> datetime:
    """Convert UTC to User Local Time (Asia/Bangkok)."""
    if not dt: return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo("UTC"))
    return dt.astimezone(ZoneInfo("Asia/Bangkok"))

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.db import get_session, InferenceLog, Trade, AgentLog
from agent.config import get_config
from sqlmodel import select

# Page config
st.set_page_config(
    page_title="AI Trading Agent",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS
st.markdown("""
<style>
    .stMetric {
        background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
        padding: 1rem;
        border-radius: 10px;
        border: 1px solid #2a2a4e;
    }
    .signal-long { color: #00ff88; font-weight: bold; }
    .signal-short { color: #ff4444; font-weight: bold; }
    .signal-hold { color: #ffaa00; font-weight: bold; }
    .signal-close { color: #888888; font-weight: bold; }
    .signal-cut_loss { color: #ff0000; font-weight: bold; }
    .signal-scale_in { color: #00ff88; font-weight: bold; }
    .signal-scale_out { color: #ff8800; font-weight: bold; }
    .decision-approve { color: #00ff88; }
    .decision-reject { color: #ff4444; }
</style>
""", unsafe_allow_html=True)


def get_recent_inferences(limit: int = 10):
    """Fetch recent inference logs from database."""
    with get_session() as session:
        stmt = select(InferenceLog).order_by(InferenceLog.timestamp.desc()).limit(limit)
        return session.exec(stmt).all()


def get_recent_trades(limit: int = 20):
    """Fetch recent trades from database."""
    with get_session() as session:
        stmt = select(Trade).order_by(Trade.opened_at.desc()).limit(limit)
        return session.exec(stmt).all()


def get_open_trades():
    """Fetch open trades (trades with no closed_at)."""
    with get_session() as session:
        stmt = select(Trade).where(Trade.closed_at == None).order_by(Trade.opened_at.desc())
        return session.exec(stmt).all()


def get_agent_logs(limit: int = 50):
    """Fetch recent agent logs."""
    with get_session() as session:
        stmt = select(AgentLog).order_by(AgentLog.timestamp.desc()).limit(limit)
        return session.exec(stmt).all()


def parse_json_safe(json_str: str | None) -> dict:
    """Safely parse JSON string."""
    if not json_str:
        return {}
    try:
        return json.loads(json_str)
    except:
        return {"raw": json_str}


def main():
    st.title("🤖 AI Trading Agent Dashboard")
    
    # Sidebar
    with st.sidebar:
        st.header("⚙️ Configuration")
        try:
            cfg = get_config()
            st.info(f"**Analyst:** {cfg.analyst_model}")
            st.info(f"**Risk:** {cfg.risk_model}")
            st.success("✅ Connected" if cfg.openrouter_api_key else "❌ No API Key")
        except Exception as e:
            st.error(f"Config error: {e}")
        
        st.divider()
        auto_refresh = st.checkbox("Auto-refresh (10s)", value=False)
        if auto_refresh:
            st.rerun()
        
        if st.button("🔄 Refresh Now"):
            st.rerun()

        st.divider()
        st.subheader("💾 Data Export")

        col_d1, col_d2 = st.columns(2)

        # 1. Shadow DB
        shadow_db_path = "/data/dspy_memory.db"
        if os.path.exists(shadow_db_path):
             with open(shadow_db_path, "rb") as f:
                 st.download_button(
                     label="📥 Shadow DB",
                     data=f.read(),
                     file_name="dspy_memory.db",
                     mime="application/x-sqlite3",
                     key="dl_shadow"
                 )
        
        # 2. Main Agent DB
        agent_db_path = "/data/agent.db"
        if os.path.exists(agent_db_path):
             with open(agent_db_path, "rb") as f:
                 st.download_button(
                     label="📥 Main DB",
                     data=f.read(),
                     file_name="agent.db",
                     mime="application/x-sqlite3",
                     key="dl_main"
                 )
    
    # Main content
    col1, col2, col3, col4 = st.columns(4)
    
    # Get stats
    inferences = get_recent_inferences(1)
    trades = get_recent_trades(100)
    open_trades = get_open_trades()
    
    last_inference = inferences[0] if inferences else None
    total_pnl = sum(t.pnl_usd or 0 for t in trades if t.pnl_usd is not None)
    
    with col1:
        st.metric(
            "Account Equity",
            f"${last_inference.account_equity:,.2f}" if last_inference and last_inference.account_equity else "N/A",
            delta=None
        )
    
    with col2:
        st.metric(
            "Open Positions", 
            len(open_trades),
            help="Count of trades currently open in DB"
        )
    
    with col3:
        st.metric(
            "Margin Usage",
            f"{last_inference.account_margin_pct:.1f}%" if last_inference and last_inference.account_margin_pct else "0%",
            delta_color="off"
        )
    
    with col4:
        st.metric(
            "Realized PnL",
            f"${total_pnl:.2f}",
            delta=f"{total_pnl:.2f}" if total_pnl != 0 else None,
            delta_color="normal" if total_pnl >= 0 else "inverse"
        )
    
    st.divider()
    
    # Tabs
    tab1, tab2, tab3 = st.tabs(["📊 Inferences", "💰 Trades", "📝 Logs"])
    
    with tab1:
        st.subheader("Recent Inference Decisions")
        
        inferences = get_recent_inferences(10)
        
        if not inferences:
            st.info("No inferences yet. Run the agent to see results here.")
        else:
            for index, inf in enumerate(inferences):
                analyst_signal = parse_json_safe(inf.analyst_signal)
                risk_decision = parse_json_safe(inf.risk_decision)
                final_action = inf.final_action or "N/A"
                
                signal = analyst_signal.get("signal", "N/A")
                signal_class = f"signal-{signal.lower()}" if signal in ["LONG", "SHORT", "HOLD"] else ""
                
                local_ts = to_local(inf.timestamp)
                ts_str = local_ts.strftime('%H:%M:%S')
                
                with st.expander(
                    f"🕐 {ts_str} | {signal} ({analyst_signal.get('coin', '')}) ➡️ {final_action}",
                    expanded=(index == 0) # Expand first item
                ):
                    # Account state at time of inference
                    if inf.account_equity or inf.account_margin_pct:
                        st.caption(f"💰 Equity: ${inf.account_equity:.2f} | Margin: {inf.account_margin_pct:.1f}%")
                    
                    col_a, col_b = st.columns(2)
                    
                    with col_a:
                        st.markdown("### 🧠 Analyst")
                        st.caption(f"Model: {inf.analyst_model}")
                        
                        # Signal badge with color
                        signal_color = {
                            "LONG": "🟢", "SHORT": "🔴", "HOLD": "🟡", 
                            "CLOSE": "⚫", "CUT_LOSS": "🔴", 
                            "SCALE_IN": "🟢", "SCALE_OUT": "🟠"
                        }.get(signal, "⚪")
                        conf = analyst_signal.get("confidence", 0)
                        conf_pct = conf * 100 if conf <= 1 else conf
                        st.markdown(f"**{signal_color} {signal}** ({conf_pct:.0f}% confidence)")
                        
                        # Entry/SL/TP if available
                        if analyst_signal.get("entry_price"):
                            st.markdown(f"**Entry:** ${analyst_signal.get('entry_price', 'N/A')}")
                        if analyst_signal.get("stop_loss"):
                            st.markdown(f"**SL:** ${analyst_signal.get('stop_loss', 'N/A')}")
                        if analyst_signal.get("take_profit"):
                            st.markdown(f"**TP:** ${analyst_signal.get('take_profit', 'N/A')}")
                        
                        st.code(json.dumps(analyst_signal, indent=2), language="json")
                         
                        st.markdown("**Reasoning:**")
                        # Escape $ to prevent LaTeX rendering
                        analyst_reason = (inf.analyst_reasoning or "")[:1500].replace("$", "\\$")
                        st.markdown(f"*{analyst_reason}...*") 
                        
                    with col_b:
                        st.markdown("### 🛡️ Risk Manager")
                        st.caption(f"Model: {inf.risk_model}")
                        
                        # Decision badge
                        risk_action = risk_decision.get("decision") or risk_decision.get("action", "N/A")
                        action_color = {
                            "APPROVE": "✅", "REJECT": "❌", "NO_TRADE": "⏸️",
                            "CUT_LOSS": "🔴", "SCALE_OUT": "🟠", "SCALE_IN": "🟢"
                        }.get(risk_action, "⚪")
                        st.markdown(f"**{action_color} {risk_action}**")
                        
                        # Sizing info
                        if risk_decision.get("size_usd"):
                            st.markdown(f"**Size:** ${risk_decision.get('size_usd', 0):.2f} @ {risk_decision.get('leverage', 1)}x")
                        
                        # Invalidation conditions if present
                        invalidation = risk_decision.get("invalidation_conditions", [])
                        if invalidation:
                            st.warning("**Invalidation Conditions:**")
                            for cond in invalidation:
                                st.markdown(f"- {cond}")
                        
                        st.code(json.dumps(risk_decision, indent=2), language="json")
                         
                        st.markdown("**Reasoning:**")
                        # Escape $ to prevent LaTeX rendering
                        risk_reason = (inf.risk_reasoning or "")[:1500].replace("$", "\\$")
                        st.markdown(f"*{risk_reason}...*")
                    
                    st.divider()
                    st.markdown(f"**Final Action:** `{final_action}` | **Models:** Analyst: `{inf.analyst_model}` | Risk: `{inf.risk_model}`")
    
    with tab2:
        st.subheader("Trade History")
        
        trades = get_recent_trades(20)
        
        if not trades:
            st.info("No trades yet.")
        else:
            # Convert to DataFrame
            trade_data = []
            for t in trades:
                status = "CLOSED" if t.closed_at else "OPEN"
                trade_data.append({
                    "Date": t.opened_at.strftime("%Y-%m-%d %H:%M"),
                    "Coin": t.coin,
                    "Side": t.direction,
                    "Entry": f"${t.entry_price:.2f}",
                    "Size": f"${t.size_usd:.2f}",
                    "Leverage": f"{t.leverage}x",
                    "Status": status,
                    "PnL": f"${t.pnl_usd:.2f}" if t.pnl_usd else "-",
                    "PnL %": f"{t.pnl_pct:.1f}%" if t.pnl_pct else "-"
                })
            
            df = pd.DataFrame(trade_data)
            st.dataframe(df, use_container_width=True, hide_index=True)
        
        # Open positions
        st.subheader("Open Positions")
        open_trades = get_open_trades()
        
        if not open_trades:
            st.info("No open positions.")
        else:
            for t in open_trades:
                st.warning(f"**{t.coin}** {t.direction} @ ${t.entry_price:.2f} | Size: ${t.size_usd:.2f} | {t.leverage}x")
    
    with tab3:
        st.subheader("Agent Logs")
        
        logs = get_agent_logs(30)
        
        if not logs:
            st.info("No logs yet.")
        else:
            for log in logs:
                icon = "🔵" if log.action_type == "LLM_RESPONSE" else "🟡" if log.action_type == "ERROR" else "⚪"
                with st.expander(f"{icon} [{log.timestamp.strftime('%H:%M:%S')}] {log.action_type} - {log.node_name or 'system'}"):
                    if log.output:
                        st.code(log.output[:500])
                    if log.error:
                        st.error(log.error)
                    if log.reasoning:
                        st.text_area("Full Reasoning", log.reasoning[:3000], height=200, label_visibility="collapsed", key=f"log_reasoning_{log.id}")


if __name__ == "__main__":
    main()
