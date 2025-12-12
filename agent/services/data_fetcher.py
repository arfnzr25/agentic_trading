"""
Data Fetcher Module

Parallel tool execution using asyncio.gather() for faster data collection.
"""

import asyncio
import time
from typing import Any


async def fetch_analyst_data(tools: list, coin: str, timestamps: dict) -> dict:
    """
    Fetch all data for analyst in parallel.
    
    Args:
        tools: List of MCP tool objects
        coin: Trading coin (e.g., "BTC")
        timestamps: Dict with start_5m, start_4h, current_ms
        
    Returns:
        dict with market_context, candles_5m, candles_4h, account_health
    """
    tool_map = {t.name: t for t in tools}
    
    # Define all fetches
    tasks = {
        "market_context": _call_tool(
            tool_map.get("get_market_context"),
            {"coin": coin}
        ),
        "candles_5m": _call_tool(
            tool_map.get("get_candles"),
            {
                "coin": coin,
                "interval": "5m",
                "start_time": timestamps["start_5m"],
                "end_time": timestamps["current_ms"]
            }
        ),
        "candles_1h": _call_tool(
            tool_map.get("get_candles"),
            {
                "coin": coin,
                "interval": "1h",
                "start_time": timestamps["start_1h"],
                "end_time": timestamps["current_ms"]
            }
        ),
        "candles_4h": _call_tool(
            tool_map.get("get_candles"),
            {
                "coin": coin,
                "interval": "4h",
                "start_time": timestamps["start_4h"],
                "end_time": timestamps["current_ms"]
            }
        ),
        "candles_1d": _call_tool(
            tool_map.get("get_candles"),
            {
                "coin": coin,
                "interval": "1d",
                "start_time": timestamps["start_1d"],
                "end_time": timestamps["current_ms"]
            }
        ),
        "account_health": _call_tool(
            tool_map.get("get_account_health"),
            {}
        ),
    }
    
    # Execute all in parallel
    start = time.time()
    results = await asyncio.gather(*tasks.values(), return_exceptions=True)
    elapsed = (time.time() - start) * 1000
    
    print(f"[DataFetcher] Parallel fetch completed in {elapsed:.0f}ms")
    
    # Map results back to keys
    return dict(zip(tasks.keys(), results))


async def _call_tool(tool, args: dict) -> Any:
    """Call a single tool safely."""
    if tool is None:
        return "Error: Tool not found"
    
    try:
        result = await tool.ainvoke(args)
        return result
    except Exception as e:
        return f"Error: {str(e)}"


def calculate_timestamps() -> dict:
    """Calculate standard timestamp ranges for candle fetching."""
    current_ms = int(time.time() * 1000)
    
    return {
        "current_ms": current_ms,
        "start_5m": current_ms - (50 * 5 * 60 * 1000),      # Last 50 5-min candles (~4h)
        "start_1h": current_ms - (48 * 60 * 60 * 1000),    # Last 48 1-hour candles (2 days)
        "start_4h": current_ms - (50 * 4 * 60 * 60 * 1000), # Last 50 4-hour candles (~8 days)
        "start_1d": current_ms - (30 * 24 * 60 * 60 * 1000) # Last 30 daily candles (~1 month)
    }


def summarize_candles(candles_json: str, max_candles: int = 10) -> str:
    """
    Compress candle data for LLM consumption.
    Shows summary stats AND recent candle patterns for structure analysis.
    """
    import json
    
    try:
        if isinstance(candles_json, str):
            if candles_json.startswith('['):
                candles = json.loads(candles_json)
            elif candles_json.startswith('Error'):
                return f"FETCH ERROR: {candles_json}"
            else:
                return "No candle data available."
        else:
            candles = candles_json if candles_json else []
            
        if not candles:
            return "No candles returned from API."
            
        # Parse candles (handle both string and dict formats)
        parsed = []
            
        for c in candles[-max_candles:]:
            # Handle MCP/LangChain TextContent objects (dict with 'text' field)
            if isinstance(c, dict) and "text" in c and isinstance(c.get("text"), str):
                try:
                    import json
                    c = json.loads(c["text"])
                except:
                    continue
            # Handle stringified JSON
            elif isinstance(c, str):
                try:
                    import json
                    c = json.loads(c)
                except:
                    continue
            
            # Extract data
            try:
                parsed.append({
                    "o": float(c.get("o", 0)),
                    "h": float(c.get("h", 0)),
                    "l": float(c.get("l", 0)),
                    "c": float(c.get("c", 0))
                })
            except:
                continue
        
        if not parsed:
            return "Could not parse candle data."
            
        # Calculate summary stats
        opens = [c["o"] for c in parsed]
        highs = [c["h"] for c in parsed]
        lows = [c["l"] for c in parsed]
        closes = [c["c"] for c in parsed]
        
        current = closes[-1]
        first_close = closes[0]
        trend_pct = ((current / first_close) - 1) * 100 if first_close else 0
        volatility = ((max(highs) - min(lows)) / current) * 100 if current else 0
        
        # Recent 5 candle pattern for structure
        recent_5 = parsed[-5:] if len(parsed) >= 5 else parsed
        pattern = []
        for i, c in enumerate(recent_5):
            body = c["c"] - c["o"]
            candle_type = "GREEN" if body > 0 else "RED" if body < 0 else "DOJI"
            pattern.append(f"{candle_type}(${c['c']:.0f})")
        
        # Structure analysis: Compare swing points (not just consecutive candles)
        # A swing high is a candle with lower highs on both sides
        # A swing low is a candle with higher lows on both sides
        
        recent_highs = highs[-10:] if len(highs) >= 10 else highs
        recent_lows = lows[-10:] if len(lows) >= 10 else lows
        
        # Simple structure: compare first half avg vs second half avg
        mid = len(recent_highs) // 2
        first_half_high = sum(recent_highs[:mid]) / max(mid, 1)
        second_half_high = sum(recent_highs[mid:]) / max(len(recent_highs) - mid, 1)
        first_half_low = sum(recent_lows[:mid]) / max(mid, 1)
        second_half_low = sum(recent_lows[mid:]) / max(len(recent_lows) - mid, 1)
        
        # Determine structure based on how highs and lows are moving
        higher_highs = second_half_high > first_half_high
        higher_lows = second_half_low > first_half_low
        lower_highs = second_half_high < first_half_high
        lower_lows = second_half_low < first_half_low
        
        if higher_highs and higher_lows:
            structure = "BULLISH (HH+HL)"
        elif lower_highs and lower_lows:
            structure = "BEARISH (LH+LL)"
        elif higher_lows and not lower_highs:
            structure = "BULLISH BIAS (HL forming)"
        elif lower_highs and not higher_lows:
            structure = "BEARISH BIAS (LH forming)"
        else:
            structure = "RANGING (no clear trend)"
        
        # Key levels
        swing_high = max(highs[-5:]) if len(highs) >= 5 else max(highs)
        swing_low = min(lows[-5:]) if len(lows) >= 5 else min(lows)
        
        return f"""Range: ${min(lows):.2f} - ${max(highs):.2f}
Current: ${current:.2f}
Trend: {'UP' if trend_pct > 0 else 'DOWN'} ({trend_pct:+.2f}%)
Volatility: {volatility:.2f}%
Structure: {structure}
Key Levels: Support ${swing_low:.2f} | Resistance ${swing_high:.2f}
Recent 5: {' -> '.join(pattern)}"""
        
    except Exception as e:
        return f"Candle parse error: {e}"
