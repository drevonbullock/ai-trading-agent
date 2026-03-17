"""
alerts/telegram_bot.py
Telegram delivery layer for the AI Trading Agent.
Sends signal alerts (text + chart image) to a configured Telegram chat.
"""
from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
from typing import Any, Dict, List, Optional

import requests
from dotenv import load_dotenv

from signal_agent.signal_engine import Signal
from utils.claude_client import generate_signal_message, generate_chart_caption

load_dotenv()

# ── Constants ──────────────────────────────────────────────────────────────────

_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")
_BASE_URL  = f"https://api.telegram.org/bot{_BOT_TOKEN}"
_TIMEOUT   = 20  # seconds

log = logging.getLogger(__name__)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _check_credentials() -> None:
    """Raise if bot token or chat ID are missing."""
    if not _BOT_TOKEN:
        raise EnvironmentError("TELEGRAM_BOT_TOKEN not set in environment or .env file.")
    if not _CHAT_ID:
        raise EnvironmentError("TELEGRAM_CHAT_ID not set in environment or .env file.")


def _handle_response(resp: requests.Response, action: str) -> bool:
    """Log and return success status from a Telegram API response."""
    try:
        data = resp.json()
    except Exception:
        data = {}

    if resp.ok and data.get("ok"):
        log.info("[telegram] %s — OK (message_id=%s)", action, data.get("result", {}).get("message_id"))
        return True

    log.error(
        "[telegram] %s failed — HTTP %s | %s",
        action, resp.status_code, data.get("description", resp.text[:200]),
    )
    return False


# ── Public API ─────────────────────────────────────────────────────────────────

def send_message(
    text: str,
    parse_mode: str = "HTML",
) -> bool:
    """
    Send a text message to the configured Telegram chat.

    Parameters
    ----------
    text       : Message body (HTML or plain text depending on parse_mode)
    parse_mode : 'HTML' | 'Markdown' | 'MarkdownV2' (default: 'HTML')

    Returns
    -------
    True on success, False on any error.
    """
    _check_credentials()

    url     = f"{_BASE_URL}/sendMessage"
    payload = {
        "chat_id":    _CHAT_ID,
        "text":       text,
        "parse_mode": parse_mode,
    }

    try:
        resp = requests.post(url, json=payload, timeout=_TIMEOUT)
        return _handle_response(resp, "send_message")
    except requests.RequestException as exc:
        log.error("[telegram] send_message network error: %s", exc)
        return False


def send_photo(
    image_path: str,
    caption: Optional[str] = None,
    parse_mode: str = "HTML",
) -> bool:
    """
    Send a photo file to the configured Telegram chat.

    Parameters
    ----------
    image_path : Absolute or relative path to the image file.
    caption    : Optional caption text (max 1024 chars).
    parse_mode : Caption formatting mode (default: 'HTML').

    Returns
    -------
    True on success, False on any error.
    """
    _check_credentials()

    if not os.path.isfile(image_path):
        log.error("[telegram] send_photo — file not found: %s", image_path)
        return False

    url  = f"{_BASE_URL}/sendPhoto"
    data: Dict[str, Any] = {"chat_id": _CHAT_ID}
    if caption:
        data["caption"]    = caption[:1024]
        data["parse_mode"] = parse_mode

    try:
        with open(image_path, "rb") as img:
            resp = requests.post(
                url,
                data=data,
                files={"photo": img},
                timeout=_TIMEOUT,
            )
        return _handle_response(resp, "send_photo")
    except requests.RequestException as exc:
        log.error("[telegram] send_photo network error: %s", exc)
        return False


def send_signal_alert(
    signal: Signal,
    claude_analysis: Dict[str, Any],
    chart_path: Optional[str] = None,
    analysis: Optional[Dict[str, Any]] = None,
) -> bool:
    """
    Main delivery function: sends a full signal alert to Telegram.

    Workflow
    --------
    1. Send the formatted text signal message.
    2. If chart_path is provided and the file exists, send the chart image
       with a compact caption.

    Parameters
    ----------
    signal          : Signal dataclass instance.
    claude_analysis : Dict returned by claude_client.analyze_signal().
    chart_path      : Optional path to the chart PNG.
    analysis        : Optional raw TA analysis dict (used for chart caption).

    Returns
    -------
    True only if all send operations succeeded.
    """
    results: List[bool] = []

    # ── Step 1: text message ──────────────────────────────────────────────────
    text = generate_signal_message(signal, claude_analysis)
    text_ok = send_message(text, parse_mode="HTML")
    results.append(text_ok)

    if text_ok:
        log.info("[telegram] Signal text delivered for %s %s", signal.asset, signal.direction)
    else:
        log.warning("[telegram] Signal text FAILED for %s %s", signal.asset, signal.direction)

    # ── Step 2: chart image (optional) ───────────────────────────────────────
    if chart_path:
        caption = None
        if analysis is not None:
            caption = generate_chart_caption(signal, analysis, claude_analysis)

        photo_ok = send_photo(chart_path, caption=caption)
        results.append(photo_ok)

        if photo_ok:
            log.info("[telegram] Chart image delivered: %s", chart_path)
        else:
            log.warning("[telegram] Chart image FAILED: %s", chart_path)

    return all(results)


_DIV = "━━━━━━━━━━━━━━━━━━━━━━━━━━"

_MARKET_ICON: Dict[str, str] = {
    "crypto":      "₿",
    "stocks":      "📈",
    "forex":       "💱",
    "commodities": "🛢️",
}


def send_scan_summary(signals: List[Signal], weekend_mode: bool = False) -> bool:
    """
    Send a scan completion summary to Telegram.

    Shows total signal count, per-market breakdown, and next scan time.

    Parameters
    ----------
    signals : List of Signal objects emitted during the scan.
    weekend_mode : When True, appends a note that only crypto and commodities
                  were scanned (forex/stocks closed on weekends).

    Returns
    -------
    True on success, False on any error.
    """
    mode_line = "\n🗓️ <i>Weekend mode — Crypto &amp; Commodities only</i>" if weekend_mode else ""

    if not signals:
        text = (
            f"{_DIV}\n"
            f"🔍 <b>SCAN COMPLETE</b>\n"
            f"{_DIV}\n"
            f"No qualifying signals found.\n"
            f"{mode_line}\n"
            f"🕐 Next scan in 30 min\n"
            f"{_DIV}"
        )
        return send_message(text, parse_mode="HTML")

    # Count per market
    markets: Dict[str, int] = {}
    for s in signals:
        markets[s.market] = markets.get(s.market, 0) + 1

    count_word = f"{len(signals)} signal{'s' if len(signals) != 1 else ''} found"

    breakdown_lines = []
    for market, count in sorted(markets.items()):
        icon = _MARKET_ICON.get(market, "•")
        breakdown_lines.append(f"{icon} {market.capitalize():<12}{count}")

    breakdown = "\n".join(breakdown_lines)

    text = (
        f"{_DIV}\n"
        f"🔍 <b>SCAN COMPLETE</b>\n"
        f"{_DIV}\n"
        f"{count_word}\n"
        f"\n"
        f"{breakdown}\n"
        f"{mode_line}\n"
        f"🕐 Next scan in 30 min\n"
        f"{_DIV}"
    )
    return send_message(text, parse_mode="HTML")


def test_connection() -> bool:
    """
    Send a test message to verify the bot token and chat ID are working.

    Returns
    -------
    True if the test message was delivered successfully.
    """
    text = "✅ <b>AI Trading Agent</b> — Telegram connection verified."
    ok   = send_message(text, parse_mode="HTML")
    if ok:
        print("[telegram] test_connection OK — bot is live.")
    else:
        print("[telegram] test_connection FAILED — check BOT_TOKEN and CHAT_ID.")
    return ok


# ── Module load confirmation ───────────────────────────────────────────────────

print("[telegram_bot] Module loaded — chat_id:", _CHAT_ID or "(not set)")


# ── Standalone test ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import json
    from datetime import datetime, timezone

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    print("\n=== Telegram Bot Test ===\n")

    # ── 1. Connection test ────────────────────────────────────────────────────
    print("Step 1: test_connection()")
    conn_ok = test_connection()
    print(f"  → {'OK' if conn_ok else 'FAILED'}\n")

    if not conn_ok:
        print("Aborting — fix TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID first.")
        raise SystemExit(1)

    # ── 2. Build mock GBP_USD signal ─────────────────────────────────────────
    from utils.claude_client import analyze_signal

    mock_signal = Signal(
        asset            = "GBP_USD",
        market           = "forex",
        direction        = "SHORT",
        entry_low        = 1.26200,
        entry_high       = 1.26400,
        target           = 1.25000,
        stop_loss        = 1.27000,
        risk_reward      = 1.75,
        confidence       = 68,
        confluence_score = 3,
        conditions_met   = [
            "price_at_supply_zone",
            "bearish_engulfing",
            "fibonacci_50_resistance",
        ],
        reasoning        = (
            "GBP/USD rejected a H4 supply zone at 1.2630 with a bearish engulfing candle. "
            "50% Fibonacci retracement aligns with zone top. Bias: BEARISH."
        ),
        timestamp        = datetime.now(timezone.utc).isoformat(),
    )

    mock_analysis: Dict[str, Any] = {
        "bias":              "BEARISH",
        "confluence_score":  3,
        "support_levels":    [1.2500, 1.2450],
        "resistance_levels": [1.2640, 1.2700],
        "supply_zones":      [{"high": 1.2645, "low": 1.2620, "strength": 2}],
        "demand_zones":      [{"high": 1.2510, "low": 1.2490, "strength": 2}],
        "fibonacci_levels":  {"0.5": 1.2635, "0.382": 1.2680},
        "atr":               0.0060,
    }

    # ── 3. Get Claude analysis ────────────────────────────────────────────────
    print("Step 2: analyze_signal() via Claude ...")
    try:
        ca = analyze_signal(mock_signal, mock_analysis)
        print("  Claude verdict:", ca.get("verdict"), ca.get("verdict_emoji"))
    except Exception as exc:
        print(f"  [ERROR] Claude call failed: {exc}")
        ca = {
            "narrative":          "Mock analysis — Claude unavailable.",
            "verdict":            "NEUTRAL",
            "verdict_emoji":      "⚪",
            "risk_note":          "n/a",
            "confluence_summary": ", ".join(mock_signal.conditions_met),
        }

    # ── 4. send_signal_alert (no real chart — pass None) ─────────────────────
    mock_chart_path: Optional[str] = None  # swap in a real PNG path to test send_photo

    print("\nStep 3: send_signal_alert()")
    ok = send_signal_alert(mock_signal, ca, chart_path=mock_chart_path, analysis=mock_analysis)
    print(f"  → {'OK — signal delivered' if ok else 'FAILED — check logs'}\n")

    # ── 5. scan summary ───────────────────────────────────────────────────────
    print("Step 4: send_scan_summary()")
    summary_ok = send_scan_summary([mock_signal])
    print(f"  → {'OK' if summary_ok else 'FAILED'}\n")
