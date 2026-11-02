"""历史数据读写与迁移测试。"""

from __future__ import annotations

import csv
import tempfile
import unittest
from datetime import date
from pathlib import Path

from scripts import history as hist


LEGACY_HEADER = [
    "timestamp",
    "beijing",
    "gold",
    "silver",
    "copper",
    "aluminum",
    "dxy",
    "gsr",
    "copper_gold_ratio",
    "aluminum_copper_ratio",
    "quadrant",
]


class LegacyMigrationTest(unittest.TestCase):
    def _write(self, rows: list[list[str]]) -> Path:
        tmp = Path(tempfile.mkdtemp()) / "history.csv"
        with tmp.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(LEGACY_HEADER)
            writer.writerows(rows)
        return tmp

    def test_reads_legacy_columns(self) -> None:
        path = self._write(
            [
                [
                    "2026-05-15T22:28:00",
                    "2026-05-15 22:28",
                    "4559.9",
                    "77.325",
                    "6.323",
                    "1.5913",
                    "99.172",
                    "58.97",
                    "0.00139",
                    "0.2517",
                    "D",
                ]
            ]
        )
        rows = hist.load(path)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["date"], "2026-05-15")
        self.assertEqual(rows[0]["quadrant"], "D")
        self.assertAlmostEqual(rows[0]["gold"], 4559.9)

    def test_duplicate_days_collapse_to_last(self) -> None:
        path = self._write(
            [
                ["2026-05-15T22:28:00", "2026-05-15 22:28", "4559.9", "77.3", "6.3", "1.5", "99", "58.9", "0.0013", "0.25", "D"],
                ["2026-05-15T23:30:00", "2026-05-15 23:30", "4564.8", "77.4", "6.3", "1.5", "99", "58.9", "0.0013", "0.25", "D"],
            ]
        )
        rows = hist.load(path)
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0]["gold"], 4564.8)

    def test_bad_rows_are_dropped(self) -> None:
        path = self._write(
            [
                ["", "", "", "", "", "", "", "", "", "", ""],
                ["2026-05-15T22:28:00", "2026-05-15 22:28", "", "", "", "", "", "", "", "", ""],
                ["2026-05-16T22:28:00", "2026-05-16 22:28", "4560", "77", "6", "", "", "59", "", "", ""],
            ]
        )
        rows = hist.load(path)
        self.assertEqual([r["date"] for r in rows], ["2026-05-16"])

    def test_invalid_quadrant_is_blanked(self) -> None:
        path = self._write(
            [["2026-05-15T22:28:00", "2026-05-15 22:28", "4560", "77", "6", "", "", "59", "", "", "X"]]
        )
        self.assertEqual(hist.load(path)[0]["quadrant"], "")


class RoundTripTest(unittest.TestCase):
    def test_save_and_load(self) -> None:
        tmp = Path(tempfile.mkdtemp()) / "history.csv"
        rows = [
            {"date": "2026-11-01", "timestamp": "2026-11-01T00:00:00+00:00",
             "gold": 4000.0, "silver": 50.0, "copper": 4.0, "aluminum": 1.0,
             "dxy": 100.0, "gsr": 80.0, "copper_gold_ratio": 0.001,
             "aluminum_copper_ratio": 0.25, "quadrant": "B"},
            {"date": "2026-11-02", "timestamp": "2026-11-02T00:00:00+00:00",
             "gold": None, "silver": 51.0, "copper": 4.1, "aluminum": None,
             "dxy": 101.0, "gsr": 80.5, "copper_gold_ratio": 0.0011,
             "aluminum_copper_ratio": None, "quadrant": "C"},
        ]
        hist.save(rows, tmp)
        loaded = hist.load(tmp)
        self.assertEqual([r["date"] for r in loaded], ["2026-11-01", "2026-11-02"])
        self.assertIsNone(loaded[1]["gold"])
        self.assertAlmostEqual(loaded[1]["gsr"], 80.5)

    def test_upsert_replaces_same_day(self) -> None:
        rows = [{"date": "2026-11-02", "gold": 1.0}]
        updated = hist.upsert(rows, {"date": "2026-11-02", "gold": 2.0})
        self.assertEqual(len(updated), 1)
        self.assertEqual(updated[0]["gold"], 2.0)

    def test_upsert_keeps_order(self) -> None:
        rows = [{"date": "2026-11-01"}, {"date": "2026-11-03"}]
        updated = hist.upsert(rows, {"date": "2026-11-02"})
        self.assertEqual([r["date"] for r in updated], ["2026-11-01", "2026-11-02", "2026-11-03"])


class QueryTest(unittest.TestCase):
    rows = [
        {"date": "2026-10-30", "gsr": 70.0},
        {"date": "2026-11-02", "gsr": 71.0},
    ]

    def test_previous_before(self) -> None:
        self.assertEqual(hist.previous_before(self.rows, "2026-11-02")["date"], "2026-10-30")
        self.assertIsNone(hist.previous_before(self.rows, "2026-10-30"))

    def test_series_skips_empty(self) -> None:
        rows = [{"gsr": 70.0}, {"gsr": None}, {"gsr": "71"}]
        self.assertEqual(hist.series(rows, "gsr"), [70.0])

    def test_trim(self) -> None:
        rows = [{"date": "2020-01-01"}, {"date": "2026-11-01"}]
        kept = hist.trim(rows, today=date(2026, 11, 2), keep_days=30)
        self.assertEqual([r["date"] for r in kept], ["2026-11-01"])


if __name__ == "__main__":
    unittest.main()
