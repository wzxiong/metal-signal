#!/usr/bin/env python3
"""历史回填：一次性补齐日线历史，让走势图与分位数立刻有意义。

  python -m scripts.backfill            # 历史不足时自动回填
  python -m scripts.backfill --force    # 强制重拉并补齐缺失日期

幂等：只补历史里缺的交易日，已有记录（真实快照）优先，不会被覆盖。
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path
from typing import Any, Callable, Sequence

from scripts import history as hist
from scripts.config import (
    ASSETS,
    BACKFILL_MIN_DAYS,
    BACKFILL_YEARS,
    HISTORY_FILE,
    RATIOS,
)
from scripts.market import fetch_daily
from scripts.signals import compute_ratios, quadrant_code, resolve_thresholds

DailyFetcher = Callable[[str, int], list[tuple[date, float]]]


def fetch_asset_series(
    fetcher: DailyFetcher, years: int
) -> dict[str, dict[date, float]]:
    """抓每个品种的日线，换算单位并做区间校验。"""
    out: dict[str, dict[date, float]] = {}

    for asset in ASSETS:
        for symbol in asset.symbols:
            divisor = asset.divisors.get(symbol)
            if divisor is None:
                continue
            try:
                raw = fetcher(symbol, years)
            except Exception as exc:  # noqa: BLE001 - 单个品种失败不影响整体
                print(f"  ✗ {asset.label} ({symbol}): {exc}")
                continue

            converted: dict[date, float] = {}
            dropped = 0
            for day, close in raw:
                value = close / divisor
                if asset.sane_min <= value <= asset.sane_max:
                    converted[day] = round(value, asset.decimals + 2)
                else:
                    dropped += 1

            if len(converted) >= 30:
                out[asset.key] = converted
                suffix = f"，丢弃 {dropped} 个异常值" if dropped else ""
                print(f"  ✓ {asset.label} ({symbol}): {len(converted)} 个交易日{suffix}")
                break
            print(f"  ✗ {asset.label} ({symbol}): 有效数据仅 {len(converted)} 条，换下一个候选")

    return out


def build_rows(series: dict[str, dict[date, float]]) -> list[dict[str, Any]]:
    """把各品种日线合并成按日期排列的历史行。"""
    all_days: set[date] = set()
    for points in series.values():
        all_days.update(points)

    rows: list[dict[str, Any]] = []
    for day in sorted(all_days):
        prices = {key: points.get(day) for key, points in series.items()}
        ratios = compute_ratios(prices)
        if all(prices.get(k) is None for k in ("gold", "silver", "copper", "aluminum")):
            continue
        row: dict[str, Any] = {
            "date": day.isoformat(),
            "timestamp": f"{day.isoformat()}T00:00:00+00:00",
            "quadrant": "",
        }
        for asset in ASSETS:
            row[asset.key] = prices.get(asset.key)
        for spec in RATIOS:
            row[spec.key] = ratios.get(spec.key)
        rows.append(row)

    # 用整段历史统一解析阈值，再逐日标注象限，保证色带前后可比
    thresholds = resolve_thresholds(rows)
    gsr_cut = thresholds["gsr"]["value"]
    cgr_cut = thresholds["copper_gold_ratio"]["value"]
    for row in rows:
        row["quadrant"] = (
            quadrant_code(row.get("gsr"), row.get("copper_gold_ratio"), gsr_cut, cgr_cut)
            or ""
        )

    return rows


def merge(
    existing: list[dict[str, Any]],
    backfilled: list[dict[str, Any]],
    prefer_backfilled: bool = False,
) -> list[dict[str, Any]]:
    """默认已有真实快照优先，回填只补缺口。

    prefer_backfilled=True（--force）时用回填结果覆盖同一天，
    用于修正历史字段（例如交易日期口径变了）。
    """
    if prefer_backfilled:
        by_day = {r["date"]: r for r in existing}
        for row in backfilled:
            by_day[row["date"]] = row
    else:
        by_day = {r["date"]: r for r in backfilled}
        for row in existing:
            by_day[row["date"]] = row
    return [by_day[day] for day in sorted(by_day)]


def run(
    fetcher: DailyFetcher | None = None,
    years: int = BACKFILL_YEARS,
    force: bool = False,
    min_days: int = BACKFILL_MIN_DAYS,
    history_path: Path | str = HISTORY_FILE,
    write: bool = True,
) -> list[dict[str, Any]]:
    fetcher = fetcher or fetch_daily
    history_path = Path(history_path)

    existing = hist.load(history_path)
    if len(existing) >= min_days and not force:
        print(f"历史已有 {len(existing)} 个交易日，无需回填（--force 可强制）")
        return existing

    print(f"历史仅 {len(existing)} 个交易日，开始回填最近 {years} 年日线…")
    series = fetch_asset_series(fetcher, years)
    if not series:
        print("✗ 回填失败：没有任何品种取到数据")
        return existing

    backfilled = hist.trim(build_rows(series), today=date.today())
    merged = merge(existing, backfilled, prefer_backfilled=force)

    print(
        f"回填完成：{len(backfilled)} 个交易日，合并后 {len(merged)} 个交易日"
        f"（{merged[0]['date']} → {merged[-1]['date']}）"
    )
    if write:
        hist.save(merged, history_path)
    return merged


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="回填贵金属日线历史")
    parser.add_argument("--years", type=int, default=BACKFILL_YEARS, help="拉取年数")
    parser.add_argument("--force", action="store_true", help="即使已有历史也重新回填")
    parser.add_argument(
        "--min-days",
        type=int,
        default=BACKFILL_MIN_DAYS,
        help="少于该天数才回填",
    )
    args = parser.parse_args(argv)

    run(years=args.years, force=args.force, min_days=args.min_days)
    return 0


if __name__ == "__main__":
    sys.exit(main())
