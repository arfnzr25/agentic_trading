"""
AI Trading Agent Configuration

Loads settings from environment variables and provides
default risk parameters for the trading agent.
"""

import os
from pathlib import Path
from pydantic import BaseModel, Field
from dotenv import load_dotenv

# Load environment variables from multiple locations
# 1. agent/.env (preferred)
# 2. project root .env (fallback)
# 1. agent/.env (preferred)
# 2. project root .env (fallback)
config_dir = Path(__file__).parent
agent_dir = config_dir.parent
project_root = agent_dir.parent

load_dotenv(agent_dir / ".env")  # agent/.env
load_dotenv(project_root / ".env")  # project root .env


class RiskParams(BaseModel):
    """Risk management parameters."""
    max_position_pct: float = Field(default=0.75, description="Max position as % of portfolio (0.75 = 75%)")
    max_drawdown_pct: float = Field(default=0.50, description="Max drawdown before panic close (0.50 = 50%)")
    default_sl_btc_pct: float = Field(default=0.02, description="Default stop-loss for BTC (2%)")
    default_sl_alt_pct: float = Field(default=0.05, description="Default stop-loss for alts (5%)")
    max_concurrent_positions: int = Field(default=3, description="Maximum open positions")
    auto_approve_usd: float = Field(default=100.0, description="Auto-approve trades below this USD value")
    prefer_max_leverage: bool = Field(default=True, description="Use maximum leverage for profit optimization")


class AgentConfig(BaseModel):
    """Main agent configuration."""
    
    # OpenRouter
    openrouter_api_key: str = Field(default_factory=lambda: os.getenv("OPENROUTER_API_KEY", ""))
    openrouter_base_url: str = Field(default="https://openrouter.ai/api/v1")
    
    # LLM Models (user-configurable via env)
    analyst_model: str = Field(default_factory=lambda: os.getenv("ANALYST_MODEL", "anthropic/claude-sonnet-4"))
    risk_model: str = Field(default_factory=lambda: os.getenv("RISK_MODEL", "openai/gpt-4o"))
    
    # MCP Server
    mcp_server_url: str = Field(default_factory=lambda: os.getenv("MCP_SERVER_URL", "http://localhost:8000/sse"))
    
    # Telegram
    telegram_bot_token: str = Field(default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN", ""))
    telegram_chat_id: str = Field(default_factory=lambda: os.getenv("TELEGRAM_CHAT_ID", ""))
    
    # Database
    database_url: str = Field(default_factory=lambda: os.getenv("DATABASE_URL", "sqlite:///agent.db"))
    
    # Trading
    inference_interval_seconds: int = Field(default=180, description="3 minutes between inference cycles")
    focus_coins: list[str] = Field(default=["BTC"], description="Primary focus coins")
    
    # Risk
    risk: RiskParams = Field(default_factory=RiskParams)
    
    # Site info for OpenRouter headers
    site_url: str = Field(default_factory=lambda: os.getenv("SITE_URL", "http://localhost"))
    site_name: str = Field(default="Hyperliquid Trading Agent")


# Global config instance
config = AgentConfig()


def get_config() -> AgentConfig:
    """Get the global configuration."""
    return config
