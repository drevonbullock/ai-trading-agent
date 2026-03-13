"""
chart_agent/markup.py
Generates annotated candlestick chart images using pure matplotlib.
TradingView-style dark theme with manual candle drawing (no mplfinance).
"""
from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from typing import Any, Dict, List, Optional
import pandas as pd
import matplotlib
matplotlib.use("Agg")          # headless — no display required
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.lines as mlines

from chart_agent.ta_analysis import run_full_analysis
from signal_agent.signal_engine import Signal


# ── Theme constants ────────────────────────────────────────────────────────────

BG           = "#131722"   # TradingView dark background
BG_PANEL     = "#1e2130"   # panel / box backgrounds
FG           = "#d1d4dc"   # primary text
BULL_COLOUR  = "#26a69a"   # teal-green bull candles
BEAR_COLOUR  = "#ef5350"   # red bear candles
ENTRY_COLOUR = "#f59e0b"   # amber entry zone
TEXT_DIM     = "#64748b"   # muted text
SPINE_COLOR  = "#2a3245"   # axis spine / box borders
GRID_COLOR   = "#1e2a3a"   # subtle grid lines

# Per-ratio fibonacci colours (TradingView style)
_FIB_COLORS: Dict[str, str] = {
    "0.236": "#f44336",
    "0.382": "#ff9800",
    "0.5":   "#ffffff",
    "0.618": "#4caf50",
    "0.786": "#2196f3",
}

# Fibonacci ratios to annotate
_FIB_SHOW = set(_FIB_COLORS.keys())


# ── Helpers ────────────────────────────────────────────────────────────────────

def _prepare_df(df: pd.DataFrame) -> pd.DataFrame:
    """
    Ensure the DataFrame is indexed by DatetimeIndex (required by mplfinance)
    and has the OHLCV columns in the right types.
    """
    df = df.copy()
    df.columns = df.columns.str.lower()

    # If there's no datetime index, create a synthetic one
    if not isinstance(df.index, pd.DatetimeIndex):
        if "timestamp" in df.columns:
            df.index = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
        elif "date" in df.columns:
            df.index = pd.to_datetime(df["date"], utc=True, errors="coerce")
        else:
            # Synthetic daily index
            df.index = pd.date_range(
                end=pd.Timestamp.utcnow(), periods=len(df), freq="D"
            )

    df = df[["open", "high", "low", "close", "volume"]].apply(
        pd.to_numeric, errors="coerce"
    )
    df = df.dropna(subset=["open", "high", "low", "close"])
    df = df.sort_index()
    df.index.name = "Date"
    return df


def _price_fmt(price: float) -> str:
    """Format a price with appropriate decimal places."""
    if price == 0:
        return "0"
    if price < 0.01:
        return f"{price:.6f}"
    if price < 1:
        return f"{price:.5f}"
    if price < 100:
        return f"{price:.4f}"
    if price < 10_000:
        return f"{price:.2f}"
    return f"{price:,.0f}"


# ── Main chart function ────────────────────────────────────────────────────────

def draw_chart(
    df: pd.DataFrame,
    signal: Signal,
    analysis: Dict[str, Any],
    output_path: str,
) -> str:
    """
    Generate a TradingView-style annotated candlestick chart using pure matplotlib.

    Layout  : GridSpec(2,1) — 75% price panel / 25% volume panel
    Candles : Manual Rectangle patches (bodies) + ax.plot lines (wicks)
    Layers  : supply/demand zones, fibonacci, entry zone, target, stop
    Style   : Dark theme #131722, right-side price labels, info box, legend

    Args:
        df          : OHLCV DataFrame (any index — will be converted)
        signal      : Signal dataclass from signal_engine
        analysis    : dict from run_full_analysis()
        output_path : absolute or relative path to save the PNG

    Returns:
        output_path (the saved file path)
    """
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

    # ── Prep data ──────────────────────────────────────────────────────────────
    df_plot = _prepare_df(df).iloc[-60:]
    n       = len(df_plot)
    opens   = df_plot["open"].values
    highs   = df_plot["high"].values
    lows    = df_plot["low"].values
    closes  = df_plot["close"].values
    vols    = df_plot["volume"].values

    # ── Extract analysis layers ────────────────────────────────────────────────
    fib_levels   = analysis.get("fibonacci", {}).get("levels", {})
    sdz          = analysis.get("supply_demand_zones", {})
    demand_zones = sdz.get("demand_zones", [])
    supply_zones = sdz.get("supply_zones", [])

    # ── Figure + layout ────────────────────────────────────────────────────────
    fig = plt.figure(figsize=(14, 8), facecolor=BG)
    gs  = fig.add_gridspec(2, 1, height_ratios=[3, 1], hspace=0)
    ax     = fig.add_subplot(gs[0])
    ax_vol = fig.add_subplot(gs[1], sharex=ax)

    for a in (ax, ax_vol):
        a.set_facecolor(BG)
        for spine in a.spines.values():
            spine.set_edgecolor(SPINE_COLOR)
            spine.set_linewidth(0.5)
        a.tick_params(colors=TEXT_DIM, labelsize=7, direction="in", length=3)

    ax.grid(True, color=GRID_COLOR, linewidth=0.5, zorder=0)
    ax_vol.grid(False)

    # ── Draw candles ───────────────────────────────────────────────────────────
    CANDLE_W = 0.6
    for i in range(n):
        o, h, l, c = opens[i], highs[i], lows[i], closes[i]
        colour = BULL_COLOUR if c >= o else BEAR_COLOUR

        # Wick
        ax.plot([i, i], [l, h], color=colour, linewidth=0.8, zorder=2,
                solid_capstyle="round")

        # Body (avoid zero-height doji by using a minimum sliver)
        body_lo = min(o, c)
        body_h  = max(abs(c - o), (h - l) * 0.002)
        ax.add_patch(mpatches.Rectangle(
            (i - CANDLE_W / 2, body_lo), CANDLE_W, body_h,
            facecolor=colour, edgecolor=colour, linewidth=0, zorder=3,
        ))

    # Bug 3 fix: left padding of 1.5 candle widths so first candle isn't clipped
    ax.set_xlim(-1.5, n + 0.5)

    # Bug 1+2 fix: explicitly lock y-axis to cover candle range PLUS all signal
    # price levels.  axhline/axhspan don't update autoscale, but if any signal
    # level lies outside the plotted candle range the chart clips silently.
    _all_y = (list(lows) + list(highs)
              + [signal.entry_low, signal.entry_high,
                 signal.target, signal.stop_loss])
    _y_lo  = min(_all_y)
    _y_hi  = max(_all_y)
    _y_pad = (_y_hi - _y_lo) * 0.06
    ax.set_ylim(_y_lo - _y_pad, _y_hi + _y_pad)

    # Debug: confirm signal levels vs y-axis range
    print(
        f"[markup] DEBUG  entry={signal.entry_low:.5f}–{signal.entry_high:.5f}"
        f"  TP={signal.target:.5f}  SL={signal.stop_loss:.5f}"
        f"  y=[{ax.get_ylim()[0]:.5f}, {ax.get_ylim()[1]:.5f}]"
    )

    # ── Supply zones ───────────────────────────────────────────────────────────
    tfm_y = ax.get_yaxis_transform()   # x: axes fraction, y: data coords

    for zone in supply_zones[:3]:
        ax.axhspan(zone["bottom"], zone["top"],
                   color=BEAR_COLOUR, alpha=0.12, zorder=1)
        mid_y = (zone["bottom"] + zone["top"]) / 2
        ax.text(0.005, mid_y, "Supply", transform=tfm_y,
                color=BEAR_COLOUR, fontsize=6, va="center", ha="left",
                alpha=0.75, clip_on=True)

    # ── Demand zones ───────────────────────────────────────────────────────────
    for zone in demand_zones[:3]:
        ax.axhspan(zone["bottom"], zone["top"],
                   color=BULL_COLOUR, alpha=0.12, zorder=1)
        mid_y = (zone["bottom"] + zone["top"]) / 2
        ax.text(0.005, mid_y, "Demand", transform=tfm_y,
                color=BULL_COLOUR, fontsize=6, va="center", ha="left",
                alpha=0.75, clip_on=True)

    # ── Entry zone ─────────────────────────────────────────────────────────────
    ax.axhspan(signal.entry_low, signal.entry_high, alpha=0.25, color='#f59e0b', zorder=2)
    ax.axhline(y=signal.entry_low,  color='#f59e0b', linewidth=0.5, alpha=0.8)
    ax.axhline(y=signal.entry_high, color='#f59e0b', linewidth=0.5, alpha=0.8)
    ax.text(n - 1, (signal.entry_low + signal.entry_high) / 2,
            f'  ENTRY\n  {signal.entry_low:.5f}–{signal.entry_high:.5f}',
            color='#f59e0b', fontsize=7, fontweight='bold', va='center')

    # ── Fibonacci lines ────────────────────────────────────────────────────────
    for ratio, level in fib_levels.items():
        fc = _FIB_COLORS.get(ratio)
        if fc and level:
            ax.axhline(level, color=fc, linewidth=0.8, alpha=0.7,
                       linestyle="-", zorder=2)

    # ── Target + Stop lines ────────────────────────────────────────────────────
    ax.axhline(y=signal.target, color='#26a69a', linewidth=1.5, zorder=3)
    ax.text(n - 1, signal.target, f'  TP {signal.target:.5f}',
            color='#26a69a', fontsize=8, fontweight='bold', va='center')

    ax.axhline(y=signal.stop_loss, color='#ef5350', linewidth=1.5, zorder=3)
    ax.text(n - 1, signal.stop_loss, f'  SL {signal.stop_loss:.5f}',
            color='#ef5350', fontsize=8, fontweight='bold', va='center')

    # ── Fibonacci right-side labels ────────────────────────────────────────────
    for ratio, level in fib_levels.items():
        fc = _FIB_COLORS.get(ratio)
        if fc and level:
            ax.text(n - 1, level, f'  {ratio}  {_price_fmt(level)}',
                    color=fc, fontsize=6, va='center', alpha=0.85)

    # ── Y-axis: right side only ────────────────────────────────────────────────
    ax.yaxis.set_label_position("right")
    ax.yaxis.tick_right()
    ax.tick_params(axis="y", colors=TEXT_DIM, labelsize=7)

    # ── X-axis ticks (on volume panel only) ───────────────────────────────────
    step     = max(1, n // 8)
    tick_pos = list(range(0, n, step))
    tick_lbl = [df_plot.index[i].strftime("%b %d") for i in tick_pos]
    ax_vol.set_xticks(tick_pos)
    ax_vol.set_xticklabels(tick_lbl, fontsize=7, color=TEXT_DIM)
    ax.tick_params(axis="x", which="both", bottom=False, labelbottom=False)

    # ── Volume bars + MA ───────────────────────────────────────────────────────
    for i in range(n):
        vc = BULL_COLOUR if closes[i] >= opens[i] else BEAR_COLOUR
        ax_vol.bar(i, vols[i], color=vc, alpha=0.7, width=0.7, zorder=2)

    vol_ma = pd.Series(vols).rolling(window=20, min_periods=1).mean()
    ax_vol.plot(range(n), vol_ma.values, color="#ffffff", alpha=0.3,
                linewidth=1.0, zorder=3)
    ax_vol.yaxis.set_visible(False)
    for spine in ax_vol.spines.values():
        spine.set_edgecolor(SPINE_COLOR)

    # ── Title bar ─────────────────────────────────────────────────────────────
    dir_arrow = "▲" if signal.direction == "LONG" else "▼"
    title_txt = (
        f"{signal.asset}  ·  {signal.market.upper()}  ·  "
        f"{dir_arrow} {signal.direction}  ·  Confluence {signal.confluence_score}/6"
    )
    ax.set_title(
        title_txt, fontsize=11, color=FG, pad=8, fontfamily="monospace",
        bbox=dict(facecolor=BG_PANEL, edgecolor=SPINE_COLOR,
                  boxstyle="square,pad=0.4"),
    )

    # Trend badge — top-right corner
    trend = analysis.get("market_structure", {}).get("trend", "")
    if trend:
        badge_c = (BULL_COLOUR if "BULL" in trend.upper()
                   else BEAR_COLOUR if "BEAR" in trend.upper()
                   else TEXT_DIM)
        ax.text(
            0.995, 1.01, trend,
            transform=ax.transAxes, color=badge_c,
            fontsize=8, fontweight="bold", va="bottom", ha="right",
            clip_on=False,
            bbox=dict(facecolor=BG_PANEL, edgecolor=badge_c,
                      boxstyle="round,pad=0.3"),
        )

    # ── Info box — top-left ────────────────────────────────────────────────────
    info_txt = (
        f"Confidence  {signal.confidence}%\n"
        f"R:R         {signal.risk_reward:.2f} : 1\n"
        f"Confluence  {signal.confluence_score}/6"
    )
    ax.text(
        0.01, 0.97, info_txt,
        transform=ax.transAxes, color=FG, fontsize=8,
        va="top", ha="left", fontfamily="monospace",
        bbox=dict(facecolor=BG_PANEL, edgecolor=SPINE_COLOR,
                  alpha=0.90, boxstyle="square,pad=0.4"),
    )

    # ── Legend ─────────────────────────────────────────────────────────────────
    legend_handles = [
        mpatches.Patch(color=BEAR_COLOUR,  alpha=0.45, label="Supply Zone"),
        mpatches.Patch(color=BULL_COLOUR,  alpha=0.45, label="Demand Zone"),
        mpatches.Patch(color=ENTRY_COLOUR, alpha=0.55, label="Entry Zone"),
        mlines.Line2D([], [], color=BULL_COLOUR, linewidth=2.0, label="Target"),
        mlines.Line2D([], [], color=BEAR_COLOUR, linewidth=2.0, label="Stop"),
        mlines.Line2D([], [], color="#aaaaaa",   linewidth=0.8,
                      alpha=0.7, label="Fibonacci"),
    ]
    ax.legend(
        handles=legend_handles,
        loc="lower left",
        fontsize=7,
        framealpha=0.65,
        facecolor=BG_PANEL,
        edgecolor=SPINE_COLOR,
        labelcolor=FG,
        ncol=6,
        borderpad=0.5,
        handlelength=1.5,
    )

    # ── Save ──────────────────────────────────────────────────────────────────
    fig.subplots_adjust(right=0.84, left=0.03, top=0.91, bottom=0.07)
    fig.savefig(output_path, dpi=150, bbox_inches="tight",
                facecolor=BG, edgecolor="none")
    plt.close(fig)

    print(f"[markup] Chart saved: {output_path}")
    return output_path


# ── Convenience wrapper ────────────────────────────────────────────────────────

def generate_chart_for_signal(
    signal: Signal,
    market: str,
    charts_dir: Optional[str] = None,
) -> Optional[str]:
    """
    Fetch fresh OHLCV data for a signal, run TA analysis, draw the chart,
    and save it to the charts/ folder.

    Args:
        signal     : Signal dataclass (from signal_engine.generate_signal)
        market     : 'crypto' | 'stocks' | 'forex' | 'commodities'
        charts_dir : Override output directory. Defaults to <project_root>/charts/

    Returns:
        Absolute path to the saved PNG, or None on failure.
    """
    from data_feeds import crypto, stocks, forex, commodities
    from signal_agent.signal_engine import _OHLCV_PARAMS, _DISPLAY_SYMBOL, build_dataframe

    # Resolve the original CoinGecko ID if needed (reverse display map)
    _REVERSE_DISPLAY: Dict[str, str] = {v: k for k, v in _DISPLAY_SYMBOL.items()}
    feed_symbol = _REVERSE_DISPLAY.get(signal.asset, signal.asset)

    params = _OHLCV_PARAMS.get(market, {})
    try:
        if market == "crypto":
            ohlcv = crypto.get_ohlcv(feed_symbol, **params)
        elif market == "stocks":
            ohlcv = stocks.get_ohlcv(feed_symbol, **params)
        elif market == "forex":
            ohlcv = forex.get_ohlcv(feed_symbol, **params)
        elif market == "commodities":
            ohlcv = commodities.get_ohlcv(feed_symbol, **params)
        else:
            print(f"[markup] Unknown market: {market}")
            return None
    except Exception as e:
        print(f"[markup] OHLCV fetch failed for {signal.asset}: {e}")
        return None

    df = build_dataframe(ohlcv, market)
    if df.empty:
        print(f"[markup] Empty DataFrame for {signal.asset}")
        return None

    # Re-run TA so the analysis is in sync with the freshly fetched data
    timeframe_map = {
        "crypto": "Daily", "stocks": "Daily",
        "forex": "H4",     "commodities": "Monthly",
    }
    try:
        analysis = run_full_analysis(
            df, symbol=signal.asset,
            timeframe=timeframe_map.get(market, "Unknown"),
        )
    except Exception as e:
        print(f"[markup] TA analysis failed for {signal.asset}: {e}")
        return None

    # Determine output path
    if charts_dir is None:
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        charts_dir   = os.path.join(project_root, "charts")
    os.makedirs(charts_dir, exist_ok=True)

    safe_asset = signal.asset.replace("/", "-").replace("_", "-")
    filename   = f"{safe_asset}_{market}_{signal.direction}.png"
    output_path = os.path.join(charts_dir, filename)

    return draw_chart(df, signal, analysis, output_path)


# ── Module load confirmation ──────────────────────────────────────────────────
print("[markup] Chart markup module loaded successfully.")


# ── Standalone test ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("\n" + "=" * 65)
    print("  AI Trading Agent — Chart Markup Test")
    print("=" * 65)

    from data_feeds import forex as fx_feed
    from signal_agent.signal_engine import (
        build_dataframe, run_full_analysis,
        score_signal, calculate_entry_target_stop,
        Signal, _OHLCV_PARAMS,
    )
    from datetime import datetime, timezone

    print("\n  Fetching EUR_USD H4 data...")
    ohlcv = fx_feed.get_ohlcv("EUR_USD", granularity="H4", count=100)
    if not ohlcv:
        print("  ERROR: No OHLCV data returned.")
        sys.exit(1)

    df = build_dataframe(ohlcv, "forex")
    print(f"  Loaded {len(df)} candles.")

    analysis = run_full_analysis(df, symbol="EUR_USD", timeframe="H4")

    bias = analysis.get("bias", "NEUTRAL")
    direction = "LONG" if "BULLISH" in bias else "SHORT"

    confidence, conditions = score_signal(analysis, direction)
    current_price = analysis["key_levels"].get("current_price", float(df["close"].iloc[-1]))
    entry_low, entry_high, target, stop_loss, rr = calculate_entry_target_stop(
        analysis, direction, current_price
    )

    trend       = analysis["market_structure"]["trend"]
    confluence  = analysis.get("confluence_score", 0)
    pa_patterns = analysis["price_action"]["patterns"]
    vol_signal  = analysis["volume"]["signal"]

    parts = [f"{trend} trend", f"confluence {confluence}/6"]
    if pa_patterns:
        parts.append(f"PA: {', '.join(pa_patterns[:2])}")
    if vol_signal == "CONFIRMING":
        parts.append("volume confirming")
    reasoning = " | ".join(parts)

    test_signal = Signal(
        asset="EUR_USD",
        market="forex",
        direction=direction,
        entry_low=entry_low,
        entry_high=entry_high,
        target=target,
        stop_loss=stop_loss,
        risk_reward=rr,
        confidence=confidence,
        confluence_score=confluence,
        conditions_met=conditions,
        reasoning=reasoning,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )

    print(f"\n  Signal: {test_signal.direction}  confidence {test_signal.confidence}%"
          f"  R:R {test_signal.risk_reward}  confluence {test_signal.confluence_score}/6")
    print(f"  Entry  : {test_signal.entry_low} – {test_signal.entry_high}")
    print(f"  Target : {test_signal.target}")
    print(f"  Stop   : {test_signal.stop_loss}")

    # Determine output path
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out_path     = os.path.join(project_root, "charts", "test_chart.png")

    print(f"\n  Generating chart -> {out_path}")
    saved = draw_chart(df, test_signal, analysis, out_path)

    print(f"\n  Chart saved: {saved}")
    print("\n" + "=" * 65)
    print("  Test complete.")
    print("=" * 65 + "\n")
