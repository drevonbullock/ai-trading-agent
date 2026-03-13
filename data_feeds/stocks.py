import os
import requests
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

ALPACA_API_KEY = os.getenv("ALPACA_API_KEY")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")

PAPER_BASE_URL = "https://paper-api.alpaca.markets/v2"
DATA_BASE_URL = "https://data.alpaca.markets/v2"

# Default symbols: SPY, AAPL, TSLA, NVDA
DEFAULT_SYMBOLS = ["SPY", "AAPL", "TSLA", "NVDA"]


def _headers() -> Dict[str, str]:
    """Return Alpaca auth headers."""
    if not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
        raise EnvironmentError(
            "[stocks] ALPACA_API_KEY or ALPACA_SECRET_KEY not set in .env"
        )
    return {
        "APCA-API-KEY-ID": ALPACA_API_KEY,
        "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY,
        "accept": "application/json",
    }


def _get(url: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Shared GET helper with error handling.

    Raises:
        requests.HTTPError on non-2xx responses.
    """
    resp = requests.get(url, headers=_headers(), params=params, timeout=15)
    resp.raise_for_status()
    return resp.json()


def get_price_data(symbol: str) -> Dict[str, Any]:
    """
    Fetch the latest trade, quote, and daily bar for a symbol.

    Combines:
      - /stocks/{symbol}/trades/latest  → last trade price & size
      - /stocks/{symbol}/quotes/latest  → bid/ask spread
      - /stocks/{symbol}/bars/latest    → today's OHLCV bar (daily change)

    Args:
        symbol: Ticker symbol (e.g. 'AAPL')

    Returns:
        dict with keys: symbol, price, bid, ask, volume, open,
                        change, change_pct — or {} on error.
    """
    try:
        feed_params = {"feed": "iex"}  # IEX is available on free/paper accounts

        trade_data = _get(
            f"{DATA_BASE_URL}/stocks/{symbol}/trades/latest", params=feed_params
        )
        quote_data = _get(
            f"{DATA_BASE_URL}/stocks/{symbol}/quotes/latest", params=feed_params
        )
        bar_data = _get(
            f"{DATA_BASE_URL}/stocks/{symbol}/bars/latest",
            params={"timeframe": "1Day", "feed": "iex"},
        )

        trade = trade_data.get("trade", {})
        quote = quote_data.get("quote", {})
        bar = bar_data.get("bar", {})

        price = trade.get("p", 0.0)
        open_price = bar.get("o", 0.0)
        change = price - open_price if open_price else 0.0
        change_pct = (change / open_price * 100) if open_price else 0.0

        return {
            "symbol": symbol,
            "price": price,
            "size": trade.get("s", 0),
            "bid": quote.get("bp", 0.0),
            "ask": quote.get("ap", 0.0),
            "open": open_price,
            "high": bar.get("h", 0.0),
            "low": bar.get("l", 0.0),
            "volume": bar.get("v", 0),
            "vwap": bar.get("vw", 0.0),
            "change": change,
            "change_pct": change_pct,
            "timestamp": trade.get("t", ""),
        }
    except Exception as e:
        print(f"[stocks] Error fetching price data for {symbol}: {e}")
        return {}


def get_ohlcv(
    symbol: str,
    timeframe: str = "1Day",
    limit: int = 7,
) -> List[Dict[str, Any]]:
    """
    Fetch historical OHLCV bars for a symbol.

    Args:
        symbol:    Ticker symbol (e.g. 'AAPL')
        timeframe: Bar size — '1Min', '5Min', '15Min', '1Hour', '1Day'
        limit:     Number of bars to return (most recent first from API,
                   returned here sorted ascending by timestamp)

    Returns:
        List of dicts: {timestamp, open, high, low, close, volume, vwap}
        Sorted ascending by timestamp. Returns [] on error.
    """
    try:
        params = {
            "timeframe": timeframe,
            "limit": limit,
            "sort": "desc",
            "feed": "iex",
        }
        data = _get(f"{DATA_BASE_URL}/stocks/{symbol}/bars", params=params)
        raw_bars = data.get("bars", [])

        candles = []
        for bar in raw_bars:
            candles.append(
                {
                    "timestamp": bar.get("t", ""),
                    "open": bar.get("o", 0.0),
                    "high": bar.get("h", 0.0),
                    "low": bar.get("l", 0.0),
                    "close": bar.get("c", 0.0),
                    "volume": bar.get("v", 0),
                    "vwap": bar.get("vw", 0.0),
                }
            )

        # Return chronological order (API returns desc)
        return list(reversed(candles))

    except Exception as e:
        print(f"[stocks] Error fetching OHLCV for {symbol}: {e}")
        return []


def get_market_summary(symbols: Optional[List[str]] = None) -> Dict[str, Any]:
    """
    Fetch latest bars for multiple symbols in a single API call.

    Alpaca's /stocks/bars endpoint accepts a comma-separated symbols list,
    so this is one request regardless of how many tickers are requested.

    Args:
        symbols: List of ticker symbols. Defaults to DEFAULT_SYMBOLS.

    Returns:
        dict keyed by symbol, each value is a price-data dict.
        Symbols with no data returned are omitted.
    """
    if symbols is None:
        symbols = DEFAULT_SYMBOLS

    try:
        params = {
            "symbols": ",".join(symbols),
            "timeframe": "1Day",
            "limit": 2,       # Need 2 bars: today + yesterday for daily change
            "sort": "desc",
            "feed": "iex",
        }
        data = _get(f"{DATA_BASE_URL}/stocks/bars", params=params)
        bars_by_symbol: Dict[str, List[Any]] = data.get("bars", {})

    except Exception as e:
        print(f"[stocks] Error fetching market summary: {e}")
        # Per-symbol fallback
        summary: Dict[str, Any] = {}
        for sym in symbols:
            result = get_price_data(sym)
            if result:
                summary[sym] = result
        return summary

    summary = {}
    for sym in symbols:
        sym_bars = bars_by_symbol.get(sym, [])
        if not sym_bars:
            print(f"[stocks] No bar data returned for {sym}")
            continue

        today = sym_bars[0]    # most recent (sort=desc)
        prev = sym_bars[1] if len(sym_bars) > 1 else {}

        close = today.get("c", 0.0)
        prev_close = prev.get("c", 0.0) if prev else today.get("o", 0.0)
        change = close - prev_close if prev_close else 0.0
        change_pct = (change / prev_close * 100) if prev_close else 0.0

        summary[sym] = {
            "symbol": sym,
            "open": today.get("o", 0.0),
            "high": today.get("h", 0.0),
            "low": today.get("l", 0.0),
            "close": close,
            "volume": today.get("v", 0),
            "vwap": today.get("vw", 0.0),
            "prev_close": prev_close,
            "change": change,
            "change_pct": change_pct,
            "timestamp": today.get("t", ""),
        }

    return summary


# ── Module load confirmation ──────────────────────────────────────────────────
print("[stocks] Stock data feed module loaded successfully.")


# ── Standalone test ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("  AI Trading Agent — Stock Data Feed Test")
    print("=" * 60)

    print("\n[1] Market Summary (SPY, AAPL, TSLA, NVDA)")
    print("-" * 60)
    summary = get_market_summary()
    if summary:
        for sym, data in summary.items():
            direction = "+" if data["change_pct"] >= 0 else ""
            print(
                f"  {sym:<4}  "
                f"${data['close']:>9,.2f}  "
                f"24h: {direction}{data['change_pct']:.2f}%  "
                f"Vol: {data['volume']:>12,.0f}  "
                f"VWAP: ${data['vwap']:>9,.2f}"
            )
    else:
        print("  ERROR: Could not fetch market summary.")

    print("\n[2] Individual Price Data — AAPL")
    print("-" * 60)
    aapl = get_price_data("AAPL")
    if aapl:
        for key, val in aapl.items():
            if isinstance(val, float):
                print(f"  {key:<14}: {val:,.4f}")
            else:
                print(f"  {key:<14}: {val}")
    else:
        print("  ERROR: Could not fetch AAPL price data.")

    print("\n[3] OHLCV — SPY (last 7 daily bars)")
    print("-" * 60)
    spy_ohlcv = get_ohlcv("SPY", timeframe="1Day", limit=7)
    if spy_ohlcv:
        print(f"  {'Date':<26} {'Open':>9} {'High':>9} {'Low':>9} {'Close':>9} {'Volume':>12}")
        print(f"  {'-'*24} {'-'*9} {'-'*9} {'-'*9} {'-'*9} {'-'*12}")
        for candle in spy_ohlcv:
            print(
                f"  {candle['timestamp']:<26}"
                f"  {candle['open']:>8,.2f}"
                f"  {candle['high']:>8,.2f}"
                f"  {candle['low']:>8,.2f}"
                f"  {candle['close']:>8,.2f}"
                f"  {candle['volume']:>12,.0f}"
            )
    else:
        print("  ERROR: Could not fetch SPY OHLCV data.")

    print("\n" + "=" * 60)
    print("  Test complete.")
    print("=" * 60 + "\n")
