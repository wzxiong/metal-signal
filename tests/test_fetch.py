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


class GuardUnitTest(unittest.TestCase):
    def test_missing_symbol_is_not_silently_ignored(self) -> None:
        from scripts.fetch_data import collect

        result = collect(quotes({"GC=F": 4000.0}))
        self.assertEqual(result.prices.get("gold"), 4000.0)
        self.assertIn("silver", result.failed)
        self.assertIn("silver", result.errors)


if __name__ == "__main__":
    unittest.main()
