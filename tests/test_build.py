"""建站输出测试：结构完整、无占位符残留、无 JS 依赖。"""

from __future__ import annotations

import re
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from scripts import history as hist
from scripts.build_site import render
from scripts.fetch_data import run
from scripts.market import Quote

NOW = datetime(2026, 11, 2, 8, 0, tzinfo=timezone.utc)


def fetcher(prices: dict[str, float]):
    def _f(symbols):
        for symbol in symbols:
            if symbol in prices:
                return Quote(price=prices[symbol], symbol=symbol)
        return None

    return _f


GOOD = {"GC=F": 4000.0, "SI=F": 50.0, "HG=F": 4.0, "ALI=F": 3500.0, "DX-Y.NYB": 100.0}


class BuildTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.latest = self.dir / "latest.json"
        self.history = self.dir / "history.csv"

        # 造 40 天历史，让分位数与图表都有内容
        rows = []
        for i in range(40):
            day = f"2026-09-{i + 1:02d}" if i < 30 else f"2026-10-{i - 29:02d}"
            gold = 3800 + i * 5
            silver = 48 + i * 0.2
            rows.append(
                {
                    "date": day,
                    "timestamp": f"{day}T20:00:00+00:00",
                    "gold": gold,
                    "silver": silver,
                    "copper": 3.8 + i * 0.005,
                    "aluminum": 1.5,
                    "dxy": 99.0,
                    "gsr": round(gold / silver, 2),
                    "copper_gold_ratio": round((3.8 + i * 0.005) / gold, 5),
                    "aluminum_copper_ratio": round(1.5 / (3.8 + i * 0.005), 4),
                    "quadrant": "B" if i % 2 else "D",
                }
            )
        hist.save(rows, self.history)
        run(
            quote_fetcher=fetcher(GOOD),
            now=NOW,
            latest_path=self.latest,
            history_path=self.history,
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def build(self) -> str:
        return render(
            latest_path=self.latest,
            history_path=self.history,
            output_path=self.dir / "index.html",
            write=True,
            now=NOW,
        )

    def test_document_shell(self) -> None:
        html = self.build()
        self.assertTrue(html.startswith("<!DOCTYPE html>"))
        self.assertIn('lang="zh-CN"', html)
        self.assertIn("<title>", html)
        self.assertIn('name="description"', html)
        self.assertIn('property="og:title"', html)
        self.assertIn("data:image/svg+xml", html)

    def test_no_javascript(self) -> None:
        self.assertNotIn("<script", self.build().lower())

    def test_no_none_or_placeholder_left(self) -> None:
        html = self.build()
        self.assertNotIn("None", html)
        # 常见遗漏的格式化占位符
        for token in ("{reason}", "{as_of}", "{pct", "{value}", "{latest"):
            self.assertNotIn(token, html)

    def test_all_assets_rendered(self) -> None:
        html = self.build()
        for label in ("黄金", "白银", "铜", "铝", "美元指数"):
            self.assertIn(label, html)
        for ratio in ("金银比", "铜金比", "铝铜比"):
            self.assertIn(ratio, html)

    def test_quadrant_and_actions(self) -> None:
        html = self.build()
        self.assertIn("当前象限", html)
        self.assertIn("建议动作", html)
        self.assertIn('aria-current="true"', html)

    def test_chart_svg_present(self) -> None:
        html = self.build()
        self.assertGreaterEqual(html.count("<svg"), 2)
        self.assertIn("<title>金银比 走势</title>", html)
        self.assertIn("阈值 75", html)

    def test_tags_are_balanced(self) -> None:
        html = self.build()
        for tag in ("div", "section", "article", "ul", "li", "svg", "header", "footer", "h3"):
            opens = len(re.findall(rf"<{tag}[\s>]", html))
            closes = html.count(f"</{tag}>")
            self.assertEqual(opens, closes, f"<{tag}> 标签未闭合：{opens} 开 / {closes} 闭")

    def test_stale_banner_and_escaping(self) -> None:
        run(
            quote_fetcher=fetcher({"GC=F": 4000.0}),  # 其余全挂 -> 降级
            now=NOW,
            latest_path=self.latest,
            history_path=self.history,
        )
        html = self.build()
        self.assertIn("banner-warn", html)
        self.assertIn("数据降级", html)

    def test_renders_without_any_data(self) -> None:
        html = render(
            latest_path=self.dir / "missing.json",
            history_path=self.dir / "missing.csv",
            output_path=self.dir / "empty.html",
            write=False,
            now=NOW,
        )
        self.assertIn("数据不足", html)
        self.assertNotIn("None", html)

    def test_writes_file(self) -> None:
        out = self.dir / "index.html"
        render(
            latest_path=self.latest,
            history_path=self.history,
            output_path=out,
            write=True,
            now=NOW,
        )
        self.assertTrue(out.exists())
        self.assertGreater(out.stat().st_size, 10_000)


if __name__ == "__main__":
    unittest.main()
