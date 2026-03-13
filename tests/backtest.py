"""
tests/backtest.py
Historical rolling-window backtest for the AI Trading Agent signal engine.

For each asset:
  1. Fetch N candles of OHLCV history.
  2. Slide a window forward one candle at a time.
  3. At each position, run TA analysis + signal scoring.
  4. If confluence >= threshold, record the signal.
  5. Look forward 20 candles: WIN / LOSS / EXPIRED.
  6. Report accuracy stats.
"""
from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from chart_agent.ta_analysis import run_full_analysis
from signal_agent.signal_engine import (
    score_signal,
    calculate_entry_target_stop,
    build_dataframe,
    _MIN_CONFLUENCE,
    _MIN_RR,
)


# ── Result dataclass ───────────────────────────────────────────────────────────

@dataclass
class BacktestResult:
    asset:              str
    market:             str
    direction:          str
    confidence:         int
    confluence:         int
    entry:              float
    target:             float
    stop_loss:          float
    risk_reward:        float
    candle_index:       int
    outcome:            str   = ""   # WIN | LOSS | EXPIRED
    candles_to_outcome: int   = 0
    pnl_pct:            float = 0.0


# ── Forward-test helper ────────────────────────────────────────────────────────

def _check_outcome(
    direction:  str,
    entry:      float,
    target:     float,
    stop_loss:  float,
    future_df:  pd.DataFrame,
) -> Tuple[str, int, float]:
    """
    Scan the next N candles to determine trade outcome.

    Returns
    -------
    outcome        : 'WIN' | 'LOSS' | 'EXPIRED'
    candles_to_hit : candles elapsed until the outcome (0 = EXPIRED)
    pnl_pct        : approximate % P&L (positive = profit)
    """
    for i, (_, row) in enumerate(future_df.iterrows(), start=1):
        high = float(row["high"])
        low  = float(row["low"])

        if direction == "LONG":
            if high >= target:
                return "WIN",  i,  abs(target - entry) / entry * 100
            if low  <= stop_loss:
                return "LOSS", i, -abs(entry - stop_loss) / entry * 100
        else:  # SHORT
            if low  <= target:
                return "WIN",  i,  abs(entry - target) / entry * 100
            if high >= stop_loss:
                return "LOSS", i, -abs(stop_loss - entry) / entry * 100

    return "EXPIRED", 0, 0.0


# ── Core backtest ──────────────────────────────────────────────────────────────

def run_backtest(
    asset:          str,
    market:         str,
    candles:        int   = 500,
    min_confluence: int   = _MIN_CONFLUENCE,
    min_rr:         float = _MIN_RR,
    lookforward:    int   = 20,
    lookback:       int   = 60,
) -> List[BacktestResult]:
    """
    Run a rolling-window backtest for a single asset.

    Parameters
    ----------
    asset          : feed-native symbol, e.g. 'EUR_USD' or 'bitcoin'
    market         : 'forex' | 'crypto' | 'stocks' | 'commodities'
    candles        : total historical candles to request
    min_confluence : minimum confluence score — defaults to signal_engine._MIN_CONFLUENCE
    min_rr         : minimum R:R ratio      — defaults to signal_engine._MIN_RR
    lookforward    : candles ahead used to resolve WIN/LOSS/EXPIRED
    lookback       : minimum window size before analysis is run

    Returns
    -------
    List of BacktestResult objects (one per signal fired).
    """
    _DIV = "─" * 60
    print(f"\n{_DIV}")
    print(f"  Backtesting {asset}  [{market.upper()}]  —  {candles} candles requested")
    print(f"  Filters: confluence >= {min_confluence}  |  R:R >= {min_rr}")
    print(_DIV)

    # ── Fetch OHLCV ───────────────────────────────────────────────────────────
    ohlcv: List[Dict[str, Any]] = []
    try:
        if market == "forex":
            from data_feeds import forex as _feed
            ohlcv = _feed.get_ohlcv(asset, granularity="H4", count=candles)

        elif market == "crypto":
            from data_feeds import crypto as _feed
            # CoinGecko returns daily bars for days > 90; approx 1 bar per day
            days = min(candles, 365)
            ohlcv = _feed.get_ohlcv(asset, days=days)

        elif market == "stocks":
            from data_feeds import stocks as _feed
            ohlcv = _feed.get_ohlcv(asset, count=candles)

        else:
            from data_feeds import commodities as _feed
            ohlcv = _feed.get_ohlcv(asset, count=candles)

    except Exception as exc:
        print(f"  [ERROR] OHLCV fetch failed: {exc}")
        return []

    if not ohlcv:
        print("  [ERROR] No OHLCV data returned.")
        return []

    df_full = build_dataframe(ohlcv, market)
    total   = len(df_full)
    print(f"  Loaded {total} candles.")

    if total < lookback + lookforward + 5:
        print("  [ERROR] Not enough candles for a meaningful backtest.")
        return []

    # ── Timeframe label (informational only) ──────────────────────────────────
    timeframe_map: Dict[str, str] = {
        "crypto":      "Daily",
        "stocks":      "Daily",
        "forex":       "H4",
        "commodities": "Monthly",
    }
    timeframe = timeframe_map.get(market, "H4")

    # ── Rolling window ────────────────────────────────────────────────────────
    results:    List[BacktestResult] = []
    skip_until: int = 0     # de-duplicate: skip until previous trade is resolved
    end_idx:    int = total - lookforward  # need lookforward bars ahead

    progress_step = max(1, (end_idx - lookback) // 20)   # ~5% increments

    for window_end in range(lookback, end_idx):
        # Progress indicator
        done = window_end - lookback
        span = end_idx - lookback
        if done % progress_step == 0:
            pct = done / span * 100
            print(f"  [{pct:4.0f}%]  window={window_end}  signals={len(results)}", end="\r")

        # Skip if still inside the lookforward window of a previous signal
        if window_end < skip_until:
            continue

        df_slice = df_full.iloc[:window_end].copy()

        # Run TA analysis
        try:
            analysis = run_full_analysis(df_slice, symbol=asset, timeframe=timeframe)
        except Exception:
            continue

        bias        = analysis.get("bias", "NEUTRAL")
        confluence  = analysis.get("confluence_score", 0)

        if confluence < min_confluence:
            continue
        if "BULL" in bias.upper():
            direction = "LONG"
        elif "BEAR" in bias.upper():
            direction = "SHORT"
        else:
            continue   # NEUTRAL — no signal

        # Score the signal
        try:
            confidence, _ = score_signal(analysis, direction)
        except Exception:
            continue

        current_price: float = analysis.get("key_levels", {}).get(
            "current_price", float(df_slice["close"].iloc[-1])
        )

        # Compute entry / target / stop
        try:
            entry_low, entry_high, target, stop_loss, rr = calculate_entry_target_stop(
                analysis, direction, current_price
            )
        except Exception:
            continue

        # Apply same R:R filter as generate_signal()
        if rr < min_rr:
            continue

        entry = (entry_low + entry_high) / 2.0

        # Resolve outcome on the next `lookforward` candles
        future = df_full.iloc[window_end : window_end + lookforward]
        outcome, candles_hit, pnl_pct = _check_outcome(
            direction, entry, target, stop_loss, future
        )

        results.append(BacktestResult(
            asset              = asset,
            market             = market,
            direction          = direction,
            confidence         = confidence,
            confluence         = confluence,
            entry              = entry,
            target             = target,
            stop_loss          = stop_loss,
            risk_reward        = rr,
            candle_index       = window_end,
            outcome            = outcome,
            candles_to_outcome = candles_hit,
            pnl_pct            = pnl_pct,
        ))

        # Jump forward so the same trade isn't counted twice
        skip_until = window_end + max(lookforward, 5)

    print(f"  [100%]  Done.  Signals evaluated: {len(results)}            ")
    return results


# ── Statistics ─────────────────────────────────────────────────────────────────

def _compute_stats(results: List[BacktestResult]) -> Dict[str, Any]:
    if not results:
        return {}

    wins    = [r for r in results if r.outcome == "WIN"]
    losses  = [r for r in results if r.outcome == "LOSS"]
    expired = [r for r in results if r.outcome == "EXPIRED"]
    total   = len(results)

    avg_rr_wins  = sum(r.risk_reward for r in wins)   / len(wins)   if wins   else 0.0
    avg_pnl_win  = sum(r.pnl_pct     for r in wins)   / len(wins)   if wins   else 0.0
    avg_pnl_loss = sum(r.pnl_pct     for r in losses) / len(losses) if losses else 0.0
    largest_win  = max((r.pnl_pct for r in wins),   default=0.0)
    largest_loss = min((r.pnl_pct for r in losses), default=0.0)

    # Streak calculation
    max_w = max_l = cur_w = cur_l = 0
    for r in results:
        if r.outcome == "WIN":
            cur_w += 1; cur_l = 0
            max_w = max(max_w, cur_w)
        elif r.outcome == "LOSS":
            cur_l += 1; cur_w = 0
            max_l = max(max_l, cur_l)
        else:
            cur_w = cur_l = 0

    return {
        "total":          total,
        "wins":           len(wins),
        "losses":         len(losses),
        "expired":        len(expired),
        "win_rate":       len(wins)    / total * 100,
        "loss_rate":      len(losses)  / total * 100,
        "expired_rate":   len(expired) / total * 100,
        "avg_rr_wins":    avg_rr_wins,
        "avg_pnl_win":    avg_pnl_win,
        "avg_pnl_loss":   avg_pnl_loss,
        "largest_win":    largest_win,
        "largest_loss":   largest_loss,
        "max_win_streak": max_w,
        "max_loss_streak":max_l,
    }


# ── Print helpers ──────────────────────────────────────────────────────────────

def print_results(
    label:   str,
    market:  str,
    results: List[BacktestResult],
) -> Dict[str, Any]:
    """
    Print a formatted results table for one asset.
    Returns the stats dict so callers can aggregate.
    """
    stats = _compute_stats(results)
    _D    = "━" * 52

    print(f"\n{_D}")
    print(f"  BACKTEST  —  {label}  [{market.upper()}]")
    print(_D)

    if not stats:
        print("  No signals generated.")
        print(_D)
        return {}

    print(f"  Total signals   :  {stats['total']}")
    print(f"  WIN             :  {stats['wins']:>3}   ({stats['win_rate']:.1f}%)")
    print(f"  LOSS            :  {stats['losses']:>3}   ({stats['loss_rate']:.1f}%)")
    print(f"  EXPIRED         :  {stats['expired']:>3}   ({stats['expired_rate']:.1f}%)")
    print(f"  {'─'*48}")
    print(f"  Avg R:R (wins)  :  {stats['avg_rr_wins']:.2f} : 1")
    print(f"  Avg P&L (win)   : +{stats['avg_pnl_win']:.3f}%")
    print(f"  Avg P&L (loss)  :  {stats['avg_pnl_loss']:.3f}%")
    print(f"  Largest win     : +{stats['largest_win']:.3f}%")
    print(f"  Largest loss    :  {stats['largest_loss']:.3f}%")
    print(f"  {'─'*48}")
    print(f"  Max win streak  :  {stats['max_win_streak']}")
    print(f"  Max loss streak :  {stats['max_loss_streak']}")
    print(_D)

    return stats


def print_combined_summary(all_stats: Dict[str, Dict[str, Any]]) -> None:
    """Print a one-line-per-asset combined summary table."""
    _D = "━" * 64
    print(f"\n{_D}")
    print("  COMBINED BACKTEST SUMMARY")
    print(_D)
    print(f"  {'Asset':<14} {'Signals':>7} {'Win%':>7} {'Loss%':>7} "
          f"{'Exp%':>7} {'AvgRR':>7}")
    print(f"  {'─'*60}")

    total_signals = total_wins = total_losses = 0

    for label, stats in all_stats.items():
        if not stats:
            print(f"  {label:<14}  {'—':>7}")
            continue
        total_signals += stats["total"]
        total_wins    += stats["wins"]
        total_losses  += stats["losses"]
        print(
            f"  {label:<14}"
            f"  {stats['total']:>7}"
            f"  {stats['win_rate']:>6.1f}%"
            f"  {stats['loss_rate']:>6.1f}%"
            f"  {stats['expired_rate']:>6.1f}%"
            f"  {stats['avg_rr_wins']:>6.2f}"
        )

    if total_signals:
        overall_wr = total_wins / total_signals * 100
        print(f"  {'─'*60}")
        print(f"  {'OVERALL':<14}  {total_signals:>7}  {overall_wr:>6.1f}%")

    print(_D)


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

    print("\n" + "=" * 60)
    print("  AI Trading Agent — Historical Backtest")
    print("=" * 60)

    # (feed_symbol, market, display_label)
    TARGETS: List[Tuple[str, str, str]] = [
        ("EUR_USD", "forex",  "EUR_USD"),
        ("GBP_USD", "forex",  "GBP_USD"),
        ("bitcoin", "crypto", "BTC"),
    ]

    all_stats: Dict[str, Dict[str, Any]] = {}

    for feed_sym, market, label in TARGETS:
        results = run_backtest(
            asset       = feed_sym,
            market      = market,
            candles     = 500,
            lookforward = 20,
            lookback    = 60,
        )
        stats = print_results(label, market, results)
        all_stats[label] = stats

    print_combined_summary(all_stats)
    print("\n  Backtest complete.\n")
