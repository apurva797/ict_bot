"""Tests for the UI-independent platform core: indicators, ICT, risk, execution."""

import unittest

import numpy as np
import pandas as pd

import demo_data
from demo_safety import SafetyError
from platform_core.analytics import compute_analytics, equity_curve
from platform_core.errors import PlatformError
from platform_core.execution import PaperExecutionEngine
from platform_core.ict import analyze_ict, candles_from_frame
from platform_core.indicators import INDICATOR_REGISTRY, atr as atr_indicator, compute_indicator
from platform_core.risk import RiskEngine, position_size
from platform_core.service import TradingService
from platform_core.settings import RiskConfig, Settings, load_settings
from platform_core.signals import Signal
from platform_core.status import DataHealth, Status


def candles(count=250, seed=11, freq="5min"):
    index = pd.date_range("2026-01-01", periods=count, freq=freq, tz="UTC")
    random = np.random.default_rng(seed)
    closes = 100 + np.cumsum(random.normal(0, 0.3, count))
    return pd.DataFrame({
        "open": closes - 0.1,
        "high": closes + 0.5,
        "low": closes - 0.5,
        "close": closes,
        "volume": random.integers(100, 900, count).astype(float),
    }, index=index)


def long_signal(**overrides) -> Signal:
    payload = {
        "strategy_id": "ict", "strategy_name": "ARJUNA", "symbol": "BTC/USDT",
        "timeframe": "5m", "direction": "LONG", "timestamp": pd.Timestamp("2026-01-01", tz="UTC"),
        "reason": "sweep + displacement + MSS + FVG", "entry": 100.0,
        "stop_loss": 99.0, "take_profit": 102.0,
    }
    payload.update(overrides)
    return Signal(**payload)


def with_metadata(signal: Signal, **metadata) -> Signal:
    return Signal(**{**signal.__dict__, "metadata": metadata})


class IndicatorTests(unittest.TestCase):
    def setUp(self):
        # Two full UTC days so day-structure indicators have real reference data.
        self.frame = candles(count=600, freq="5min")

    def test_every_registered_indicator_computes_from_real_candles(self):
        level_types = {"SUPPORT_RESISTANCE", "PREVIOUS_HIGH_LOW", "SESSION_HIGH_LOW"}
        for name, spec in INDICATOR_REGISTRY.items():
            with self.subTest(indicator=name):
                result = compute_indicator(self.frame, name)
                self.assertEqual(list(result.columns), list(spec.outputs))
                self.assertGreater(len(result), 0)
                self.assertTrue(result.notna().any().any())
                if name not in level_types:
                    self.assertEqual(len(result), len(self.frame))

    def test_sma_and_ema_match_known_mathematics(self):
        closes = [float(value) for value in self.frame["close"]]
        self.assertAlmostEqual(
            float(compute_indicator(self.frame, "SMA", period=20)["sma"].iloc[-1]),
            float(np.mean(closes[-20:])), places=8)
        alpha = 2 / 13
        expected = closes[0]
        for value in closes[1:]:
            expected = alpha * value + (1 - alpha) * expected
        self.assertAlmostEqual(
            float(compute_indicator(self.frame, "EMA", period=12)["ema"].iloc[-1]),
            expected, places=6)

    def test_rsi_is_bounded_and_reacts_to_a_pure_uptrend(self):
        series = compute_indicator(self.frame, "RSI", period=14)["rsi"].dropna()
        self.assertTrue(series.between(0, 100).all())
        up = self.frame.copy()
        up["close"] = np.arange(len(up), dtype=float) + 100
        up["open"] = up["close"] - 0.1
        up["high"] = up["close"] + 0.2
        up["low"] = up["close"] - 0.2
        self.assertGreater(float(compute_indicator(up, "RSI", period=14)["rsi"].iloc[-1]), 90)

    def test_macd_atr_bollinger_and_vwap_relationships_hold(self):
        macd = compute_indicator(self.frame, "MACD")
        self.assertAlmostEqual(float((macd["macd"] - macd["signal"]).iloc[-1]),
                               float(macd["histogram"].iloc[-1]), places=10)
        bands = compute_indicator(self.frame, "BB", period=20, deviations=2.0)
        self.assertGreaterEqual(float(bands["upper"].iloc[-1]), float(bands["lower"].iloc[-1]))
        self.assertGreater(float(compute_indicator(self.frame, "ATR", period=14)["atr"].iloc[-1]), 0)
        vwap_value = float(compute_indicator(self.frame, "VWAP")["vwap"].iloc[-1])
        self.assertGreater(vwap_value, float(self.frame["low"].min()))
        self.assertLess(vwap_value, float(self.frame["high"].max()))

    def test_supertrend_direction_is_only_plus_or_minus_one(self):
        result = compute_indicator(self.frame, "SUPERTREND")
        self.assertTrue(set(result["direction"].dropna().unique()).issubset({1.0, -1.0}))

    def test_unknown_indicator_raises_key_error(self):
        with self.assertRaises(KeyError):
            compute_indicator(self.frame, "__import__")


class IctAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.frame = demo_data._load_sample_ohlcv("BTC/USDT", "5m", 500)

    def test_analysis_matches_the_existing_signal_and_exposes_stages(self):
        from strategies.ict import ict_signal

        analysis = analyze_ict(self.frame, rr=2.0)
        legacy = ict_signal(candles_from_frame(self.frame))
        self.assertEqual(analysis.side, legacy["side"])
        self.assertEqual(analysis.score, legacy["score"])
        self.assertEqual(
            {stage.name for stage in analysis.stages},
            {"HTF_BIAS", "LIQUIDITY_SWEEP", "DISPLACEMENT", "MSS", "FVG", "ORDER_BLOCK",
             "SCORE_THRESHOLD"})
        for stage in analysis.stages:
            self.assertTrue(stage.detail)

    def test_insufficient_and_empty_data_return_truthful_statuses(self):
        with self.assertRaises(PlatformError) as short:
            analyze_ict(self.frame.tail(50))
        self.assertEqual(short.exception.status, Status.INSUFFICIENT_DATA)
        with self.assertRaises(PlatformError) as empty:
            analyze_ict(pd.DataFrame())
        self.assertEqual(empty.exception.status, Status.DATA_UNAVAILABLE)

    def test_no_setup_is_neutral_not_an_error(self):
        analysis = analyze_ict(self.frame)
        self.assertIn(analysis.side, {"LONG", "SHORT", "NEUTRAL"})
        if not analysis.has_setup:
            self.assertIsNone(analysis.entry)
            self.assertIsNone(analysis.target)
            self.assertIn("SCORE_THRESHOLD", [stage.name for stage in analysis.failed_stages])

    def test_levels_are_derived_from_real_atr_when_a_setup_exists(self):
        analysis = analyze_ict(self.frame, rr=2.0, atr_period=14, atr_multiplier=1.5)
        if analysis.has_setup:
            atr_value = float(atr_indicator(self.frame, 14).dropna().iloc[-1])
            self.assertAlmostEqual(analysis.risk_distance, atr_value * 1.5, places=8)
            self.assertAlmostEqual(abs(analysis.target - analysis.entry) / analysis.risk_distance,
                                   2.0, places=6)
        else:
            self.assertIsNone(analysis.entry)

    def test_overlays_only_contain_detected_structures(self):
        overlays = analyze_ict(self.frame).overlays
        self.assertIn("liquidity", overlays)
        self.assertIn("dealing_range", overlays)
        self.assertIn("sweep_detected", overlays["liquidity"])
        self.assertIn("bullish_mss", overlays["structure"])


class RiskEngineTests(unittest.TestCase):
    def setUp(self):
        self.engine = RiskEngine(RiskConfig())
        self.account = {"balance": 10_000.0}

    def test_valid_signal_is_approved_and_sized_deterministically(self):
        decision = self.engine.validate(long_signal(), self.account)
        self.assertTrue(decision.approved)
        self.assertEqual(decision.status, Status.SIGNAL_CREATED)
        self.assertAlmostEqual(decision.quantity, 100.0, places=6)
        self.assertAlmostEqual(decision.risk_amount, 100.0, places=6)
        self.assertAlmostEqual(decision.actual_rr, 2.0, places=6)
        self.assertEqual(self.engine.validate(long_signal(), self.account).quantity,
                         decision.quantity)

    def test_invalid_levels_low_rr_and_risk_limits_are_rejected(self):
        cases = {
            "below minimum rr": long_signal(take_profit=100.5),
            "long stop above entry": long_signal(stop_loss=101.0),
            "long target below entry": long_signal(take_profit=99.0),
            "missing levels": long_signal(entry=None),
            "zero risk distance": long_signal(stop_loss=100.0),
            "excess risk": with_metadata(long_signal(), risk_fraction=0.05),
            "excess leverage": with_metadata(long_signal(), leverage=3.0),
        }
        for label, signal in cases.items():
            with self.subTest(case=label):
                decision = self.engine.validate(signal, self.account)
                self.assertFalse(decision.approved)
                self.assertIn(decision.status, {Status.RISK_REJECTED, Status.NO_VALID_SETUP})

    def test_exposure_cooldown_and_daily_limits_are_enforced(self):
        for kwargs in ({"cooldown_active": True}, {"open_positions": 1}, {"daily_trades": 3},
                       {"daily_r": -2.5}, {"data_health": DataHealth.STALE},
                       {"data_health": DataHealth.UNAVAILABLE}):
            with self.subTest(**kwargs):
                decision = self.engine.validate(long_signal(), self.account, **kwargs)
                self.assertFalse(decision.approved)
                self.assertEqual(decision.status, Status.RISK_REJECTED)

    def test_short_signal_sizing_and_direction_validation(self):
        decision = self.engine.validate(
            long_signal(direction="SHORT", entry=100.0, stop_loss=101.0, take_profit=98.0),
            self.account)
        self.assertTrue(decision.approved)
        self.assertAlmostEqual(decision.quantity, 100.0, places=6)
        self.assertFalse(self.engine.validate(
            long_signal(direction="SHORT", entry=100.0, stop_loss=99.0, take_profit=98.0),
            self.account).approved)

    def test_position_size_guards_never_produce_invalid_orders(self):
        for args in ((0, 0.01, 100, 99), (10_000, 0, 100, 99), (10_000, 0.01, 0, 99),
                     (10_000, 0.01, 100, 100), (-5, 0.01, 100, 99)):
            quantity, risk_amount = position_size(*args)
            self.assertEqual(quantity, 0.0)
            self.assertEqual(risk_amount, 0.0)

    def test_leverage_caps_notional(self):
        quantity, _ = position_size(10_000, 0.5, 100, 99, max_leverage=1.0)
        self.assertLessEqual(quantity * 100, 10_000 + 1e-9)


class PaperExecutionTests(unittest.TestCase):
    def setUp(self):
        self.engine = PaperExecutionEngine(RiskEngine(RiskConfig()))
        self.account = {"balance": 10_000.0}
        self.timestamp = pd.Timestamp("2026-01-01", tz="UTC")

    def test_full_lifecycle_creates_order_position_and_realized_pnl(self):
        order, decision = self.engine.submit(long_signal(), self.account, fill_price=100.0)
        self.assertTrue(decision.approved)
        self.assertEqual(order.status, "FILLED")
        self.assertEqual(order.environment, "PAPER")
        self.assertTrue(order.order_id.startswith("paper-order-"))
        self.engine.open_position(order)
        self.assertTrue(order.position_id.startswith("paper-position-"))
        self.engine.close_position(order, 102.0, "TAKE_PROFIT",
                                   self.timestamp + pd.Timedelta(hours=2))
        self.assertEqual(order.status, "CLOSED")
        self.assertEqual(order.exit_reason, "TAKE_PROFIT")
        self.assertAlmostEqual(order.realized_pnl,
                               (102.0 - 100.0) * order.quantity - order.fees, places=8)
        self.assertGreater(order.fees, 0)
        self.assertEqual(len(self.engine.orders), 1)

    def test_actual_fill_price_is_used_for_pnl_and_fees(self):
        order, decision = self.engine.submit(long_signal(), self.account, fill_price=99.5)
        self.assertTrue(decision.approved)
        self.assertEqual(order.entry, 99.5)
        self.engine.open_position(order)
        self.engine.close_position(order, 101.5, "TAKE_PROFIT", self.timestamp)
        self.assertAlmostEqual(
            order.realized_pnl,
            (101.5 - 99.5) * order.quantity - order.fees,
            places=8,
        )

    def test_position_lifecycle_rejects_duplicate_open_and_close(self):
        order, _ = self.engine.submit(long_signal(), self.account, fill_price=100.0)
        self.engine.open_position(order)
        with self.assertRaises(PlatformError):
            self.engine.open_position(order)
        self.engine.close_position(order, 102.0, "TAKE_PROFIT", self.timestamp)
        with self.assertRaises(PlatformError):
            self.engine.close_position(order, 102.0, "DUPLICATE", self.timestamp)

    def test_non_finite_inputs_are_rejected(self):
        with self.assertRaises(PlatformError):
            self.engine.submit(long_signal(), self.account, fill_price=float("nan"))
        self.assertFalse(self.engine.risk_engine.validate(
            with_metadata(long_signal(), risk_fraction=float("nan")), self.account).approved)
        self.assertFalse(self.engine.risk_engine.validate(
            with_metadata(long_signal(), leverage=float("inf")), self.account).approved)

    def test_risk_rejection_creates_no_order(self):
        order, decision = self.engine.submit(long_signal(take_profit=100.5), self.account)
        self.assertIsNone(order)
        self.assertEqual(decision.status, Status.RISK_REJECTED)
        self.assertEqual(self.engine.orders, [])

    def test_unfilled_order_cannot_open_a_position(self):
        order, _ = self.engine.submit(long_signal(), self.account)
        self.assertEqual(order.status, "CREATED")
        with self.assertRaises(PlatformError):
            self.engine.open_position(order)

    def test_invalid_exit_price_is_refused(self):
        order, _ = self.engine.submit(long_signal(), self.account, fill_price=100.0)
        self.engine.open_position(order)
        for price in (0, -5, None):
            with self.subTest(price=price):
                with self.assertRaises(PlatformError):
                    self.engine.close_position(order, price, "TEST")

    def test_short_close_produces_negative_pnl(self):
        order, _ = self.engine.submit(
            long_signal(direction="SHORT", entry=100.0, stop_loss=101.0, take_profit=98.0),
            self.account, fill_price=100.0)
        self.engine.open_position(order)
        self.engine.close_position(order, 101.0, "STOP_LOSS", self.timestamp)
        self.assertLess(order.realized_pnl, 0)

    def test_live_order_path_fails_closed(self):
        with self.assertRaises(SafetyError):
            self.engine.place_live_order("BTC/USDT", "BUY", 1)


class AnalyticsTests(unittest.TestCase):
    def test_empty_history_returns_none_not_fake_performance(self):
        result = compute_analytics([])
        self.assertEqual(result["total_trades"], 0)
        self.assertIsNone(result["win_rate_pct"])
        self.assertIsNone(result["profit_factor"])
        self.assertEqual(result["realized_pnl"], 0.0)

    def test_statistics_are_derived_from_real_trades(self):
        trades = [
            {"net_pnl": 200.0, "risk_amount": 100.0, "fees": 2.0, "closed_at": "2026-01-01"},
            {"net_pnl": -100.0, "risk_amount": 100.0, "fees": 2.0, "closed_at": "2026-01-02"},
            {"net_pnl": 100.0, "risk_amount": 100.0, "fees": 2.0, "closed_at": "2026-01-03"},
        ]
        result = compute_analytics(trades)
        self.assertEqual(result["total_trades"], 3)
        self.assertEqual(result["winning_trades"], 2)
        self.assertAlmostEqual(result["win_rate_pct"], 200 / 3, places=6)
        self.assertAlmostEqual(result["realized_pnl"], 200.0, places=6)
        self.assertAlmostEqual(result["profit_factor"], 3.0, places=6)
        self.assertAlmostEqual(result["average_r"], 200.0 / 300.0, places=6)
        self.assertLess(result["max_drawdown_pct"], 0)
        self.assertEqual(result["fees_paid"], 6.0)

    def test_equity_curve_is_chronological(self):
        points = equity_curve([
            {"net_pnl": 10.0, "closed_at": "2026-01-03"},
            {"net_pnl": -5.0, "closed_at": "2026-01-01"},
        ], starting_balance=100.0)
        self.assertEqual([point["closed_at"] for point in points],
                         ["2026-01-01", "2026-01-03"])
        self.assertAlmostEqual(points[-1]["equity"], 105.0, places=6)


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.service = TradingService()
        self.frame = demo_data._load_sample_ohlcv("BTC/USDT", "5m", 500)

    def test_health_reports_paper_only_and_configured_risk(self):
        health = self.service.health()
        self.assertEqual(health["environment"], "PAPER")
        self.assertTrue(health["paper_only"])
        self.assertFalse(health["live_orders_enabled"])
        self.assertEqual(health["risk"]["risk_per_trade"], 0.01)
        self.assertEqual(health["risk"]["min_rr"], 1.5)
        self.assertEqual(health["risk"]["max_leverage"], 1.0)
        self.assertEqual(health["risk"]["cooldown_minutes"], 30)
        self.assertGreaterEqual(len(health["indicators"]), 15)

    def test_strategy_status_states_are_explicit(self):
        self.assertEqual(self.service.strategy_status(None)["state"], "DATA_UNAVAILABLE")
        self.assertEqual(self.service.strategy_status(self.frame.tail(20))["state"],
                         "INSUFFICIENT_DATA")
        self.assertIn(self.service.strategy_status(self.frame)["state"],
                      {"ACTIVE", "MONITORING"})

    def test_settings_are_env_driven_and_never_enable_live_trading(self):
        settings = load_settings()
        self.assertTrue(settings.is_paper_only)
        self.assertFalse(settings.kill_zone_enabled)
        self.assertEqual(settings.risk.min_rr, 1.5)
        self.assertEqual(settings.risk.default_rr, 2.0)
        custom = Settings(risk=RiskConfig(risk_per_trade=0.005, min_rr=1.5))
        self.assertTrue(custom.is_paper_only)
        self.assertEqual(custom.risk.risk_per_trade, 0.005)

    def test_catalogue_lists_real_indicator_metadata(self):
        catalogue = self.service.catalogue()["indicators"]
        identifiers = {item["id"] for item in catalogue}
        for expected in ("EMA", "SMA", "RSI", "MACD", "ATR", "BB", "VWAP"):
            self.assertIn(expected, identifiers)
        for item in catalogue:
            self.assertTrue(item["name"])
            self.assertTrue(item["outputs"])


if __name__ == "__main__":
    unittest.main()