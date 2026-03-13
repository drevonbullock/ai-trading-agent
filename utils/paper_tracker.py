"""
utils/paper_tracker.py
Paper-trade logger for the AI Trading Agent.
Appends every generated signal to logs/paper_trades.csv and tracks outcomes.
"""
from __future__ import annotations

import csv
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from signal_agent.signal_engine import Signal

# ── Constants ──────────────────────────────────────────────────────────────────

_CSV_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "logs", "paper_trades.csv",
)

_COLUMNS = [
    "date", "asset", "market", "direction",
    "entry_low", "entry_high", "target", "stop_loss",
    "confidence", "confluence", "verdict",
    "result", "pnl_pct", "notes",
]


# ── Internal helpers ───────────────────────────────────────────────────────────

def _ensure_header() -> None:
    """Write the CSV header if the file is empty or doesn't exist."""
    exists    = os.path.isfile(_CSV_PATH)
    is_empty  = exists and os.path.getsize(_CSV_PATH) == 0

    if not exists or is_empty:
        with open(_CSV_PATH, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=_COLUMNS)
            writer.writeheader()


def _read_rows() -> List[Dict[str, str]]:
    """Return all rows from the CSV as a list of dicts."""
    _ensure_header()
    with open(_CSV_PATH, "r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _write_rows(rows: List[Dict[str, str]]) -> None:
    """Overwrite the CSV with the given rows (preserves header)."""
    with open(_CSV_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


# ── Public API ─────────────────────────────────────────────────────────────────

def log_signal(
    signal: Signal,
    claude_analysis: Dict[str, Any],
    notes: str = "",
) -> None:
    """
    Append a new paper-trade row when a signal fires.

    Parameters
    ----------
    signal          : Signal dataclass from signal_engine.
    claude_analysis : Dict returned by claude_client.analyze_signal().
    notes           : Optional free-text note (e.g. 'high-impact news day').
    """
    _ensure_header()

    row: Dict[str, str] = {
        "date":        datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
        "asset":       signal.asset,
        "market":      signal.market,
        "direction":   signal.direction,
        "entry_low":   f"{signal.entry_low:.6g}",
        "entry_high":  f"{signal.entry_high:.6g}",
        "target":      f"{signal.target:.6g}",
        "stop_loss":   f"{signal.stop_loss:.6g}",
        "confidence":  str(signal.confidence),
        "confluence":  str(signal.confluence_score),
        "verdict":     claude_analysis.get("verdict", "—"),
        "result":      "PENDING",
        "pnl_pct":     "",
        "notes":       notes,
    }

    with open(_CSV_PATH, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_COLUMNS)
        writer.writerow(row)

    print(f"[paper_tracker] Logged: {signal.asset} {signal.direction}  verdict={row['verdict']}")


def update_result(
    asset: str,
    date: str,
    result: str,
    pnl_pct: Optional[float] = None,
    notes: str = "",
) -> bool:
    """
    Update the outcome of an existing trade row.

    Matches on asset + date prefix (YYYY-MM-DD HH:MM).  Updates the first
    PENDING row that matches.

    Parameters
    ----------
    asset   : Asset ticker, e.g. 'EUR_USD'.
    date    : Date string matching the row's date column (full or prefix).
    result  : 'WIN' | 'LOSS' | 'PENDING'.
    pnl_pct : Percentage P&L, e.g. 2.35 for +2.35%, -1.1 for -1.1%.
    notes   : Optional note to append / overwrite.

    Returns
    -------
    True if a matching row was found and updated, False otherwise.
    """
    rows   = _read_rows()
    updated = False

    for row in rows:
        if (
            row["asset"] == asset
            and row["date"].startswith(date)
            and row["result"] == "PENDING"
        ):
            row["result"]  = result.upper()
            row["pnl_pct"] = f"{pnl_pct:.2f}" if pnl_pct is not None else ""
            if notes:
                row["notes"] = notes
            updated = True
            break  # update only the first matching PENDING row

    if updated:
        _write_rows(rows)
        print(f"[paper_tracker] Updated: {asset} {date} → {result}"
              + (f"  ({pnl_pct:+.2f}%)" if pnl_pct is not None else ""))
    else:
        print(f"[paper_tracker] No PENDING row found for {asset} on {date}.")

    return updated


def print_summary() -> None:
    """
    Print overall paper-trade performance to stdout.

    Shows: total signals, wins, losses, pending, win rate, avg P&L per closed trade.
    """
    rows = _read_rows()

    if not rows:
        print("[paper_tracker] No trades logged yet.")
        return

    total   = len(rows)
    wins    = [r for r in rows if r["result"] == "WIN"]
    losses  = [r for r in rows if r["result"] == "LOSS"]
    pending = [r for r in rows if r["result"] == "PENDING"]
    closed  = wins + losses

    win_rate = (len(wins) / len(closed) * 100) if closed else 0.0

    pnl_values: List[float] = []
    for r in closed:
        try:
            pnl_values.append(float(r["pnl_pct"]))
        except (ValueError, KeyError):
            pass

    avg_pnl = sum(pnl_values) / len(pnl_values) if pnl_values else 0.0
    total_pnl = sum(pnl_values)

    # Per-market breakdown
    markets: Dict[str, Dict[str, int]] = {}
    for r in rows:
        m = r.get("market", "unknown")
        if m not in markets:
            markets[m] = {"total": 0, "wins": 0, "losses": 0}
        markets[m]["total"] += 1
        if r["result"] == "WIN":
            markets[m]["wins"] += 1
        elif r["result"] == "LOSS":
            markets[m]["losses"] += 1

    print()
    print("=" * 50)
    print("  PAPER TRADE SUMMARY")
    print("=" * 50)
    print(f"  Total signals : {total}")
    print(f"  Wins          : {len(wins)}")
    print(f"  Losses        : {len(losses)}")
    print(f"  Pending       : {len(pending)}")
    print(f"  Win rate      : {win_rate:.1f}%  ({len(closed)} closed)")
    print(f"  Avg P&L       : {avg_pnl:+.2f}%")
    print(f"  Total P&L     : {total_pnl:+.2f}%")
    print()
    print("  By market:")
    for market, counts in sorted(markets.items()):
        closed_m = counts["wins"] + counts["losses"]
        wr_m     = (counts["wins"] / closed_m * 100) if closed_m else 0.0
        print(f"    {market:<14} total={counts['total']}  "
              f"W={counts['wins']} L={counts['losses']}  WR={wr_m:.0f}%")
    print("=" * 50)
    print()


# ── Module load confirmation ───────────────────────────────────────────────────

print(f"[paper_tracker] Module loaded — CSV: {_CSV_PATH}")


# ── Standalone test ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    from datetime import datetime, timezone

    print("\n=== Paper Tracker Test ===\n")

    # Mock signal
    mock_signal = Signal(
        asset            = "EUR_USD",
        market           = "forex",
        direction        = "LONG",
        entry_low        = 1.08200,
        entry_high       = 1.08350,
        target           = 1.09100,
        stop_loss        = 1.07800,
        risk_reward      = 2.25,
        confidence       = 74,
        confluence_score = 4,
        conditions_met   = ["price_at_demand_zone", "bullish_engulfing"],
        reasoning        = "Test signal.",
        timestamp        = datetime.now(timezone.utc).isoformat(),
    )
    mock_analysis = {"verdict": "BUY", "verdict_emoji": "🟢"}

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    print("1. Logging signal ...")
    log_signal(mock_signal, mock_analysis, notes="test run")

    print("\n2. Summary after log:")
    print_summary()

    print("3. Updating result to WIN (+2.35%) ...")
    update_result("EUR_USD", today, "WIN", pnl_pct=2.35)

    print("\n4. Summary after update:")
    print_summary()
