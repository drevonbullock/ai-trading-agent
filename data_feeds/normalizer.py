from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional

# ── Feed imports ──────────────────────────────────────────────────────────────
# Each import triggers the module's load-time print confirmation.
from data_feeds import crypto, stocks, forex, commodities


# ── Unified data model ────────────────────────────────────────────────────────

@dataclass
class MarketData:
    """
    Unified market record normalised from any of the four data feeds.

    Field availability by market:
      crypto      — symbol, market, price, volume, change_pct, timestamp
      stocks      — symbol, market, price, volume, change_pct, open, high, low, close, timestamp
      forex       — symbol, market, price, bid, ask, volume, timestamp
                    (volume = tick count from OANDA candles, not dollar volume)
      commodities — symbol, market, price, change_pct, timestamp
                    (open/high/low/close available for ETF proxies like GLD/USO)

    Fields that are not provided by a particular feed are set to None.
    """
    symbol:     str
    market:     str                  # "crypto" | "stocks" | "forex" | "commodities"
    price:      float                # Best single price: mid (forex), close (stocks), price otherwise

    bid:        Optional[float] = None
    ask:        Optional[float] = None
    volume:     Optional[float] = None
    change_pct: Optional[float] = None
    high:       Optional[float] = None
    low:        Optional[float] = None
    open:       Optional[float] = None
    close:      Optional[float] = None
    timestamp:  Optional[str]  = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ── Per-feed normalizers ──────────────────────────────────────────────────────

def _from_crypto(symbol: str, data: Dict[str, Any]) -> MarketData:
    """
    Normalise a crypto feed record.

    crypto.get_market_summary() keys:
      coin_id, symbol, price, market_cap, volume_24h,
      change_24h, change_pct_24h, last_updated
    """
    return MarketData(
        symbol=data.get("symbol", symbol),
        market="crypto",
        price=data.get("price", 0.0),
        bid=None,
        ask=None,
        volume=data.get("volume_24h"),
        change_pct=data.get("change_pct_24h"),
        high=None,
        low=None,
        open=None,
        close=None,
        timestamp=data.get("last_updated"),
    )


def _from_stocks(symbol: str, data: Dict[str, Any]) -> MarketData:
    """
    Normalise a stocks feed record.

    stocks.get_market_summary() keys:
      symbol, open, high, low, close, volume, vwap,
      prev_close, change, change_pct, timestamp
    """
    close = data.get("close", 0.0)
    return MarketData(
        symbol=data.get("symbol", symbol),
        market="stocks",
        price=close,
        bid=None,
        ask=None,
        volume=data.get("volume"),
        change_pct=data.get("change_pct"),
        high=data.get("high"),
        low=data.get("low"),
        open=data.get("open"),
        close=close,
        timestamp=data.get("timestamp"),
    )


def _from_forex(symbol: str, data: Dict[str, Any]) -> MarketData:
    """
    Normalise a forex feed record.

    forex.get_market_summary() keys:
      instrument, bid, ask, mid, spread, tradeable, timestamp
    """
    return MarketData(
        symbol=data.get("instrument", symbol),
        market="forex",
        price=data.get("mid", 0.0),
        bid=data.get("bid"),
        ask=data.get("ask"),
        volume=None,        # Live quote — no volume available at this level
        change_pct=None,    # Would require a prior close; not in summary response
        high=None,
        low=None,
        open=None,
        close=None,
        timestamp=data.get("timestamp"),
    )


def _from_commodities(symbol: str, data: Dict[str, Any]) -> MarketData:
    """
    Normalise a commodities feed record.

    commodities.get_market_summary() keys (native commodity):
      symbol, label, price, prev_price, change, change_pct, unit, interval, date

    Extra keys for ETF proxies (GLD, USO, …):
      open, high, low, volume  (in addition to the above)
    """
    return MarketData(
        symbol=data.get("symbol", symbol),
        market="commodities",
        price=data.get("price", 0.0),
        bid=None,
        ask=None,
        volume=data.get("volume"),          # Present for ETF proxies, None for native
        change_pct=data.get("change_pct"),
        high=data.get("high"),              # ETF proxies only
        low=data.get("low"),                # ETF proxies only
        open=data.get("open"),              # ETF proxies only
        close=data.get("price"),            # treat price as close for uniformity
        timestamp=data.get("date"),
    )


# ── Feed registry ─────────────────────────────────────────────────────────────
# Maps market name → (get_market_summary callable, per-record normalizer)

_FEED_REGISTRY: Dict[str, Any] = {
    "crypto":      (crypto.get_market_summary,      _from_crypto),
    "stocks":      (stocks.get_market_summary,      _from_stocks),
    "forex":       (forex.get_market_summary,        _from_forex),
    "commodities": (commodities.get_market_summary, _from_commodities),
}

# Maps market name → per-symbol get_price_data callable
_PRICE_DATA_FN: Dict[str, Any] = {
    "crypto":      crypto.get_price_data,
    "stocks":      stocks.get_price_data,
    "forex":       forex.get_price_data,
    "commodities": commodities.get_price_data,
}

# ── Public API ────────────────────────────────────────────────────────────────

def normalize_all() -> List[MarketData]:
    """
    Fetch and normalise data from all four feeds using their default symbols.

    Feeds that fail entirely are skipped; individual symbols that fail are
    omitted with a printed warning.

    Returns:
        List of MarketData objects, ordered: crypto → stocks → forex → commodities.
    """
    results: List[MarketData] = []

    for market, (summary_fn, normalizer) in _FEED_REGISTRY.items():
        try:
            raw: Dict[str, Any] = summary_fn()
        except Exception as e:
            print(f"[normalizer] {market} feed failed entirely: {e}")
            continue

        for sym_key, data in raw.items():
            if not data:
                print(f"[normalizer] Empty record for {sym_key} in {market}")
                continue
            try:
                results.append(normalizer(sym_key, data))
            except Exception as e:
                print(f"[normalizer] Failed to normalise {sym_key} ({market}): {e}")

    return results


def get_normalized(symbol: str, market: str) -> Optional[MarketData]:
    """
    Fetch and normalise a single asset by symbol and market.

    Args:
        symbol: The asset identifier as used by that feed
                (e.g. 'bitcoin', 'SPY', 'EUR_USD', 'WTI')
        market: One of 'crypto', 'stocks', 'forex', 'commodities'

    Returns:
        A MarketData instance, or None if the fetch fails or market is unknown.
    """
    market = market.lower()
    if market not in _FEED_REGISTRY:
        print(f"[normalizer] Unknown market '{market}'. "
              f"Choose from: {list(_FEED_REGISTRY.keys())}")
        return None

    price_fn = _PRICE_DATA_FN[market]
    _, normalizer = _FEED_REGISTRY[market]

    try:
        data = price_fn(symbol)
        if not data:
            print(f"[normalizer] No data returned for {symbol} ({market})")
            return None
        return normalizer(symbol, data)
    except Exception as e:
        print(f"[normalizer] Error fetching {symbol} ({market}): {e}")
        return None


# ── Module load confirmation ──────────────────────────────────────────────────
print("[normalizer] Market data normalizer loaded successfully.")


# ── Standalone test ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    COL = {
        "crypto":      "\033[96m",   # cyan
        "stocks":      "\033[92m",   # green
        "forex":       "\033[93m",   # yellow
        "commodities": "\033[91m",   # red
        "reset":       "\033[0m",
    }

    def _color(market: str, text: str) -> str:
        return f"{COL.get(market, '')}{text}{COL['reset']}"

    def _fmt_price(md: MarketData) -> str:
        """Format price with appropriate decimal places."""
        if md.market == "forex" and md.price < 10:
            return f"{md.price:.5f}"
        return f"{md.price:,.3f}" if md.price < 100 else f"{md.price:,.2f}"

    def _fmt_change(pct: Optional[float]) -> str:
        if pct is None:
            return "     n/a"
        sign = "+" if pct >= 0 else ""
        return f"{sign}{pct:.2f}%"

    def _fmt_vol(vol: Optional[float]) -> str:
        if vol is None:
            return "          n/a"
        if vol >= 1_000_000_000:
            return f"{vol / 1_000_000_000:>9.2f}B"
        if vol >= 1_000_000:
            return f"{vol / 1_000_000:>9.2f}M"
        if vol >= 1_000:
            return f"{vol / 1_000:>9.2f}K"
        return f"{vol:>13,.0f}"

    # ── [1] Full normalized table ─────────────────────────────────────────────
    print("\n" + "=" * 78)
    print("  AI Trading Agent — Unified Market Data (All Feeds)")
    print("=" * 78)
    print(
        f"  {'MARKET':<12} {'SYMBOL':<12} {'PRICE':>12}  "
        f"{'CHANGE%':>8}  {'VOLUME':>13}  {'BID':>10}  {'ASK':>10}"
    )
    print(f"  {'-'*11} {'-'*11} {'-'*12}  {'-'*8}  {'-'*13}  {'-'*10}  {'-'*10}")

    all_data = normalize_all()
    for md in all_data:
        bid_str = f"{md.bid:.5f}" if md.bid else "         —"
        ask_str = f"{md.ask:.5f}" if md.ask else "         —"
        market_label = _color(md.market, f"{md.market:<12}")
        print(
            f"  {market_label} {md.symbol:<12} {_fmt_price(md):>12}  "
            f"{_fmt_change(md.change_pct):>8}  {_fmt_vol(md.volume):>13}  "
            f"{bid_str:>10}  {ask_str:>10}"
        )

    print(f"\n  {len(all_data)} assets normalised across {len(_FEED_REGISTRY)} feeds.")

    # ── [2] Single asset lookups ──────────────────────────────────────────────
    print("\n" + "=" * 78)
    print("  Single-asset get_normalized() lookups")
    print("=" * 78)

    lookups = [
        ("bitcoin",  "crypto"),
        ("SPY",      "stocks"),
        ("EUR_USD",  "forex"),
        ("WTI",      "commodities"),
    ]

    for sym, mkt in lookups:
        md = get_normalized(sym, mkt)
        if md:
            extra = ""
            if md.open and md.high and md.low:
                extra = (
                    f"  O:{md.open:,.2f}  H:{md.high:,.2f}  "
                    f"L:{md.low:,.2f}  C:{md.close:,.2f}"
                )
            elif md.bid and md.ask:
                extra = f"  Bid:{md.bid:.5f}  Ask:{md.ask:.5f}"

            print(
                f"  {_color(md.market, md.market):<22} {md.symbol:<12} "
                f"${_fmt_price(md):>12}  {_fmt_change(md.change_pct):>8}"
                f"{extra}"
            )
        else:
            print(f"  ERROR: Could not fetch {sym} ({mkt})")

    print("\n" + "=" * 78)
    print("  Test complete.")
    print("=" * 78 + "\n")
