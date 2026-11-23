#!/usr/bin/env python3
"""信号计算：比率、四象限、分位上下文、风险分。

这个模块是唯一的"判断逻辑"来源，抓取脚本和建站脚本都调用它，
保证页面上的文案、阈值、象限结论永远一致。
"""

from __future__ import annotations

from typing import Any

from statistics import median

from scripts.config import (
    CGR_THRESHOLD,
    DXY_HEADWIND_PCT,
    GSR_THRESHOLD,
    PERCENTILE_MIN_SAMPLES,
    QUADRANTS,
    RATIOS,
    RATIOS_BY_KEY,
    REAL_YIELD_HEADWIND_BP,
    REVERSAL_ALERT_THRESHOLD,
    THRESHOLD_MODE,
    THRESHOLD_WINDOW,
    VIX_HEADWIND_LEVEL,
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


# ---------------------------------------------------------------- 阈值

def resolve_thresholds(
    rows: list[dict[str, Any]],
    mode: str = THRESHOLD_MODE,
    window: int = THRESHOLD_WINDOW,
) -> dict[str, dict[str, Any]]:
    """决定每个比率的分界线。

    默认用近 window 个交易日的中位数：比率是随货币环境漂移的，
    写死的绝对阈值（如铜金比 0.002）迟早会落到历史区间之外，
    让某个象限永远不可达。历史不足时回落到框架默认值。
    """
    resolved: dict[str, dict[str, Any]] = {}

    for spec in RATIOS:
        values = [
            float(r[spec.key])
            for r in rows
            if isinstance(r.get(spec.key), (int, float))
        ][-window:]

        use_dynamic = mode == "percentile" and len(values) >= PERCENTILE_MIN_SAMPLES
        value = median(values) if use_dynamic else spec.threshold

        resolved[spec.key] = {
            "value": round(value, spec.decimals + 4),
            "display": f"{value:.{spec.decimals}f}",
            "source": "median" if use_dynamic else "default",
            "samples": len(values),
            "default": spec.threshold,
        }

    return resolved


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

def _threshold_note(threshold: dict[str, Any]) -> str:
    if threshold.get("source") == "median":
        return f"近 {threshold.get('samples') or THRESHOLD_WINDOW} 个交易日中位"
    return "框架默认阈值"


def _ratio_sentence(
    spec_key: str,
    value: float | None,
    context: dict[str, Any] | None,
    missing_hint: str,
    threshold: dict[str, Any],
) -> str:
    spec = RATIOS_BY_KEY[spec_key]
    if value is None:
        return f"⚠️ {missing_hint}"

    cut = float(threshold["value"])
    above = value >= cut
    verdict = spec.high_means if above else spec.low_means
    relation = "高于" if above else "低于"
    display = threshold.get("display") or f"{cut:g}"
    text = (
        f"{spec.label} {value:.{spec.decimals}f} {relation}阈值 {display}"
        f"（{_threshold_note(threshold)}，{verdict}）"
    )
    if context and context.get("percentile") is not None:
        text += f"，处于近 {context['window']} 个交易日 {context['percentile']:.0f}% 分位"
    return text


# ---------------------------------------------------------------- 宏观锚

def _points(rows: list[dict[str, Any]], key: str) -> list[tuple[str, float]]:
    """取出 (日期, 数值) 序列，跳过空值。"""
    out = []
    for row in rows:
        value = row.get(key)
        if isinstance(value, (int, float)):
            out.append((row["date"], float(value)))
    return out


def change_over(rows: list[dict[str, Any]], key: str, back: int) -> float | None:
    """最近 back 个样本的绝对变化。"""
    points = _points(rows, key)
    if len(points) < back + 1:
        return None
    return points[-1][1] - points[-1 - back][1]


def pct_over(rows: list[dict[str, Any]], key: str, back: int) -> float | None:
    """最近 back 个样本的百分比变化。"""
    points = _points(rows, key)
    if len(points) < back + 1:
        return None
    previous = points[-1 - back][1]
    if previous == 0:
        return None
    return (points[-1][1] - previous) / previous * 100


def _pearson(pairs: list[tuple[float, float]]) -> float | None:
    if len(pairs) < 10:
        return None
    xs = [p[0] for p in pairs]
    ys = [p[1] for p in pairs]
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx <= 0 or vy <= 0:
        return None
    return cov / ((vx * vy) ** 0.5)


def real_yield_context(rows: list[dict[str, Any]], window: int = 250) -> dict[str, Any] | None:
    """实际利率的位置、近期变化，以及它与黄金的滚动相关性。

    原文的核心判断：黄金最怕的是真实利率上行。这里把"水平 + 变化 + 与金价
    的实际相关性"一次给出，避免只盯着金价本身看。
    """
    points = _points(rows, "real_yield")
    if len(points) < 21:
        return None

    latest = points[-1]
    change_20 = latest[1] - points[-21][1]
    bp = round(change_20 * 100, 1)

    values = [v for _, v in points][-window:]
    pct = None
    if len(values) >= PERCENTILE_MIN_SAMPLES:
        pct = round(sum(1 for v in values if v <= latest[1]) / len(values) * 100, 1)

    # 与黄金的滚动相关性：用近 60 个交易日的日变化对齐
    gold_points = _points(rows, "gold")
    by_day = dict(gold_points)
    pairs = []
    for day, value in points[-61:]:
        if day in by_day:
            pairs.append((value, by_day[day]))
    diffs = [
        (pairs[i][0] - pairs[i - 1][0], pairs[i][1] - pairs[i - 1][1])
        for i in range(1, len(pairs))
    ]
    corr = _pearson(diffs)

    if bp >= REAL_YIELD_HEADWIND_BP:
        state, tone = "逆风", "down"
    elif bp <= -REAL_YIELD_HEADWIND_BP:
        state, tone = "顺风", "up"
    else:
        state, tone = "中性", "flat"

    return {
        "value": round(latest[1], 2),
        "date": latest[0],
        "change_20d_bp": bp,
        "percentile": pct,
        "corr_gold_60d": None if corr is None else round(corr, 2),
        "state": state,
        "tone": tone,
    }


# ---------------------------------------------------------------- 反转清单

def build_reversal_checklist(
    rows: list[dict[str, Any]],
    prices: dict[str, float | None],
    ratios: dict[str, float | None],
) -> dict[str, Any]:
    """原文的"6 条反转信号清单"，命中 3 条以上默认进入高风险区。

    第 5 条（叙事变化）无法从价格数据推出，保留为人工判断项、不计分。
    """
    items: list[dict[str, Any]] = []

    def add(key: str, label: str, hit: bool | None, detail: str) -> None:
        items.append({"key": key, "label": label, "hit": hit, "detail": detail})

    # 1) 价格加速：最近 20 日涨幅明显陡于前 20 日
    r20 = pct_over(rows, "gold", 20)
    r20_prev = None
    points = _points(rows, "gold")
    if len(points) >= 41:
        older = points[-41][1]
        mid = points[-21][1]
        if older:
            r20_prev = (mid - older) / older * 100
    if r20 is None or r20_prev is None:
        add("acceleration", "价格加速", None, "历史不足，无法判断")
    else:
        hit = r20 > 8 and r20 > 2 * max(r20_prev, 0.1)
        add(
            "acceleration",
            "价格加速",
            hit,
            f"黄金近 20 日 {r20:+.1f}%，前 20 日 {r20_prev:+.1f}%",
        )

    # 2) 动能背离：创 60 日新高，但涨速在衰减
    gold_points = _points(rows, "gold")
    if len(gold_points) < 61 or r20 is None or r20_prev is None:
        add("divergence", "动能背离", None, "历史不足，无法判断")
    else:
        window = [v for _, v in gold_points][-60:]
        is_high = gold_points[-1][1] >= max(window) - 1e-9
        hit = is_high and r20 < r20_prev
        detail = (
            f"创 60 日新高但涨速由 {r20_prev:+.1f}% 降到 {r20:+.1f}%"
            if hit
            else ("未创新高" if not is_high else "新高且仍在加速")
        )
        add("divergence", "动能背离", hit, detail)

    # 3) 相关资产不跟：贵金属内部白银跑输黄金
    silver_r20 = pct_over(rows, "silver", 20)
    if r20 is None or silver_r20 is None:
        add("lagging", "相关资产不跟", None, "历史不足，无法判断")
    else:
        spread = silver_r20 - r20
        # 只有"金在涨、银跟不上"才是顶部背离；同涨同跌时看 beta 没有意义
        hit = r20 > 0 and spread < -5
        add(
            "lagging",
            "相关资产不跟",
            hit,
            f"白银近 20 日 {silver_r20:+.1f}%，相对黄金 {spread:+.1f}%"
            + ("（金强而银不跟，动能不纯）" if hit else ""),
        )

    # 4) 宏观逆风：美元走强 / 实际利率上行 / 恐慌指数抬升
    dxy20 = pct_over(rows, "dxy", 20)
    ry_context = real_yield_context(rows)
    ry_bp = ry_context["change_20d_bp"] if ry_context else None
    vix_points = _points(rows, "vix")
    vix_now = vix_points[-1][1] if vix_points else None

    triggers = []
    if dxy20 is not None and dxy20 >= DXY_HEADWIND_PCT:
        triggers.append(f"美元 20 日 {dxy20:+.1f}%")
    if ry_bp is not None and ry_bp >= REAL_YIELD_HEADWIND_BP:
        triggers.append(f"实际利率 20 日 {ry_bp:+.0f}bp")
    if vix_now is not None and vix_now >= VIX_HEADWIND_LEVEL:
        triggers.append(f"VIX {vix_now:.1f}")

    if dxy20 is None and ry_bp is None and vix_now is None:
        add("headwind", "宏观逆风", None, "缺少美元 / 实际利率 / VIX 数据")
    else:
        add(
            "headwind",
            "宏观逆风",
            bool(triggers),
            "、".join(triggers) if triggers else "美元、实际利率、波动率均未形成逆风",
        )

    # 5) 叙事变化：只能人工判断
    add(
        "narrative",
        "叙事变化",
        None,
        "需人工判断：驱动是否从「需求趋势」转向「供给冲击 / 监管 / 复产」",
    )

    # 6) 末端补涨：铝开始明显跑赢铜（供给/成本驱动的后半段）
    acr20 = pct_over(rows, "aluminum_copper_ratio", 20)
    acr_now = ratios.get("aluminum_copper_ratio")
    acr_pct = None
    if acr_now is not None:
        values = [v for _, v in _points(rows, "aluminum_copper_ratio")][-250:]
        if len(values) >= PERCENTILE_MIN_SAMPLES:
            acr_pct = sum(1 for v in values if v <= acr_now) / len(values) * 100

    if acr20 is None or acr_pct is None:
        add("late_stage", "末端补涨", None, "历史不足，无法判断铝铜比位置")
    else:
        hit = acr20 > 3 and acr_pct > 70
        add(
            "late_stage",
            "末端补涨",
            hit,
            f"铝铜比近 20 日 {acr20:+.1f}%，处于 {acr_pct:.0f}% 分位",
        )

    scored = [i for i in items if i["hit"] is not None]
    score = sum(1 for i in scored if i["hit"])

    if score >= REVERSAL_ALERT_THRESHOLD:
        level, tone = "高风险区", "down"
    elif score == REVERSAL_ALERT_THRESHOLD - 1:
        level, tone = "需警惕", "flat"
    else:
        level, tone = "趋势健康", "up"

    # 清单针对的是"上涨末端的反转"。如果价格已经在下行段，命中数会天然偏低，
    # 这时必须明确说明适用性，而不是显示一个绿色的"趋势健康"。
    note = ""
    if r20 is not None and r20 <= -5:
        level, tone = "已在下行段", "down"
        note = (
            f"黄金近 20 日 {r20:+.1f}%，趋势已经转弱；"
            "本清单检测的是上涨末端的反转信号，此时命中数偏低属于正常，请以象限与宏观锚为主。"
        )

    return {
        "score": score,
        "total": len(scored),
        "alert": score >= REVERSAL_ALERT_THRESHOLD,
        "level": level,
        "tone": tone,
        "note": note,
        "items": items,
    }


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

    thresholds = resolve_thresholds(rows)
    code = quadrant_code(
        gsr,
        cgr,
        thresholds["gsr"]["value"],
        thresholds["copper_gold_ratio"]["value"],
    )
    quadrant = QUADRANTS.get(code) if code else None

    context = {
        spec.key: ratio_context(rows, spec.key, ratios.get(spec.key))
        for spec in RATIOS
    }

    details: list[str] = []
    details.append(
        _ratio_sentence(
            "gsr", gsr, context.get("gsr"),
            "白银或黄金数据缺失，无法计算金银比", thresholds["gsr"],
        )
    )
    details.append(
        _ratio_sentence(
            "copper_gold_ratio",
            cgr,
            context.get("copper_gold_ratio"),
            "铜或黄金数据缺失，无法计算铜金比",
            thresholds["copper_gold_ratio"],
        )
    )
    details.append(
        _ratio_sentence(
            "aluminum_copper_ratio",
            acr,
            context.get("aluminum_copper_ratio"),
            "铝或铜数据缺失，无法计算铝铜比",
            thresholds["aluminum_copper_ratio"],
        )
    )

    if quadrant is None:
        details.insert(0, "关键比率不完整，暂不判定象限（按上一条有效记录保持观察）")

    real_yield = real_yield_context(rows)
    reversal = build_reversal_checklist(rows, prices, ratios)

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
            f"金银比 {thresholds['gsr']['display']}（金强 / 银强）× "
            f"铜金比 {thresholds['copper_gold_ratio']['display']}（铜强 / 铜弱）"
            + (
                f"，取近 {THRESHOLD_WINDOW} 个交易日中位数"
                if thresholds["gsr"]["source"] == "median"
                else f"，框架默认阈值（历史不足 {PERCENTILE_MIN_SAMPLES} 个交易日）"
            )
        ),
        "thresholds": thresholds,
        "real_yield": real_yield,
        "reversal": reversal,
        "details": details,
        "actions": list(quadrant.actions) if quadrant else [],
        "context": {k: v for k, v in context.items() if v},
        "trend": trend,
    }


__all__ = [
    "resolve_thresholds",
    "safe_div",
    "compute_ratios",
    "quadrant_code",
    "percentile_rank",
    "ratio_context",
    "trend_pct",
    "build_signals",
]
