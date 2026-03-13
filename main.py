"""
main.py
Entry point for the AI Trading Agent.
Orchestrates the full scan → chart → analysis → alert pipeline.
"""
from __future__ import annotations

import logging
import os
import sys
import time
from datetime import datetime, timezone
from typing import List, Optional

# ── Logging setup (before any project imports so module-load prints stay clean) ─

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(
            os.path.join(os.path.dirname(__file__), "logs", "agent.log"),
            encoding="utf-8",
        ),
    ],
)
log = logging.getLogger(__name__)

# ── Project imports ────────────────────────────────────────────────────────────

from signal_agent.signal_engine import scan_all_markets, Signal
from chart_agent.markup import generate_chart_for_signal
from chart_agent.ta_analysis import run_full_analysis
from utils.claude_client import analyze_signal
from alerts.telegram_bot import send_signal_alert, send_scan_summary, test_connection


# ── Pipeline ───────────────────────────────────────────────────────────────────

def _process_signal(signal: Signal) -> bool:
    """
    Run the full pipeline for a single signal:
      chart → Claude analysis → Telegram alert.

    Returns True if the alert was delivered successfully.
    """
    log.info("Processing signal: %s %s  confidence=%d%%  R:R=%.2f",
             signal.asset, signal.direction, signal.confidence, signal.risk_reward)

    # 1. Generate chart ────────────────────────────────────────────────────────
    chart_path: Optional[str] = None
    try:
        chart_path = generate_chart_for_signal(signal, signal.market)
        if chart_path:
            log.info("Chart saved: %s", chart_path)
        else:
            log.warning("Chart generation returned None for %s — alert will send without image.", signal.asset)
    except Exception as exc:
        log.error("Chart generation failed for %s: %s", signal.asset, exc)

    # 2. Get Claude analysis ───────────────────────────────────────────────────
    # Re-run TA to supply analysis dict (generate_chart_for_signal already ran it
    # internally, but we need the dict for the Claude prompt).
    analysis: dict = {}
    try:
        from data_feeds import crypto, stocks, forex, commodities
        from signal_agent.signal_engine import _OHLCV_PARAMS, _DISPLAY_SYMBOL, build_dataframe

        _REVERSE = {v: k for k, v in _DISPLAY_SYMBOL.items()}
        feed_sym = _REVERSE.get(signal.asset, signal.asset)
        params   = _OHLCV_PARAMS.get(signal.market, {})

        if signal.market == "crypto":
            ohlcv = crypto.get_ohlcv(feed_sym, **params)
        elif signal.market == "stocks":
            ohlcv = stocks.get_ohlcv(feed_sym, **params)
        elif signal.market == "forex":
            ohlcv = forex.get_ohlcv(feed_sym, **params)
        else:
            ohlcv = commodities.get_ohlcv(feed_sym, **params)

        df       = build_dataframe(ohlcv, signal.market)
        analysis = run_full_analysis(df)
    except Exception as exc:
        log.warning("Could not build analysis dict for %s: %s — Claude prompt will be sparse.", signal.asset, exc)

    claude_result: dict = {}
    try:
        claude_result = analyze_signal(signal, analysis)
        log.info("Claude verdict for %s: %s %s",
                 signal.asset,
                 claude_result.get("verdict", "—"),
                 claude_result.get("verdict_emoji", ""))
    except Exception as exc:
        log.error("Claude analysis failed for %s: %s", signal.asset, exc)
        claude_result = {
            "narrative":          signal.reasoning,
            "verdict":            "NEUTRAL",
            "verdict_emoji":      "⚪",
            "risk_note":          "Claude analysis unavailable.",
            "confluence_summary": ", ".join(signal.conditions_met),
        }

    # 3. Send Telegram alert ───────────────────────────────────────────────────
    try:
        ok = send_signal_alert(
            signal,
            claude_result,
            chart_path=chart_path,
            analysis=analysis or None,
        )
        if ok:
            log.info("Alert delivered for %s %s", signal.asset, signal.direction)
        else:
            log.warning("Alert delivery failed for %s %s", signal.asset, signal.direction)
        return ok
    except Exception as exc:
        log.error("send_signal_alert raised for %s: %s", signal.asset, exc)
        return False


def run_scan() -> List[Signal]:
    """
    Full market scan pipeline.

    1. Scan all markets for qualifying signals.
    2. For each signal: chart + Claude analysis + Telegram alert.
    3. Send scan summary.

    Returns the list of Signal objects emitted during this scan.
    """
    start = time.monotonic()
    now   = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    log.info("─" * 60)
    log.info("Scan started at %s", now)

    # ── Scan ──────────────────────────────────────────────────────────────────
    try:
        signals = scan_all_markets()
    except Exception as exc:
        log.error("scan_all_markets() raised: %s", exc)
        signals = []

    log.info("Scan found %d qualifying signal(s).", len(signals))

    # ── Process each signal ───────────────────────────────────────────────────
    delivered = 0
    for sig in signals:
        ok = _process_signal(sig)
        if ok:
            delivered += 1
        # Small pause between signals to avoid Telegram rate limits
        time.sleep(1)

    # ── Summary ───────────────────────────────────────────────────────────────
    try:
        send_scan_summary(signals)
    except Exception as exc:
        log.error("send_scan_summary() raised: %s", exc)

    elapsed = time.monotonic() - start
    log.info("Scan complete in %.1fs — %d/%d alerts delivered.", elapsed, delivered, len(signals))
    log.info("─" * 60)

    return signals


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    log.info("AI Trading Agent — manual run")

    # Verify Telegram is reachable before committing to a full scan
    try:
        if not test_connection():
            log.error("Telegram connection test failed. Check BOT_TOKEN and CHAT_ID.")
            sys.exit(1)
    except Exception as exc:
        log.error("Telegram connection check raised: %s", exc)
        sys.exit(1)

    run_scan()
