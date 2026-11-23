"""变盘提醒：确认逻辑与文案。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.alert import build_message, main


def latest(events=None, **over):
    base = {
        "as_of": "2026-11-24",
        "timestamp_beijing": "2026-11-24 06:15",
        "gold": 3515.4, "silver": 41.03, "copper": 5.477, "aluminum": 1.2973, "dxy": 103.81,
        "gsr": 85.6, "copper_gold_ratio": 0.00156, "aluminum_copper_ratio": 0.2307,
        "changes": {"gold": {"value": -1.0, "pct": -0.03}},
        "signals": {
            "quadrant": "C",
            "quadrant_name": "复杂盘",
            "risk_level": "中高",
            "regime_days": 3,
            "thresholds": {
                "gsr": {"value": 66.0, "display": "66.00", "source": "median"},
                "copper_gold_ratio": {"value": 0.0013, "display": "0.0013", "source": "median"},
                "aluminum_copper_ratio": {"value": 0.2439, "display": "0.2439", "source": "median"},
            },
            "context": {"gsr": {"percentile": 93.2, "window": 250}},
            "reversal": {
                "score": 1, "total": 5, "level": "需警惕",
                "items": [{"label": "宏观逆风", "hit": True, "detail": "实际利率 20 日 +24bp"}],
            },
        },
        "events": events or [],
    }
    base.update(over)
    return base


class AlertMessageTest(unittest.TestCase):
    def test_no_events_means_no_alert(self) -> None:
        self.assertFalse(build_message({})["alert"])
        self.assertFalse(build_message(latest())["alert"])

    def test_quadrant_change_message(self) -> None:
        events = [{
            "type": "quadrant",
            "title": "象限切换 B → C",
            "detail": "防守盘 → 复杂盘，已连续 3 个交易日确认。",
        }]
        message = build_message(latest(events), owner="wzxiong")

        self.assertTrue(message["alert"])
        self.assertIn("象限切换 B → C", message["title"])
        self.assertIn("2026-11-24", message["title"])
        self.assertIn("@wzxiong", message["body"])
        self.assertIn("已连续 3 个交易日确认", message["body"])
        self.assertIn("https://wzxiong.github.io/metal-signal/", message["body"])
        self.assertIn("金银比", message["body"])

    def test_body_lists_every_event(self) -> None:
        events = [
            {"type": "quadrant", "title": "象限切换 A → D", "detail": "x"},
            {"type": "reversal", "title": "反转信号 3 / 5 命中", "detail": "价格加速"},
        ]
        body = build_message(latest(events))["body"]
        for event in events:
            self.assertIn(event["title"], body)

    def test_cli_writes_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "latest.json"
            out = Path(tmp) / "alert.json"
            src.write_text(json.dumps(latest([
                {"type": "quadrant", "title": "象限切换 B → C", "detail": "d"}
            ]), ensure_ascii=False), encoding="utf-8")

            main(["--latest", str(src), "--output", str(out), "--owner", "u"])
            payload = json.loads(out.read_text(encoding="utf-8"))
            self.assertTrue(payload["alert"])
            self.assertIn("@u", payload["body"])


if __name__ == "__main__":
    unittest.main()
