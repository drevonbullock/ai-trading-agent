"""
chart_agent/ta_analysis.py
Technical analysis engine — runs on OHLCV DataFrames from any data feed.
"""
from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from typing import Any, Dict, List, Optional, Tuple
import pandas as pd
import ta


# ── Helpers ───────────────────────────────────────────────────────────────────

def _validate(df: pd.DataFrame) -> pd.DataFrame:
    """
    Ensure the DataFrame has the required columns and a clean numeric index.
    Raises ValueError if required columns are missing.
    """
    required = {"open", "high", "low", "close", "volume"}
    missing = required - set(df.columns.str.lower())
    if missing:
        raise ValueError(f"[ta] DataFrame missing columns: {missing}")

    df = df.copy()
    df.columns = df.columns.str.lower()
    df = df[["open", "high", "low", "close", "volume"]].apply(pd.to_numeric, errors="coerce")
    df = df.dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)
    return df


def _swing_highs(high: pd.Series, order: int = 3) -> List[int]:
    """Return indices of local swing highs (highest in a window of ±order bars)."""
    highs = []
    for i in range(order, len(high) - order):
        window = high.iloc[i - order: i + order + 1]
        if high.iloc[i] == window.max():
            highs.append(i)
    return highs


def _swing_lows(low: pd.Series, order: int = 3) -> List[int]:
    """Return indices of local swing lows (lowest in a window of ±order bars)."""
    lows = []
    for i in range(order, len(low) - order):
        window = low.iloc[i - order: i + order + 1]
        if low.iloc[i] == window.min():
            lows.append(i)
    return lows


def _cluster_levels(
    levels: List[float],
    tolerance_pct: float = 0.003,
) -> List[float]:
    """
    Merge price levels that are within tolerance_pct of each other.
    Returns the mean of each cluster, sorted ascending.
    """
    if not levels:
        return []
    sorted_lvls = sorted(levels)
    clusters: List[List[float]] = [[sorted_lvls[0]]]
    for lvl in sorted_lvls[1:]:
        if abs(lvl - clusters[-1][-1]) / max(clusters[-1][-1], 1e-9) <= tolerance_pct:
            clusters[-1].append(lvl)
        else:
            clusters.append([lvl])
    return [round(sum(c) / len(c), 6) for c in clusters]


# ── 1. Market Structure ───────────────────────────────────────────────────────

def get_market_structure(df: pd.DataFrame) -> Dict[str, Any]:
    """
    Identify trend direction and recent swing structure.

    Trend logic:
      BULLISH  — last two swing highs are ascending AND last two swing lows ascending
      BEARISH  — last two swing highs descending AND last two swing lows descending
      RANGING  — mixed signals

    Returns dict with:
      trend          : "BULLISH" | "BEARISH" | "RANGING"
      higher_highs   : list of price values
      lower_highs    : list of price values
      higher_lows    : list of price values
      lower_lows     : list of price values
      last_swing_high: float
      last_swing_low : float
    """
    df = _validate(df)
    sh_idx = _swing_highs(df["high"])
    sl_idx = _swing_lows(df["low"])

    sh_prices = [df["high"].iloc[i] for i in sh_idx]
    sl_prices = [df["low"].iloc[i] for i in sl_idx]

    higher_highs: List[float] = []
    lower_highs: List[float] = []
    for i in range(1, len(sh_prices)):
        if sh_prices[i] > sh_prices[i - 1]:
            higher_highs.append(round(sh_prices[i], 6))
        else:
            lower_highs.append(round(sh_prices[i], 6))

    higher_lows: List[float] = []
    lower_lows: List[float] = []
    for i in range(1, len(sl_prices)):
        if sl_prices[i] > sl_prices[i - 1]:
            higher_lows.append(round(sl_prices[i], 6))
        else:
            lower_lows.append(round(sl_prices[i], 6))

    # Trend determination from last two swings
    hh = len(sh_prices) >= 2 and sh_prices[-1] > sh_prices[-2]
    lh = len(sh_prices) >= 2 and sh_prices[-1] < sh_prices[-2]
    hl = len(sl_prices) >= 2 and sl_prices[-1] > sl_prices[-2]
    ll = len(sl_prices) >= 2 and sl_prices[-1] < sl_prices[-2]

    if hh and hl:
        trend = "BULLISH"
    elif lh and ll:
        trend = "BEARISH"
    else:
        trend = "RANGING"

    return {
        "trend": trend,
        "higher_highs": higher_highs[-5:],
        "lower_highs": lower_highs[-5:],
        "higher_lows": higher_lows[-5:],
        "lower_lows": lower_lows[-5:],
        "last_swing_high": round(sh_prices[-1], 6) if sh_prices else None,
        "last_swing_low": round(sl_prices[-1], 6) if sl_prices else None,
    }


# ── 2. Key Levels ─────────────────────────────────────────────────────────────

def get_key_levels(df: pd.DataFrame) -> Dict[str, Any]:
    """
    Identify major support and resistance levels from swing highs/lows.

    Returns dict with:
      support   : top 3 support levels below current price (descending)
      resistance: top 3 resistance levels above current price (ascending)
      current_price: float
    """
    df = _validate(df)
    current_price = df["close"].iloc[-1]

    sh_idx = _swing_highs(df["high"], order=2)
    sl_idx = _swing_lows(df["low"], order=2)

    resistance_raw = [df["high"].iloc[i] for i in sh_idx]
    support_raw = [df["low"].iloc[i] for i in sl_idx]

    resistance_clustered = _cluster_levels(
        [r for r in resistance_raw if r > current_price]
    )
    support_clustered = _cluster_levels(
        [s for s in support_raw if s < current_price]
    )

    # Closest 3 above and below
    resistance_levels = sorted(resistance_clustered)[:3]
    support_levels = sorted(support_clustered, reverse=True)[:3]

    return {
        "current_price": round(current_price, 6),
        "support": [round(s, 6) for s in support_levels],
        "resistance": [round(r, 6) for r in resistance_levels],
    }


# ── 3. Fibonacci ──────────────────────────────────────────────────────────────

def get_fibonacci(df: pd.DataFrame) -> Dict[str, Any]:
    """
    Find the last major swing high and swing low, then compute Fibonacci
    retracement levels from that range.

    Returns dict with:
      swing_high: float
      swing_low : float
      direction : "UP" (retracing from high) | "DOWN" (retracing from low)
      levels    : dict mapping ratio string → price level
    """
    df = _validate(df)
    sh_idx = _swing_highs(df["high"], order=3)
    sl_idx = _swing_lows(df["low"], order=3)

    if not sh_idx or not sl_idx:
        return {"error": "Insufficient swing data for Fibonacci"}

    last_sh = sh_idx[-1]
    last_sl = sl_idx[-1]

    swing_high = df["high"].iloc[last_sh]
    swing_low = df["low"].iloc[last_sl]

    # Direction: was the swing high or swing low more recent?
    direction = "DOWN" if last_sh > last_sl else "UP"
    price_range = swing_high - swing_low

    ratios = [0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0]
    levels: Dict[str, float] = {}
    for r in ratios:
        if direction == "DOWN":
            # Retracing down from high
            levels[str(r)] = round(swing_high - r * price_range, 6)
        else:
            # Retracing up from low
            levels[str(r)] = round(swing_low + r * price_range, 6)

    return {
        "swing_high": round(swing_high, 6),
        "swing_low": round(swing_low, 6),
        "direction": direction,
        "levels": levels,
    }


# ── 4. Supply & Demand Zones ──────────────────────────────────────────────────

def get_supply_demand_zones(df: pd.DataFrame) -> Dict[str, Any]:
    """
    Identify supply zones (areas of strong selling / resistance) and
    demand zones (areas of strong buying / support).

    A supply zone forms at swing highs where price reversed sharply
    (large bearish candle following the high).
    A demand zone forms at swing lows where price reversed sharply
    (large bullish candle following the low).

    Each zone is a dict: {top, bottom, strength (1–3), origin_index}

    Returns dict with:
      supply_zones: list of zone dicts (highest-strength first)
      demand_zones: list of zone dicts (highest-strength first)
    """
    df = _validate(df)
    atr = ta.volatility.AverageTrueRange(
        df["high"], df["low"], df["close"], window=14
    ).average_true_range()
    avg_atr = atr.mean()

    sh_idx = _swing_highs(df["high"], order=2)
    sl_idx = _swing_lows(df["low"], order=2)

    supply_zones: List[Dict[str, Any]] = []
    for i in sh_idx:
        if i + 1 >= len(df):
            continue
        reversal_candle = df.iloc[i + 1]
        body = abs(reversal_candle["close"] - reversal_candle["open"])
        is_bearish = reversal_candle["close"] < reversal_candle["open"]
        if is_bearish and body > avg_atr * 0.5:
            strength = min(3, max(1, int(body / avg_atr)))
            zone_high = df["high"].iloc[i]
            zone_low = max(df["open"].iloc[i], df["close"].iloc[i])
            supply_zones.append({
                "top": round(zone_high, 6),
                "bottom": round(zone_low, 6),
                "strength": strength,
                "origin_index": i,
            })

    demand_zones: List[Dict[str, Any]] = []
    for i in sl_idx:
        if i + 1 >= len(df):
            continue
        reversal_candle = df.iloc[i + 1]
        body = abs(reversal_candle["close"] - reversal_candle["open"])
        is_bullish = reversal_candle["close"] > reversal_candle["open"]
        if is_bullish and body > avg_atr * 0.5:
            strength = min(3, max(1, int(body / avg_atr)))
            zone_low = df["low"].iloc[i]
            zone_high = min(df["open"].iloc[i], df["close"].iloc[i])
            demand_zones.append({
                "top": round(zone_high, 6),
                "bottom": round(zone_low, 6),
                "strength": strength,
                "origin_index": i,
            })

    supply_zones.sort(key=lambda z: z["strength"], reverse=True)
    demand_zones.sort(key=lambda z: z["strength"], reverse=True)

    return {
        "supply_zones": supply_zones[:5],
        "demand_zones": demand_zones[:5],
    }


# ── 5. Price Action Patterns ──────────────────────────────────────────────────

def get_price_action(df: pd.DataFrame) -> Dict[str, Any]:
    """
    Read the last 5 candles and identify candlestick patterns.

    Detected patterns:
      bullish_engulfing, bearish_engulfing,
      pin_bar_bullish, pin_bar_bearish,
      inside_bar, doji, liquidity_sweep_high, liquidity_sweep_low

    Returns dict with:
      patterns      : list of pattern name strings detected on the last candle
      last_candle   : dict summarising the most recent bar
      candle_context: list of last 5 candle summaries
    """
    df = _validate(df)
    if len(df) < 5:
        return {"patterns": [], "error": "Need at least 5 candles"}

    tail = df.iloc[-5:].reset_index(drop=True)
    patterns: List[str] = []

    prev = tail.iloc[-2]
    curr = tail.iloc[-1]

    prev_body = abs(prev["close"] - prev["open"])
    curr_body = abs(curr["close"] - curr["open"])
    curr_range = curr["high"] - curr["low"]
    curr_upper_wick = curr["high"] - max(curr["open"], curr["close"])
    curr_lower_wick = min(curr["open"], curr["close"]) - curr["low"]

    # Doji: tiny body relative to range
    if curr_range > 0 and curr_body / curr_range < 0.1:
        patterns.append("doji")

    # Bullish engulfing
    if (
        prev["close"] < prev["open"]           # prev bearish
        and curr["close"] > curr["open"]        # curr bullish
        and curr["open"] < prev["close"]
        and curr["close"] > prev["open"]
    ):
        patterns.append("bullish_engulfing")

    # Bearish engulfing
    if (
        prev["close"] > prev["open"]            # prev bullish
        and curr["close"] < curr["open"]        # curr bearish
        and curr["open"] > prev["close"]
        and curr["close"] < prev["open"]
    ):
        patterns.append("bearish_engulfing")

    # Pin bar — bullish (hammer): lower wick > 2× body, small upper wick
    if (
        curr_range > 0
        and curr_lower_wick >= curr_body * 2
        and curr_upper_wick <= curr_body * 0.5
        and curr_body > 0
    ):
        patterns.append("pin_bar_bullish")

    # Pin bar — bearish (shooting star): upper wick > 2× body, small lower wick
    if (
        curr_range > 0
        and curr_upper_wick >= curr_body * 2
        and curr_lower_wick <= curr_body * 0.5
        and curr_body > 0
    ):
        patterns.append("pin_bar_bearish")

    # Inside bar: current candle range completely within previous candle range
    if curr["high"] < prev["high"] and curr["low"] > prev["low"]:
        patterns.append("inside_bar")

    # Liquidity sweep: spike above recent 4-bar high then close back inside
    recent_high = tail.iloc[:-1]["high"].max()
    recent_low = tail.iloc[:-1]["low"].min()
    if curr["high"] > recent_high and curr["close"] < recent_high:
        patterns.append("liquidity_sweep_high")
    if curr["low"] < recent_low and curr["close"] > recent_low:
        patterns.append("liquidity_sweep_low")

    def _candle_summary(row: pd.Series) -> Dict[str, Any]:
        body = abs(row["close"] - row["open"])
        rng = row["high"] - row["low"]
        return {
            "open": round(row["open"], 6),
            "high": round(row["high"], 6),
            "low": round(row["low"], 6),
            "close": round(row["close"], 6),
            "direction": "BULL" if row["close"] >= row["open"] else "BEAR",
            "body_pct": round(body / rng * 100, 1) if rng > 0 else 0,
        }

    return {
        "patterns": patterns,
        "last_candle": _candle_summary(curr),
        "candle_context": [_candle_summary(tail.iloc[i]) for i in range(len(tail))],
    }


# ── 6. Volume Analysis ────────────────────────────────────────────────────────

def get_volume_analysis(df: pd.DataFrame) -> Dict[str, Any]:
    """
    Check whether current volume confirms, is weak for, or diverges from
    the most recent price move.

    Logic:
      CONFIRMING — volume above 20-bar average AND price move is in trend direction
      WEAK       — volume below 20-bar average
      DIVERGING  — volume above average but price move contradicts recent trend

    Returns dict with:
      signal          : "CONFIRMING" | "WEAK" | "DIVERGING"
      current_volume  : float
      avg_volume      : float
      volume_ratio    : float (current / avg)
      price_direction : "UP" | "DOWN" | "FLAT"
    """
    df = _validate(df)
    window = min(20, len(df) - 1)

    vol_series = df["volume"].replace(0, pd.NA).dropna()
    if len(vol_series) < 2:
        return {"signal": "WEAK", "error": "Insufficient volume data"}

    avg_volume = vol_series.iloc[-window - 1:-1].mean()
    current_volume = vol_series.iloc[-1]
    volume_ratio = current_volume / avg_volume if avg_volume else 1.0

    price_change = df["close"].iloc[-1] - df["close"].iloc[-2]
    if abs(price_change) < df["close"].iloc[-1] * 0.0001:
        price_direction = "FLAT"
    elif price_change > 0:
        price_direction = "UP"
    else:
        price_direction = "DOWN"

    # Determine recent bias from last 5 closes
    recent = df["close"].iloc[-6:-1]
    trend_up = recent.iloc[-1] > recent.iloc[0]

    if volume_ratio < 0.8:
        signal = "WEAK"
    elif (price_direction == "UP" and trend_up) or (price_direction == "DOWN" and not trend_up):
        signal = "CONFIRMING"
    elif price_direction == "FLAT":
        signal = "WEAK"
    else:
        signal = "DIVERGING"

    return {
        "signal": signal,
        "current_volume": round(float(current_volume), 2),
        "avg_volume": round(float(avg_volume), 2),
        "volume_ratio": round(float(volume_ratio), 3),
        "price_direction": price_direction,
    }


# ── 7. Confluence Score Helper ────────────────────────────────────────────────

def _score_confluence(
    structure: Dict[str, Any],
    key_levels: Dict[str, Any],
    fib: Dict[str, Any],
    sd_zones: Dict[str, Any],
    price_action: Dict[str, Any],
    volume: Dict[str, Any],
) -> Tuple[int, List[str]]:
    """
    Score 0–6 based on how many factors align in the same direction.
    Returns (score, list_of_reasons).
    """
    score = 0
    reasons: List[str] = []

    # 1. Trend structure
    if structure.get("trend") in ("BULLISH", "BEARISH"):
        score += 1
        reasons.append(f"Trend: {structure['trend']}")

    # 2. Price near a key level
    price = key_levels.get("current_price", 0)
    supports = key_levels.get("support", [])
    resists = key_levels.get("resistance", [])
    near_support = supports and abs(price - supports[0]) / price < 0.005
    near_resistance = resists and abs(price - resists[0]) / price < 0.005
    if near_support or near_resistance:
        score += 1
        label = "support" if near_support else "resistance"
        reasons.append(f"Price near key {label}")

    # 3. Price near a Fibonacci level
    if "levels" in fib:
        for ratio, level in fib["levels"].items():
            if level and abs(price - level) / max(price, 1e-9) < 0.004:
                score += 1
                reasons.append(f"Price near Fib {ratio} ({level})")
                break

    # 4. Inside a supply or demand zone
    in_zone = False
    for zone in sd_zones.get("demand_zones", []) + sd_zones.get("supply_zones", []):
        if zone["bottom"] <= price <= zone["top"]:
            in_zone = True
            zone_type = "demand" if zone in sd_zones.get("demand_zones", []) else "supply"
            reasons.append(f"Price inside {zone_type} zone (strength {zone['strength']})")
            break
    if in_zone:
        score += 1

    # 5. Clear price action pattern on last candle
    pa_patterns = price_action.get("patterns", [])
    if pa_patterns:
        score += 1
        reasons.append(f"Price action: {', '.join(pa_patterns)}")

    # 6. Volume confirmation
    if volume.get("signal") == "CONFIRMING":
        score += 1
        reasons.append(f"Volume confirming (ratio {volume.get('volume_ratio', 0):.2f}x avg)")
    elif volume.get("signal") == "DIVERGING":
        reasons.append("Volume diverging — caution")

    return score, reasons


# ── 8. Master Runner ──────────────────────────────────────────────────────────

def run_full_analysis(
    df: pd.DataFrame,
    symbol: str,
    timeframe: str,
) -> Dict[str, Any]:
    """
    Run all 6 analysis functions and return a single structured result dict.

    Args:
        df        : OHLCV DataFrame (columns: open, high, low, close, volume)
        symbol    : Asset symbol string (e.g. 'ETH', 'EUR_USD')
        timeframe : Timeframe string (e.g. 'H4', '1Day')

    Returns:
        dict with keys:
          symbol, timeframe, candle_count,
          market_structure, key_levels, fibonacci,
          supply_demand_zones, price_action, volume,
          confluence_score (int 0–6),
          confluence_reasons (list of strings),
          bias (str: overall directional lean)
    """
    df = _validate(df)

    structure  = get_market_structure(df)
    key_levels = get_key_levels(df)
    fib        = get_fibonacci(df)
    sd_zones   = get_supply_demand_zones(df)
    pa         = get_price_action(df)
    volume     = get_volume_analysis(df)

    score, reasons = _score_confluence(structure, key_levels, fib, sd_zones, pa, volume)

    # Overall bias
    trend = structure.get("trend", "RANGING")
    patterns = pa.get("patterns", [])
    bullish_pa = any(p in patterns for p in ("bullish_engulfing", "pin_bar_bullish", "liquidity_sweep_low"))
    bearish_pa = any(p in patterns for p in ("bearish_engulfing", "pin_bar_bearish", "liquidity_sweep_high"))

    if trend == "BULLISH" and (bullish_pa or not bearish_pa):
        bias = "BULLISH"
    elif trend == "BEARISH" and (bearish_pa or not bullish_pa):
        bias = "BEARISH"
    elif bullish_pa and not bearish_pa:
        bias = "CAUTIOUSLY_BULLISH"
    elif bearish_pa and not bullish_pa:
        bias = "CAUTIOUSLY_BEARISH"
    else:
        bias = "NEUTRAL"

    return {
        "symbol":               symbol,
        "timeframe":            timeframe,
        "candle_count":         len(df),
        "market_structure":     structure,
        "key_levels":           key_levels,
        "fibonacci":            fib,
        "supply_demand_zones":  sd_zones,
        "price_action":         pa,
        "volume":               volume,
        "confluence_score":     score,
        "confluence_reasons":   reasons,
        "bias":                 bias,
    }


# ── Module load confirmation ──────────────────────────────────────────────────
print("[ta_analysis] Technical analysis engine loaded successfully.")


# ── Standalone test ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    import json

    print("\n" + "=" * 65)
    print("  AI Trading Agent — TA Analysis Engine Test")
    print("=" * 65)

    # Fetch EUR_USD H4 candles from the forex feed
    print("\n  Fetching EUR_USD H4 data from OANDA forex feed...")
    from data_feeds import forex

    candles = forex.get_ohlcv("EUR_USD", granularity="H4", count=100)
    if not candles:
        print("  ERROR: Could not fetch forex candles.")
        sys.exit(1)

    df = pd.DataFrame(candles)
    # OANDA candles use 'timestamp' not 'date'; all OHLCV columns already present
    print(f"  Loaded {len(df)} candles  |  columns: {list(df.columns)}\n")

    result = run_full_analysis(df, symbol="EUR_USD", timeframe="H4")

    # ── Print formatted report ────────────────────────────────────────────────
    print("=" * 65)
    print(f"  ANALYSIS: {result['symbol']}  [{result['timeframe']}]"
          f"  —  {result['candle_count']} candles")
    print("=" * 65)

    ms = result["market_structure"]
    print(f"\n  TREND          : {ms['trend']}")
    print(f"  Last S.High    : {ms['last_swing_high']}")
    print(f"  Last S.Low     : {ms['last_swing_low']}")
    print(f"  Higher Highs   : {ms['higher_highs']}")
    print(f"  Higher Lows    : {ms['higher_lows']}")
    print(f"  Lower Highs    : {ms['lower_highs']}")
    print(f"  Lower Lows     : {ms['lower_lows']}")

    kl = result["key_levels"]
    print(f"\n  PRICE          : {kl['current_price']}")
    print(f"  Resistance     : {kl['resistance']}")
    print(f"  Support        : {kl['support']}")

    fb = result["fibonacci"]
    if "levels" in fb:
        print(f"\n  FIB SWING HIGH : {fb['swing_high']}")
        print(f"  FIB SWING LOW  : {fb['swing_low']}")
        print(f"  FIB DIRECTION  : {fb['direction']}")
        for ratio, level in fb["levels"].items():
            marker = " <-- price" if abs(kl["current_price"] - level) / max(kl["current_price"], 1e-9) < 0.004 else ""
            print(f"    {ratio:<6} → {level}{marker}")

    sdz = result["supply_demand_zones"]
    print(f"\n  SUPPLY ZONES   : {len(sdz['supply_zones'])} found")
    for z in sdz["supply_zones"][:3]:
        print(f"    {z['bottom']} – {z['top']}  (strength {z['strength']})")
    print(f"  DEMAND ZONES   : {len(sdz['demand_zones'])} found")
    for z in sdz["demand_zones"][:3]:
        print(f"    {z['bottom']} – {z['top']}  (strength {z['strength']})")

    pa = result["price_action"]
    print(f"\n  PATTERNS       : {pa['patterns'] or 'none'}")
    lc = pa["last_candle"]
    print(f"  LAST CANDLE    : {lc['direction']}  O:{lc['open']}  H:{lc['high']}"
          f"  L:{lc['low']}  C:{lc['close']}  body:{lc['body_pct']}%")

    vol = result["volume"]
    print(f"\n  VOLUME SIGNAL  : {vol['signal']}")
    print(f"  Vol ratio      : {vol['volume_ratio']}x avg")
    print(f"  Price direction: {vol['price_direction']}")

    print(f"\n  BIAS           : {result['bias']}")
    print(f"  CONFLUENCE     : {result['confluence_score']} / 6")
    for reason in result["confluence_reasons"]:
        print(f"    ✓ {reason}")

    print("\n" + "=" * 65)
    print("  Test complete.")
    print("=" * 65 + "\n")
