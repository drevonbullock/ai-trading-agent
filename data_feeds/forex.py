import os
import requests
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

OANDA_API_KEY = os.getenv("OANDA_API_KEY")
OANDA_ACCOUNT_ID = os.getenv("OANDA_ACCOUNT_ID")

BASE_URL = "https://api-fxpractice.oanda.com/v3"

# Default forex pairs (XAU_USD = spot gold, priced in USD)
DEFAULT_INSTRUMENTS = ["EUR_USD", "GBP_USD", "USD_JPY", "XAU_USD"]

# Instruments where price has more decimal places (JPY pairs are 2 dp, most others 4-5)
JPY_PAIRS = {"USD_JPY", "EUR_JPY", "GBP_JPY", "AUD_JPY", "CAD_JPY", "CHF_JPY"}


def _headers() -> Dict[str, str]:
    """Return OANDA auth headers."""
    if not OANDA_API_KEY:
        raise EnvironmentError("[forex] OANDA_API_KEY not set in .env")
    return {
        "Authorization": f"Bearer {OANDA_API_KEY}",
        "Content-Type": "application/json",
        "Accept-Datetime-Format": "RFC3339",
    }


def _get(url: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Shared GET helper — raises on non-2xx responses."""
    resp = requests.get(url, headers=_headers(), params=params, timeout=15)
    resp.raise_for_status()
    return resp.json()


def _pip_decimals(instrument: str) -> int:
    """Return the number of decimal places to display for a given instrument."""
    return 2 if instrument in JPY_PAIRS else 5


def get_price_data(instrument: str) -> Dict[str, Any]:
    """
    Fetch the current live bid/ask/mid price for a forex instrument.

    Uses the /accounts/{id}/pricing endpoint which returns real-time
    streaming prices for the given instrument(s).

    Args:
        instrument: OANDA instrument name (e.g. 'EUR_USD', 'XAU_USD')

    Returns:
        dict with keys: instrument, bid, ask, mid, spread, tradeable,
                        timestamp — or {} on error.
    """
    try:
        data = _get(
            f"{BASE_URL}/accounts/{OANDA_ACCOUNT_ID}/pricing",
            params={"instruments": instrument},
        )
        prices = data.get("prices", [])
        if not prices:
            print(f"[forex] No pricing data returned for {instrument}")
            return {}

        price = prices[0]
        bids = price.get("bids", [{}])
        asks = price.get("asks", [{}])

        bid = float(bids[0].get("price", 0)) if bids else 0.0
        ask = float(asks[0].get("price", 0)) if asks else 0.0
        mid = round((bid + ask) / 2, _pip_decimals(instrument))
        spread = round(ask - bid, _pip_decimals(instrument))

        return {
            "instrument": instrument,
            "bid": bid,
            "ask": ask,
            "mid": mid,
            "spread": spread,
            "tradeable": price.get("tradeable", False),
            "timestamp": price.get("time", ""),
        }
    except Exception as e:
        print(f"[forex] Error fetching price data for {instrument}: {e}")
        return {}


def get_ohlcv(
    instrument: str,
    granularity: str = "H4",
    count: int = 7,
) -> List[Dict[str, Any]]:
    """
    Fetch historical OHLCV candles for a forex instrument.

    OANDA candles include separate bid/ask/mid OHLC sets. This function
    returns the mid-price candles, which is the standard for analysis.

    Args:
        instrument:  OANDA instrument name (e.g. 'EUR_USD')
        granularity: Candle size — S5/S10/S30, M1/M5/M15/M30,
                     H1/H2/H3/H4/H6/H8/H12, D, W, M
        count:       Number of candles to return (most recent, ascending)

    Returns:
        List of dicts: {timestamp, open, high, low, close, volume}
        Sorted ascending by timestamp. Returns [] on error.
    """
    try:
        data = _get(
            f"{BASE_URL}/instruments/{instrument}/candles",
            params={
                "granularity": granularity,
                "count": count,
                "price": "M",  # M = mid-price candles
            },
        )
        raw_candles = data.get("candles", [])

        candles = []
        for c in raw_candles:
            mid = c.get("mid", {})
            candles.append(
                {
                    "timestamp": c.get("time", ""),
                    "open": float(mid.get("o", 0)),
                    "high": float(mid.get("h", 0)),
                    "low": float(mid.get("l", 0)),
                    "close": float(mid.get("c", 0)),
                    "volume": c.get("volume", 0),
                    "complete": c.get("complete", True),
                }
            )

        # OANDA returns candles in ascending order already
        return candles

    except Exception as e:
        print(f"[forex] Error fetching OHLCV for {instrument}: {e}")
        return []


def get_market_summary(
    instruments: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Fetch live pricing for multiple instruments in a single API call.

    OANDA's pricing endpoint accepts a comma-separated instruments list,
    so this is one request regardless of how many pairs are requested.

    Args:
        instruments: List of OANDA instrument names. Defaults to
                     DEFAULT_INSTRUMENTS (EUR_USD, GBP_USD, USD_JPY, XAU_USD).

    Returns:
        dict keyed by instrument, each value is a price-data dict.
        Instruments with no data are omitted.
    """
    if instruments is None:
        instruments = DEFAULT_INSTRUMENTS

    try:
        data = _get(
            f"{BASE_URL}/accounts/{OANDA_ACCOUNT_ID}/pricing",
            params={"instruments": ",".join(instruments)},
        )
        prices = data.get("prices", [])

    except Exception as e:
        print(f"[forex] Error fetching market summary: {e}")
        # Per-instrument fallback
        summary: Dict[str, Any] = {}
        for inst in instruments:
            result = get_price_data(inst)
            if result:
                summary[inst] = result
        return summary

    summary = {}
    for price in prices:
        inst = price.get("instrument", "")
        if not inst:
            continue

        bids = price.get("bids", [{}])
        asks = price.get("asks", [{}])
        bid = float(bids[0].get("price", 0)) if bids else 0.0
        ask = float(asks[0].get("price", 0)) if asks else 0.0
        mid = round((bid + ask) / 2, _pip_decimals(inst))
        spread = round(ask - bid, _pip_decimals(inst))

        summary[inst] = {
            "instrument": inst,
            "bid": bid,
            "ask": ask,
            "mid": mid,
            "spread": spread,
            "tradeable": price.get("tradeable", False),
            "timestamp": price.get("time", ""),
        }

    # Report any instruments that came back empty
    for inst in instruments:
        if inst not in summary:
            print(f"[forex] No data returned for {inst}")

    return summary


# ── Module load confirmation ──────────────────────────────────────────────────
print("[forex] Forex data feed module loaded successfully.")


# ── Standalone test ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("  AI Trading Agent — Forex Data Feed Test")
    print("=" * 60)

    print("\n[1] Market Summary (EUR_USD, GBP_USD, USD_JPY, XAU_USD)")
    print("-" * 60)
    summary = get_market_summary()
    if summary:
        for inst, data in summary.items():
            dp = _pip_decimals(inst)
            tradeable = "LIVE" if data["tradeable"] else "CLOSED"
            print(
                f"  {inst:<9}  "
                f"Bid: {data['bid']:.{dp}f}  "
                f"Ask: {data['ask']:.{dp}f}  "
                f"Mid: {data['mid']:.{dp}f}  "
                f"Spread: {data['spread']:.{dp}f}  "
                f"[{tradeable}]"
            )
    else:
        print("  ERROR: Could not fetch market summary.")

    print("\n[2] Individual Price Data — EUR_USD")
    print("-" * 60)
    eur_usd = get_price_data("EUR_USD")
    if eur_usd:
        for key, val in eur_usd.items():
            print(f"  {key:<14}: {val}")
    else:
        print("  ERROR: Could not fetch EUR_USD price data.")

    print("\n[3] OHLCV — GBP_USD  (7 x H4 candles)")
    print("-" * 60)
    gbp_ohlcv = get_ohlcv("GBP_USD", granularity="H4", count=7)
    if gbp_ohlcv:
        print(
            f"  {'Timestamp':<35} {'Open':>8} {'High':>8} "
            f"{'Low':>8} {'Close':>8} {'Vol':>7}"
        )
        print(f"  {'-'*33} {'-'*8} {'-'*8} {'-'*8} {'-'*8} {'-'*7}")
        for candle in gbp_ohlcv:
            status = "" if candle["complete"] else " *"
            print(
                f"  {candle['timestamp']:<35}"
                f"  {candle['open']:.5f}"
                f"  {candle['high']:.5f}"
                f"  {candle['low']:.5f}"
                f"  {candle['close']:.5f}"
                f"  {candle['volume']:>6}{status}"
            )
        print("  (* = incomplete/in-progress candle)")
    else:
        print("  ERROR: Could not fetch GBP_USD OHLCV data.")

    print("\n[4] OHLCV — XAU_USD  (7 x D candles — spot gold)")
    print("-" * 60)
    gold_ohlcv = get_ohlcv("XAU_USD", granularity="D", count=7)
    if gold_ohlcv:
        for candle in gold_ohlcv:
            print(
                f"  {candle['timestamp'][:10]}  "
                f"O:{candle['open']:>9,.3f}  "
                f"H:{candle['high']:>9,.3f}  "
                f"L:{candle['low']:>9,.3f}  "
                f"C:{candle['close']:>9,.3f}  "
                f"Vol:{candle['volume']:>6}"
            )
    else:
        print("  ERROR: Could not fetch XAU_USD OHLCV data.")

    print("\n" + "=" * 60)
    print("  Test complete.")
    print("=" * 60 + "\n")
