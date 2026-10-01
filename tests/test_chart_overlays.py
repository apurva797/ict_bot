"""Chart overlay tests: real indicator series and real ICT structures only."""

import json
import unittest

import demo_data
from charting import make_chart_payload
from charting_overlays import build_ict_overlay, build_overlay_series
from platform_core.ict import analyze_ict


class OverlaySeriesTests(unittest.TestCase):
    def setUp(self):
        self.frame = demo_data._load_sample_ohlcv("BTC/USDT", "5m", 300)

    def test_overlays_contain_real_indicator_values(self):
        overlays = build_overlay_series(self.frame, [
            {"id": "EMA", "params": {"period": 20}},
            {"id": "RSI"},
            {"id": "SUPERTREND"},
        ])
        names = {overlay["name"] for overlay in overlays}
        self.assertIn("EMA", names)
        self.assertIn("RSI", names)
        for overlay in overlays:
            self.assertTrue(overlay["data"])
            for point in overlay["data"]:
                self.assertIsInstance(point["time"], int)
                self.assertIsInstance(point["value"], float)

    def test_overlay_times_align_with_real_candles(self):
        valid_times = {int(stamp.value // 1_000_000_000) for stamp in self.frame.index}
        for overlay in build_overlay_series(self.frame, [{"id": "EMA"}, {"id": "BB"}]):
            for point in overlay["data"]:
                self.assertIn(point["time"], valid_times)

    def test_default_selection_includes_trend_and_momentum(self):
        names = {overlay["name"] for overlay in build_overlay_series(self.frame)}
        self.assertIn("EMA", names)
        self.assertIn("RSI", names)

    def test_unknown_indicator_and_bad_params_are_skipped_not_fatal(self):
        overlays = build_overlay_series(self.frame, [
            {"id": "__import__"},
            {"id": "EMA", "params": {"not_a_parameter": 5}},
            {"id": "EMA"},
        ])
        self.assertTrue(any(overlay["name"] == "EMA" for overlay in overlays))

    def test_empty_frame_yields_no_overlays(self):
        self.assertEqual(build_overlay_series(None), [])


class IctOverlayTests(unittest.TestCase):
    def setUp(self):
        self.frame = demo_data._load_sample_ohlcv("BTC/USDT", "5m", 500)
        self.analysis = analyze_ict(self.frame).to_dict()

    def test_overlay_only_contains_detected_structures(self):
        overlay = build_ict_overlay(self.analysis)
        self.assertIsNotNone(overlay)
        low = float(self.frame["low"].min())
        high = float(self.frame["high"].max())
        for line in overlay["lines"]:
            self.assertIsInstance(line["price"], float)
            # Levels are derived from the real dealing range of these candles.
            self.assertGreaterEqual(line["price"], low * 0.999)
            self.assertLessEqual(line["price"], high * 1.001)

    def test_levels_appear_only_when_a_setup_exists(self):
        overlay = build_ict_overlay(self.analysis)
        titles = {line["title"] for line in overlay["lines"]}
        if self.analysis["has_setup"]:
            self.assertTrue({"Entry", "SL", "TP"} & titles)
        else:
            self.assertFalse({"Entry", "SL", "TP"} & titles)

    def test_toggles_disable_sections(self):
        disabled = build_ict_overlay(self.analysis, {
            "liquidity": False, "premium_discount": False, "fvg": False,
            "mss": False, "displacement": False, "levels": False,
        })
        self.assertIsNone(disabled)
        enabled = build_ict_overlay(self.analysis, {"fvg": False, "mss": False,
                                                   "displacement": False, "levels": False})
        titles = {line["title"] for line in (enabled or {"lines": []})["lines"]}
        self.assertNotIn("Entry", titles)

    def test_no_analysis_means_no_overlay(self):
        self.assertIsNone(build_ict_overlay(None))
        self.assertIsNone(build_ict_overlay({"overlays": {}}))


class PayloadTests(unittest.TestCase):
    def setUp(self):
        self.frame = demo_data._load_sample_ohlcv("BTC/USDT", "5m", 200)
        self.bad_overlay = {"name": "bad", "series": [{"time": 1, "value": float("nan")}]}

    def test_payload_is_json_serializable_and_carries_overlays(self):
        analysis = analyze_ict(self.frame).to_dict()
        payload = make_chart_payload(
            self.frame, "BTC/USDT 5m", "BTC/USDT|5m",
            overlays=build_overlay_series(self.frame),
            ict=build_ict_overlay(analysis),
        )
        json.dumps(payload)
        self.assertEqual(len(payload["candles"]), len(self.frame))
        self.assertEqual(len(payload["volumes"]), len(self.frame))
        self.assertTrue(payload["overlays"])
        self.assertEqual(len(payload["digest"]), 64)

    def test_digest_changes_when_overlays_change(self):
        plain = make_chart_payload(self.frame, "t", "d")
        with_overlay = make_chart_payload(
            self.frame, "t", "d", overlays=build_overlay_series(self.frame, [{"id": "EMA"}]))
        self.assertNotEqual(plain["digest"], with_overlay["digest"])
        self.assertEqual(plain["candles"], with_overlay["candles"])

    def test_malformed_overlays_ict_and_drawings_are_dropped(self):
        payload = make_chart_payload(
            self.frame, "t", "d",
            overlays=[{"name": "empty", "series": []}, "not-a-dict", self.bad_overlay],
            ict={"lines": [{"price": None}], "boxes": [], "markers": []},
            drawings=[{"kind": "unknown"},
                      {"kind": "horizontal_line", "points": [[1, "x"]]}],
        )
        self.assertEqual(payload["overlays"], [])
        self.assertEqual(payload["drawings"], [])
        self.assertEqual(payload["ict"]["lines"], [])

    def test_valid_drawings_survive_sanitization(self):
        payload = make_chart_payload(
            self.frame, "t", "d",
            drawings=[
                {"kind": "horizontal_line", "points": [[0, 100.0]], "color": "#f00"},
                {"kind": "trend_line", "points": [[0, 1.0], [10, 2.0]]},
            ],
        )
        self.assertEqual(len(payload["drawings"]), 2)
        self.assertEqual(payload["drawings"][0]["kind"], "horizontal_line")


if __name__ == "__main__":
    unittest.main()