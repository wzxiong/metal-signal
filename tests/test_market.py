"""Yahoo 响应解析测试（使用录制的响应结构，不联网）。"""

from __future__ import annotations

import unittest
from datetime import date, datetime, timezone

from scripts.market import YahooError, _trading_date, parse_daily, parse_quote


def payload(meta: dict, timestamps: list[int], closes: list[float | None]) -> dict:
    return {
        "chart": {
            "result": [
                {
                    "meta": meta,
                    "timestamp": timestamps,
                    "indicators": {"quote": [{"close": closes}]},
                }
            ],
            "error": None,
        }
    }


def ts(*args: int) -> int:
    return int(datetime(*args, tzinfo=timezone.utc).timestamp())


class QuoteTest(unittest.TestCase):
    def test_reads_regular_market_price(self) -> None:
        data = payload(
            {
                "currency": "USD",
                "symbol": "GC=F",
                "regularMarketPrice": 4000.25,
                "regularMarketTime": ts(2026, 11, 2, 13, 30),
                "gmtoffset": -18000,
            },
            [ts(2026, 10, 31, 13, 30)],
            [3980.0],
        )
        quote = parse_quote(data, "GC=F")
        self.assertAlmostEqual(quote.price, 4000.25)
        self.assertEqual(quote.symbol, "GC=F")
        self.assertEqual(quote.market_date, date(2026, 11, 2))

    def test_falls_back_to_last_close(self) -> None:
        data = payload(
            {"currency": "USD", "gmtoffset": -18000},
            [ts(2026, 10, 30, 13, 30), ts(2026, 10, 31, 13, 30), ts(2026, 11, 2, 13, 30)],
            [1.0, None, 3.5],
        )
        self.assertAlmostEqual(parse_quote(data, "X").price, 3.5)

    def test_error_payload_raises(self) -> None:
        with self.assertRaises(YahooError):
            parse_quote(
                {"chart": {"result": None, "error": {"code": "Not Found"}}}, "X"
            )

    def test_empty_result_raises(self) -> None:
        with self.assertRaises(YahooError):
            parse_quote({"chart": {"result": [], "error": None}}, "X")

    def test_negative_price_is_rejected(self) -> None:
        data = payload(
            {"regularMarketPrice": -5, "gmtoffset": 0},
            [ts(2026, 1, 1)],
            [None],
        )
        with self.assertRaises(YahooError):
            parse_quote(data, "X")


class TradingDateTest(unittest.TestCase):
    """期货日线时间戳是"交易日开盘"，不能直接当日期用。"""

    def test_session_start_maps_to_next_day(self) -> None:
        # 周四 18:00 ET 开盘 -> 归属周五
        self.assertEqual(
            _trading_date({"gmtoffset": -14400}, ts(2026, 10, 29, 22, 0)),
            date(2026, 10, 30),
        )

    def test_sunday_session_maps_to_monday(self) -> None:
        # 周日 18:00 ET 开盘（周一盘） -> 归属周一，而不是周日
        self.assertEqual(
            _trading_date({"gmtoffset": -18000}, ts(2026, 11, 1, 23, 0)),
            date(2026, 11, 2),
        )

    def test_saturday_rolls_to_monday(self) -> None:
        self.assertEqual(
            _trading_date({"gmtoffset": 0}, ts(2026, 10, 30, 18, 0)),  # 周六 03:00? -> 顺延
            date(2026, 11, 2),
        )

    def test_cash_session_keeps_same_day(self) -> None:
        # 指数 09:30 ET 开盘 -> 当天
        self.assertEqual(
            _trading_date({"gmtoffset": -14400}, ts(2026, 10, 30, 13, 30)),
            date(2026, 10, 30),
        )


class DailyTest(unittest.TestCase):
    def test_parses_dates_in_exchange_timezone(self) -> None:
        # 13:30 UTC，纽约时区 -5h -> 当天 08:30，日期不变
        data = payload(
            {"gmtoffset": -18000},
            [ts(2026, 10, 30, 13, 30), ts(2026, 11, 2, 13, 30)],
            [1.0, 2.0],
        )
        rows = parse_daily(data)
        self.assertEqual([d for d, _ in rows], [date(2026, 10, 30), date(2026, 11, 2)])
        self.assertEqual([v for _, v in rows], [1.0, 2.0])

    def test_drops_null_closes_and_dedupes(self) -> None:
        # 2026-01-05 是周一，01-06 是周二
        data = payload(
            {"gmtoffset": 0},
            [ts(2026, 1, 5), ts(2026, 1, 6), ts(2026, 1, 6, 12)],
            [None, 10.0, 11.0],
        )
        rows = parse_daily(data)
        self.assertEqual(rows, [(date(2026, 1, 6), 11.0)])

    def test_sorted_ascending(self) -> None:
        data = payload(
            {"gmtoffset": 0},
            [ts(2026, 1, 5), ts(2026, 1, 2)],
            [5.0, 2.0],
        )
        self.assertEqual([v for _, v in parse_daily(data)], [2.0, 5.0])


if __name__ == "__main__":
    unittest.main()
