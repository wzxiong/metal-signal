#!/usr/bin/env python3
"""信号计算：比率、四象限、分位上下文、风险分。

这个模块是唯一的"判断逻辑"来源，抓取脚本和建站脚本都调用它，
保证页面上的文案、阈值、象限结论永远一致。
"""

from __future__ import annotations

from typing import Any

from scripts.config import (
    CGR_THRESHOLD,
    GSR_THRESHOLD,
    PERCENTILE_MIN_SAMPLES,
    QUADRANTS,
    RATIOS,
    RATIOS_BY_KEY,
)


# ---------------------------------------------------------------- 基础比率

def safe_div(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None:
        return None
    if denominator == 0:
        return None
    return numerator / denominator


def _positive(value: Any) -> float | None:
    """价格必须是正数，0 或负数一律视为缺失，避免算出无意义的比率。"""
    if isinstance(value, (int, float)) and value > 0:
        return float(value)
    return None


def compute_ratios(prices: dict[str, float | None]) -> dict[str, float | None]:
    """由价格算派生比率。缺数据时对应比率为 None。"""
    gold = _positive(prices.get("gold"))
    silver = _positive(prices.get("silver"))
    copper = _positive(prices.get("copper"))
    aluminum = _positive(prices.get("aluminum"))

    return {
        "gsr": _round(safe_div(gold, silver), 2),
        "silver_gold_ratio": _round(safe_div(silver, gold), 5),
        "copper_gold_ratio": _round(safe_div(copper, gold), 5),
        "aluminum_copper_ratio": _round(safe_div(aluminum, copper), 4),
    }


def _round(value: float | None, digits: int) -> float | None:
    return None if value is None else round(value, digits)


# ---------------------------------------------------------------- 四象限

def quadrant_code(
    gsr: float | None,
    cgr: float | None,
    gsr_threshold: float = GSR_THRESHOLD,
    cgr_threshold: float = CGR_THRESHOLD,
) -> str | None:
    """金银比 × 铜金比 → A/B/C/D。

    金强 = 金银比 >= 阈值；铜强 = 铜金比 >= 阈值。
      A 进攻：铜强 + 银强（金银比低）
      B 防守：金强 + 铜弱
      C 复杂：金强 + 铜强
      D 高风险：银强 + 铜弱
    """
    if gsr is None or cgr is None:
        return None

    gold_strong = gsr >= gsr_threshold
    copper_strong = cgr >= cgr_threshold

    if copper_strong:
        return "C" if gold_strong else "A"
    return "B" if gold_strong else "D"


# ---------------------------------------------------------------- 分位

def percentile_rank(value: float | None, values: list[float]) -> float | None:
    """当前值在历史序列中的百分位（0-100）。样本不足返回 None。"""
    if value is None or not values:
        return None
    if len(values) < PERCENTILE_MIN_SAMPLES:
        return None
    below_or_equal = sum(1 for v in values if v <= value)
    return round(below_or_equal / len(values) * 100, 1)


def ratio_context(
    rows: list[dict[str, Any]], key: str, value: float | None
) -> dict[str, Any] | None:
    """某个比率的历史分位与区间。"""
    values = [
        float(r[key])
        for r in rows
        if isinstance(r.get(key), (int, float))
    ]
    if value is None or len(values) < PERCENTILE_MIN_SAMPLES:
        return None

    window = values[-250:]  # 最多看一年
    return {
        "percentile": percentile_rank(value, window),
        "window": len(window),
        "min": round(min(window), RATIOS_BY_KEY[key].decimals),
        "max": round(max(window), RATIOS_BY_KEY[key].decimals),
    }


def trend_pct(rows: list[dict[str, Any]], key: str, back: int) -> float | None:
    """最近 back 个样本的变化百分比。"""
    values = [float(r[key]) for r in rows if isinstance(r.get(key), (int, float))]
    if len(values) < back + 1:
        return None
    previous = values[-1 - back]
    if previous == 0:
        return None
    return round((values[-1] - previous) / previous * 100, 2)


# ---------------------------------------------------------------- 文案

def _ratio_sentence(
    spec_key: str,
    value: float | None,
    context: dict[str, Any] | None,
    missing_hint: str,
) -> str:
    spec = RATIOS_BY_KEY[spec_key]
    if value is None:
        return f"⚠️ {missing_hint}"

    above = value >= spec.threshold
    verdict = spec.high_means if above else spec.low_means
    relation = "高于" if above else "低于"
    text = (
        f"{spec.label} {value:.{spec.decimals}f} "
        f"{relation}阈值 {spec.threshold:g}（{verdict}）"
    )
    if context and context.get("percentile") is not None:
        text += f"，处于近 {context['window']} 个交易日 {context['percentile']:.0f}% 分位"
    return text


# ---------------------------------------------------------------- 汇总

def build_signals(
    prices: dict[str, float | None],
    ratios: dict[str, float | None],
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """生成完整的信号结构，写入 latest.json 供页面直接渲染。"""
    gsr = ratios.get("gsr")
    cgr = ratios.get("copper_gold_ratio")
    acr = ratios.get("aluminum_copper_ratio")

    code = quadrant_code(gsr, cgr)
    quadrant = QUADRANTS.get(code) if code else None

    context = {
        spec.key: ratio_context(rows, spec.key, ratios.get(spec.key))
        for spec in RATIOS
    }

    details: list[str] = []
    details.append(
        _ratio_sentence("gsr", gsr, context.get("gsr"), "白银或黄金数据缺失，无法计算金银比")
    )
    details.append(
        _ratio_sentence(
            "copper_gold_ratio",
            cgr,
            context.get("copper_gold_ratio"),
            "铜或黄金数据缺失，无法计算铜金比",
        )
    )
    details.append(
        _ratio_sentence(
            "aluminum_copper_ratio",
            acr,
            context.get("aluminum_copper_ratio"),
            "铝或铜数据缺失，无法计算铝铜比",
        )
    )

    if quadrant is None:
        details.insert(0, "关键比率不完整，暂不判定象限（按上一条有效记录保持观察）")

    trend = {}
    for spec in RATIOS:
        trend[spec.key] = {
            "d5": trend_pct(rows, spec.key, 5),
            "d20": trend_pct(rows, spec.key, 20),
        }

    return {
        "quadrant": code,
        "quadrant_name": quadrant.name if quadrant else "数据不足",
        "description": (
            quadrant.description if quadrant else "核心比率缺失，本次不做象限判断。"
        ),
        "risk_level": quadrant.risk_level if quadrant else "未知",
        "risk_score": quadrant.risk_score if quadrant else 0,
        "method": (
            f"金银比阈值 {GSR_THRESHOLD:g}（金强 / 银强）× "
            f"铜金比阈值 {CGR_THRESHOLD:g}（铜强 / 铜弱）"
        ),
        "details": details,
        "actions": list(quadrant.actions) if quadrant else [],
        "context": {k: v for k, v in context.items() if v},
        "trend": trend,
    }


__all__ = [
    "safe_div",
    "compute_ratios",
    "quadrant_code",
    "percentile_rank",
    "ratio_context",
    "trend_pct",
    "build_signals",
]
