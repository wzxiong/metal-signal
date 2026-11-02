"""信号计算与文案一致性测试。"""

from __future__ import annotations

import unittest

from scripts.config import PERCENTILE_MIN_SAMPLES, QUADRANTS
from scripts.signals import (
    build_signals,
    compute_ratios,
    percentile_rank,
    quadrant_code,
    ratio_context,
    trend_pct,
)


class QuadrantTest(unittest.TestCase):
    def test_matrix(self) -> None:
        # (金银比, 铜金比) -> 象限
        cases = {
            (77.0, 0.0015): "B",  # 金强 + 铜弱
            (77.0, 0.0025): "C",  # 金强 + 铜强
            (60.0, 0.0025): "A",  # 银强 + 铜强
            (60.0, 0.0015): "D",  # 银强 + 铜弱
        }
        for (gsr, cgr), expected in cases.items():
            with self.subTest(gsr=gsr, cgr=cgr):
                self.assertEqual(quadrant_code(gsr, cgr), expected)

    def test_boundaries_are_inclusive_on_high_side(self) -> None:
        self.assertEqual(quadrant_code(75.0, 0.002), "C")
        self.assertEqual(quadrant_code(74.99, 0.002), "A")
        self.assertEqual(quadrant_code(75.0, 0.00199), "B")
        self.assertEqual(quadrant_code(74.99, 0.00199), "D")

    def test_missing_input(self) -> None:
        self.assertIsNone(quadrant_code(None, 0.002))
        self.assertIsNone(quadrant_code(75.0, None))
        self.assertIsNone(quadrant_code(None, None))

    def test_four_quadrants_are_all_reachable(self) -> None:
        results = {
            quadrant_code(gsr, cgr)
            for gsr in (60.0, 90.0)
            for cgr in (0.001, 0.003)
        }
        self.assertEqual(results, set("ABCD"))

    def test_risk_score_monotonic(self) -> None:
        scores = [QUADRANTS[c].risk_score for c in "ABCD"]
        self.assertEqual(scores, sorted(scores))
        self.assertEqual(len(set(scores)), 4)


class RatioTest(unittest.TestCase):
    def test_compute_ratios(self) -> None:
        ratios = compute_ratios(
            {"gold": 4000.0, "silver": 50.0, "copper": 4.0, "aluminum": 1.0}
        )
        self.assertAlmostEqual(ratios["gsr"], 80.0)
        self.assertAlmostEqual(ratios["copper_gold_ratio"], 0.001)
        self.assertAlmostEqual(ratios["aluminum_copper_ratio"], 0.25)

    def test_missing_inputs_give_none(self) -> None:
        ratios = compute_ratios({"gold": 4000.0, "silver": None, "copper": None})
        self.assertIsNone(ratios["gsr"])
        self.assertIsNone(ratios["copper_gold_ratio"])

    def test_zero_denominator(self) -> None:
        ratios = compute_ratios({"gold": 0.0, "silver": 50.0, "copper": 4.0})
        self.assertIsNone(ratios["gsr"])


class PercentileTest(unittest.TestCase):
    def test_rank(self) -> None:
        values = [float(v) for v in range(100)]
        self.assertAlmostEqual(percentile_rank(0, values), 1.0)
        self.assertAlmostEqual(percentile_rank(99, values), 100.0)
        self.assertAlmostEqual(percentile_rank(49, values), 50.0)

    def test_needs_enough_samples(self) -> None:
        too_few = [1.0, 2.0, 3.0]
        self.assertIsNone(percentile_rank(2.0, too_few))
        self.assertIsNone(percentile_rank(None, [1.0] * 100))

    def test_context_requires_samples(self) -> None:
        rows = [{"gsr": 70.0 + i} for i in range(10)]
        self.assertIsNone(ratio_context(rows, "gsr", 75.0))

        rows = [{"gsr": 60.0 + (i % 20)} for i in range(PERCENTILE_MIN_SAMPLES + 5)]
        context = ratio_context(rows, "gsr", 70.0)
        self.assertIsNotNone(context)
        assert context is not None
        self.assertGreaterEqual(context["percentile"], 0)
        self.assertLessEqual(context["percentile"], 100)
        self.assertEqual(context["window"], len(rows))

    def test_trend(self) -> None:
        rows = [{"gsr": 100.0}, {"gsr": 110.0}]
        self.assertAlmostEqual(trend_pct(rows, "gsr", 1), 10.0)
        self.assertIsNone(trend_pct(rows, "gsr", 5))


class SignalCopyTest(unittest.TestCase):
    def _build(self, gsr: float, cgr: float, rows=None):
        ratios = {"gsr": gsr, "copper_gold_ratio": cgr, "aluminum_copper_ratio": 0.25}
        prices = {"gold": 4000.0, "silver": 50.0, "copper": 4.0, "aluminum": 1.0}
        return build_signals(prices, ratios, rows or [])

    def test_details_follow_the_actual_threshold(self) -> None:
        """文案里的阈值必须与判定阈值为同一套数字（旧版这里是 75 对 80/70）。"""
        signals = self._build(77.0, 0.0015)
        gsr_line = next(d for d in signals["details"] if "金银比" in d)
        self.assertIn("75", gsr_line)
        self.assertNotIn("> 80", gsr_line)
        self.assertNotIn("70-80", gsr_line)
        self.assertIn("高于阈值", gsr_line)

    def test_sentence_matches_verdict(self) -> None:
        high = self._build(90.0, 0.0015)
        self.assertIn("高于", next(d for d in high["details"] if "金银比" in d))
        low = self._build(60.0, 0.001)
        self.assertIn("低于", next(d for d in low["details"] if "金银比" in d))

    def test_missing_ratio_is_flagged(self) -> None:
        signals = build_signals(
            {"gold": 4000.0, "silver": None, "copper": 4.0},
            {"gsr": None, "copper_gold_ratio": 0.001, "aluminum_copper_ratio": None},
            [],
        )
        self.assertIsNone(signals["quadrant"])
        self.assertEqual(signals["risk_level"], "未知")
        self.assertEqual(signals["actions"], [])
        self.assertTrue(any("无法计算金银比" in d for d in signals["details"]))

    def test_actions_match_quadrant(self) -> None:
        signals = self._build(90.0, 0.0015)  # B
        self.assertEqual(signals["quadrant"], "B")
        self.assertEqual(signals["actions"], list(QUADRANTS["B"].actions))


if __name__ == "__main__":
    unittest.main()
