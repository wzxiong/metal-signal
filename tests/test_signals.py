"""信号计算与文案一致性测试。"""

from __future__ import annotations

import statistics
import unittest

from scripts.config import PERCENTILE_MIN_SAMPLES, QUADRANTS, RATIOS_BY_KEY
from scripts.signals import (
    build_reversal_checklist,
    build_signals,
    real_yield_context,
    resolve_thresholds,
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


class ThresholdTest(unittest.TestCase):
    """阈值必须随历史自校准：固定 0.002 在 2024-2026 从未被触及，会让象限退化成两种。"""

    def _rows(self, gsr: list[float], cgr: list[float]) -> list[dict]:
        return [
            {"date": f"2026-01-{i % 28 + 1:02d}", "gsr": g, "copper_gold_ratio": c}
            for i, (g, c) in enumerate(zip(gsr, cgr))
        ]

    def test_falls_back_to_defaults_without_history(self) -> None:
        resolved = resolve_thresholds(self._rows([70.0, 80.0], [0.001, 0.002]))
        self.assertEqual(resolved["gsr"]["source"], "default")
        self.assertEqual(resolved["gsr"]["value"], RATIOS_BY_KEY["gsr"].threshold)
        self.assertEqual(
            resolved["copper_gold_ratio"]["value"],
            RATIOS_BY_KEY["copper_gold_ratio"].threshold,
        )

    def test_uses_median_when_history_is_enough(self) -> None:
        n = PERCENTILE_MIN_SAMPLES
        gsr = [60.0 + i for i in range(n)]          # 60..119, 中位 ≈ 89.5
        cgr = [0.0010 + i * 0.000001 for i in range(n)]
        resolved = resolve_thresholds(self._rows(gsr, cgr))

        self.assertEqual(resolved["gsr"]["source"], "median")
        self.assertAlmostEqual(resolved["gsr"]["value"], 89.5, places=2)
        self.assertAlmostEqual(
            resolved["copper_gold_ratio"]["value"], statistics.median(cgr), places=6
        )

    def test_absolute_mode_is_still_available(self) -> None:
        n = PERCENTILE_MIN_SAMPLES
        rows = self._rows([60.0 + i for i in range(n)], [0.001] * n)
        resolved = resolve_thresholds(rows, mode="absolute")
        self.assertEqual(resolved["gsr"]["source"], "default")
        self.assertEqual(resolved["gsr"]["value"], 75.0)

    def test_all_four_quadrants_reachable_with_dynamic_thresholds(self) -> None:
        """固定阈值下 CGR 最高 0.0017 < 0.002，A/C 永远到不了；动态阈值必须能覆盖四种状态。"""
        n = PERCENTILE_MIN_SAMPLES
        rows = self._rows(
            [60.0 + (i % 40) for i in range(n)],
            [0.0010 + (i % 20) * 0.00005 for i in range(n)],
        )
        resolved = resolve_thresholds(rows)
        gsr_cut = resolved["gsr"]["value"]
        cgr_cut = resolved["copper_gold_ratio"]["value"]
        codes = {
            quadrant_code(g, c, gsr_cut, cgr_cut)
            for g, c in [(50.0, 0.0009), (50.0, 0.0020), (99.0, 0.0009), (99.0, 0.0020)]
        }
        self.assertEqual(codes, set("ABCD"))

    def test_sentence_shows_resolved_threshold(self) -> None:
        n = PERCENTILE_MIN_SAMPLES
        rows = self._rows([60.0 + i for i in range(n)], [0.0012] * n)
        signals = build_signals(
            {"gold": 4000.0, "silver": 50.0, "copper": 4.0, "aluminum": 1.0},
            {"gsr": 99.0, "copper_gold_ratio": 0.0012, "aluminum_copper_ratio": 0.25},
            rows,
        )
        line = next(d for d in signals["details"] if "金银比" in d)
        self.assertIn("中位", line)
        self.assertNotIn("阈值 75（框架默认", line)
        self.assertIn("中位", signals["method"])


def _series(days: int, start: float = 100.0, step: float = 0.0) -> list[dict]:
    """构造一段连续的日序列。"""
    import datetime as dt

    base = dt.date(2026, 1, 1)
    return [
        {"date": (base + dt.timedelta(days=i)).isoformat(), "value": start + step * i}
        for i in range(days)
    ]


class RealYieldTest(unittest.TestCase):
    """原文：黄金最大的对手是实际利率。"""

    def _rows(self, yld: list[float], gold: list[float] | None = None) -> list[dict]:
        import datetime as dt

        base = dt.date(2026, 1, 1)
        rows = []
        for i, y in enumerate(yld):
            row = {"date": (base + dt.timedelta(days=i)).isoformat(), "real_yield": y}
            if gold:
                row["gold"] = gold[i]
            rows.append(row)
        return rows

    def test_rising_yield_is_headwind(self) -> None:
        rows = self._rows([2.0 + 0.01 * i for i in range(40)])
        context = real_yield_context(rows)
        self.assertEqual(context["state"], "逆风")
        self.assertAlmostEqual(context["change_20d_bp"], 20.0, places=1)

    def test_falling_yield_is_tailwind(self) -> None:
        rows = self._rows([3.0 - 0.01 * i for i in range(40)])
        context = real_yield_context(rows)
        self.assertEqual(context["state"], "顺风")

    def test_flat_yield_is_neutral(self) -> None:
        rows = self._rows([2.0] * 40)
        self.assertEqual(real_yield_context(rows)["state"], "中性")

    def test_returns_none_without_history(self) -> None:
        self.assertIsNone(real_yield_context(self._rows([2.0] * 5)))

    def test_correlation_picks_up_inverse_move(self) -> None:
        import math

        yld = [2.0 + 0.01 * i + 0.02 * math.sin(i) for i in range(80)]
        gold = [4000 - 3000 * (y - 2.0) for y in yld]  # 明确的负相关
        context = real_yield_context(self._rows(yld, gold))
        self.assertIsNotNone(context["corr_gold_60d"])
        self.assertLess(context["corr_gold_60d"], -0.5)


class ReversalChecklistTest(unittest.TestCase):
    """原文的 6 条清单：命中 3 条以上进入高风险区。"""

    def _rows(self, n: int = 120, gold_slope: float = 1.0, silver_slope: float = 1.0,
              dxy_slope: float = 0.0, yield_slope: float = 0.0) -> list[dict]:
        import datetime as dt

        base = dt.date(2025, 1, 1)
        rows = []
        for i in range(n):
            rows.append({
                "date": (base + dt.timedelta(days=i)).isoformat(),
                "gold": 3000 + gold_slope * i,
                "silver": 40 + silver_slope * i,
                "copper": 5.0,
                "aluminum": 1.2,
                "dxy": 100 + dxy_slope * i,
                "gsr": (3000 + gold_slope * i) / (40 + silver_slope * i),
                "copper_gold_ratio": 5.0 / (3000 + gold_slope * i),
                "aluminum_copper_ratio": 1.2 / 5.0,
                "real_yield": 2.0 + yield_slope * i,
                "vix": 15.0,
            })
        return rows

    def test_score_and_level(self) -> None:
        ratios = {"aluminum_copper_ratio": 0.24}
        result = build_reversal_checklist(self._rows(), {}, ratios)
        self.assertEqual(result["total"], 5)  # 叙事变化不计分
        self.assertLess(result["score"], 2)
        self.assertFalse(result["alert"])
        self.assertEqual(result["level"], "趋势健康")

    def test_downtrend_marks_list_as_not_applicable(self) -> None:
        rows = self._rows(gold_slope=-10.0)
        result = build_reversal_checklist(rows, {}, {"aluminum_copper_ratio": 0.24})
        self.assertEqual(result["level"], "已在下行段")
        self.assertIn("趋势已经转弱", result["note"])

    def test_headwind_triggers(self) -> None:
        rows = self._rows(dxy_slope=0.2, yield_slope=0.02)  # 美元与利率同时上行
        result = build_reversal_checklist(rows, {}, {"aluminum_copper_ratio": 0.24})
        headwind = next(i for i in result["items"] if i["key"] == "headwind")
        self.assertTrue(headwind["hit"])
        self.assertIn("20 日", headwind["detail"])

    def test_all_four_scored_items_present(self) -> None:
        result = build_reversal_checklist(self._rows(), {}, {"aluminum_copper_ratio": 0.24})
        keys = [i["key"] for i in result["items"]]
        self.assertEqual(
            keys, ["acceleration", "divergence", "lagging", "headwind", "narrative", "late_stage"]
        )


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
