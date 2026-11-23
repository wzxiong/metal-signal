"""抓取主流程的端到端测试（不联网，注入假行情）。"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable, Sequence

from scripts import history as hist
from scripts.fetch_data import run
from scripts.market import Quote

TODAY = date(2026, 11, 2)
NOW = datetime(2026, 11, 2, 8, 0, tzinfo=timezone.utc)

# 基准记录：2026-10-31
BASELINE = {
    "date": "2026-10-31",
    "timestamp": "2026-10-31T20:00:00+00:00",
    "gold": 3900.0,
    "silver": 49.0,
    "copper": 3.9,
    "aluminum": 1.55,
    "dxy": 99.0,
    "gsr": 79.59,
    "copper_gold_ratio": 0.001,
    "aluminum_copper_ratio": 0.3974,
    "quadrant": "B",
}


def quotes(prices: dict[str, float], market_date: date = TODAY) -> Callable:
    """构造一个假的抓取函数：symbol -> 报价。"""

    def fetcher(symbols: Sequence[str]) -> Quote | None:
        for symbol in symbols:
            if symbol in prices:
                return Quote(price=prices[symbol], symbol=symbol, market_date=market_date)
        return None

    return fetcher


GOOD = {
    "GC=F": 4000.0,
    "SI=F": 50.0,
    "HG=F": 4.0,
    "ALI=F": 3500.0,  # 美元/公吨
    "DX-Y.NYB": 100.0,
}


class Harness(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.latest = self.dir / "latest.json"
        self.history = self.dir / "history.csv"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def seed(self, rows: list[dict]) -> None:
        hist.save(rows, self.history)

    def go(self, fetcher, **kwargs):
        return run(
            quote_fetcher=fetcher,
            macro_fetcher=lambda: {},
            now=NOW,
            latest_path=self.latest,
            history_path=self.history,
            **kwargs,
        )


class HappyPathTest(Harness):
    def test_full_record(self) -> None:
        self.seed([BASELINE])
        record, code = self.go(quotes(GOOD))

        self.assertEqual(code, 0)
        self.assertEqual(record["as_of"], "2026-11-02")
        self.assertFalse(record["stale"])

        self.assertAlmostEqual(record["gold"], 4000.0)
        self.assertAlmostEqual(record["gsr"], 80.0, places=2)
        self.assertAlmostEqual(record["copper_gold_ratio"], 0.001, places=4)
        # 铝：3500 美元/吨 -> 美元/磅
        self.assertAlmostEqual(record["aluminum"], 3500 / 2204.6226218, places=4)

        # 金强(80 >= 75) + 铜弱(0.001 < 0.002) => B
        self.assertEqual(record["signals"]["quadrant"], "B")

        # 涨跌对比上一交易日 10-31
        self.assertEqual(record["baseline"]["date"], "2026-10-31")
        self.assertTrue(record["baseline"]["usable"])
        self.assertAlmostEqual(record["changes"]["gold"]["pct"], 2.56, places=2)

    def test_writes_json_and_history(self) -> None:
        self.seed([BASELINE])
        self.go(quotes(GOOD))

        payload = json.loads(self.latest.read_text(encoding="utf-8"))
        self.assertEqual(payload["schema"], 2)
        self.assertIn("signals", payload)

        rows = hist.load(self.history)
        self.assertEqual([r["date"] for r in rows], ["2026-10-31", "2026-11-02"])

    def test_same_day_rerun_overwrites(self) -> None:
        self.seed([BASELINE])
        self.go(quotes(GOOD))
        self.go(quotes({**GOOD, "GC=F": 4010.0}))
        rows = hist.load(self.history)
        self.assertEqual(len(rows), 2)  # 11-02 只有一行
        self.assertAlmostEqual(rows[-1]["gold"], 4010.0)

    def test_no_history_still_works(self) -> None:
        record, code = self.go(quotes(GOOD))
        self.assertEqual(code, 0)
        self.assertEqual(record["changes"], {})
        self.assertIsNone(record["baseline"])
        self.assertEqual(len(hist.load(self.history)), 1)


class StalenessTest(Harness):
    def test_old_baseline_hides_changes(self) -> None:
        old = dict(BASELINE, date="2026-05-15", timestamp="2026-05-15T20:00:00+00:00")
        self.seed([old])
        record, _ = self.go(quotes(GOOD))

        self.assertFalse(record["baseline"]["usable"])
        self.assertEqual(record["changes"], {})  # 不再把半年涨跌当成日涨跌

    def test_total_failure(self) -> None:
        self.seed([BASELINE])
        before = self.history.read_text(encoding="utf-8")
        record, code = self.go(quotes({}))

        self.assertEqual(code, 1)
        self.assertTrue(record["stale"])
        self.assertIn("历史缓存", record["stale_reason"])
        self.assertEqual(self.history.read_text(encoding="utf-8"), before)
        # 用上一有效值兜底展示
        self.assertAlmostEqual(record["gold"], 3900.0)

    def test_future_dated_rows_are_dropped(self) -> None:
        """历史里残留的未来日期行（盘中价写成了当天）应被清理。"""
        import json

        from scripts import history as hist_mod

        self.seed([
            BASELINE,
            {"date": "2026-11-04", "timestamp": "", "gold": 9999.0, "silver": 1.0,
             "copper": 1.0, "aluminum": 1.0, "dxy": 100.0},
        ])
        self.go(quotes(GOOD))  # 观察日 = 11-02
        dates = [r["date"] for r in hist_mod.load(self.history)]
        self.assertNotIn("2026-11-04", dates)

    def test_observation_date_falls_back_to_history(self) -> None:
        """完全抓不到行情时，观察日取历史最后一天，而不是今天。"""
        self.seed([BASELINE])
        record, _ = self.go(quotes({}))
        self.assertEqual(record["as_of"], "2026-10-31")

    def test_out_of_range_aluminum_is_degraded(self) -> None:
        self.seed([BASELINE])
        record, code = self.go(quotes({**GOOD, "ALI=F": 100.0}))  # 100 美元/吨显然不对

        self.assertEqual(code, 0)
        self.assertIn("aluminum", record["degraded"])
        self.assertTrue(record["stale"])
        self.assertAlmostEqual(record["aluminum"], 1.55)  # 沿用上一交易日

    def test_single_day_jump_is_guarded(self) -> None:
        self.seed([BASELINE])
        record, _ = self.go(quotes({**GOOD, "GC=F": 7000.0}))  # +79%

        self.assertIn("gold", record["degraded"])
        self.assertAlmostEqual(record["gold"], 3900.0)
        self.assertNotIn("gold", record["changes"])

    def test_guard_skipped_when_baseline_is_old(self) -> None:
        old = dict(BASELINE, date="2026-05-15", timestamp="2026-05-15T20:00:00+00:00")
        self.seed([old])
        record, _ = self.go(quotes({**GOOD, "GC=F": 7000.0}))
        self.assertNotIn("gold", record["degraded"])
        self.assertAlmostEqual(record["gold"], 7000.0)

    def test_partial_failure_keeps_other_data(self) -> None:
        self.seed([BASELINE])
        prices = {k: v for k, v in GOOD.items() if k != "SI=F"}
        record, code = self.go(quotes(prices))

        self.assertEqual(code, 0)
        self.assertIn("silver", record["degraded"])
        self.assertAlmostEqual(record["silver"], 49.0)
        self.assertTrue(record["stale"])
        self.assertIsNotNone(record["gsr"])  # 用沿用的白银仍可算比率


class EventDetectionTest(unittest.TestCase):
    """变盘事件：象限切换需要连续确认，反转清单首次升级。"""

    def _rows(self, codes):
        return [
            {"date": f"2026-10-{i + 1:02d}", "quadrant": code, "gsr": 80.0,
             "copper_gold_ratio": 0.0016, "reversal_score": 1}
            for i, code in enumerate(codes)
        ]

    def _signals(self, code="C", streak=3, alert=False):
        return {
            "quadrant": code,
            "regime_days": streak,
            "thresholds": {
                "gsr": {"value": 75.0, "display": "75.00"},
                "copper_gold_ratio": {"value": 0.002, "display": "0.002"},
                "aluminum_copper_ratio": {"value": 0.6, "display": "0.6"},
            },
            "reversal": {"score": 3 if alert else 1, "total": 5, "alert": alert,
                         "items": [{"label": "宏观逆风", "hit": True}]},
        }

    def _detect(self, codes, **kw):
        from scripts.fetch_data import detect_events

        rows = self._rows(codes)
        return detect_events(rows, {"gsr": 80.0, "copper_gold_ratio": 0.0016},
                             self._signals(**kw), rows[-2])

    def test_no_alert_before_confirmation(self) -> None:
        self.assertEqual(self._detect(["B", "C", "C"], streak=2), [])

    def test_alert_on_the_confirmation_day(self) -> None:
        events = self._detect(["B", "C", "C", "C"], streak=3)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["title"], "象限切换 B → C")
        self.assertIn("已连续 3 个交易日确认", events[0]["detail"])

    def test_no_repeat_after_confirmation(self) -> None:
        self.assertEqual(self._detect(["B", "C", "C", "C", "C"], streak=4), [])

    def test_reversal_escalation(self) -> None:
        events = self._detect(["C", "C", "C"], streak=3, alert=True)
        self.assertTrue(any(e["type"] == "reversal" for e in events))


class GuardUnitTest(unittest.TestCase):
    def test_missing_symbol_is_not_silently_ignored(self) -> None:
        from scripts.fetch_data import collect

        result = collect(quotes({"GC=F": 4000.0}))
        self.assertEqual(result.prices.get("gold"), 4000.0)
        self.assertIn("silver", result.failed)
        self.assertIn("silver", result.errors)


if __name__ == "__main__":
    unittest.main()
