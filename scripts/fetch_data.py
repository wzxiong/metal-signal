#!/usr/bin/env python3
"""每日抓取主流程。

  python -m scripts.fetch_data

流程：
  1. 抓取各品种最新价，做单位换算 + 区间校验
  2. 与"上一个交易日"对比，做过大波动复核（仅在基准足够新时启用）
  3. 计算比率与四象限信号
  4. 写入 data/latest.json 与 data/history.csv（按交易日去重）
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

from scripts import history as hist
from scripts.config import (
    ASSETS,
    ASSETS_BY_KEY,
    BEIJING,
    CORE_KEYS,
    HISTORY_FILE,
    LATEST_FILE,
    MAX_BASELINE_AGE_DAYS,
    MAX_DAILY_CHANGE,
    RATIOS,
    UTC,
)
from scripts.market import Quote, fetch_quote, transport_name
from scripts.signals import build_signals, compute_ratios

SCHEMA_VERSION = 2

QuoteFetcher = Callable[[Sequence[str]], Quote | None]


@dataclass
class FetchResult:
    """一次抓取的结果。"""

    prices: dict[str, float | None] = field(default_factory=dict)
    sources: dict[str, str] = field(default_factory=dict)
    failed: list[str] = field(default_factory=list)
    market_dates: list[date] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------- 抓取

def collect(fetcher: QuoteFetcher) -> FetchResult:
    result = FetchResult()

    for asset in ASSETS:
        quote = fetcher(asset.symbols)
        if quote is None:
            result.failed.append(asset.key)
            result.errors[asset.key] = "无行情返回"
            print(f"  ✗ {asset.label}: 无行情返回")
            continue

        divisor = asset.divisors.get(quote.symbol)
        if divisor is None:
            result.failed.append(asset.key)
            result.errors[asset.key] = f"{quote.symbol} 单位未知"
            print(f"  ✗ {asset.label}: symbol {quote.symbol} 的单位未登记，跳过")
            continue

        value = quote.price / divisor
        if not (asset.sane_min <= value <= asset.sane_max):
            result.failed.append(asset.key)
            result.errors[asset.key] = (
                f"{value:.4g} 超出合理区间 [{asset.sane_min:g}, {asset.sane_max:g}]"
            )
            print(
                f"  ✗ {asset.label}: {quote.symbol} 换算后 {value:.4g} "
                f"超出 [{asset.sane_min:g}, {asset.sane_max:g}]，判定为数据异常"
            )
            continue

        result.prices[asset.key] = round(value, asset.decimals + 2)
        result.sources[asset.key] = quote.symbol
        if quote.market_date:
            result.market_dates.append(quote.market_date)
        unit = f" {asset.unit}" if asset.unit else ""
        print(f"  ✓ {asset.label}: {value:.{asset.decimals}f}{unit}  ({quote.symbol})")

    return result


# ---------------------------------------------------------------- 校验

def _pct(curr: float | None, prev: float | None) -> float | None:
    if curr is None or prev is None or prev == 0:
        return None
    return (curr - prev) / prev * 100


def apply_guards(
    prices: dict[str, float | None],
    baseline: dict[str, Any] | None,
    baseline_age: int | None,
) -> tuple[dict[str, float | None], dict[str, str]]:
    """单日波动复核。返回 (修正后的价格, {品种: 原因})。"""
    flags: dict[str, str] = {}
    if baseline is None or baseline_age is None:
        return prices, flags
    if baseline_age > MAX_BASELINE_AGE_DAYS:
        # 基准太旧，说明中间断更了，此时"日涨跌"没有意义，不做波动复核
        return prices, flags

    for key, limit in MAX_DAILY_CHANGE.items():
        curr = prices.get(key)
        prev = hist.parse_number(baseline.get(key))
        if curr is None or prev is None:
            continue
        change = abs(curr - prev) / prev
        if change > limit:
            flags[key] = (
                f"较 {baseline['date']} 变动 {change * 100:.1f}%，超过 {limit * 100:.0f}% 复核线"
            )
            print(
                f"  ⚠ {ASSETS_BY_KEY[key].label}: 变动 {change * 100:.1f}% "
                f"> {limit * 100:.0f}%，判定异常，沿用上一交易日价格"
            )
            prices[key] = prev
    return prices, flags


# ---------------------------------------------------------------- 主流程

def run(
    quote_fetcher: QuoteFetcher | None = None,
    today: date | None = None,
    now: datetime | None = None,
    latest_path: Path | str = LATEST_FILE,
    history_path: Path | str = HISTORY_FILE,
    write: bool = True,
) -> tuple[dict[str, Any], int]:
    """执行抓取 + 计算 + 落盘，返回 (记录, 退出码)。"""
    fetcher = quote_fetcher or fetch_quote
    now = now or datetime.now(timezone.utc)
    today = today or now.astimezone(BEIJING).date()

    latest_path = Path(latest_path)
    history_path = Path(history_path)

    print(f"[{now.astimezone(BEIJING):%Y-%m-%d %H:%M}] 开始抓取行情… (传输: {transport_name()})")
    fetched = collect(fetcher)

    rows = hist.load(history_path)
    core_ok = any(fetched.prices.get(k) for k in CORE_KEYS)

    # 观察日：优先用交易所的交易日，避免周末/跨时区把日期算错；
    # 完全没抓到行情时退回历史最后一天，绝不把"今天"冒充成观察日
    if fetched.market_dates:
        as_of = max(fetched.market_dates)
    elif rows:
        as_of = date.fromisoformat(rows[-1]["date"])
    else:
        as_of = today
    as_of_str = as_of.isoformat()

    baseline = hist.previous_before(rows, as_of_str)
    baseline_age = None
    if baseline:
        baseline_age = (as_of - date.fromisoformat(baseline["date"])).days

    # 波动复核
    prices, guard_flags = apply_guards(dict(fetched.prices), baseline, baseline_age)

    # 缺失值兜底：先用当天已有记录，再用上一交易日
    today_row = hist.find(rows, as_of_str)
    degraded: dict[str, str] = dict(guard_flags)

    for asset in ASSETS:
        key = asset.key
        if prices.get(key) is not None:
            continue
        carried_from = None
        if today_row and hist.parse_number(today_row.get(key)) is not None:
            prices[key] = hist.parse_number(today_row.get(key))
            carried_from = "今日早前记录"
        elif baseline and hist.parse_number(baseline.get(key)) is not None:
            prices[key] = hist.parse_number(baseline.get(key))
            carried_from = f"{baseline['date']} 记录"
        else:
            prices[key] = None

        if carried_from:
            reason = fetched.errors.get(key, "抓取失败")
            degraded[key] = f"{reason}，沿用 {carried_from}"
            print(f"  ↺ {asset.label}: 沿用 {carried_from}")

    ratios = compute_ratios(prices)

    # 写入历史（一天一行）
    row = {
        "date": as_of_str,
        "timestamp": now.astimezone(UTC).isoformat(timespec="seconds"),
        "quadrant": "",  # 先落位，算完信号再回填
    }
    for asset in ASSETS:
        row[asset.key] = prices.get(asset.key)
    for spec in RATIOS:
        row[spec.key] = ratios.get(spec.key)

    if core_ok:
        rows = hist.upsert(rows, row)
        rows = hist.trim(rows, today=today)
    else:
        print("  ✗ 核心品种全部抓取失败，本次不写入历史")

    # 信号（用更新后的历史算分位与阈值）
    signals = build_signals(prices, ratios, rows)

    if core_ok:
        row["quadrant"] = signals.get("quadrant") or ""
        rows = hist.upsert(rows, row)
        if write:
            hist.save(rows, history_path)

    # 涨跌：只对比上一个交易日，且基准过旧时直接隐藏
    changes: dict[str, Any] = {}
    baseline_info: dict[str, Any] | None = None
    if baseline:
        baseline_info = {
            "date": baseline["date"],
            "age_days": baseline_age,
            "usable": baseline_age is not None and baseline_age <= MAX_BASELINE_AGE_DAYS,
        }
        if baseline_info["usable"]:
            keys = [a.key for a in ASSETS] + [s.key for s in RATIOS]
            for key in keys:
                if key in degraded and key != "quadrant":
                    continue  # 沿用值不产生涨跌
                curr = ratios.get(key) if key in {s.key for s in RATIOS} else prices.get(key)
                prev = hist.parse_number(baseline.get(key))
                pct = _pct(curr, prev)
                if curr is not None and prev is not None and pct is not None:
                    changes[key] = {
                        "value": round(curr - prev, 6),
                        "pct": round(pct, 2),
                    }

    stale = bool(degraded) or not core_ok
    if not core_ok:
        stale_reason = "核心品种全部抓取失败，展示的是历史缓存值"
    elif degraded:
        names = "、".join(ASSETS_BY_KEY[k].label for k in degraded)
        stale_reason = f"{names} 数据异常或缺失，已沿用前一有效值"
    else:
        stale_reason = None

    record: dict[str, Any] = {
        "schema": SCHEMA_VERSION,
        "timestamp": now.astimezone(UTC).isoformat(timespec="seconds"),
        "timestamp_beijing": now.astimezone(BEIJING).strftime("%Y-%m-%d %H:%M"),
        "as_of": as_of_str,
        "stale": stale,
        "stale_reason": stale_reason,
        "degraded": degraded,
        "sources": [f"{ASSETS_BY_KEY[k].label} {sym}" for k, sym in fetched.sources.items()],
        "prices": {a.key: prices.get(a.key) for a in ASSETS},
        "ratios": ratios,
        "signals": signals,
        "changes": changes,
        "baseline": baseline_info,
        "history": {
            "days": len([r for r in rows if r.get("gsr") is not None]),
            "from": rows[0]["date"] if rows else None,
            "to": rows[-1]["date"] if rows else None,
        },
    }
    # 兼容旧键名，页面与外部脚本都能读到
    for asset in ASSETS:
        record[asset.key] = prices.get(asset.key)
    record.update({spec.key: ratios.get(spec.key) for spec in RATIOS})

    if write:
        latest_path.parent.mkdir(parents=True, exist_ok=True)
        latest_path.write_text(
            json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    _print_summary(record)
    _write_step_summary(record)

    return record, (0 if core_ok else 1)


def _print_summary(record: dict[str, Any]) -> None:
    sig = record["signals"]
    print()
    print(f"  观察日   : {record['as_of']}")
    print(f"  象限     : {sig['quadrant'] or '—'} {sig['quadrant_name']}")
    print(f"  风险等级 : {sig['risk_level']}")
    if record["stale"]:
        print(f"  ⚠ 降级   : {record['stale_reason']}")
    if record["baseline"] and not record["baseline"]["usable"]:
        print(
            f"  ⚠ 基准   : 上一记录 {record['baseline']['date']} "
            f"已过去 {record['baseline']['age_days']} 天，隐藏涨跌幅"
        )
    print(f"  历史样本 : {record['history']['days']} 个交易日")


def _write_step_summary(record: dict[str, Any]) -> None:
    """在 GitHub Actions 里生成可读的任务摘要。"""
    target = os.environ.get("GITHUB_STEP_SUMMARY")
    if not target:
        return

    sig = record["signals"]
    lines = [
        "## 贵金属信号灯",
        "",
        f"- 观察日：`{record['as_of']}`（北京时间 {record['timestamp_beijing']} 抓取）",
        f"- 象限：**{sig['quadrant'] or '—'} {sig['quadrant_name']}**（风险 {sig['risk_level']}）",
    ]
    if record["stale"]:
        lines.append(f"- ⚠️ 数据降级：{record['stale_reason']}")
    lines += ["", "| 品种 | 价格 | 日涨跌 |", "| --- | --- | --- |"]
    for key, asset in ((a.key, a) for a in ASSETS):
        value = record.get(key)
        change = record["changes"].get(key) or {}
        pct = change.get("pct")
        shown = "—" if value is None else f"{value:,.{asset.decimals}f}"
        delta = "—" if pct is None else f"{pct:+.2f}%"
        lines.append(f"| {asset.label} | {shown} | {delta} |")

    Path(target).write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    _record, code = run()
    return code


if __name__ == "__main__":
    sys.exit(main())
