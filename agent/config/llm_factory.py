"""
OpenRouter LLM Factory

Creates LangChain ChatOpenAI instances configured for OpenRouter
with role-based model selection and model-specific configurations.
"""

from langchain_openai import ChatOpenAI
from agent.config.config import get_config


def is_gemini_model(model: str) -> bool:
    """Check if model is a Gemini model requiring special handling."""
    return "gemini" in model.lower() or "google/" in model.lower()


def is_reasoning_model(model: str) -> bool:
    """Check if model supports/requires reasoning parameters."""
    reasoning_models = ["gemini", "thinking", "o1", "o3"]
    return any(rm in model.lower() for rm in reasoning_models)


def get_llm(
    model: str | None = None,
    temperature: float = 0.1,
    role: str | None = None
) -> ChatOpenAI:
    """
    Factory for OpenRouter-backed LLMs.
    
    Args:
        model: Specific model identifier (e.g., "anthropic/claude-sonnet-4")
        temperature: Sampling temperature (0.0-1.0)
        role: Agent role for automatic model selection ("analyst", "risk")
        
    Returns:
        Configured ChatOpenAI instance pointing to OpenRouter
    """
    cfg = get_config()
    
    # Role-based model selection
    if model is None:
        if role == "analyst":
            model = cfg.analyst_model
        elif role == "risk":
            model = cfg.risk_model
        else:
            model = cfg.analyst_model  # Default
    
    # Base configuration
    llm_kwargs = {
        "api_key": cfg.openrouter_api_key,
        "base_url": cfg.openrouter_base_url,
        "model": model,
        "temperature": temperature,
        "default_headers": {
            "HTTP-Referer": cfg.site_url,
            "X-Title": cfg.site_name,
        }
    }
    
    # Model-specific extra body parameters
    model_kwargs = {}
    
    # Gemini models require reasoning parameters
    if is_gemini_model(model):
        model_kwargs["include_reasoning"] = True
        # For Gemini thinking/reasoning models
        if is_reasoning_model(model):
            model_kwargs["reasoning"] = {
                "effort": "medium",
                "exclude": False
            }
    
    # Some models work better with strict JSON mode
    if "gpt-4" in model.lower() or "claude" in model.lower():
        pass  # These handle JSON well by default
    
    if model_kwargs:
        llm_kwargs["model_kwargs"] = model_kwargs
    
    return ChatOpenAI(**llm_kwargs)


def get_analyst_llm(temperature: float = 0.1) -> ChatOpenAI:
    """Get LLM configured for market analysis."""
    return get_llm(role="analyst", temperature=temperature)


def get_risk_llm(temperature: float = 0.0) -> ChatOpenAI:
    """Get LLM configured for risk management (more deterministic)."""
    return get_llm(role="risk", temperature=temperature)

