"""历史回填测试（注入假日线，不联网）。"""

from __future__ import annotations

import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from scripts import history as hist
from scripts.backfill import build_rows, merge, run


def days(start: date, count: int) -> list[date]:
    return [start + timedelta(days=i) for i in range(count)]


SERIES = {
    "GC=F": (4000.0, 5.0),
    "SI=F": (50.0, 0.1),
    "HG=F": (4.0, 0.01),
    "ALI=F": (3500.0, 10.0),  # 美元/公吨
    "DX-Y.NYB": (100.0, 0.1),
}


def fake_fetcher(symbol: str, years: int):
    base, step = SERIES[symbol]
    start = date(2026, 1, 1)
    return [(d, base + step * i) for i, d in enumerate(days(start, 120))]


class BuildRowsTest(unittest.TestCase):
    def test_converts_units_and_computes_ratios(self) -> None:
        series = {
            "gold": {date(2026, 1, 2): 4000.0},
            "silver": {date(2026, 1, 2): 50.0},
            "copper": {date(2026, 1, 2): 4.0},
            "aluminum": {date(2026, 1, 2): 1.5876},
        }
        rows = build_rows(series)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["date"], "2026-01-02")
        self.assertAlmostEqual(row["gsr"], 80.0, places=2)
        self.assertAlmostEqual(row["copper_gold_ratio"], 0.001, places=4)
        self.assertEqual(row["quadrant"], "B")

    def test_skips_dates_without_any_price(self) -> None:
        rows = build_rows({"gold": {date(2026, 1, 2): 4000.0}})
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]["silver"])


class MergeTest(unittest.TestCase):
    def test_existing_rows_win(self) -> None:
        existing = [{"date": "2026-01-02", "gold": 999.0}]
        backfilled = [{"date": "2026-01-02", "gold": 1.0}, {"date": "2026-01-03", "gold": 2.0}]
        merged = merge(existing, backfilled)
        self.assertEqual([r["date"] for r in merged], ["2026-01-02", "2026-01-03"])
        self.assertEqual(merged[0]["gold"], 999.0)


class RunTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "history.csv"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_backfills_when_history_is_short(self) -> None:
        hist.save([{"date": "2026-01-01", "gold": 3900.0}], self.path)
        rows = run(fetcher=fake_fetcher, history_path=self.path, min_days=120)

        self.assertGreaterEqual(len(rows), 120)
        self.assertEqual(rows[0]["date"], "2026-01-01")
        # 已有真实记录不被覆盖
        self.assertAlmostEqual(hist.find(rows, "2026-01-01")["gold"], 3900.0)
        # 铝已换算成 USD/lb
        sample = hist.find(rows, "2026-01-05")
        self.assertLess(sample["aluminum"], 5)
        self.assertGreater(sample["aluminum"], 1)

    def test_skips_when_enough_history(self) -> None:
        rows = [{"date": f"2026-01-{i:02d}", "gold": 4000.0} for i in range(1, 29)]
        rows += [{"date": f"2026-02-{i:02d}", "gold": 4000.0} for i in range(1, 29)]
        rows += [{"date": f"2026-03-{i:02d}", "gold": 4000.0} for i in range(1, 29)]
        rows += [{"date": f"2026-04-{i:02d}", "gold": 4000.0} for i in range(1, 29)]
        rows += [{"date": f"2026-05-{i:02d}", "gold": 4000.0} for i in range(1, 29)]
        hist.save(rows, self.path)

        called = []

        def fetcher(symbol, years):
            called.append(symbol)
            return []

        result = run(fetcher=fetcher, history_path=self.path, min_days=120)
        self.assertEqual(len(result), len(rows))
        self.assertEqual([r["date"] for r in result], [r["date"] for r in rows])
        self.assertEqual(called, [])

    def test_survives_total_fetch_failure(self) -> None:
        hist.save([{"date": "2026-01-01", "gold": 3900.0}], self.path)

        def broken(symbol, years):
            raise RuntimeError("network down")

        rows = run(fetcher=broken, history_path=self.path, min_days=120)
        self.assertEqual(len(rows), 1)
        self.assertEqual(hist.load(self.path)[0]["date"], "2026-01-01")


if __name__ == "__main__":
    unittest.main()
