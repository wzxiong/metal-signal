#!/usr/bin/env python3
"""把 latest.json 里的"变盘事件"整理成一条可发布的提醒。

  python -m scripts.alert --output alert.json

输出 JSON：{"alert": bool, "title": "...", "body": "..."}。
工作流读取后，若有 alert 就开一个 GitHub Issue（@ 仓库所有者，手机/邮箱可收到推送）。
不依赖任何第三方密钥；换渠道（Telegram / 企业微信 / Bark）只需要替换发送那一步。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scripts.config import (
    ASSETS,
    LATEST_FILE,
    QUADRANTS,
    RATIOS_BY_KEY,
    SITE_URL,
)


def _fmt(value: Any, decimals: int) -> str:
    if not isinstance(value, (int, float)):
        return "—"
    return f"{value:,.{decimals}f}"


def build_message(latest: dict[str, Any], owner: str | None = None) -> dict[str, Any]:
    events = latest.get("events") or []
    if not events:
        return {"alert": False, "title": "", "body": ""}

    signals = latest.get("signals") or {}
    code = str(signals.get("quadrant") or "—")
    name = str(signals.get("quadrant_name") or "")
    as_of = latest.get("as_of") or ""

    headline = events[0]["title"]
    title = f"🔔 变盘提醒 · {headline} · {as_of}"

    lines: list[str] = []
    if owner:
        lines.append(f"@{owner}")
        lines.append("")

    lines.append(f"**观察日 {as_of}** · 当前象限 **{code} {name}**"
                 f"（风险 {signals.get('risk_level', '—')}）")
    lines.append("")
    lines.append("### 触发事件")
    for event in events:
        detail = event.get("detail") or ""
        lines.append(f"- **{event['title']}**")
        if detail:
            lines.append(f"  - {detail}")
    lines.append("")

    lines.append("### 当前读数")
    lines.append("")
    lines.append("| 指标 | 数值 | 阈值 / 分位 |")
    lines.append("| --- | --- | --- |")
    for asset in ASSETS:
        value = latest.get(asset.key)
        change = (latest.get("changes") or {}).get(asset.key) or {}
        pct = change.get("pct")
        delta = f"（{pct:+.2f}%）" if isinstance(pct, (int, float)) else ""
        unit = f" {asset.unit}" if asset.unit else ""
        lines.append(f"| {asset.label} | {_fmt(value, asset.decimals)}{unit}{delta} | | ")

    thresholds = signals.get("thresholds") or {}
    context = signals.get("context") or {}
    for key, spec in RATIOS_BY_KEY.items():
        value = latest.get(key)
        cut = thresholds.get(key) or {}
        pct = (context.get(key) or {}).get("percentile")
        tail = f"{cut.get('display', '—')}" + (f" · {pct:.0f}% 分位" if isinstance(pct, (int, float)) else "")
        lines.append(f"| {spec.label} | {_fmt(value, spec.decimals)} | {tail} |")

    reversal = signals.get("reversal") or {}
    if reversal:
        lines.append("")
        lines.append(
            f"### 反转清单 {reversal.get('score')} / {reversal.get('total')} 命中 · {reversal.get('level')}"
        )
        for item in reversal.get("items", []):
            mark = "●" if item.get("hit") is True else ("○" if item.get("hit") is False else "–")
            lines.append(f"- {mark} **{item['label']}**：{item.get('detail', '')}")

    lines.append("")
    lines.append(f"[查看完整页面]({SITE_URL})")
    lines.append("")
    lines.append("> 本提醒由「贵金属信号灯」自动生成，仅为状态跟踪，不构成投资建议。")

    return {"alert": True, "title": title, "body": "\n".join(lines)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成变盘提醒")
    parser.add_argument("--latest", default=str(LATEST_FILE))
    parser.add_argument("--output", default="alert.json")
    parser.add_argument("--owner", default=os.environ.get("GITHUB_REPOSITORY_OWNER", ""))
    args = parser.parse_args(argv)

    path = Path(args.latest)
    latest: dict[str, Any] = {}
    if path.exists():
        latest = json.loads(path.read_text(encoding="utf-8"))

    message = build_message(latest, owner=args.owner or None)

    # 没有事件时也写文件，方便工作流用 jq 判断
    Path(args.output).write_text(
        json.dumps(message, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if message["alert"]:
        print(f"[{stamp}] 需要提醒：{message['title']}")
    else:
        print(f"[{stamp}] 无变盘事件，跳过提醒")

    return 0


if __name__ == "__main__":
    sys.exit(main())
