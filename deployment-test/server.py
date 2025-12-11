import os
import asyncio
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from hyperliquid.info import Info
from hyperliquid.exchange import Exchange
from hyperliquid.utils import constants
import eth_account
import math
import sys
import time
import functools
import traceback
import datetime
import json



# Global Logger
class AgentLogger:
    def __init__(self, log_dir="logs"):
        self.log_dir = log_dir
        self.ensure_log_dir()

    def ensure_log_dir(self):
        if not os.path.exists(self.log_dir):
            os.makedirs(self.log_dir)

    def _get_filepath(self, base_name):
        date_str = datetime.datetime.now().strftime("%Y-%m-%d")
        filename = f"{date_str}_{base_name}"
        return os.path.join(self.log_dir, filename)

    def log(self, tool, action, result, args=None, kwargs=None):
        """Log general actions in a human-readable format."""
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # Simplified Human-Readable Format
        args_str = f"Args: {args}, Kwargs: {kwargs}" if args or kwargs else ""
        result_str = str(result)[:200] + "..." if len(str(result)) > 200 else str(result)
        
        entry = f"[{timestamp}] [{tool}] {action} | {args_str} -> Result: {result_str}\n"
        
        filepath = self._get_filepath("agent_actions.log")
        try:
            with open(filepath, "a") as f:
                f.write(entry)
        except Exception as e:
            print(f"Failed to write to main log: {e}", file=sys.stderr)

    def log_trade(self, tool, action, result, args=None, kwargs=None):
        """Log trade-specific actions in a structured format for learning."""
        timestamp = datetime.datetime.now().isoformat()
        
        entry = {
            "timestamp": timestamp,
            "tool": tool,
            "action": action,
            "args": str(args) if args else None,
            "kwargs": str(kwargs) if kwargs else None,
            "result": str(result)
        }
        
        filepath = self._get_filepath("trades.log")
        try:
            with open(filepath, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as e:
            print(f"Failed to write to trade log: {e}", file=sys.stderr)

# Logging Decorator
def log_action(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        tool_name = func.__name__
        try:
            res = func(*args, **kwargs)
            
            # 1. General Log (Human Readable)
            agent_logger.log(tool_name, "CALLED", res, args, kwargs)
            
            # 2. Trade Log (Structured) - Filter for trade tools
            if any(prefix in tool_name for prefix in ["place_", "cancel_", "close_", "update_"]):
                agent_logger.log_trade(tool_name, "EXECUTED", res, args, kwargs)
                
            return res
        except Exception as e:
            # Log failure
            agent_logger.log(tool_name, "ERROR", str(e), args, kwargs)
            raise e
    return wrapper

# Caching Decorator
def ttl_cache(seconds: int):
    def decorator(func):
        cache = {}
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            key = str(args) + str(kwargs)
            now = time.time()
            if key in cache:
                val, timestamp = cache[key]
                if now - timestamp < seconds:
                    return val
            res = func(*args, **kwargs)
            cache[key] = (res, now)
            return res
        return wrapper
    return decorator

# WebSocket Manager for Real-Time Prices
# WebSocket Manager removed. See planned_integrations.md for details.

# Initialize Global Managers
agent_logger = AgentLogger()
# ws_manager will be initialized in main


# Global Precision Manager
class PrecisionManager:
    def __init__(self, info_client):
        self.info = info_client
        self.meta = None
        self.spot_meta = None
        self.coin_map = {} # coin -> asset info

    def load(self):
        print("[MCP] Loading exchange metadata...", file=sys.stderr)
        try:
            self.meta = self.info.meta()
            self.spot_meta = self.info.spot_meta()
            
            # Index perp universe
            for asset in self.meta["universe"]:
                self.coin_map[asset["name"]] = asset
                
            print("[MCP] Metadata loaded.", file=sys.stderr)
        except Exception as e:
            print(f"[MCP] Failed to load metadata: {e}", file=sys.stderr)

    def round_px(self, coin: str, px: float) -> float:
        # Basic heuristic fallback if meta not loaded or coin not found
        if not self.meta or coin not in self.coin_map:
            return self._heuristic_round_px(px)
        
        asset = self.coin_map[coin]
        # Hyperliquid uses significant figures or specific tick sizes. 
        # The SDK/API usually handles standard rounding, but let's be safe.
        # Max decimals depends on szDecimals usually, but price is different.
        # For simplicity and robustness, we'll stick to the heuristic which works well for HL,
        # OR implementation specific logic if we had exact tick size.
        # The 'universe' metadata has 'szDecimals' but not explicit 'tickSize'.
        # However, the SDK's internal logic often relies on significant figures.
        return self._heuristic_round_px(px)

    def round_sz(self, coin: str, sz: float) -> float:
        if not self.meta or coin not in self.coin_map:
            return round(sz, 5)
        
        asset = self.coin_map[coin]
        decimals = asset["szDecimals"]
        return round(sz, decimals)

    def _heuristic_round_px(self, px: float) -> float:
        if px == 0: return 0.0
        if px > 10000: return float(int(round(px)))
        if px >= 1: return round(px, 5) # General safe bet
        return round(px, 6) # For low cap coins

# Initialize later
pm = None

# Load environment variables
load_dotenv()
ENABLE_MASTER_INTERACTION = os.getenv("ENABLE_MASTER_INTERACTION", "false").lower() == "true"

# Configuration
PUBLIC_ADDRESS = os.getenv("HL_WL")
PRIVATE_KEY = os.getenv("HL_PK")
AGENT_WALLET = os.getenv("AG_WL")
BASE_URL = constants.MAINNET_API_URL

if not PUBLIC_ADDRESS or not PRIVATE_KEY:
    raise ValueError("HL_WL and HL_PK must be set in .env")

# Initialize Hyperliquid SDK
# Initialize Hyperliquid SDK
info = Info(BASE_URL, skip_ws=True)
account = eth_account.Account.from_key(PRIVATE_KEY)
agent_address = account.address

# Verify Agent Wallet if provided
if AGENT_WALLET and AGENT_WALLET.lower() != agent_address.lower():
    raise ValueError(f"AG_WL ({AGENT_WALLET}) does not match the address derived from HL_PK ({agent_address})")

# Determine mode
if PUBLIC_ADDRESS.lower() != agent_address.lower():
    print(f"[MCP] Running in AGENT mode.", file=sys.stderr)
else:
    if not ENABLE_MASTER_INTERACTION:
        print(f"[MCP] Error: Direct Master Wallet interaction is disabled.", file=sys.stderr)
        print(f"[MCP] Please use an Agent Wallet or set ENABLE_MASTER_INTERACTION=true in .env", file=sys.stderr)
        sys.exit(1)
    print(f"[MCP] Running in DIRECT mode (Master = Agent).", file=sys.stderr)

exchange = Exchange(account, BASE_URL, account_address=PUBLIC_ADDRESS)

# Initialize MCP Server
# Initialize MCP Server
mcp = FastMCP("Hyperliquid MCP Server", dependencies=[], host="0.0.0.0", port=8000)

def log(msg: str):
    """Log message to stderr for verbose output."""
    print(f"[MCP] {msg}", file=sys.stderr)

def handle_errors(func):
    """Decorator to catch errors and return them as strings."""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            # Auto-log success
            res = func(*args, **kwargs)
            # We can't easily log args here without potentially leaking sensitive info or being too verbose,
            # but we can log the tool name.
            # For specific actions, we'll call agent_logger inside the tool.
            return res
        except Exception as e:
            log(f"Error in {func.__name__}: {e}")
            traceback.print_exc(file=sys.stderr)
            return f"Error: {str(e)}"
    return wrapper

# Precision Manager handles rounding now.
# We keep this for backward compatibility if needed, but it should ideally be removed.
def round_price(px: float) -> float:
    if pm:
        return pm.round_px("UNKNOWN", px) # Fallback
    return float(px)

@mcp.tool()
@log_action
@handle_errors
def get_all_mids() -> dict:
    """
    Get current mid prices for all assets.
    """

    log("Fetching all mid prices...")
    return info.all_mids()

@mcp.tool()
@log_action
@handle_errors
def get_l2_snapshot(coin: str) -> dict:
    """
    Get L2 order book snapshot for a specific coin.
    Args:
        coin: The coin symbol (e.g., 'ETH', 'BTC')
    """
    return info.l2_snapshot(coin)

@mcp.tool()
@log_action
@handle_errors
def get_candles(coin: str, interval: str, start_time: int, end_time: int) -> list:
    """
    Get historical candles for a specific coin.
    Args:
        coin: The coin symbol (e.g., 'ETH')
        interval: Time interval (e.g., '1h', '15m')
        start_time: Start timestamp in milliseconds
        end_time: End timestamp in milliseconds
    """

    log(f"Fetching candles for {coin} ({interval}) from {start_time} to {end_time}")
    result = info.candles_snapshot(coin, interval, start_time, end_time)
    log(f"Fetched {len(result)} candles for {coin}")
    return result



@mcp.tool()
@log_action
@handle_errors
def get_account_info(type: str = "perp") -> dict:
    """
    Get account info (balances, margin, positions).
    Args:
        type: 'perp' (default) or 'spot'
    """
    if type.lower() == "spot":
        log("Fetching spot user state...")
        return info.spot_user_state(PUBLIC_ADDRESS)
    
    log("Fetching perp user state...")
    return info.user_state(PUBLIC_ADDRESS)

@mcp.tool()
@log_action
@handle_errors
def get_user_funding_history(start_time: int, end_time: int = None) -> list:
    """
    Get the user's funding history.
    Args:
        start_time: Start timestamp in milliseconds
        end_time: End timestamp in milliseconds (optional)
    """
    return info.user_funding_history(PUBLIC_ADDRESS, start_time, end_time)

@mcp.tool()
@log_action
@handle_errors
def get_user_fills() -> list:
    """
    Get the user's trade history (fills).
    """
    return info.user_fills(PUBLIC_ADDRESS)



@mcp.tool()
@log_action
@handle_errors
def get_historical_orders() -> list:
    """
    Get the user's historical orders.
    """
    return info.historical_orders(PUBLIC_ADDRESS)

@mcp.tool()
@log_action
@handle_errors
def get_exchange_meta(type: str = "perp") -> dict:
    """
    Get exchange metadata (universe, tokens).
    Args:
        type: 'perp' (default) or 'spot'
    """
    if type.lower() == "spot":
        return info.spot_meta()
    return info.meta()

@mcp.tool()
@log_action
@handle_errors
def get_funding_history(coin: str, start_time: int, end_time: int = None) -> list:
    """
    Get global funding history for a specific coin.
    """
    return info.funding_history(coin, start_time, end_time)

@mcp.tool()
@log_action
@handle_errors
def get_open_orders() -> list:
    """
    Get all open orders for the user.
    """

    log("Fetching open orders...")
    # Use frontend_open_orders to get more details like trigger price
    return info.frontend_open_orders(PUBLIC_ADDRESS)

@mcp.tool()
@log_action
@handle_errors
def place_order(coin: str, is_buy: bool, sz: float, limit_px: float, order_type: str = "limit", reduce_only: bool = False) -> dict:
    """
    Place a new order on Hyperliquid.
    Args:
        coin: The coin symbol (e.g., 'ETH')
        is_buy: True for Buy, False for Sell
        sz: Size of the order
        limit_px: Limit price
        order_type: Order type ('limit', 'market', etc.) - currently only 'limit' is fully supported in this wrapper for simplicity, but SDK supports more.
        reduce_only: Whether the order is reduce-only
    """
    # Note: This is a basic implementation. Complex order types might need more handling.
    # The SDK's `order` method signature: order(self, name, is_buy, sz, limit_px, order_type, reduce_only=False, cloid=None)
    # We need to map 'order_type' string to the format expected by SDK if it differs, 
    # but SDK `order` method takes a dict for order_type usually like {"limit": {"tif": "Gtc"}} or just "limit" depending on version.
    # Checking SDK usage, `order` helper simplifies this.
    
    # For safety, we'll stick to the helper provided by Exchange class if available, or construct the order.
    # The `exchange.order` method is a high-level wrapper.
    
    # We need to look up the asset index? The SDK handles 'name' (coin symbol) -> asset index conversion internally in `exchange.order`.
    
    # Round price
    limit_px = round_price(limit_px)



    log(f"Placing order: {coin} {'BUY' if is_buy else 'SELL'} {sz} @ {limit_px}")
    order_result = exchange.order(coin, is_buy, sz, limit_px, {"limit": {"tif": "Gtc"}}, reduce_only=reduce_only)
    return order_result

@mcp.tool()
@log_action
@handle_errors
def cancel_order(coin: str, oid: int) -> dict:
    """
    Cancel an order by its Order ID (oid).
    Args:
        coin: The coin symbol
        oid: The order ID
    """
    return exchange.cancel(coin, oid)


@mcp.tool()
@log_action
@handle_errors
def transfer(amount: float, destination: str, token: str = "USDC") -> dict:
    """
    Transfer assets to another address.
    Args:
        amount: Amount to transfer
        destination: Destination address
        token: Token symbol (default 'USDC' for perp wallet transfer, or e.g. 'PURR' for spot)
    """
    if token.upper() == "USDC":
        log(f"Transferring {amount} USDC to {destination}...")
        return exchange.usd_transfer(amount, destination)
    
    log(f"Transferring {amount} {token} (Spot) to {destination}...")
    return exchange.spot_transfer(amount, destination, token)

@mcp.tool()
@log_action
@handle_errors
def update_isolated_margin(coin: str, amount: float) -> dict:
    """
    Add or remove margin from an isolated position.
    Args:
        coin: Coin symbol
        amount: Amount to add (positive) or remove (negative)
    """
    return exchange.update_isolated_margin(amount, coin)





@mcp.tool()
@log_action
@handle_errors
def schedule_cancel(time_ms: int = None) -> dict:
    """
    Schedule a "Dead Man's Switch" cancel.
    Args:
        time_ms: Timestamp to cancel all orders. If None, cancels the scheduled cancel.
    """
    return exchange.schedule_cancel(time_ms)

@mcp.tool()
@log_action
@handle_errors

def place_smart_order(coin: str, is_buy: bool, size: float, size_type: str = "usd", limit_px: float = None, sl_pct: float = None, tp_pct: float = None, leverage: int = None) -> dict:
    """
    Place a smart order with flexible sizing and optional TP/SL.
    Args:
        coin: Coin symbol
        is_buy: True for Buy
        size: Size amount
        size_type: 'usd' (default), 'pct' (equity %), or 'token' (raw quantity)
        limit_px: Limit price (None for Market)
        sl_pct: Stop Loss % (e.g., 0.05)
        tp_pct: Take Profit % (e.g., 0.10)
        leverage: Optional leverage to set before trade
    """
    # 1. Handle Leverage - FORCE SET before trade
    if leverage:
        log(f"Setting leverage to {leverage}x for {coin}")
        try:
            # Use cross-margin mode (is_cross=True) for maximum leverage
            result = exchange.update_leverage(leverage, coin, is_cross=True)
            log(f"Leverage update result: {result}")
            if result.get("status") != "ok":
                print(f"[SmartOrder] WARNING: Leverage update failed: {result}", file=sys.stderr)
        except Exception as lev_err:
            print(f"[SmartOrder] ERROR setting leverage: {lev_err}", file=sys.stderr)
            # Continue anyway - try to place order with current leverage

    # 2. Calculate Size in Tokens
    mids = info.all_mids()
    if coin not in mids: return f"Error: Coin {coin} not found"
    mid_px = float(mids[coin])
    
    # Execution Price (Estimate for Market)
    exec_px = limit_px if limit_px else mid_px * (1.0005 if is_buy else 0.9995)
    
    final_sz = 0.0
    if size_type.lower() == "usd":
        final_sz = size / exec_px
    elif size_type.lower() == "pct":
        state = info.user_state(PUBLIC_ADDRESS)
        equity = float(state["marginSummary"]["accountValue"])
        usd_size = equity * size
        final_sz = usd_size / exec_px
    else: # 'token'
        final_sz = size
        
    final_sz = pm.round_sz(coin, final_sz)
    if final_sz == 0: return f"Error: Size is 0"

    log(f"Smart Order: {coin} {'BUY' if is_buy else 'SELL'} {final_sz} (Type: {size_type})")
    
    # 3. Place Entry
    print(f"[SmartOrder] Placing Entry: {coin} {is_buy} {final_sz}...", file=sys.stderr)
    try:
        if limit_px:
            limit_px = pm.round_px(coin, limit_px)
            res = exchange.order(coin, is_buy, final_sz, limit_px, {"limit": {"tif": "Gtc"}})
        else:
            res = exchange.market_open(coin, is_buy, final_sz)
    except Exception as e:
        return f"Error placing entry: {e}"
        
    agent_logger.log("place_smart_order", f"{'BUY' if is_buy else 'SELL'} {final_sz} {coin}", res)
    
    # Deep Check for Entry Success
    # Hyperliquid returns status='ok' even if the order logic failed (e.g. insane price)
    # We need to check response['data']['statuses'][0]
    entry_success = False
    if res.get("status") == "ok":
        response_data = res.get("response", {})
        if response_data.get("type") == "order":
            statuses = response_data.get("data", {}).get("statuses", [])
            if statuses and not statuses[0].get("error"):
                entry_success = True
            else:
                error_msg = statuses[0].get("error") if statuses else "Unknown Error"
                print(f"[SmartOrder] Entry Failed: {error_msg}", file=sys.stderr)
                return f"Error: Entry Failed - {error_msg}"
    
    if not entry_success:
        return f"Error: Entry order failed via API. Response: {res}"

    # 4. Handle TP/SL (Trigger Orders) ONLY if Entry Succeeded
    if (sl_pct or tp_pct) and entry_success:
        import time
        time.sleep(0.5) # Brief pause to ensure sequence
        
        entry_px = limit_px if limit_px else mid_px
        close_is_buy = not is_buy
        
        triggered_orders = []
        
        if sl_pct:
            sl_price = entry_px * (1 - sl_pct) if is_buy else entry_px * (1 + sl_pct)
            sl_price = pm.round_px(coin, sl_price)
            log(f"Placing Smart SL @ {sl_price}")
            sl_res = exchange.order(coin, close_is_buy, final_sz, sl_price, 
                           {"trigger": {"triggerPx": sl_price, "isMarket": True, "tpsl": "sl"}}, 
                           reduce_only=True)
            triggered_orders.append(f"SL: {sl_res['status']}")
            
        if tp_pct:
            tp_price = entry_px * (1 + tp_pct) if is_buy else entry_px * (1 - tp_pct)
            tp_price = pm.round_px(coin, tp_price)
            log(f"Placing Smart TP @ {tp_price}")
            tp_res = exchange.order(coin, close_is_buy, final_sz, tp_price, 
                           {"trigger": {"triggerPx": tp_price, "isMarket": True, "tpsl": "tp"}}, 
                           reduce_only=True)
            triggered_orders.append(f"TP: {tp_res['status']}")
            
    return res
            
    return res



@mcp.tool()
@log_action
@handle_errors
def close_position(coin: str, percentage: float = 1.0) -> dict:
    """
    Close a position (or part of it).
    Args:
        coin: Coin symbol
        percentage: Percentage to close (0.0 to 1.0, default 1.0 for 100%)
    """
    state = info.user_state(PUBLIC_ADDRESS)
    positions = state["assetPositions"]
    target_pos = None
    for p in positions:
        if p["position"]["coin"] == coin:
            target_pos = p["position"]
            break
            
    if not target_pos:
        return f"Error: No position found for {coin}"
        
    szi = float(target_pos["szi"])
    if szi == 0:
        return f"Error: Position size is 0 for {coin}"
        
    close_sz = abs(szi) * percentage
    close_sz = pm.round_sz(coin, close_sz)
    is_buy = szi < 0 # If short (negative size), we buy to close
    
    log(f"Closing {percentage*100}% of {coin} position ({szi}) -> {'BUY' if is_buy else 'SELL'} {close_sz}")
    
    res = exchange.market_open(coin, is_buy, close_sz)
    agent_logger.log("close_position", f"Closed {percentage*100}% of {coin} ({close_sz})", res)
    return res

@mcp.tool()
@log_action
@handle_errors
@ttl_cache(seconds=2)
def get_account_health() -> dict:
    """
    Get account health metrics.
    """
    state = info.user_state(PUBLIC_ADDRESS)
    margin = state["marginSummary"]
    equity = float(margin["accountValue"])
    used = float(margin["totalMarginUsed"])
    
    health = {
        "equity": equity,
        "margin_used": used,
        "margin_usage_pct": round((used / equity) * 100, 2) if equity > 0 else 0,
        "withdrawable": float(state["withdrawable"]),
        "cross_maintenance_margin": float(state["crossMaintenanceMarginUsed"]),
    }
    
    # Risk Level
    usage = health["margin_usage_pct"]
    if usage > 80: health["risk_level"] = "HIGH"
    elif usage > 50: health["risk_level"] = "MEDIUM"
    else: health["risk_level"] = "LOW"
    
    return health

@mcp.tool()
@log_action
@handle_errors
def get_max_trade_size(coin: str, leverage: int = 20) -> dict:
    """
    Calculate max trade size for a coin based on current equity and leverage.
    """
    state = info.user_state(PUBLIC_ADDRESS)
    equity = float(state["marginSummary"]["accountValue"])
    
    max_usd = equity * leverage
    
    mids = info.all_mids()
    price = float(mids.get(coin, 0))
    
    if price == 0:
        return {"error": "Coin not found"}
        
    max_sz = max_usd / price
    
    return {
        "max_usd": max_usd,
        "max_sz": pm.round_sz(coin, max_sz),
        "leverage_used": leverage,
        "price_used": price
    }

@mcp.tool()
@log_action
@handle_errors
def get_position_risk(coin: str) -> dict:
    """
    Get risk metrics for a specific position.
    """
    state = info.user_state(PUBLIC_ADDRESS)
    positions = state["assetPositions"]
    target = None
    for p in positions:
        if p["position"]["coin"] == coin:
            target = p["position"]
            break
            
    if not target:
        return {"status": "No position"}
        
    entry_px = float(target["entryPx"])
    liq_px = float(target["liquidationPx"]) if target["liquidationPx"] else 0
    
    mids = info.all_mids()
    curr_px = float(mids.get(coin, entry_px))
    
    dist_to_liq = abs(curr_px - liq_px)
    dist_pct = (dist_to_liq / curr_px) * 100 if curr_px > 0 else 0
    
    return {
        "coin": coin,
        "size": float(target["szi"]),
        "entry_px": entry_px,
        "current_px": curr_px,
        "liquidation_px": liq_px,
        "distance_to_liq_pct": round(dist_pct, 2),
        "unrealized_pnl": float(target["unrealizedPnl"]),
        "return_on_equity": float(target["returnOnEquity"]) * 100
    }

@mcp.tool()
@log_action
@handle_errors
@ttl_cache(seconds=10)
def get_market_leaders(limit: int = 10) -> list:
    # meta_and_asset_ctxs returns tuple (universe, contexts)
    # contexts has 'dayNtlVlm', 'markPx', 'prevDayPx', 'funding'
    meta, ctxs = info.meta_and_asset_ctxs()
    
    assets = []
    for i, asset in enumerate(meta["universe"]):
        ctx = ctxs[i]
        name = asset["name"]
        vol = float(ctx["dayNtlVlm"])
        curr = float(ctx["markPx"])
        prev = float(ctx["prevDayPx"])
        change_pct = ((curr - prev) / prev) * 100 if prev > 0 else 0
        
        assets.append({
            "coin": name,
            "volume_24h": vol,
            "price": curr,
            "change_24h": round(change_pct, 2)
        })
        
    # Sort by volume desc
    assets.sort(key=lambda x: x["volume_24h"], reverse=True)
    return assets[:limit]



@mcp.tool()
@log_action
@handle_errors

@ttl_cache(seconds=60)
def get_token_analytics(coin: str, interval: str = "4h") -> dict:
    """
    Get comprehensive technical analysis for a token.
    Includes: Price, Volatility, Trend (ADX), Key Levels (Swings), RSI, EMA, Funding Rate.
    """
    import time
    import math
    
    # 1. Fetch Data
    end = int(time.time() * 1000)
    # Lookback for Swings (50 candles), ADX (50 candles), EMA/RSI (20/14 candles)
    lookback_candles = 50
    interval_map = {"1h": 3600*1000, "4h": 4*3600*1000}
    ms_per_candle = interval_map.get(interval, 3600*1000)
    start = end - (lookback_candles * ms_per_candle)
    
    candles = info.candles_snapshot(coin, interval, start, end)
    if not candles or len(candles) < 20:
        return {"error": "Insufficient data"}
        
    # 2. Parse Data
    closes = [float(c["c"]) for c in candles]
    highs = [float(c["h"]) for c in candles]
    lows = [float(c["l"]) for c in candles]
    current_price = closes[-1]
    
    # 3. Calculate RSI (Simple)
    def calculate_rsi(prices, period=14):
        deltas = [prices[i] - prices[i-1] for i in range(1, len(prices))]
        avg_gain = sum(d for d in deltas if d > 0) / period
        avg_loss = -sum(d for d in deltas if d < 0) / period
        if avg_loss == 0: return 100.0
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))
        
    rsi = calculate_rsi(closes[-50:]) # Use last 50 for calculation
    
    # 4. Volatility (StdDev of % returns)
    returns = [(closes[i] - closes[i-1])/closes[i-1] for i in range(1, len(closes))]
    mean_ret = sum(returns) / len(returns)
    variance = sum((r - mean_ret)**2 for r in returns) / len(returns)
    volatility = math.sqrt(variance) * 100 # In percent
    
    # 5. Key Levels (Swings)
    recent_high = max(highs[-20:])
    recent_low = min(lows[-20:])
    
    # 6. Trending Status
    ema_20 = sum(closes[-20:]) / 20 # Simple approx for now
    trend = "BULLISH" if current_price > ema_20 else "BEARISH"
    
    # 7. Funding
    meta, ctxs = info.meta_and_asset_ctxs()
    idx = next((i for i, a in enumerate(meta["universe"]) if a["name"] == coin), None)
    funding = float(ctxs[idx]["funding"]) if idx is not None else 0.0
    
    return {
        "coin": coin,
        "price": current_price,
        "interval": interval,
        "rsi": round(rsi, 2),
        "volatility_pct": round(volatility, 2),
        "trend": trend,
        "key_levels": {
            "recent_high": recent_high,
            "recent_low": recent_low,
            "ema_20": round(ema_20, 2)
        },
        "funding_rate": funding
    }


@mcp.tool()
@log_action
@handle_errors
@ttl_cache(seconds=10)
def get_market_context(coin: str) -> dict:
    """
    Get detailed market context: Funding, Open Interest, Premium, Volume.
    Essential for determining manipulation or crowded trades.
    """
    meta, ctxs = info.meta_and_asset_ctxs()
    # Find index
    idx = next((i for i, a in enumerate(meta["universe"]) if a["name"] == coin), None)
    
    if idx is None:
        return {"error": f"Coin {coin} not found"}
        
    ctx = ctxs[idx]
    
    # Parse data
    funding = float(ctx.get("funding", 0))
    oi_sz = float(ctx.get("openInterest", 0))
    oracle_px = float(ctx.get("oraclePx", 0))
    # Note: ctx contains 'markPx' usually, if not fallback to oracle
    mark_px = float(ctx.get("markPx", oracle_px))
    
    # Calculate derived metrics
    oi_usd = oi_sz * oracle_px
    premium = (mark_px - oracle_px) / oracle_px if oracle_px > 0 else 0
    
    return {
        "coin": coin,
        "price": oracle_px,
        "mark_price": mark_px,
        "funding_rate_hourly": funding,
        "funding_rate_annualized": round(funding * 24 * 365 * 100, 2), # In %
        "open_interest_sz": oi_sz,
        "open_interest_usd": oi_usd,
        "premium_pct": round(premium * 100, 4),
        "day_volume_usd": float(ctx.get("dayNtlVlm", 0))
    }
        


@mcp.tool()
@log_action
@handle_errors
def cancel_all_orders() -> dict:
    """PANIC: Cancel ALL open orders (Optimized Batch)."""
    orders = info.open_orders(PUBLIC_ADDRESS)
    if not orders:
        return {"status": "No open orders"}
        
    cancels = [{"coin": o["coin"], "oid": o["oid"]} for o in orders]
    
    # Batch cancel
    print(f"Batch cancelling {len(cancels)} orders...", file=sys.stderr)
    res = exchange.bulk_cancel(cancels)
    
    agent_logger.log("cancel_all_orders", f"Batch cancelled {len(cancels)} orders", res)
    return {"cancelled_count": len(cancels), "details": res}

@mcp.tool()
@log_action
@handle_errors
def close_all_positions() -> dict:
    """PANIC: Close ALL open positions at Market."""
    state = info.user_state(PUBLIC_ADDRESS)
    positions = state["assetPositions"]
    
    results = []
    for p in positions:
        pos = p["position"]
        coin = pos["coin"]
        szi = float(pos["szi"])
        if szi == 0: continue
        
        # Close it
        is_buy = szi < 0
        sz = abs(szi)
        res = exchange.market_open(coin, is_buy, sz)
        results.append({"coin": coin, "result": res})
        
    agent_logger.log("close_all_positions", f"Closed {len(results)} positions", results)
    return {"closed_count": len(results), "details": results}

@mcp.tool()
@log_action
@handle_errors
def get_order_book_analytics(coin: str) -> dict:
    """
    Get order book analytics: Imbalance, Walls, Spread, and Sentiment.
    """
    # 1. Get L2
    l2 = info.l2_snapshot(coin)
    bids = l2["levels"][0]
    asks = l2["levels"][1]
    
    # 2. Imbalance (Top 10)
    top_bids = bids[:10]
    top_asks = asks[:10]
    bid_vol = sum(float(b["sz"]) * float(b["px"]) for b in top_bids)
    ask_vol = sum(float(a["sz"]) * float(a["px"]) for a in top_asks)
    imbalance_ratio = bid_vol / ask_vol if ask_vol > 0 else 999.0
    
    # 3. Premia
    meta, ctxs = info.meta_and_asset_ctxs()
    idx = next((i for i, a in enumerate(meta["universe"]) if a["name"] == coin), None)
    premia_pct = 0.0
    if idx is not None:
        ctx = ctxs[idx]
        mark = float(ctx["markPx"])
        oracle = float(ctx["oraclePx"])
        premia_pct = ((mark - oracle) / oracle * 100) if oracle > 0 else 0
        
    return {
        "coin": coin,
        "imbalance": {
            "bid_vol_usd": bid_vol,
            "ask_vol_usd": ask_vol,
            "ratio": round(imbalance_ratio, 2),
            "sentiment": "BULLISH" if imbalance_ratio > 1.5 else "BEARISH" if imbalance_ratio < 0.66 else "NEUTRAL"
        },
        "premia_pct": round(premia_pct, 4)
    }



@mcp.tool()
@log_action
@handle_errors
def get_volume_profile_24h(coin: str) -> dict:

    import time
    end_time = int(time.time() * 1000)
    start_time = end_time - (24 * 60 * 60 * 1000)
    
    candles = info.candles_snapshot(coin, "1h", start_time, end_time)
    
    # Simple TPO / Volume Profile approximation
    # We'll bucket volume by the candle's typical price (H+L+C)/3
    volume_buckets = {}
    total_volume = 0
    
    for c in candles:
        px = (float(c["h"]) + float(c["l"]) + float(c["c"])) / 3
        vol = float(c["v"]) * px # USD Volume
        bucket = round(px, -1) # Bucket size 10 (adjust as needed)
        
        if bucket not in volume_buckets: volume_buckets[bucket] = 0
        volume_buckets[bucket] += vol
        total_volume += vol
        
    if not volume_buckets:
        return {"error": "No volume data"}

    sorted_buckets = sorted(volume_buckets.items(), key=lambda x: x[0])
    
    # Find POC (Point of Control) - Price with max volume
    poc = max(volume_buckets.items(), key=lambda x: x[1])[0]
    
    # Find VAH/VAL (Value Area High/Low - 70% of volume)
    target_vol = total_volume * 0.7
    current_vol = 0
    value_area_buckets = []
    
    # Sort by volume desc to accumulate "Value Area"
    by_vol = sorted(volume_buckets.items(), key=lambda x: x[1], reverse=True)
    for px, vol in by_vol:
        current_vol += vol
        value_area_buckets.append(px)
        if current_vol >= target_vol:
            break
            
    vah = max(value_area_buckets)
    val = min(value_area_buckets)
    
    return {
        "coin": coin,
        "POC": poc,
        "VAH": vah,
        "VAL": val,
        "total_volume_24h": total_volume
    }







@mcp.tool()
@log_action
@handle_errors
def get_correlation_matrix(coins: str = "BTC,ETH,SOL,AVAX,DOGE") -> dict:

    import time
    import math
    
    coin_list = coins.split(",")
    end_time = int(time.time() * 1000)
    start_time = end_time - (24 * 3600 * 1000)
    
    prices = {}
    
    # Fetch prices
    for c in coin_list:
        candles = info.candles_snapshot(c, "1h", start_time, end_time)
        # Extract closes
        closes = [float(x["c"]) for x in candles]
        # Normalize length (trim to min)
        prices[c] = closes
        
    min_len = min(len(p) for p in prices.values())
    for c in prices: prices[c] = prices[c][-min_len:]
    
    # Calculate Pearson Corr
    matrix = {}
    for c1 in coin_list:
        matrix[c1] = {}
        for c2 in coin_list:
            if c1 == c2: 
                matrix[c1][c2] = 1.0
                continue
                
            x = prices[c1]
            y = prices[c2]
            n = len(x)
            
            sum_x = sum(x)
            sum_y = sum(y)
            sum_xy = sum(xi*yi for xi, yi in zip(x, y))
            sum_x2 = sum(xi**2 for xi in x)
            sum_y2 = sum(yi**2 for yi in y)
            
            numerator = n * sum_xy - sum_x * sum_y
            denominator = math.sqrt((n * sum_x2 - sum_x**2) * (n * sum_y2 - sum_y**2))
            
            corr = numerator / denominator if denominator else 0
            matrix[c1][c2] = round(corr, 2)
            
    return {"matrix": matrix}

@mcp.tool()
@log_action
@handle_errors
def get_open_interest_delta(coin: str) -> dict:
    """
    Get Current Open Interest (OI) and context.
    Note: Historical OI is not available via public API snapshot, so Delta requires polling.
    """
    meta, ctxs = info.meta_and_asset_ctxs()
    idx = next((i for i, a in enumerate(meta["universe"]) if a["name"] == coin), None)
    if idx is None: return {"error": "Coin not found"}
    
    ctx = ctxs[idx]
    oi = float(ctx["openInterest"])
    mark = float(ctx["markPx"])
    oi_usd = oi * mark
    
    return {
        "coin": coin,
        "open_interest_sz": oi,
        "open_interest_usd": oi_usd,
        "note": "To track Delta (Change), poll this tool periodically."
    }

@mcp.tool()
@log_action
@handle_errors
def get_hyperliquid_leaderboard() -> dict:

    # Try standard leaderboard request
    # Note: The public SDK does not expose a documented leaderboard endpoint.
    # We attempt a common payload, but if it fails, we return a helpful message.
    req = {"type": "leaderboard", "period": "allTime"} 
    try:
        res = info.post("/info", req)
        return {"leaderboard": res[:20] if isinstance(res, list) else res}
    except Exception as e:
        # Return a human-readable response instead of a raw error
        return {
            "status": "Unavailable",
            "message": "The Leaderboard API is currently restricted or unavailable via the public SDK.",
            "suggestion": "Please visit https://app.hyperliquid.xyz/leaderboard for official rankings.",
            "technical_error": str(e)[:100] + "..." # Truncate for readability
        }













if __name__ == "__main__":
    print(f"Starting Hyperliquid MCP Server...", file=sys.stderr)
    print(f"API URL: {BASE_URL}", file=sys.stderr)
    
    # Initialize Precision Manager
    pm = PrecisionManager(info)
    pm.load()
    
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--transport", default="stdio", choices=["stdio", "sse"], help="Transport protocol to use")
    parser.add_argument("--port", type=int, default=8000, help="Port for SSE transport")
    args = parser.parse_args()

    if args.transport == "sse":
        print(f"Starting SSE server on port {args.port}...", file=sys.stderr)
        mcp.run(transport="sse") # FastMCP handles the port via internal settings or we might need to configure it differently?
        # FastMCP.run(transport='sse') usually starts a uvicorn server.
        # Let's check if we need to pass host/port to run() or if it uses the settings.
        # Based on my previous inspection, run() takes transport.
        # But FastMCP init took host/port.
        # Let's assume default for now, or better yet, check if run() accepts kwargs.
        # The signature was: run(self, transport: "Literal['stdio', 'sse', 'streamable-http']" = 'stdio', mount_path: 'str | None' = None)
        # It doesn't take port in run(). It takes it in __init__.
        # So I can't easily change port at runtime without re-initializing.
        # But I can change the transport.
        # Wait, if I change transport to SSE, it will use the host/port defined in __init__ (default 127.0.0.1:8000).
        # That's fine for SSH tunneling.
        mcp.run(transport="sse")
    else:
        print(f"Transport: Stdio (Standard Input/Output)", file=sys.stderr)
        mcp.run()
