"""
chart_agent/markup.py
Generates annotated candlestick chart images using matplotlib and mplfinance.
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
import mplfinance as mpf

from chart_agent.ta_analysis import run_full_analysis
from signal_agent.signal_engine import Signal


# ── Theme constants ────────────────────────────────────────────────────────────

BG          = "#1c2433"   # chart background
BG_PANEL    = "#222d3f"   # candle panel background
FG          = "#e2e8f0"   # primary text / axes
BULL_COLOUR = "#22c55e"   # green
BEAR_COLOUR = "#ef4444"   # red
FIB_COLOUR  = "#60a5fa"   # blue
ENTRY_COLOUR= "#fbbf24"   # yellow
TEXT_DIM    = "#94a3b8"   # muted text

_MPLF_STYLE = mpf.make_mpf_style(
    base_mpf_style="nightclouds",
    marketcolors=mpf.make_marketcolors(
        up=BULL_COLOUR, down=BEAR_COLOUR,
        wick={"up": BULL_COLOUR, "down": BEAR_COLOUR},
        volume={"up": BULL_COLOUR, "down": BEAR_COLOUR},
        edge="inherit",
    ),
    facecolor=BG_PANEL,
    figcolor=BG,
    gridcolor="#2d3f55",
    gridstyle="--",
    gridaxis="both",
    rc={
        "axes.labelcolor":  FG,
        "xtick.color":      TEXT_DIM,
        "ytick.color":      TEXT_DIM,
        "text.color":       FG,
        "axes.titlecolor":  FG,
        "figure.facecolor": BG,
    },
)

# Fibonacci ratios to annotate on the chart
_FIB_LABELS = {"0.236": "0.236", "0.382": "0.382", "0.5": "0.5",
               "0.618": "0.618", "0.786": "0.786"}


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
    Generate a fully annotated candlestick chart and save it as a PNG.

    Annotations drawn:
      - Candlestick candles (bull = green, bear = red) + volume panel
      - Supply zones    : semi-transparent red rectangles
      - Demand zones    : semi-transparent green rectangles
      - Support levels  : green dashed horizontal lines
      - Resistance lvls : red dashed horizontal lines
      - Fibonacci levels: blue dashed lines with ratio labels
      - Entry zone      : yellow shaded rectangle
      - Target line     : solid green line labelled "TARGET"
      - Stop-loss line  : solid red line labelled "STOP"
      - Trend arrows    : text annotation in top-right corner
      - Title bar       : asset · timeframe · direction · confluence/bias

    Args:
        df          : OHLCV DataFrame (any index — will be converted)
        signal      : Signal dataclass from signal_engine
        analysis    : dict from run_full_analysis()
        output_path : absolute or relative path to save the PNG

    Returns:
        output_path (the saved file path)
    """
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

    # Prep data
    df_plot = _prepare_df(df)
    n = len(df_plot)

    # Extract analysis layers
    kl       = analysis.get("key_levels", {})
    fib      = analysis.get("fibonacci", {})
    sdz      = analysis.get("supply_demand_zones", {})
    ms       = analysis.get("market_structure", {})

    supports     = kl.get("support", [])
    resistances  = kl.get("resistance", [])
    fib_levels   = fib.get("levels", {})
    demand_zones = sdz.get("demand_zones", [])
    supply_zones = sdz.get("supply_zones", [])

    # ── Build mplfinance addplot lines ─────────────────────────────────────────
    addplots: list = []

    def _hline_series(price: float) -> pd.Series:
        """Return a constant-value Series aligned to the DataFrame index."""
        return pd.Series(price, index=df_plot.index)

    # Support lines
    for sup in supports[:3]:
        addplots.append(
            mpf.make_addplot(
                _hline_series(sup), color=BULL_COLOUR,
                linestyle="--", width=0.8, alpha=0.7,
            )
        )

    # Resistance lines
    for res in resistances[:3]:
        addplots.append(
            mpf.make_addplot(
                _hline_series(res), color=BEAR_COLOUR,
                linestyle="--", width=0.8, alpha=0.7,
            )
        )

    # Fibonacci lines (selected ratios only)
    for ratio, level in fib_levels.items():
        if ratio in _FIB_LABELS and level:
            addplots.append(
                mpf.make_addplot(
                    _hline_series(level), color=FIB_COLOUR,
                    linestyle=":", width=0.7, alpha=0.6,
                )
            )

    # Target line
    addplots.append(
        mpf.make_addplot(
            _hline_series(signal.target), color=BULL_COLOUR,
            linestyle="-", width=1.5, alpha=0.9,
        )
    )

    # Stop line
    addplots.append(
        mpf.make_addplot(
            _hline_series(signal.stop_loss), color=BEAR_COLOUR,
            linestyle="-", width=1.5, alpha=0.9,
        )
    )

    # ── Render with mplfinance ─────────────────────────────────────────────────
    trend   = ms.get("trend", "RANGING")
    dir_sym = "▲ LONG" if signal.direction == "LONG" else "▼ SHORT"
    title   = (
        f"{signal.asset}  ·  {signal.market.upper()}  ·  {dir_sym}"
        f"  ·  Confluence {signal.confluence_score}/6  ·  {trend}"
    )

    fig, axes = mpf.plot(
        df_plot,
        type="candle",
        style=_MPLF_STYLE,
        title=title,
        volume=True,
        addplot=addplots if addplots else None,
        figsize=(16, 10),
        tight_layout=True,
        returnfig=True,
        warn_too_much_data=10_000,
    )

    ax = axes[0]   # main price panel
    y_min, y_max = ax.get_ylim()
    x_min, x_max = 0, n - 1

    # ── Shaded rectangles (drawn on axes directly) ─────────────────────────────

    def _shade_zone(ax, bottom: float, top: float, colour: str, alpha: float = 0.12):
        ax.axhspan(bottom, top, color=colour, alpha=alpha, zorder=1)

    # Supply zones
    for zone in supply_zones[:4]:
        _shade_zone(ax, zone["bottom"], zone["top"], BEAR_COLOUR, alpha=0.10)
        ax.text(
            0.01, (zone["top"] + zone["bottom"]) / 2,
            f"Supply  str:{zone['strength']}",
            transform=ax.get_yaxis_transform(),
            color=BEAR_COLOUR, fontsize=6.5, alpha=0.75,
            va="center",
        )

    # Demand zones
    for zone in demand_zones[:4]:
        _shade_zone(ax, zone["bottom"], zone["top"], BULL_COLOUR, alpha=0.10)
        ax.text(
            0.01, (zone["top"] + zone["bottom"]) / 2,
            f"Demand  str:{zone['strength']}",
            transform=ax.get_yaxis_transform(),
            color=BULL_COLOUR, fontsize=6.5, alpha=0.75,
            va="center",
        )

    # Entry zone
    entry_mid = (signal.entry_low + signal.entry_high) / 2
    ax.axhspan(signal.entry_low, signal.entry_high,
               color=ENTRY_COLOUR, alpha=0.18, zorder=2)
    ax.text(
        0.99, entry_mid,
        f"ENTRY  {_price_fmt(signal.entry_low)} – {_price_fmt(signal.entry_high)}",
        transform=ax.get_yaxis_transform(),
        color=ENTRY_COLOUR, fontsize=7.5, alpha=0.95,
        va="center", ha="right", fontweight="bold",
    )

    # ── Level labels on right y-axis ──────────────────────────────────────────

    label_right = 0.995   # x position (axes fraction, right-aligned)

    ax.text(label_right, signal.target, f"TARGET  {_price_fmt(signal.target)}",
            transform=ax.get_yaxis_transform(),
            color=BULL_COLOUR, fontsize=7.5, va="bottom", ha="right",
            fontweight="bold")

    ax.text(label_right, signal.stop_loss, f"STOP  {_price_fmt(signal.stop_loss)}",
            transform=ax.get_yaxis_transform(),
            color=BEAR_COLOUR, fontsize=7.5, va="top", ha="right",
            fontweight="bold")

    # Support / resistance labels
    for i, sup in enumerate(supports[:3]):
        ax.text(label_right, sup, f"S{i+1}  {_price_fmt(sup)}",
                transform=ax.get_yaxis_transform(),
                color=BULL_COLOUR, fontsize=6.5, va="top", ha="right", alpha=0.7)

    for i, res in enumerate(resistances[:3]):
        ax.text(label_right, res, f"R{i+1}  {_price_fmt(res)}",
                transform=ax.get_yaxis_transform(),
                color=BEAR_COLOUR, fontsize=6.5, va="bottom", ha="right", alpha=0.7)

    # Fibonacci labels
    for ratio, level in fib_levels.items():
        if ratio in _FIB_LABELS and level:
            ax.text(0.50, level, f"Fib {ratio}  {_price_fmt(level)}",
                    transform=ax.get_yaxis_transform(),
                    color=FIB_COLOUR, fontsize=6.0, va="bottom", ha="center",
                    alpha=0.65)

    # ── Trend arrows (top-right corner) ───────────────────────────────────────
    trend_colours = {
        "BULLISH": BULL_COLOUR,
        "BEARISH": BEAR_COLOUR,
        "RANGING": ENTRY_COLOUR,
    }
    trend_arrows = {
        "BULLISH": "▲▲  BULLISH TREND",
        "BEARISH": "▼▼  BEARISH TREND",
        "RANGING": "⟺  RANGING",
    }
    ax.text(
        0.98, 0.97,
        trend_arrows.get(trend, trend),
        transform=ax.transAxes,
        color=trend_colours.get(trend, FG),
        fontsize=9, va="top", ha="right",
        fontweight="bold",
        bbox=dict(boxstyle="round,pad=0.3", facecolor=BG, alpha=0.7,
                  edgecolor=trend_colours.get(trend, FG), linewidth=0.8),
    )

    # ── Confidence / R:R badge (top-left) ─────────────────────────────────────
    badge_text = (
        f"Confidence: {signal.confidence}%\n"
        f"R:R  {signal.risk_reward}\n"
        f"Confluence  {signal.confluence_score}/6"
    )
    ax.text(
        0.02, 0.97, badge_text,
        transform=ax.transAxes,
        color=FG, fontsize=7.5, va="top", ha="left",
        fontfamily="monospace",
        bbox=dict(boxstyle="round,pad=0.4", facecolor=BG, alpha=0.75,
                  edgecolor=TEXT_DIM, linewidth=0.6),
    )

    # ── Legend ────────────────────────────────────────────────────────────────
    legend_handles = [
        mpatches.Patch(color=BEAR_COLOUR,  alpha=0.4, label="Supply zone"),
        mpatches.Patch(color=BULL_COLOUR,  alpha=0.4, label="Demand zone"),
        mpatches.Patch(color=ENTRY_COLOUR, alpha=0.5, label="Entry zone"),
        mlines.Line2D([], [], color=BULL_COLOUR, linestyle="-",  linewidth=1.5, label="Target"),
        mlines.Line2D([], [], color=BEAR_COLOUR, linestyle="-",  linewidth=1.5, label="Stop"),
        mlines.Line2D([], [], color=BULL_COLOUR, linestyle="--", linewidth=0.8, label="Support"),
        mlines.Line2D([], [], color=BEAR_COLOUR, linestyle="--", linewidth=0.8, label="Resistance"),
        mlines.Line2D([], [], color=FIB_COLOUR,  linestyle=":",  linewidth=0.7, label="Fibonacci"),
    ]
    ax.legend(
        handles=legend_handles,
        loc="lower left",
        fontsize=6.5,
        framealpha=0.6,
        facecolor=BG,
        edgecolor=TEXT_DIM,
        labelcolor=FG,
        ncol=4,
    )

    # ── Watermark ─────────────────────────────────────────────────────────────
    ax.text(
        0.5, 0.5, "AI TRADING AGENT",
        transform=ax.transAxes,
        color=FG, alpha=0.04,
        fontsize=36, fontweight="bold",
        va="center", ha="center", rotation=30,
    )

    # ── Save ──────────────────────────────────────────────────────────────────
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
