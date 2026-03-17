"""
tests/test_bug_fixes.py
Unit tests for the three bug fixes applied to the AI Trading Agent.

Bug 1 — Stop loss placement for SHORT/LONG signals (signal_engine.py)
Bug 2 — Claude API timeout/error logging (claude_client.py)
Bug 3 — Forex candle count for EMA200 (forex.py + signal_engine.py)
"""
from __future__ import annotations

import sys
import os
import types
import unittest
from unittest.mock import MagicMock, patch

# ── Stub out third-party packages that aren't in this venv ────────────────────
# This lets us import project modules without installing the full prod stack.

def _stub(name: str) -> types.ModuleType:
    mod = types.ModuleType(name)
    sys.modules[name] = mod
    return mod

for _pkg in ("pycoingecko", "alpaca_trade_api", "alpaca_trade_api.rest",
             "oandapyV20", "oandapyV20.endpoints", "oandapyV20.endpoints.instruments",
             "python_telegram_bot", "telegram", "telegram.ext",
             "mplfinance", "matplotlib", "matplotlib.pyplot",
             "matplotlib.patches", "matplotlib.lines"):
    if _pkg not in sys.modules:
        _stub(_pkg)

# CoinGeckoAPI stub
_cg = _stub("pycoingecko")
_cg.CoinGeckoAPI = MagicMock

# Ensure project root is on sys.path
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


# ══════════════════════════════════════════════════════════════════════════════
# Bug 1 — Stop loss side validation
# ══════════════════════════════════════════════════════════════════════════════

class TestStopLossSidePlacement(unittest.TestCase):
    """
    calculate_entry_target_stop must never produce a stop on the wrong side
    of the entry zone, regardless of what swing detection returns.
    """

    def _run(self, direction, swing_stop_return, entry_low, entry_high, price):
        """Run calculate_entry_target_stop with a mocked _find_swing_stop."""
        import pandas as pd
        from signal_agent.signal_engine import calculate_entry_target_stop

        analysis = {
            "key_levels": {
                "support": [entry_low * 0.99],
                "resistance": [entry_high * 1.01],
                "current_price": price,
            },
            "fibonacci": {},
            "supply_demand_zones": {"demand_zones": [], "supply_zones": []},
        }

        df = pd.DataFrame({
            "open":   [price] * 30,
            "high":   [price * 1.01] * 30,
            "low":    [price * 0.99] * 30,
            "close":  [price] * 30,
            "volume": [1000.0] * 30,
        })

        with patch("signal_agent.signal_engine._find_swing_stop",
                   return_value=swing_stop_return):
            return calculate_entry_target_stop(analysis, direction, price, df)

    # ── LONG ──────────────────────────────────────────────────────────────────

    def test_long_valid_stop_preserved(self):
        """A correct swing stop below entry_low must be preserved."""
        _, _, _, stop, _ = self._run("LONG", 97.0, 99.0, 101.0, 100.0)
        self.assertLess(stop, 99.0, "LONG stop must be below entry_low")

    def test_long_bad_swing_stop_above_entry_corrected(self):
        """Swing stop returned above entry_low must be overridden."""
        _, _, _, stop, _ = self._run("LONG", 105.0, 99.0, 101.0, 100.0)
        self.assertLess(stop, 99.0,
            f"LONG stop ({stop}) must be below entry_low (99.0) after correction")

    def test_long_stop_at_entry_boundary_corrected(self):
        """Swing stop exactly equal to entry_low must be pushed below."""
        _, _, _, stop, _ = self._run("LONG", 99.0, 99.0, 101.0, 100.0)
        self.assertLess(stop, 99.0,
            "LONG stop at entry_low must be pushed below entry_low")

    def test_long_no_swing_fallback_below_entry(self):
        """When _find_swing_stop returns None the fallback must still be below entry."""
        _, _, _, stop, _ = self._run("LONG", None, 99.0, 101.0, 100.0)
        self.assertLess(stop, 99.0,
            f"LONG fallback stop ({stop}) must be below entry_low (99.0)")

    # ── SHORT ─────────────────────────────────────────────────────────────────

    def test_short_valid_stop_preserved(self):
        """A correct swing stop above entry_high must be preserved."""
        _, _, _, stop, _ = self._run("SHORT", 103.0, 99.0, 101.0, 100.0)
        self.assertGreater(stop, 101.0, "SHORT stop must be above entry_high")

    def test_short_bad_swing_stop_below_entry_corrected(self):
        """Swing stop returned below entry_high must be overridden (the reported bug)."""
        _, _, _, stop, _ = self._run("SHORT", 95.0, 99.0, 101.0, 100.0)
        self.assertGreater(stop, 101.0,
            f"SHORT stop ({stop}) must be above entry_high (101.0) after correction")

    def test_short_stop_at_entry_boundary_corrected(self):
        """Swing stop exactly equal to entry_high must be pushed above."""
        _, _, _, stop, _ = self._run("SHORT", 101.0, 99.0, 101.0, 100.0)
        self.assertGreater(stop, 101.0,
            "SHORT stop at entry_high must be pushed above entry_high")

    def test_short_no_swing_fallback_above_entry(self):
        """When _find_swing_stop returns None the fallback must still be above entry."""
        _, _, _, stop, _ = self._run("SHORT", None, 99.0, 101.0, 100.0)
        self.assertGreater(stop, 101.0,
            f"SHORT fallback stop ({stop}) must be above entry_high (101.0)")


# ══════════════════════════════════════════════════════════════════════════════
# Bug 2 — Claude API timeout and detailed error logging
# ══════════════════════════════════════════════════════════════════════════════

class TestClaudeClientTimeout(unittest.TestCase):
    """
    analyze_signal() must pass timeout=30 to messages.create() and propagate
    real exception types with logging so Railway shows the root cause.
    """

    def _make_signal(self):
        from signal_agent.signal_engine import Signal
        from datetime import datetime, timezone
        return Signal(
            asset="BTC", market="crypto", direction="LONG",
            entry_low=50000.0, entry_high=50200.0,
            target=53000.0, stop_loss=48000.0,
            risk_reward=2.5, confidence=70, confluence_score=4,
            conditions_met=["trend_aligned"], reasoning="test",
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    def _good_response(self):
        mock_resp = MagicMock()
        mock_resp.content = [MagicMock(
            text='{"narrative":"ok","verdict":"BUY","verdict_emoji":"🟢",'
                 '"risk_note":"none","confluence_summary":"test"}'
        )]
        return mock_resp

    def test_timeout_30_passed_to_messages_create(self):
        """messages.create must receive timeout=30."""
        from utils.claude_client import analyze_signal

        with patch("utils.claude_client._client") as mock_factory:
            mock_client = MagicMock()
            mock_factory.return_value = mock_client
            mock_client.messages.create.return_value = self._good_response()

            analyze_signal(self._make_signal(), {})

            kwargs = mock_client.messages.create.call_args.kwargs
            self.assertIn("timeout", kwargs, "timeout kwarg must be present")
            self.assertEqual(kwargs["timeout"], 30, "timeout must be 30 seconds")

    def test_api_timeout_error_logged_and_reraised(self):
        """APITimeoutError must be logged at ERROR level with asset name, then re-raised."""
        import anthropic
        from utils.claude_client import analyze_signal

        with patch("utils.claude_client._client") as mock_factory:
            mock_client = MagicMock()
            mock_factory.return_value = mock_client
            mock_client.messages.create.side_effect = anthropic.APITimeoutError(
                request=MagicMock()
            )
            with patch("utils.claude_client.log") as mock_log:
                with self.assertRaises(anthropic.APITimeoutError):
                    analyze_signal(self._make_signal(), {})
                self.assertTrue(mock_log.error.called,
                    "APITimeoutError must be logged at ERROR level")
                self.assertIn("BTC", str(mock_log.error.call_args),
                    "Error log must include asset name (BTC) for Railway debugging")

    def test_api_status_error_logged_and_reraised(self):
        """APIStatusError must be logged at ERROR level and re-raised."""
        import anthropic
        from utils.claude_client import analyze_signal

        mock_resp_obj = MagicMock()
        mock_resp_obj.status_code = 503

        with patch("utils.claude_client._client") as mock_factory:
            mock_client = MagicMock()
            mock_factory.return_value = mock_client
            mock_client.messages.create.side_effect = anthropic.APIStatusError(
                message="Service unavailable",
                response=mock_resp_obj,
                body=None,
            )
            with patch("utils.claude_client.log") as mock_log:
                with self.assertRaises(anthropic.APIStatusError):
                    analyze_signal(self._make_signal(), {})
                self.assertTrue(mock_log.error.called,
                    "APIStatusError must be logged at ERROR level")

    def test_generic_exception_logged_with_exc_info(self):
        """Any other exception must be logged with exc_info=True for full Railway traceback."""
        from utils.claude_client import analyze_signal

        with patch("utils.claude_client._client") as mock_factory:
            mock_client = MagicMock()
            mock_factory.return_value = mock_client
            mock_client.messages.create.side_effect = ConnectionError("network down")

            with patch("utils.claude_client.log") as mock_log:
                with self.assertRaises(ConnectionError):
                    analyze_signal(self._make_signal(), {})
                self.assertTrue(mock_log.error.called)
                call_kwargs = mock_log.error.call_args.kwargs
                self.assertTrue(call_kwargs.get("exc_info"),
                    "Generic exceptions need exc_info=True so Railway shows a traceback")


# ══════════════════════════════════════════════════════════════════════════════
# Bug 3 — Forex candle count for EMA200
# ══════════════════════════════════════════════════════════════════════════════

class TestForexCandleCount(unittest.TestCase):
    """
    Default candle count must be >= 300 in both forex.py and signal_engine.py
    so EMA200 always has sufficient history.
    """

    def test_forex_get_ohlcv_default_count_is_300(self):
        """forex.get_ohlcv default count parameter must be >= 300."""
        import inspect
        import data_feeds.forex as forex_module

        default = inspect.signature(forex_module.get_ohlcv).parameters["count"].default
        self.assertGreaterEqual(default, 300,
            f"forex.get_ohlcv default count is {default}, must be >= 300 for EMA200")

    def test_signal_engine_forex_params_count_is_300(self):
        """_OHLCV_PARAMS['forex']['count'] must be >= 300."""
        from signal_agent.signal_engine import _OHLCV_PARAMS

        count = _OHLCV_PARAMS.get("forex", {}).get("count")
        self.assertIsNotNone(count, "_OHLCV_PARAMS['forex'] must have a 'count' key")
        self.assertGreaterEqual(count, 300,
            f"_OHLCV_PARAMS['forex']['count'] is {count}, must be >= 300 for EMA200")

    def test_forex_api_call_receives_300_candles(self):
        """When get_ohlcv is called with default args the HTTP request must ask for >= 300 candles."""
        import data_feeds.forex as forex_module

        with patch("data_feeds.forex._get") as mock_get:
            mock_get.return_value = {"candles": []}
            forex_module.get_ohlcv("EUR_USD")

            args, kwargs = mock_get.call_args
            params = kwargs.get("params") or (args[1] if len(args) > 1 else {})
            self.assertGreaterEqual(params.get("count", 0), 300,
                f"API request sent count={params.get('count')}, need >= 300")


if __name__ == "__main__":
    unittest.main(verbosity=2)
