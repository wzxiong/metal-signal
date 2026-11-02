#!/usr/bin/env python3
"""历史数据读写。

CSV 以"交易日"为主键，一天只有一行：
  * 抓取当天多次运行时，后一次覆盖前一次（不会产生同一天多条记录）
  * 兼容旧版列名（beijing / timestamp）与旧版时间格式
"""

from __future__ import annotations

import csv
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable

from scripts.config import HISTORY_FILE, HISTORY_KEEP_DAYS

# 新版列顺序
FIELDS = [
    "date",
    "timestamp",
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

NUMERIC_FIELDS = (
    "gold",
    "silver",
    "copper",
    "aluminum",
    "dxy",
    "gsr",
    "copper_gold_ratio",
    "aluminum_copper_ratio",
)


def parse_number(value: Any) -> float | None:
    """把 CSV 里的字符串转成 float，空值/脏值返回 None。"""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text or text.lower() in {"none", "nan", "null", "—", "-"}:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    if number != number:  # NaN
        return None
    return number


def _parse_day(value: str) -> str | None:
    """接受 2026-11-02 或 2026-11-02T16:10 之类，返回 YYYY-MM-DD。"""
    text = str(value or "").strip()
    if len(text) < 10:
        return None
    candidate = text[:10]
    try:
        date.fromisoformat(candidate)
    except ValueError:
        return None
    return candidate


def normalize_row(raw: dict[str, str]) -> dict[str, Any] | None:
    """把任意版本的一行整理成标准结构；无法识别则返回 None。"""
    if not isinstance(raw, dict):
        return None

    day = _parse_day(raw.get("date", ""))
    if not day:
        # 旧版列：beijing 优先，其次 timestamp
        day = _parse_day(raw.get("beijing", "")) or _parse_day(raw.get("timestamp", ""))
    if not day:
        return None

    row: dict[str, Any] = {
        "date": day,
        "timestamp": (raw.get("timestamp") or "").strip(),
    }
    for key in NUMERIC_FIELDS:
        row[key] = parse_number(raw.get(key))

    quadrant = str(raw.get("quadrant") or "").strip().upper()
    row["quadrant"] = quadrant if quadrant in {"A", "B", "C", "D"} else ""

    # 一行里至少要有一个价格，否则视为脏数据
    if not any(row.get(k) is not None for k in ("gold", "silver", "copper", "aluminum")):
        return None
    return row


def load(path: Path | str = HISTORY_FILE) -> list[dict[str, Any]]:
    """读取并规范化历史，按日期升序、同日只保留最后一条。"""
    path = Path(path)
    if not path.exists():
        return []

    by_day: dict[str, dict[str, Any]] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for raw in csv.DictReader(handle):
            row = normalize_row(raw)
            if row:
                by_day[row["date"]] = row  # 同日后者覆盖前者

    return [by_day[day] for day in sorted(by_day)]


def upsert(rows: list[dict[str, Any]], row: dict[str, Any]) -> list[dict[str, Any]]:
    """写入一行：同一天覆盖，返回按日期排序的新列表。"""
    by_day = {r["date"]: r for r in rows}
    by_day[row["date"]] = row
    return [by_day[day] for day in sorted(by_day)]


def save(rows: Iterable[dict[str, Any]], path: Path | str = HISTORY_FILE) -> None:
    """以标准列顺序写回 CSV（原子写入）。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _cell(row.get(key)) for key in FIELDS})
    tmp.replace(path)


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def trim(
    rows: list[dict[str, Any]],
    today: date | None = None,
    keep_days: int = HISTORY_KEEP_DAYS,
) -> list[dict[str, Any]]:
    """只保留最近 keep_days 个自然日。"""
    if not rows:
        return []
    today = today or date.today()
    cutoff = (today - timedelta(days=keep_days)).isoformat()
    return [r for r in rows if r["date"] >= cutoff]


def previous_before(
    rows: list[dict[str, Any]], day: str
) -> dict[str, Any] | None:
    """取严格早于 day 的最后一条记录（即"上一个交易日"）。"""
    candidate = None
    for row in rows:
        if row["date"] < day:
            candidate = row
        else:
            break
    return candidate


def series(rows: list[dict[str, Any]], key: str) -> list[float]:
    """抽出某个字段的数值序列（跳过空值）。"""
    out = []
    for row in rows:
        value = row.get(key)
        if isinstance(value, (int, float)):
            out.append(float(value))
    return out


def find(rows: list[dict[str, Any]], day: str) -> dict[str, Any] | None:
    for row in rows:
        if row["date"] == day:
            return row
    return None
