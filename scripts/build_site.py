#!/usr/bin/env python3
"""构建静态站点 docs/index.html。

  python -m scripts.build_site

设计取舍：
  * 图表在 Python 侧预先渲染成 SVG，页面不需要任何 JavaScript
  * 所有动态文本都做 HTML 转义
  * 数据缺失 / 降级 / 基准过旧都会在页面上明确提示，不再"假装正常"
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, timezone
from html import escape
from pathlib import Path
from typing import Any, Iterable, Sequence

from scripts import history as hist
from scripts.config import (
    ASSETS,
    BEIJING,
    CHART_DAYS,
    HISTORY_FILE,
    LATEST_FILE,
    MAX_BASELINE_AGE_DAYS,
    OUTPUT_FILE,
    QUADRANTS,
    RATIOS,
    RATIOS_BY_KEY,
    REFERENCE_URL,
    SITE_URL,
)
from scripts.signals import build_signals, compute_ratios

# ---------------------------------------------------------------- 常量

FONT_STACK = (
    '-apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", '
    '"Hiragino Sans GB", "Microsoft YaHei", Roboto, "Helvetica Neue", Arial, sans-serif'
)

QUADRANT_COLORS = {
    "A": "#16a34a",
    "B": "#2563eb",
    "C": "#ea580c",
    "D": "#dc2626",
}

STALE_BANNER = (
    '<div class="banner banner-warn" role="alert">'
    '<span class="banner-icon">⚠️</span>'
    "<div><strong>数据降级：</strong>{reason}</div></div>"
)

LAG_BANNER = (
    '<div class="banner banner-warn" role="alert">'
    '<span class="banner-icon">🕒</span>'
    "<div><strong>数据可能滞后：</strong>观察日为 {as_of}，距今 {age} 天，"
    "请以最新行情为准。</div></div>"
)


# ---------------------------------------------------------------- 格式化

def fmt_number(value: Any, decimals: int = 2, dash: str = "—") -> str:
    if not isinstance(value, (int, float)):
        return dash
    return f"{value:,.{decimals}f}"


def fmt_pct(value: Any) -> str:
    if not isinstance(value, (int, float)):
        return ""
    return f"{value:+.2f}%"


def change_markup(change: dict[str, Any] | None, baseline_label: str) -> str:
    """涨跌区域的 HTML。"""
    pct = None if not change else change.get("pct")
    if not isinstance(pct, (int, float)):
        return '<div class="change muted">— 无涨跌数据</div>'

    if pct > 0:
        cls, arrow = "up", "▲"
    elif pct < 0:
        cls, arrow = "down", "▼"
    else:
        cls, arrow = "flat", "•"

    return (
        f'<div class="change {cls}">{arrow} {pct:+.2f}%'
        f'<span class="change-base">较 {escape(baseline_label)}</span></div>'
    )


def short_date(day: str) -> str:
    """2026-11-02 -> 11-02"""
    return day[5:10] if len(day) >= 10 else day


# ---------------------------------------------------------------- 组件

def render_price_card(asset: Any, latest: dict[str, Any], baseline_label: str) -> str:
    value = latest.get(asset.key)
    change = (latest.get("changes") or {}).get(asset.key)
    degraded = latest.get("degraded") or {}

    badge = ""
    if asset.key in degraded:
        badge = '<span class="badge" title="{}">沿用</span>'.format(
            escape(str(degraded[asset.key]))
        )

    unit = f'<span class="unit">{escape(asset.unit)}</span>' if asset.unit else ""

    return f"""
      <article class="card price">
        <header class="card-head">
          <span class="dot" style="--dot:{asset.color}"></span>
          <span class="card-label">{escape(asset.label)}</span>
          {unit}
          {badge}
        </header>
        <div class="price-value">{fmt_number(value, asset.decimals)}</div>
        {change_markup(change, baseline_label)}
      </article>"""


def render_ratio_card(
    spec: Any,
    latest: dict[str, Any],
    rows: list[dict[str, Any]],
    baseline_label: str,
) -> str:
    value = latest.get(spec.key)
    change = (latest.get("changes") or {}).get(spec.key)
    context = (latest.get("signals", {}).get("context") or {}).get(spec.key)

    below = isinstance(value, (int, float)) and value < spec.threshold
    state_text = "低于" if below else "高于"
    state_cls = "low" if below else "high"

    if context:
        pct = context.get("percentile")
        window = context.get("window", 0)
        track = render_percentile_track(spec, rows, pct, window)
        context_html = (
            f'<div class="ratio-context">近 {window} 个交易日 '
            f'<b>{pct:.0f}%</b> 分位</div>{track}'
        )
    else:
        context_html = (
            '<div class="ratio-context muted">历史样本不足，暂不显示分位</div>'
        )

    return f"""
      <article class="card ratio">
        <div class="ratio-label">{escape(spec.label)}</div>
        <div class="ratio-value">{fmt_number(value, spec.decimals)}</div>
        <div class="ratio-desc">{escape(spec.desc)}</div>
        <div class="ratio-state {state_cls}">{state_text}阈值 {spec.threshold:g}</div>
        {context_html}
        {change_markup(change, baseline_label)}
      </article>"""


def _percentile_of(value: float, values: Sequence[float]) -> float | None:
    if not values:
        return None
    return sum(1 for v in values if v <= value) / len(values) * 100


def render_percentile_track(
    spec: Any, rows: list[dict[str, Any]], pct: float | None, window: int
) -> str:
    """百分位刻度条：标记当前位置与阈值位置。"""
    if pct is None:
        return ""

    values = [float(r[spec.key]) for r in rows if isinstance(r.get(spec.key), (int, float))]
    window_values = values[-window:] if window else values
    threshold_pct = _percentile_of(spec.threshold, window_values)

    pct_pos = min(max(pct, 1.5), 98.5)

    tick = ""
    if threshold_pct is not None:
        tick_pos = min(max(threshold_pct, 1.5), 98.5)
        tick = (
            f'<span class="tick" style="left:{tick_pos:.1f}%"'
            f' title="阈值 {spec.threshold:g} 位于 {threshold_pct:.0f}% 分位"></span>'
        )

    return (
        '<div class="ptrack" role="img" '
        f'aria-label="当前值位于历史 {pct:.0f}% 分位，阈值位于 {threshold_pct or 0:.0f}% 分位">'
        f'<div class="pfill" style="width:{pct_pos:.1f}%"></div>'
        f"{tick}"
        f'<span class="pmark" style="left:{pct_pos:.1f}%"></span>'
        "</div>"
    )


def render_quadrants(active: str | None) -> str:
    cells = []
    for code in ("A", "B", "C", "D"):
        quadrant = QUADRANTS[code]
        is_active = code == active
        cls = "q active" if is_active else "q"
        aria = ' aria-current="true"' if is_active else ""
        tag = "当前" if is_active else ""
        cells.append(
            f"""
        <div class="{cls}" style="--q:{QUADRANT_COLORS[code]}"{aria}>
          <div class="q-top"><span class="q-code">{code}</span>"""
            f"<span class=\"q-flag\">{tag}</span></div>"
            f"""
          <div class="q-name">{escape(quadrant.name)}</div>
          <div class="q-desc">{escape(quadrant.tagline)}</div>
        </div>"""
        )
    return f'<div class="quadrant-grid">{"".join(cells)}\n      </div>'


def render_risk_meter(score: int, level: str) -> str:
    score = max(0, min(100, int(score or 0)))
    if score <= 45:
        color = "#4ade80"
    elif score <= 65:
        color = "#fbbf24"
    elif score <= 80:
        color = "#fb923c"
    else:
        color = "#f87171"
    return f"""
      <div class="risk" role="img" aria-label="风险等级 {escape(level)}，评分 {score} / 100">
        <div class="risk-head"><span>风险等级</span><b>{escape(level)}</b></div>
        <div class="risk-track"><span class="risk-fill" style="width:{score}%;--risk:{color}"></span></div>
        <div class="risk-scale"><span>低</span><span>中</span><span>高</span></div>
      </div>"""


# ---------------------------------------------------------------- 图表

def render_chart(
    spec: Any,
    series: list[tuple[str, float]],
    color: str,
    uid: str,
) -> str:
    """预渲染折线图。

    线画在 SVG 里（preserveAspectRatio=none + 非缩放描边），
    坐标轴文字用绝对定位的 HTML 叠加层，这样字号不随容器宽度缩小，
    手机上也能看清。
    """
    series = series[-CHART_DAYS:]
    if len(series) < 2:
        return (
            '<div class="chart-empty">历史样本不足（至少需要 2 个交易日），'
            "等待回填后自动生成走势图。</div>"
        )

    values = [v for _, v in series]
    low, high = min(values), max(values)
    low = min(low, spec.threshold)
    high = max(high, spec.threshold)
    span = (high - low) or 1.0
    low -= span * 0.08
    high += span * 0.08
    span = high - low

    ordinals = [date.fromisoformat(day).toordinal() for day, _ in series]
    x0, x1 = ordinals[0], ordinals[-1]
    x_span = max(1, x1 - x0)

    width, height = 1000.0, 400.0
    decimals = spec.decimals

    def px(ordinal: int) -> float:
        return (ordinal - x0) / x_span * width

    def py(value: float) -> float:
        return (1 - (value - low) / span) * height

    def px_pct(ordinal: int) -> float:
        return (ordinal - x0) / x_span * 100

    def py_pct(value: float) -> float:
        return (1 - (value - low) / span) * 100

    grid: list[str] = []
    y_labels: list[str] = []
    for i in range(4):
        value = low + span * i / 3
        grid.append(
            f'<line x1="0" y1="{py(value):.1f}" x2="{width}" y2="{py(value):.1f}" '
            'class="gridline" vector-effect="non-scaling-stroke"/>'
        )
        y_labels.append(
            f'<span class="ylab" style="top:{py_pct(value):.2f}%">'
            f"{value:.{decimals}f}</span>"
        )

    points = [(px(o), py(v)) for o, (_, v) in zip(ordinals, series)]
    line = "M " + " L ".join(f"{x:.1f} {y:.1f}" for x, y in points)
    area = f"{line} L {points[-1][0]:.1f} {height} L {points[0][0]:.1f} {height} Z"

    # 悬浮点（HTML，保证可见与可访问）
    hotspots = []
    step = max(1, len(series) // 60)
    for i in range(0, len(series), step):
        hotspots.append(
            f'<span class="hotspot" style="left:{px_pct(ordinals[i]):.2f}%;'
            f'top:{py_pct(series[i][1]):.2f}%" '
            f'title="{escape(series[i][0])} · {series[i][1]:.{decimals}f}"></span>'
        )

    x_labels = []
    for i in sorted({0, len(series) // 2, len(series) - 1}):
        pct = px_pct(ordinals[i])
        if i == 0:
            transform = "translate(0,0)"
        elif i == len(series) - 1:
            transform = "translate(-100%,0)"
        else:
            transform = "translate(-50%,0)"
        x_labels.append(
            f'<span class="xlab" style="left:{pct:.2f}%;transform:{transform}">'
            f"{short_date(series[i][0])}</span>"
        )

    last_x = px_pct(ordinals[-1])
    last_y = py_pct(values[-1])
    last_cls = "vlab below" if last_y < 16 else "vlab"

    return f"""
      <div class="chart-box">
        <div class="chart-plot">
          <svg viewBox="0 0 {int(width)} {int(height)}" preserveAspectRatio="none"
               class="chart-svg" role="img"
               aria-label="{escape(spec.label)}近 {len(series)} 个交易日走势，当前 {values[-1]:.{decimals}f}">
            <title>{escape(spec.label)} 走势</title>
            <desc>最近 {len(series)} 个交易日，区间 {min(values):.{decimals}f} 至 {max(values):.{decimals}f}，虚线为阈值 {spec.threshold:g}。</desc>
            <defs>
              <linearGradient id="grad-{uid}" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stop-color="{color}" stop-opacity="0.30"/>
                <stop offset="100%" stop-color="{color}" stop-opacity="0"/>
              </linearGradient>
            </defs>
            {"".join(grid)}
            <path d="{area}" fill="url(#grad-{uid})"/>
            <path d="{line}" class="chart-line" stroke="{color}"
                  vector-effect="non-scaling-stroke"/>
            <line x1="0" y1="{py(spec.threshold):.1f}" x2="{width}" y2="{py(spec.threshold):.1f}"
                  class="threshold" vector-effect="non-scaling-stroke"/>
          </svg>
          {"".join(y_labels)}
          {"".join(x_labels)}
          <span class="tlab" style="top:{py_pct(spec.threshold):.2f}%">阈值 {spec.threshold:g}</span>
          <span class="{last_cls}" style="left:{last_x:.2f}%;top:{last_y:.2f}%;color:{color}">
            {values[-1]:.{decimals}f}</span>
          <span class="chart-dot" style="left:{last_x:.2f}%;top:{last_y:.2f}%;--c:{color}"></span>
          {"".join(hotspots)}
        </div>
      </div>"""


def render_quadrant_strip(rows: list[dict[str, Any]], limit: int = 40) -> str:
    recent = [r for r in rows if r.get("quadrant")][-limit:]
    if not recent:
        return '<div class="strip-empty">暂无足够历史判定每日象限。</div>'

    cells = []
    for row in recent:
        code = row["quadrant"]
        color = QUADRANT_COLORS.get(code, "#475569")
        cells.append(
            f'<span class="strip-cell" style="--c:{color}" '
            f'title="{escape(row["date"])} · {code} {escape(QUADRANTS[code].name)}"></span>'
        )

    counts = {code: sum(1 for r in recent if r["quadrant"] == code) for code in "ABCD"}
    share = " · ".join(
        f"{code} {counts[code]}天" for code in "ABCD" if counts[code]
    )
    return (
        f'<div class="strip">{"".join(cells)}</div>'
        f'<div class="strip-legend">最近 {len(recent)} 个交易日分布：{escape(share)}</div>'
    )


# ---------------------------------------------------------------- 页面

def _series(rows: Iterable[dict[str, Any]], key: str) -> list[tuple[str, float]]:
    out = []
    for row in rows:
        value = row.get(key)
        if isinstance(value, (int, float)):
            out.append((row["date"], float(value)))
    return out


def render(
    latest_path: Path | str = LATEST_FILE,
    history_path: Path | str = HISTORY_FILE,
    output_path: Path | str = OUTPUT_FILE,
    write: bool = True,
    now: datetime | None = None,
) -> str:
    latest_path = Path(latest_path)
    output_path = Path(output_path)

    latest: dict[str, Any] = {}
    if latest_path.exists():
        latest = json.loads(latest_path.read_text(encoding="utf-8"))

    rows = hist.load(history_path)
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(BEIJING).date()

    as_of = latest.get("as_of") or (rows[-1]["date"] if rows else today.isoformat())
    baseline = latest.get("baseline") or {}
    baseline_usable = bool(baseline.get("usable"))
    baseline_label = short_date(baseline["date"]) if baseline_usable and baseline.get("date") else ""

    signals = latest.get("signals") or {}

    fallback_note = None
    # 若 latest.json 缺失（例如首次部署），退化成"从历史推算"，页面仍然可用
    if not latest:
        last_row = rows[-1] if rows else {}
        prices = {a.key: last_row.get(a.key) for a in ASSETS}
        ratios = compute_ratios(prices)
        signals = build_signals(prices, ratios, rows)
        latest = {
            "as_of": last_row.get("date"),
            "timestamp_beijing": "",
            "sources": [],
            "changes": {},
            "baseline": None,
            "degraded": {},
            "stale": False,
            "stale_reason": None,
            "history": {"days": len([r for r in rows if r.get("gsr") is not None])},
        }
        latest.update(prices)
        latest.update(ratios)
        latest["signals"] = signals
        fallback_note = (
            "还没有抓取到最新快照，以下内容由最近一次历史记录推算。"
            if last_row
            else "还没有任何行情数据，等待首次自动更新。"
        )

    quadrant = signals.get("quadrant")

    try:
        as_of_date = date.fromisoformat(as_of)
        lag_days = (today - as_of_date).days
    except ValueError:
        lag_days = 0

    banners = []
    if fallback_note:
        banners.append(
            '<div class="banner banner-info" role="status">'
            '<span class="banner-icon">ℹ️</span>'
            f"<div>{escape(fallback_note)}</div></div>"
        )
    if latest.get("stale"):
        banners.append(STALE_BANNER.format(reason=escape(str(latest.get("stale_reason") or ""))))
    if lag_days > MAX_BASELINE_AGE_DAYS:
        banners.append(LAG_BANNER.format(as_of=escape(as_of), age=lag_days))
    if signals.get("quadrant") is None:
        banners.append(
            '<div class="banner banner-info" role="status">'
            '<span class="banner-icon">ℹ️</span>'
            "<div>核心比率不完整，本次不做象限判断，仅展示价格。</div></div>"
        )
    banner_html = "".join(banners)

    price_cards = "".join(
        render_price_card(asset, latest, baseline_label or "上次") for asset in ASSETS
    )
    ratio_cards = "".join(
        render_ratio_card(spec, latest, rows, baseline_label or "上次") for spec in RATIOS
    )

    details_html = "".join(
        f"<li>{escape(str(item))}</li>" for item in signals.get("details", [])
    )
    actions_html = "".join(
        f"<li>{escape(str(item))}</li>" for item in signals.get("actions", [])
    )
    if not actions_html:
        actions_html = '<li class="muted">数据不完整，暂不给出建议动作。</li>'

    charts = "".join(
        f"""
      <section class="panel chart-panel">
        <div class="panel-head">
          <h3>{escape(spec.label)}<span class="panel-sub">{escape(spec.desc)}</span></h3>
          <div class="panel-legend"><span class="legend-line" style="--c:{color}"></span>每日收盘</div>
        </div>
        {render_chart(spec, _series(rows, spec.key), color, spec.key)}
      </section>"""
        for spec, color in (
            (RATIOS_BY_KEY["gsr"], "#fbbf24"),
            (RATIOS_BY_KEY["copper_gold_ratio"], "#60a5fa"),
        )
    )

    history_meta = latest.get("history") or {}
    samples = history_meta.get("days") or len([r for r in rows if r.get("gsr") is not None])
    sources = " · ".join(str(s) for s in latest.get("sources") or []) or "—"
    generated = now.astimezone(BEIJING).strftime("%Y-%m-%d %H:%M")

    method = str(signals.get("method") or "")
    method_html = (
        f'<div class="method">判定方法：{escape(method)}</div>' if method else ""
    )
    fetched = str(latest.get("timestamp_beijing") or "")
    fetched_pill = (
        f'<span class="pill">抓取 <b>{escape(fetched)}</b></span>' if fetched else ""
    )
    risk_score = int(signals.get("risk_score") or 0)
    risk_level = str(signals.get("risk_level") or "未知")

    trend = signals.get("trend") or {}
    trend_html = render_trend(trend)

    html = (
        HEAD_OPEN
        + f"<title>贵金属信号灯 · {escape(signals.get('quadrant_name') or '市场状态')} | 宏观框架</title>"
        + HEAD_CSS
        + CSS
        + HEAD_CLOSE
        + f"""
  <div class="wrap">
    <header class="site-head">
      <div class="brand"><span class="brand-mark">🥇</span> 贵金属信号灯</div>
      <p class="tagline">Allocation Lab 宏观交易框架 · 每日市场状态跟踪</p>
      <div class="meta-row">
        <span class="pill">观察日 <b>{escape(as_of)}</b></span>
        {fetched_pill}
        <span class="pill">样本 <b>{samples}</b> 个交易日</span>
      </div>
    </header>

    {banner_html}

    <section class="grid prices" aria-label="主要品种价格">
      {price_cards}
    </section>

    <section class="grid ratios" aria-label="核心比率">
      {ratio_cards}
    </section>

    <section class="panel status">
      <div class="status-head">
        <div>
          <div class="status-kicker">当前象限</div>
          <h2 class="status-title">{escape(str(signals.get('quadrant') or '—'))} · {escape(str(signals.get('quadrant_name') or '数据不足'))}</h2>
          <p class="status-desc">{escape(str(signals.get('description') or ''))}</p>
        </div>
        {render_risk_meter(risk_score, risk_level)}
      </div>
      {render_quadrants(quadrant)}
      <ul class="signals">{details_html}</ul>
      {method_html}
    </section>

    {trend_html}

    <section class="charts">
      {charts}
    </section>

    <section class="panel strip-panel">
      <div class="panel-head"><h3>最近状态<span class="panel-sub">每日象限</span></h3></div>
      {render_quadrant_strip(rows)}
    </section>

    <section class="panel actions">
      <div class="panel-head"><h3>🎯 建议动作<span class="panel-sub">框架推导，非投资建议</span></h3></div>
      <ul class="signals">{actions_html}</ul>
    </section>

    <section class="panel notice">
      <h3>⚠️ 使用须知</h3>
      <p>本工具仅为宏观状态跟踪与数据可视化，<strong>不构成任何投资建议</strong>。金属价格受多重因素驱动，历史规律不代表未来表现；四象限基于经验阈值划分，请结合自身风险承受能力独立决策。</p>
    </section>

    <footer class="site-foot">
      <p>数据来源 Yahoo Finance（{escape(sources)}）· 生成于 {escape(generated)}</p>
      <p><a href="{escape(SITE_URL)}" target="_blank" rel="noopener">在线地址</a> · <a href="{escape(REFERENCE_URL)}" target="_blank" rel="noopener">参考文章：Allocation Lab 宏观交易框架</a></p>
    </footer>
  </div>
</body>
</html>
"""
    )

    if write:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(html, encoding="utf-8")
        print(f"站点已生成: {output_path}")

    return html


def render_trend(trend: dict[str, Any]) -> str:
    """5 日 / 20 日变化的小面板。"""
    cells = []
    for spec in RATIOS:
        item = trend.get(spec.key) or {}
        d5 = item.get("d5")
        d20 = item.get("d20")

        def cell(value: Any) -> str:
            if not isinstance(value, (int, float)):
                return '<span class="muted">—</span>'
            cls = "up" if value > 0 else "down" if value < 0 else "flat"
            return f'<span class="{cls}">{value:+.2f}%</span>'

        cells.append(
            f"""
        <div class="trend-cell">
          <div class="trend-label">{escape(spec.label)}</div>
          <div class="trend-vals"><span class="trend-k">5日</span>{cell(d5)}<span class="trend-k">20日</span>{cell(d20)}</div>
        </div>"""
        )
    return (
        '<section class="panel trend-panel">'
        '<div class="panel-head"><h3>变化率<span class="panel-sub">比率相对自身</span></h3></div>'
        f'<div class="trend-grid">{"".join(cells)}</div></section>'
    )


HEAD_OPEN = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="dark">
<meta name="theme-color" content="#080b12">
<meta name="description" content="基于 Allocation Lab 宏观交易框架的每日贵金属市场状态分析：金银比、铜金比、铝铜比与四象限信号。">
<meta property="og:type" content="website">
<meta property="og:title" content="贵金属信号灯 · 宏观状态跟踪">
<meta property="og:description" content="金银比、铜金比、铝铜比与四象限信号，每日自动更新。">
<meta property="og:url" content="https://wzxiong.github.io/metal-signal/">
<meta name="twitter:card" content="summary">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Ccircle cx='16' cy='16' r='14' fill='%23f5c542'/%3E%3C/svg%3E">
"""

HEAD_CSS = """<style>
"""

HEAD_CLOSE = """</style>
</head>
<body>
"""

CSS = """
:root{
  --bg:#080b12; --bg-soft:#0d1220; --panel:rgba(255,255,255,.035);
  --panel-strong:rgba(255,255,255,.06); --border:rgba(255,255,255,.08);
  --border-strong:rgba(255,255,255,.16);
  --text:#e6ebf5; --muted:#94a3b8; --dim:#64748b;
  --gold:#f5c542; --up:#4ade80; --down:#f87171; --flat:#94a3b8;
  --radius:16px; --shadow:0 18px 40px -24px rgba(0,0,0,.9);
  --font:""" + FONT_STACK + """;
}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{
  margin:0; background:
    radial-gradient(1100px 520px at 50% -220px, rgba(245,197,66,.10), transparent 60%),
    radial-gradient(900px 500px at 88% 8%, rgba(96,165,250,.07), transparent 55%),
    var(--bg);
  color:var(--text); font-family:var(--font); line-height:1.65;
  -webkit-font-smoothing:antialiased; text-rendering:optimizeLegibility;
}
.wrap{max-width:1000px; margin:0 auto; padding:40px 20px 72px}
h1,h2,h3{letter-spacing:.01em}
a{color:#93b4e6; text-decoration:none; border-bottom:1px solid rgba(147,180,230,.3)}
a:hover{color:#c7dcf8}

/* ---------- header ---------- */
.site-head{text-align:center; margin-bottom:28px}
.brand{font-size:30px; font-weight:800; letter-spacing:.06em}
.brand-mark{filter:drop-shadow(0 0 14px rgba(245,197,66,.45))}
.tagline{margin:8px 0 16px; color:var(--muted); font-size:14px}
.meta-row{display:flex; gap:8px; justify-content:center; flex-wrap:wrap}
.pill{
  font-size:12.5px; color:var(--muted); padding:5px 12px; border-radius:999px;
  border:1px solid var(--border); background:var(--panel);
}
.pill b{color:var(--text); font-variant-numeric:tabular-nums}

/* ---------- banners ---------- */
.banner{
  display:flex; gap:12px; align-items:flex-start; padding:14px 18px;
  border-radius:14px; margin-bottom:18px; font-size:14px; line-height:1.6;
}
.banner-warn{background:rgba(251,146,60,.10); border:1px solid rgba(251,146,60,.3); color:#fed7aa}
.banner-info{background:rgba(96,165,250,.09); border:1px solid rgba(96,165,250,.28); color:#bfdbfe}
.banner-icon{font-size:16px; line-height:1.5}

/* ---------- cards ---------- */
.grid{display:grid; gap:14px; margin-bottom:18px}
.prices{grid-template-columns:repeat(5,minmax(0,1fr))}
.ratios{grid-template-columns:repeat(3,minmax(0,1fr))}
@media (max-width:860px){.prices{grid-template-columns:repeat(3,minmax(0,1fr))}}
@media (max-width:560px){.prices{grid-template-columns:repeat(2,minmax(0,1fr))}.ratios{grid-template-columns:minmax(0,1fr)}}

.card{
  position:relative; background:var(--panel); border:1px solid var(--border);
  border-radius:var(--radius); padding:16px 16px 14px; box-shadow:var(--shadow);
  transition:transform .18s ease, border-color .18s ease, background .18s ease;
}
.card:hover{transform:translateY(-2px); border-color:var(--border-strong); background:var(--panel-strong)}
.card-head{display:flex; align-items:center; gap:8px; margin-bottom:10px}
.dot{width:9px; height:9px; border-radius:50%; background:var(--dot); box-shadow:0 0 12px var(--dot); flex:none}
.card-label{font-size:13.5px; color:var(--muted)}
.unit{font-size:11px; color:var(--dim); margin-left:auto; font-variant-numeric:tabular-nums}
.badge{
  font-size:10.5px; padding:2px 7px; border-radius:999px; color:#fca5a5;
  border:1px solid rgba(248,113,113,.4); background:rgba(248,113,113,.1);
}
.price-value{font-size:26px; font-weight:750; letter-spacing:.01em; font-variant-numeric:tabular-nums; line-height:1.25}
.change{margin-top:6px; font-size:13px; font-variant-numeric:tabular-nums; display:flex; align-items:baseline; gap:8px; flex-wrap:wrap}
.change-base{font-size:11px; color:var(--dim)}
.up{color:var(--up)} .down{color:var(--down)} .flat{color:var(--flat)} .muted{color:var(--dim)}

.ratio{text-align:center}
.ratio-label{font-size:13px; color:var(--muted); letter-spacing:.04em}
.ratio-value{font-size:30px; font-weight:750; margin:4px 0 2px; font-variant-numeric:tabular-nums}
.ratio-desc{font-size:11.5px; color:var(--dim)}
.ratio-state{font-size:12px; margin-top:6px; color:var(--muted)}
.ratio-state.high{color:#fbbf24}
.ratio-context{font-size:11.5px; color:var(--muted); margin-top:6px}
.ratio-context b{color:var(--text)}
.ptrack{
  position:relative; height:5px; margin:8px auto 2px; width:86%;
  background:rgba(255,255,255,.09); border-radius:999px; overflow:visible;
}
.pfill{position:absolute; inset:0 auto 0 0; height:100%; background:linear-gradient(90deg,rgba(96,165,250,.55),rgba(245,197,66,.75)); border-radius:999px}
.pmark{position:absolute; top:50%; width:9px; height:9px; margin-left:-4.5px; border-radius:50%; background:#fff; transform:translateY(-50%); box-shadow:0 0 8px rgba(255,255,255,.7)}
.tick{position:absolute; top:-4px; bottom:-4px; width:1px; background:rgba(255,255,255,.55); margin-left:-.5px}
.ratio .change{justify-content:center; margin-top:8px}

/* ---------- panels ---------- */
.panel{
  background:var(--panel); border:1px solid var(--border); border-radius:20px;
  padding:22px; margin-bottom:18px; box-shadow:var(--shadow);
}
.panel-head{display:flex; align-items:baseline; justify-content:space-between; gap:12px; margin-bottom:14px; flex-wrap:wrap}
.panel-head h3{margin:0; font-size:15px; font-weight:700; color:var(--text)}
.panel-sub{margin-left:10px; font-size:12px; font-weight:400; color:var(--dim)}
.panel-legend{font-size:11.5px; color:var(--dim); display:flex; align-items:center; gap:6px}
.legend-line{width:16px; height:2px; background:var(--c); border-radius:2px}

/* ---------- status ---------- */
.status{background:linear-gradient(160deg,rgba(245,197,66,.07),rgba(255,255,255,.02) 55%); border-color:rgba(245,197,66,.22)}
.status-head{display:flex; gap:24px; justify-content:space-between; align-items:flex-start; flex-wrap:wrap}
.status-kicker{font-size:11.5px; letter-spacing:.14em; text-transform:uppercase; color:var(--muted)}
.status-title{margin:4px 0 8px; font-size:26px; font-weight:800; color:var(--gold)}
.status-desc{margin:0; font-size:14.5px; color:#cbd5e1; max-width:56ch}
.risk{min-width:220px; flex:1 1 220px; max-width:320px}
.risk-head{display:flex; justify-content:space-between; font-size:12px; color:var(--muted); margin-bottom:6px}
.risk-head b{color:var(--text); font-size:13px}
.risk-track{height:7px; border-radius:999px; background:rgba(255,255,255,.1); overflow:hidden}
.risk-fill{display:block; height:100%; background:var(--risk); border-radius:999px; box-shadow:0 0 14px var(--risk)}
.risk-scale{display:flex; justify-content:space-between; font-size:10.5px; color:var(--dim); margin-top:4px}

.quadrant-grid{display:grid; grid-template-columns:repeat(4,1fr); gap:10px; margin:22px 0 18px}
@media (max-width:720px){.quadrant-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}
.q{
  border-radius:14px; padding:14px 12px; border:1px solid var(--border);
  background:rgba(255,255,255,.02); opacity:.45; transition:all .22s ease;
}
.q.active{
  opacity:1; border-color:color-mix(in srgb, var(--q) 55%, transparent);
  background:linear-gradient(150deg,color-mix(in srgb, var(--q) 26%,transparent),transparent 70%);
  box-shadow:0 0 0 1px color-mix(in srgb, var(--q) 30%,transparent), 0 16px 32px -20px var(--q);
  transform:translateY(-1px);
}
.q-top{display:flex; align-items:center; justify-content:space-between; margin-bottom:6px}
.q-code{font-size:11px; font-weight:800; color:#fff; background:var(--q); padding:1px 7px; border-radius:6px; opacity:.9}
.q-flag{font-size:10px; color:var(--gold); letter-spacing:.1em}
.q-name{font-size:15px; font-weight:700}
.q-desc{font-size:11.5px; color:var(--muted); margin-top:3px}

.signals{list-style:none; padding:0; margin:0}
.signals li{padding:9px 0; border-top:1px solid rgba(255,255,255,.05); font-size:13.5px; color:#cbd5e1}
.signals li:first-child{border-top:none}
.method{margin-top:14px; padding-top:12px; border-top:1px dashed rgba(255,255,255,.08); font-size:11.5px; color:var(--dim)}

/* ---------- trend ---------- */
.trend-grid{display:grid; grid-template-columns:repeat(3,1fr); gap:12px}
@media (max-width:640px){.trend-grid{grid-template-columns:minmax(0,1fr)}}
.trend-cell{background:rgba(255,255,255,.02); border:1px solid var(--border); border-radius:12px; padding:12px 14px}
.trend-label{font-size:12.5px; color:var(--muted); margin-bottom:6px}
.trend-vals{display:flex; align-items:center; gap:8px; font-size:13.5px; font-variant-numeric:tabular-nums}
.trend-k{font-size:10.5px; color:var(--dim)}

/* ---------- charts ---------- */
.charts{display:grid; gap:18px; grid-template-columns:minmax(0,1fr); margin-bottom:18px}
.chart-panel{margin-bottom:0}
/* 折线由 SVG 绘制，坐标文字用 HTML 叠加，避免随宽度缩成小字 */
.chart-box{position:relative; height:215px}
.chart-plot{position:absolute; inset:14px 12px 26px 56px}
.chart-svg{position:absolute; inset:0; width:100%; height:100%; overflow:visible}
.chart-line{fill:none; stroke-width:2; stroke-linejoin:round; stroke-linecap:round}
.gridline{stroke:rgba(255,255,255,.055); stroke-width:1}
.threshold{stroke:rgba(245,197,66,.55); stroke-width:1.2; stroke-dasharray:5 5}
.ylab{position:absolute; left:-9px; transform:translate(-100%,-50%); font-size:11px; color:var(--dim); font-variant-numeric:tabular-nums; white-space:nowrap}
.xlab{position:absolute; bottom:-21px; font-size:11px; color:var(--dim); white-space:nowrap}
.tlab{position:absolute; right:2px; transform:translateY(-140%); font-size:10.5px; color:rgba(245,197,66,.85); white-space:nowrap; text-shadow:0 0 5px #080b12,0 0 10px #080b12}
.vlab{position:absolute; font-size:12.5px; font-weight:700; white-space:nowrap; transform:translate(-100%,-165%); font-variant-numeric:tabular-nums; text-shadow:0 0 5px #080b12,0 0 10px #080b12,0 0 2px #080b12}
.vlab.below{transform:translate(-100%,70%)}
.chart-dot{position:absolute; width:8px; height:8px; border-radius:50%; background:var(--c); transform:translate(-50%,-50%); box-shadow:0 0 12px var(--c)}
.hotspot{position:absolute; width:11px; height:11px; margin:-5.5px 0 0 -5.5px; border-radius:50%; background:transparent; cursor:crosshair}
.hotspot:hover{background:rgba(255,255,255,.22)}
.chart-empty{padding:36px 12px; text-align:center; color:var(--dim); font-size:13px; border:1px dashed var(--border); border-radius:12px}

/* ---------- strip ---------- */
.strip{display:flex; gap:3px; flex-wrap:wrap}
.strip-cell{width:14px; height:14px; border-radius:4px; background:var(--c); opacity:.85}
.strip-cell:hover{opacity:1; outline:1px solid rgba(255,255,255,.5)}
.strip-legend{font-size:11.5px; color:var(--dim); margin-top:10px}
.strip-empty{font-size:13px; color:var(--dim)}

/* ---------- notice / footer ---------- */
.notice h3{margin:0 0 8px; font-size:15px}
.notice p{margin:0; font-size:13px; color:var(--muted); line-height:1.8}
.notice strong{color:#fca5a5}
.site-foot{text-align:center; color:#475569; font-size:12px; margin-top:34px}
.site-foot p{margin:4px 0}
.site-foot a{color:#64748b; border-bottom-color:transparent}

@media (max-width:640px){.chart-box{height:170px}.chart-plot{inset:10px 8px 24px 48px}}
@media (prefers-reduced-motion:reduce){
  *{transition:none !important; animation:none !important}
}
@media print{
  body{background:#fff; color:#111}
  .card,.panel{border-color:#ddd; background:#fff; box-shadow:none}
  .site-foot{color:#666}
}
"""


def main(argv: Sequence[str] | None = None) -> int:
    render()
    return 0


if __name__ == "__main__":
    sys.exit(main())
