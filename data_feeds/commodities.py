import os
import time
import requests
from typing import Any, Dict, List, Optional
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

ALPHA_VANTAGE_API_KEY = os.getenv("ALPHA_VANTAGE_API_KEY")
BASE_URL = "https://www.alphavantage.co/query"

# Default commodity symbols using Alpha Vantage's commodity functions
DEFAULT_SYMBOLS = ["WTI", "NATURAL_GAS", "COPPER", "WHEAT"]

# Map of commodity symbols to their Alpha Vantage function names and display labels.
# Symbols not in this map are treated as equity tickers (TIME_SERIES_DAILY).
COMMODITY_FUNCTIONS: Dict[str, Dict[str, str]] = {
    "WTI":         {"function": "WTI",         "label": "Crude Oil (WTI)",  "unit": "USD/barrel"},
    "BRENT":       {"function": "BRENT",        "label": "Crude Oil (Brent)","unit": "USD/barrel"},
    "NATURAL_GAS": {"function": "NATURAL_GAS",  "label": "Natural Gas",      "unit": "USD/MMBtu"},
    "COPPER":      {"function": "COPPER",        "label": "Copper",           "unit": "USD/pound"},
    "ALUMINUM":    {"function": "ALUMINUM",      "label": "Aluminum",         "unit": "USD/metric ton"},
    "WHEAT":       {"function": "WHEAT",         "label": "Wheat",            "unit": "USD/bushel"},
    "CORN":        {"function": "CORN",          "label": "Corn",             "unit": "USD/bushel"},
    "COTTON":      {"function": "COTTON",        "label": "Cotton",           "unit": "USD/pound"},
    "SUGAR":       {"function": "SUGAR",         "label": "Sugar",            "unit": "USD/pound"},
    "COFFEE":      {"function": "COFFEE",        "label": "Coffee",           "unit": "USD/pound"},
}


def _is_commodity(symbol: str) -> bool:
    return symbol.upper() in COMMODITY_FUNCTIONS


def _get(params: Dict[str, Any]) -> Dict[str, Any]:
    """Shared GET helper — injects API key and raises on non-2xx responses."""
    if not ALPHA_VANTAGE_API_KEY:
        raise EnvironmentError("[commodities] ALPHA_VANTAGE_API_KEY not set in .env")
    params["apikey"] = ALPHA_VANTAGE_API_KEY
    resp = requests.get(BASE_URL, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    # Alpha Vantage returns error messages inside the JSON body
    if "Error Message" in data:
        raise ValueError(f"Alpha Vantage error: {data['Error Message']}")
    if "Note" in data:
        # Rate-limit notice (5 calls/min on free tier)
        raise RuntimeError(f"Alpha Vantage rate limit hit: {data['Note']}")
    if "Information" in data:
        raise RuntimeError(f"Alpha Vantage info: {data['Information']}")
    return data


def _get_commodity_series(symbol: str, interval: str = "monthly") -> List[Dict[str, Any]]:
    """
    Fetch a raw time-series for a native commodity symbol.

    Alpha Vantage commodity endpoints (WTI, BRENT, etc.) return:
      { "data": [ {"date": "YYYY-MM-DD", "value": "..."}, ... ] }

    Args:
        symbol:   One of the keys in COMMODITY_FUNCTIONS
        interval: 'daily', 'weekly', or 'monthly'

    Returns:
        List of {"date", "value"} dicts, newest-first.
    """
    func = COMMODITY_FUNCTIONS[symbol.upper()]["function"]
    data = _get({"function": func, "interval": interval})
    return data.get("data", [])


def _get_equity_series(symbol: str, outputsize: str = "compact") -> Dict[str, Any]:
    """
    Fetch TIME_SERIES_DAILY for an equity/ETF ticker (e.g. GLD, USO).

    Returns the 'Time Series (Daily)' sub-dict keyed by date string.
    """
    data = _get({"function": "TIME_SERIES_DAILY", "symbol": symbol, "outputsize": outputsize})
    return data.get("Time Series (Daily)", {})


def get_price_data(symbol: str) -> Dict[str, Any]:
    """
    Fetch current price and daily change for a commodity or ETF symbol.

    For native commodity symbols (WTI, COPPER, etc.) the latest and
    previous monthly data points are used to compute change.
    For equity tickers (GLD, USO) the latest daily bar is used.

    Args:
        symbol: Commodity symbol or equity ticker

    Returns:
        dict with keys: symbol, label, price, prev_price, change,
                        change_pct, unit, date — or {} on error.
    """
    sym = symbol.upper()
    try:
        if _is_commodity(sym):
            meta = COMMODITY_FUNCTIONS[sym]
            # Use monthly for commodities — daily is only on paid plans
            series = _get_commodity_series(sym, interval="monthly")
            if len(series) < 2:
                print(f"[commodities] Insufficient data for {sym}")
                return {}

            latest = series[0]
            previous = series[1]
            price = float(latest["value"])
            prev_price = float(previous["value"])
            change = price - prev_price
            change_pct = (change / prev_price * 100) if prev_price else 0.0

            return {
                "symbol": sym,
                "label": meta["label"],
                "price": price,
                "prev_price": prev_price,
                "change": change,
                "change_pct": change_pct,
                "unit": meta["unit"],
                "interval": "monthly",
                "date": latest["date"],
            }
        else:
            # Equity/ETF path
            ts = _get_equity_series(sym, outputsize="compact")
            if not ts:
                print(f"[commodities] No time-series data for {sym}")
                return {}

            dates = sorted(ts.keys(), reverse=True)
            if len(dates) < 2:
                print(f"[commodities] Insufficient data for {sym}")
                return {}

            today_bar = ts[dates[0]]
            prev_bar = ts[dates[1]]
            price = float(today_bar["4. close"])
            prev_price = float(prev_bar["4. close"])
            change = price - prev_price
            change_pct = (change / prev_price * 100) if prev_price else 0.0

            return {
                "symbol": sym,
                "label": sym,
                "price": price,
                "prev_price": prev_price,
                "change": change,
                "change_pct": change_pct,
                "unit": "USD",
                "interval": "daily",
                "date": dates[0],
                "open": float(today_bar["1. open"]),
                "high": float(today_bar["2. high"]),
                "low": float(today_bar["3. low"]),
                "volume": int(today_bar["5. volume"]),
            }

    except Exception as e:
        print(f"[commodities] Error fetching price data for {sym}: {e}")
        return {}


def get_ohlcv(
    symbol: str,
    outputsize: str = "compact",
) -> List[Dict[str, Any]]:
    """
    Return the last 7 data points for a commodity or ETF symbol.

    For native commodity symbols, the series is monthly (daily requires
    a paid Alpha Vantage plan) and up to 7 months are returned.
    For equity tickers, daily OHLCV bars are returned.

    Args:
        symbol:     Commodity symbol or equity ticker
        outputsize: 'compact' (last 100 bars) or 'full' — equity only

    Returns:
        List of dicts sorted ascending by date. Returns [] on error.
    """
    sym = symbol.upper()
    try:
        if _is_commodity(sym):
            series = _get_commodity_series(sym, interval="monthly")
            # newest-first from API — take last 7, then reverse to ascending
            recent = series[:7]
            candles = []
            for point in reversed(recent):
                val = float(point["value"])
                candles.append(
                    {
                        "date": point["date"],
                        # Commodity series has only one price point per interval
                        "open": val,
                        "high": val,
                        "low": val,
                        "close": val,
                        "volume": None,  # Not available from commodity endpoints
                        "interval": "monthly",
                    }
                )
            return candles
        else:
            ts = _get_equity_series(sym, outputsize=outputsize)
            if not ts:
                return []

            dates = sorted(ts.keys(), reverse=True)[:7]
            candles = []
            for date in reversed(dates):
                bar = ts[date]
                candles.append(
                    {
                        "date": date,
                        "open": float(bar["1. open"]),
                        "high": float(bar["2. high"]),
                        "low": float(bar["3. low"]),
                        "close": float(bar["4. close"]),
                        "volume": int(bar["5. volume"]),
                        "interval": "daily",
                    }
                )
            return candles

    except Exception as e:
        print(f"[commodities] Error fetching OHLCV for {sym}: {e}")
        return []


def get_market_summary(
    symbols: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Fetch current price data for multiple commodity symbols.

    Note: Alpha Vantage's free tier allows 25 requests/day and 5/minute.
    Each symbol in the list costs one request, so keep the list small or
    add delays between calls if you hit rate limits.

    Args:
        symbols: List of commodity symbols or ETF tickers.
                 Defaults to DEFAULT_SYMBOLS (WTI, NATURAL_GAS, COPPER, WHEAT).

    Returns:
        dict keyed by symbol, each value is a price-data dict.
        Symbols that fail are omitted.
    """
    if symbols is None:
        symbols = DEFAULT_SYMBOLS

    summary: Dict[str, Any] = {}
    for i, sym in enumerate(symbols):
        if i > 0:
            time.sleep(1)
        result = get_price_data(sym)
        if result:
            summary[sym.upper()] = result
        else:
            print(f"[commodities] Skipping {sym} — no data returned.")

    return summary


# ── Module load confirmation ──────────────────────────────────────────────────
print("[commodities] Commodities data feed module loaded successfully.")


# ── Standalone test ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("  AI Trading Agent — Commodities Data Feed Test")
    print("=" * 60)

    print("\n[1] Market Summary (WTI, NATURAL_GAS, COPPER, WHEAT)")
    print("-" * 60)
    summary = get_market_summary()
    if summary:
        for sym, data in summary.items():
            direction = "+" if data["change_pct"] >= 0 else ""
            print(
                f"  {data['label']:<22}  "
                f"${data['price']:>9,.3f}  "
                f"Chg: {direction}{data['change_pct']:.2f}%  "
                f"({data['unit']})  "
                f"[{data['date']}]"
            )
    else:
        print("  ERROR: Could not fetch market summary.")

    print("\n[2] Individual Price Data — WTI Crude Oil")
    print("-" * 60)
    wti = get_price_data("WTI")
    if wti:
        for key, val in wti.items():
            print(f"  {key:<14}: {val}")
    else:
        print("  ERROR: Could not fetch WTI price data.")

    print("\n[3] OHLCV — COPPER  (last 7 monthly data points)")
    print("-" * 60)
    copper_ohlcv = get_ohlcv("COPPER")
    if copper_ohlcv:
        for candle in copper_ohlcv:
            print(
                f"  {candle['date']}  "
                f"Close: ${candle['close']:>7,.4f}  "
                f"({candle['interval']})"
            )
    else:
        print("  ERROR: Could not fetch COPPER OHLCV data.")

    print("\n[4] ETF Proxy — GLD  (last 7 daily bars)")
    print("-" * 60)
    gld_ohlcv = get_ohlcv("GLD")
    if gld_ohlcv:
        print(
            f"  {'Date':<12} {'Open':>8} {'High':>8} "
            f"{'Low':>8} {'Close':>8} {'Volume':>12}"
        )
        print(f"  {'-'*10} {'-'*8} {'-'*8} {'-'*8} {'-'*8} {'-'*12}")
        for candle in gld_ohlcv:
            print(
                f"  {candle['date']:<12}"
                f"  {candle['open']:>7,.2f}"
                f"  {candle['high']:>7,.2f}"
                f"  {candle['low']:>7,.2f}"
                f"  {candle['close']:>7,.2f}"
                f"  {candle['volume']:>12,}"
            )
    else:
        print("  ERROR: Could not fetch GLD OHLCV data.")

    print("\n" + "=" * 60)
    print("  Test complete.")
    print("=" * 60 + "\n")
