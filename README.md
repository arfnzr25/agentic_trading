# Hyperliquid Agentic Trading System

An advanced AI-powered trading agent built with LangGraph, Pydantic, and DSPy, designed for the Hyperliquid decentralized exchange.

## Features

- **Agentic Workflow:** Uses LangGraph to orchestrate Analyst, Risk Manager, and Execution nodes.
- **Dual-AI Architecture:**
  - **Legacy Agent:** Sequential chain (Analyst -> Risk -> Merge -> Exec) using strict Pydantic V2 schemas.
  - **DSPy Shadow Mode:** Parallel autonomous agent ("Shadow Trader") running `dspy.Predict` with typed signatures and assertions to learn and validate strategies without risking capital (initially).
- **Safety First:**
  - Risk Management Node enforces position sizing, leverage limits, and trend-based rules.
  - Direct MCP integration for secure execution.
- **Data Persistence:**
  - `agent.db` (SQLite): Stores trade history and exit plans.
  - `dspy_memory.db` (SQLite): Stores Shadow Mode predictions and outcomes for future Optimization (MIPROv2).

## Architecture

### Nodes

1.  **Analyst (`analyst_v2.py`)**: Analyzes market structure, candles, and technicals. Outputs `TradeSignal`.
2.  **Risk Manager (`risk_v2.py`)**: Validates signals against portfolio state and risk rules. Outputs `RiskDecision`.
3.  **Merge (`merge.py`)**: Synthesizes inputs, calculating final position sizing and stop-loss/take-profit percentages.
4.  **Shadow Runner (`dspy_runner.py`)**: Asynchronous sidecar that runs the DSPy `ShadowTrader` module.

### DSPy Shadow Mode

- **Goal**: Data-driven optimization of prompts and logic.
- **Pipeline**:
  - Ingests `market_data_snapshot` from the Analyst.
  - Generates a `TradeSignal` using `dspy.Predict(StrategicAnalysis)`.
  - Validates logic using `dspy.Suggest` assertions.
  - Saves result to `dspy_memory.db`.
- **Optimization**: Future steps involve using `MIPROv2` to minimize the loss function defined in `dspy_shadow_mode_architecture.md`.

## Setup

1.  **Environment**:
    Copy `.env.example` to `agent/.env` and fill in:

    - `OPENROUTER_API_KEY`: For LLM inference.
    - `HYPERLIQUID_PRIVATE_KEY` (Wallet): For execution (via MCP).
    - `MCP_SERVER_URL`: Connection to Hyperliquid MCP.

2.  **Dependencies**:

    ```bash
    pip install -r agent/requirements.txt
    ```

3.  **Run**:
    ```bash
    python agent/main.py
    ```

## Development

- **Schemas**: defined in `agent/models/schemas.py`.
- **DSPy Modules**: defined in `agent/dspy/modules.py`.
- **Config**: logic in `agent/config.py`.

## License

Private.
