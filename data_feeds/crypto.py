import os
import requests
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from dotenv import load_dotenv
from pycoingecko import CoinGeckoAPI

# Load environment variables
load_dotenv()

COINGECKO_API_KEY = os.getenv("COINGECKO_API_KEY")
BASE_URL = "https://api.coingecko.com/api/v3"

# Default coins: BTC, ETH, SOL, BNB
DEFAULT_COINS = ["bitcoin", "ethereum", "solana", "binancecoin"]

COIN_SYMBOLS = {
    "bitcoin": "BTC",
    "ethereum": "ETH",
    "solana": "SOL",
    "binancecoin": "BNB",
}

# Initialize CoinGecko client.
# api_key= forces pro-api.coingecko.com (paid plan).
# demo_api_key= keeps the free api.coingecko.com URL.
if COINGECKO_API_KEY:
    cg = CoinGeckoAPI(demo_api_key=COINGECKO_API_KEY)
else:
    cg = CoinGeckoAPI()


def _headers() -> dict:
    """Return request headers, including API key if configured."""
    h = {"accept": "application/json"}
    if COINGECKO_API_KEY:
        h["x-cg-demo-api-key"] = COINGECKO_API_KEY
    return h


def get_price_data(coin_id: str) -> dict:
    """
    Fetch current price info for a single coin.

    Args:
        coin_id: CoinGecko coin ID (e.g. 'bitcoin')

    Returns:
        dict with keys: price, market_cap, volume_24h, change_24h,
                        change_pct_24h, last_updated — or {} on error.
    """
    try:
        raw = cg.get_price(
            ids=coin_id,
            vs_currencies="usd",
            include_market_cap=True,
            include_24hr_vol=True,
            include_24hr_change=True,
            include_last_updated_at=True,
        )
        coin_raw = raw.get(coin_id, {})
        if not coin_raw:
            return {}

        price = coin_raw.get("usd", 0)
        change_24h_usd = price * (coin_raw.get("usd_24h_change", 0) / 100)

        return {
            "coin_id": coin_id,
            "symbol": COIN_SYMBOLS.get(coin_id, coin_id.upper()),
            "price": price,
            "market_cap": coin_raw.get("usd_market_cap", 0),
            "volume_24h": coin_raw.get("usd_24h_vol", 0),
            "change_24h": change_24h_usd,
            "change_pct_24h": coin_raw.get("usd_24h_change", 0),
            "last_updated": datetime.fromtimestamp(
                coin_raw.get("last_updated_at", 0), tz=timezone.utc
            ).isoformat(),
        }
    except Exception as e:
        print(f"[crypto] Error fetching price data for {coin_id}: {e}")
        return {}


def get_ohlcv(coin_id: str, days: int = 500) -> List[Dict[str, Any]]:
    """
    Fetch OHLCV candle data for a coin.

    CoinGecko's OHLC endpoint returns [timestamp, open, high, low, close].
    Volume is fetched separately from the market_chart endpoint and merged
    by nearest timestamp so each returned record is a complete OHLCV row.

    Args:
        coin_id: CoinGecko coin ID
        days:    Lookback window — 1, 7, 14, 30, 90, 180, 365, or 'max'

    Returns:
        List of dicts: {timestamp, open, high, low, close, volume}
        Sorted ascending by timestamp. Returns [] on error.
    """
    try:
        # OHLC candles: [timestamp_ms, open, high, low, close]
        ohlc_raw = cg.get_coin_ohlc_by_id(id=coin_id, vs_currency="usd", days=days)

        # Volume from market_chart: list of [timestamp_ms, volume]
        chart_url = (
            f"{BASE_URL}/coins/{coin_id}/market_chart"
            f"?vs_currency=usd&days={days}&interval=daily"
        )
        chart_resp = requests.get(chart_url, headers=_headers(), timeout=15)
        chart_resp.raise_for_status()
        chart_data = chart_resp.json()
        volume_list = chart_data.get("total_volumes", [])

        # Build a lookup: day-bucket (seconds) -> volume
        def _day_bucket(ts_ms: int) -> int:
            return (ts_ms // 1000) // 86400

        vol_map: Dict[int, float] = {}
        for ts_ms, vol in volume_list:
            vol_map[_day_bucket(ts_ms)] = vol

        candles = []
        for row in ohlc_raw:
            ts_ms, open_, high, low, close = row
            dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
            volume = vol_map.get(_day_bucket(ts_ms), 0.0)
            candles.append(
                {
                    "timestamp": dt.isoformat(),
                    "timestamp_ms": ts_ms,
                    "open": open_,
                    "high": high,
                    "low": low,
                    "close": close,
                    "volume": volume,
                }
            )

        return sorted(candles, key=lambda c: c["timestamp_ms"])

    except Exception as e:
        print(f"[crypto] Error fetching OHLCV for {coin_id}: {e}")
        return []


def get_market_summary(coins: Optional[List[str]] = None) -> Dict[str, Any]:
    """
    Fetch a market summary for multiple coins in a single API call.

    Args:
        coins: List of CoinGecko coin IDs. Defaults to DEFAULT_COINS.

    Returns:
        dict keyed by coin_id, each value is a price-data dict.
        Coins that fail to fetch are omitted.
    """
    if coins is None:
        coins = DEFAULT_COINS

    try:
        ids_param = ",".join(coins)
        raw = cg.get_price(
            ids=ids_param,
            vs_currencies="usd",
            include_market_cap=True,
            include_24hr_vol=True,
            include_24hr_change=True,
            include_last_updated_at=True,
        )
    except Exception as e:
        print(f"[crypto] Error fetching market summary: {e}")
        # Attempt per-coin fallback
        raw = {}
        for coin in coins:
            data = get_price_data(coin)
            if data:
                raw[coin] = {
                    "usd": data["price"],
                    "usd_market_cap": data["market_cap"],
                    "usd_24h_vol": data["volume_24h"],
                    "usd_24h_change": data["change_pct_24h"],
                    "last_updated_at": 0,
                }

    summary = {}
    for coin_id in coins:
        coin_raw = raw.get(coin_id, {})
        if not coin_raw:
            print(f"[crypto] No data returned for {coin_id}")
            continue

        price = coin_raw.get("usd", 0)
        change_pct = coin_raw.get("usd_24h_change", 0)
        change_usd = price * (change_pct / 100)

        summary[coin_id] = {
            "coin_id": coin_id,
            "symbol": COIN_SYMBOLS.get(coin_id, coin_id.upper()),
            "price": price,
            "market_cap": coin_raw.get("usd_market_cap", 0),
            "volume_24h": coin_raw.get("usd_24h_vol", 0),
            "change_24h": change_usd,
            "change_pct_24h": change_pct,
            "last_updated": datetime.fromtimestamp(
                coin_raw.get("last_updated_at", 0), tz=timezone.utc
            ).isoformat(),
        }

    return summary


# ── Module load confirmation ──────────────────────────────────────────────────
print("[crypto] Crypto data feed module loaded successfully.")


# ── Standalone test ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("  AI Trading Agent — Crypto Data Feed Test")
    print("=" * 60)

    print("\n[1] Market Summary (BTC, ETH, SOL, BNB)")
    print("-" * 60)
    summary = get_market_summary()
    if summary:
        for coin_id, data in summary.items():
            direction = "+" if data["change_pct_24h"] >= 0 else ""
            print(
                f"  {data['symbol']:>4}  "
                f"${data['price']:>12,.2f}  "
                f"24h: {direction}{data['change_pct_24h']:.2f}%  "
                f"Vol: ${data['volume_24h']:>14,.0f}  "
                f"MCap: ${data['market_cap']:>16,.0f}"
            )
    else:
        print("  ERROR: Could not fetch market summary.")

    print("\n[2] Individual Price Data — bitcoin")
    print("-" * 60)
    btc = get_price_data("bitcoin")
    if btc:
        for key, val in btc.items():
            print(f"  {key:<18}: {val}")
    else:
        print("  ERROR: Could not fetch BTC price data.")

    print("\n[3] OHLCV — ethereum (last 7 days, first 3 candles)")
    print("-" * 60)
    eth_ohlcv = get_ohlcv("ethereum", days=7)
    if eth_ohlcv:
        for candle in eth_ohlcv[:3]:
            print(
                f"  {candle['timestamp']}  "
                f"O:{candle['open']:>10,.2f}  "
                f"H:{candle['high']:>10,.2f}  "
                f"L:{candle['low']:>10,.2f}  "
                f"C:{candle['close']:>10,.2f}  "
                f"V:{candle['volume']:>16,.0f}"
            )
    else:
        print("  ERROR: Could not fetch ETH OHLCV data.")

    print("\n" + "=" * 60)
    print("  Test complete.")
    print("=" * 60 + "\n")
