"""
utils/claude_client.py
Claude API client for the AI Trading Agent.
Handles signal analysis, Telegram message generation, and chart captions.
"""
from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
from typing import Any, Dict, Optional

import anthropic
from dotenv import load_dotenv

from signal_agent.signal_engine import Signal

load_dotenv()

# ── Constants ─────────────────────────────────────────────────────────────────

_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
_MODEL   = "claude-sonnet-4-20250514"

_SYSTEM_PROMPT = (
    "You are a professional quantitative trader and technical analyst. "
    "Respond only with valid JSON — no markdown, no code fences, no prose outside the JSON. "
    "Be concise, precise, and use trader terminology. "
    "Never add disclaimers or investment advice warnings."
)

# ── Client ────────────────────────────────────────────────────────────────────

def _client() -> anthropic.Anthropic:
    """Return a configured Anthropic client."""
    if not _API_KEY:
        raise EnvironmentError("ANTHROPIC_API_KEY not set in environment or .env file.")
    return anthropic.Anthropic(api_key=_API_KEY)


# ── Core functions ────────────────────────────────────────────────────────────

def analyze_signal(signal: Signal, analysis: Dict[str, Any]) -> Dict[str, Any]:
    """
    Send a trade signal + TA analysis to Claude and return a structured verdict.

    Returns a dict with keys:
        narrative         : 2-3 sentence professional analysis
        verdict           : 'STRONG BUY' | 'BUY' | 'NEUTRAL' | 'AVOID' | 'STRONG AVOID'
        verdict_emoji     : matching emoji string
        risk_note         : one-sentence risk warning specific to this setup
        confluence_summary: comma-separated list of the strongest confluences
    """
    payload = {
        "signal": signal.to_dict(),
        "ta_analysis": {
            "bias":             analysis.get("bias"),
            "confluence_score": analysis.get("confluence_score"),
            "support_levels":   analysis.get("support_levels", [])[:3],
            "resistance_levels":analysis.get("resistance_levels", [])[:3],
            "supply_zones":     analysis.get("supply_zones", [])[:2],
            "demand_zones":     analysis.get("demand_zones", [])[:2],
            "fibonacci_levels": analysis.get("fibonacci_levels", {}),
            "atr":              analysis.get("atr"),
        },
    }

    prompt = (
        "Analyse this trade signal and TA data. "
        "Return a JSON object with exactly these keys: "
        "narrative, verdict, verdict_emoji, risk_note, confluence_summary.\n\n"
        f"{json.dumps(payload, indent=2)}"
    )

    response = _client().messages.create(
        model=_MODEL,
        max_tokens=512,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = response.content[0].text.strip()

    # Strip accidental code fences if model adds them
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Fallback: return partial data rather than crash
        return {
            "narrative":          raw[:300],
            "verdict":            "NEUTRAL",
            "verdict_emoji":      "⚪",
            "risk_note":          "Unable to parse full Claude response.",
            "confluence_summary": ", ".join(signal.conditions_met),
        }


_DIV = "━━━━━━━━━━━━━━━━━━━━━━━━━━"


def _fmt(price: float) -> str:
    """Format a price with commas and up to 5 significant digits."""
    if price >= 1000:
        return f"{price:,.0f}"
    if price >= 1:
        return f"{price:,.3f}"
    return f"{price:.5g}"


def generate_signal_message(
    signal: Signal,
    claude_analysis: Dict[str, Any],
) -> str:
    """
    Build a formatted Telegram-ready HTML message from a signal + Claude analysis.

    Returns HTML suitable for Telegram parse_mode='HTML'.
    """
    direction_emoji = "🟢" if signal.direction == "LONG" else "🔴"
    verdict         = claude_analysis.get("verdict", "—")
    verdict_emoji   = claude_analysis.get("verdict_emoji", "")
    narrative       = claude_analysis.get("narrative", "—")
    risk_note       = claude_analysis.get("risk_note", "—")
    confluence_sum  = claude_analysis.get("confluence_summary", "—")

    market_label = signal.market.upper()
    ts           = signal.timestamp[:16].replace("T", " ")

    entry_zone = (
        f"{_fmt(signal.entry_low)} – {_fmt(signal.entry_high)}"
        if signal.entry_low != signal.entry_high
        else _fmt(signal.entry_low)
    )

    lines = [
        _DIV,
        "🤖 <b>AI TRADING SIGNAL</b>",
        _DIV,
        "",
        f"{direction_emoji} <b>{signal.asset} · {market_label} · {signal.direction}</b>",
        f"🕐 {ts} UTC",
        "",
        _DIV,
        "<b>TRADE SETUP</b>",
        _DIV,
        f"📍 Entry      <code>{entry_zone}</code>",
        f"🎯 Target     <code>{_fmt(signal.target)}</code>",
        f"🛑 Stop       <code>{_fmt(signal.stop_loss)}</code>",
        f"⚖️ R:R         <b>{signal.risk_reward:.2f} : 1</b>",
        f"🔥 Confidence  <b>{signal.confidence}%</b>",
        "",
        _DIV,
        "<b>ANALYSIS</b>",
        _DIV,
        narrative,
        "",
        _DIV,
        f"<b>CONFLUENCE  {signal.confluence_score} / 6</b>",
        _DIV,
        confluence_sum,
        "",
        f"⚠️ <b>RISK:</b> {risk_note}",
        "",
        _DIV,
        f"<b>VERDICT:  {verdict_emoji} {verdict}</b>",
        _DIV,
    ]

    return "\n".join(lines)


def generate_chart_caption(
    signal: Signal,
    analysis: Dict[str, Any],
    claude_analysis: Dict[str, Any],
) -> str:
    """
    Generate a compact HTML caption for a chart image (Telegram limit: 1024 chars).
    """
    verdict       = claude_analysis.get("verdict", "—")
    verdict_emoji = claude_analysis.get("verdict_emoji", "")
    narrative     = claude_analysis.get("narrative", "—")

    first_sentence = narrative.split(".")[0].strip() + "."

    entry_zone = (
        f"{_fmt(signal.entry_low)}–{_fmt(signal.entry_high)}"
        if signal.entry_low != signal.entry_high
        else _fmt(signal.entry_low)
    )

    lines = [
        f"📊 <b>{signal.asset} · {signal.direction} · Confluence {signal.confluence_score}/6</b>",
        f"📍 Entry {entry_zone}  🎯 TP {_fmt(signal.target)}  🛑 SL {_fmt(signal.stop_loss)}",
        f"⚖️ R:R {signal.risk_reward:.2f}  🔥 {signal.confidence}% confidence",
        f"{verdict_emoji} {verdict} — {first_sentence}",
    ]

    return "\n".join(lines)[:1024]


# ── Module load confirmation ───────────────────────────────────────────────────

print("[claude_client] Module loaded — model:", _MODEL)


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    from datetime import datetime, timezone

    print("\n=== Claude Client Test ===\n")

    # Mock EUR_USD LONG signal
    mock_signal = Signal(
        asset            = "EUR_USD",
        market           = "forex",
        direction        = "LONG",
        entry_low        = 1.08200,
        entry_high       = 1.08350,
        target           = 1.09100,
        stop_loss        = 1.07800,
        risk_reward       = 2.25,
        confidence       = 74,
        confluence_score = 4,
        conditions_met   = [
            "price_at_demand_zone",
            "bullish_engulfing",
            "fibonacci_61_8_support",
            "above_200_ema",
        ],
        reasoning        = "Price tapped a strong H4 demand zone at 1.0820 with a bullish engulfing candle. "
                           "61.8% Fibonacci retracement aligns with zone. Bias: BULLISH.",
        timestamp        = datetime.now(timezone.utc).isoformat(),
    )

    # Mock TA analysis dict (simplified)
    mock_analysis: Dict[str, Any] = {
        "bias":              "BULLISH",
        "confluence_score":  4,
        "support_levels":    [1.0820, 1.0780, 1.0740],
        "resistance_levels": [1.0910, 1.0955],
        "supply_zones":      [{"high": 1.0960, "low": 1.0940, "strength": 2}],
        "demand_zones":      [{"high": 1.0840, "low": 1.0815, "strength": 3}],
        "fibonacci_levels":  {"0.618": 1.0822, "0.5": 1.0865, "0.382": 1.0905},
        "atr":               0.0045,
    }

    print("Calling analyze_signal() ...")
    try:
        ca = analyze_signal(mock_signal, mock_analysis)
        print("Claude analysis:", json.dumps(ca, indent=2))

        print("\n--- Telegram Message ---")
        msg = generate_signal_message(mock_signal, ca)
        print(msg)

        print("\n--- Chart Caption ---")
        cap = generate_chart_caption(mock_signal, mock_analysis, ca)
        print(cap)

    except Exception as exc:
        print(f"[ERROR] {exc}")
