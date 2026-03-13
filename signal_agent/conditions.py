"""
signal_agent/conditions.py
Confluence condition checks — EMA trend filter, RSI momentum, and volume
confirmation used by the signal engine to raise signal quality.
"""
from __future__ import annotations

from typing import List, Tuple
import pandas as pd
import ta


def check_trend_filter(df: pd.DataFrame, direction: str) -> Tuple[bool, int, str]:
    """
    EMA 50/200 trend filter.  Hard block if price trades against the dominant
    EMA-defined trend.

    LONG  : close must be above both EMA50 and EMA200.
    SHORT : close must be below both EMA50 and EMA200.

    When fewer than 201 candles are available the filter is skipped (soft pass,
    no score awarded) so short histories are not silently blocked.

    Returns
    -------
    passed : bool  — False means hard block; reject the signal entirely.
    score  : int   — +20 when trend is confirmed, 0 otherwise.
    reason : str   — human-readable explanation.
    """
    if len(df) < 201:
        return True, 0, "Trend filter: insufficient candles for EMA200 (skipped)"

    ema50  = ta.trend.EMAIndicator(df["close"], window=50).ema_indicator()
    ema200 = ta.trend.EMAIndicator(df["close"], window=200).ema_indicator()

    close = float(df["close"].iloc[-1])
    e50   = float(ema50.iloc[-1])
    e200  = float(ema200.iloc[-1])

    if direction == "LONG":
        if close > e50 and close > e200:
            return True, 20, (
                f"Trend filter: LONG confirmed above EMA50({e50:.5f})"
                f" & EMA200({e200:.5f}) (+20pts)"
            )
        return False, 0, (
            f"Trend filter: LONG blocked — close {close:.5f} not above"
            f" EMA50({e50:.5f}) and/or EMA200({e200:.5f})"
        )
    else:  # SHORT
        if close < e50 and close < e200:
            return True, 20, (
                f"Trend filter: SHORT confirmed below EMA50({e50:.5f})"
                f" & EMA200({e200:.5f}) (+20pts)"
            )
        return False, 0, (
            f"Trend filter: SHORT blocked — close {close:.5f} not below"
            f" EMA50({e50:.5f}) and/or EMA200({e200:.5f})"
        )


def check_momentum(df: pd.DataFrame, direction: str, is_ranging: bool) -> Tuple[int, str]:
    """
    RSI momentum confirmation.

    Ranging markets : RSI must be 40–60 and moving in the signal direction.
    Trending markets:
      LONG  — RSI rising and above 45.
      SHORT — RSI falling and below 55.

    Returns
    -------
    score  : int — +15 when momentum is confirmed, 0 otherwise.
    reason : str — human-readable explanation.
    """
    if len(df) < 16:
        return 0, "Momentum: insufficient candles for RSI (skipped)"

    rsi_series = ta.momentum.RSIIndicator(df["close"], window=14).rsi()
    rsi_curr   = float(rsi_series.iloc[-1])
    rsi_prev   = float(rsi_series.iloc[-2])
    rising     = rsi_curr > rsi_prev

    if is_ranging:
        if 40.0 <= rsi_curr <= 60.0:
            if direction == "LONG" and rising:
                return 15, (
                    f"Momentum: RSI {rsi_curr:.1f} rising in range"
                    f" — confirms LONG (+15pts)"
                )
            if direction == "SHORT" and not rising:
                return 15, (
                    f"Momentum: RSI {rsi_curr:.1f} falling in range"
                    f" — confirms SHORT (+15pts)"
                )
        return 0, (
            f"Momentum: RSI {rsi_curr:.1f} — no directional momentum in range"
        )
    else:
        if direction == "LONG":
            if rising and rsi_curr > 45.0:
                return 15, (
                    f"Momentum: RSI {rsi_curr:.1f} rising above 45"
                    f" — confirms LONG (+15pts)"
                )
            return 0, (
                f"Momentum: RSI {rsi_curr:.1f} does not confirm LONG"
                f" (need rising > 45)"
            )
        else:
            if not rising and rsi_curr < 55.0:
                return 15, (
                    f"Momentum: RSI {rsi_curr:.1f} falling below 55"
                    f" — confirms SHORT (+15pts)"
                )
            return 0, (
                f"Momentum: RSI {rsi_curr:.1f} does not confirm SHORT"
                f" (need falling < 55)"
            )


def check_volume_confirmation(df: pd.DataFrame) -> Tuple[bool, int, List[str]]:
    """
    Volume check against the 20-period rolling average.

    Outcome              Action
    ────────────────     ────────────────────────────────────────────────
    vol >= avg           No change (clean signal)
    50% <= vol < avg     Reduce confidence by 20 pts; add warning
    vol < 50% of avg     Hard reject — passed=False

    Returns
    -------
    passed      : bool       — False means reject the signal entirely.
    score_delta : int        — 0 or -20 applied to confidence.
    warnings    : List[str]  — messages added to signal conditions.
    """
    window     = min(20, len(df) - 1)
    vol_series = df["volume"].replace(0, pd.NA).dropna()

    if len(vol_series) < 2:
        return True, 0, ["Volume: insufficient data (skipped)"]

    avg_vol  = float(vol_series.iloc[-window - 1:-1].mean())
    curr_vol = float(vol_series.iloc[-1])

    if avg_vol <= 0 or pd.isna(avg_vol):
        return True, 0, ["Volume: zero/invalid average (skipped)"]

    ratio = curr_vol / avg_vol

    if ratio < 0.50:
        return False, 0, [
            f"Volume: {ratio:.2f}x avg — REJECTED (below 50% of 20-period average)"
        ]
    if ratio < 1.00:
        return True, -20, [
            f"Volume: {ratio:.2f}x avg — below average, confidence reduced (-20pts)"
        ]
    return True, 0, [f"Volume: {ratio:.2f}x avg — above average (no penalty)"]
