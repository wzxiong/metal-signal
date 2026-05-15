#!/usr/bin/env python3
"""
构建静态站点 docs/index.html
"""

import json
import csv
from pathlib import Path

ROOT = Path(__file__).parent.parent.resolve()
DATA_DIR = ROOT / "data"
DOCS_DIR = ROOT / "docs"
DOCS_DIR.mkdir(exist_ok=True)

LATEST_FILE = DATA_DIR / "latest.json"
HISTORY_FILE = DATA_DIR / "history.csv"
OUTPUT_FILE = DOCS_DIR / "index.html"


def load_latest():
    if LATEST_FILE.exists():
        with open(LATEST_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def load_history(limit=90):
    rows = []
    if HISTORY_FILE.exists():
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for r in reader:
                rows.append(r)
    # 只保留最近 limit 条
    rows = rows[-limit:]
    return rows


def pct_class(v):
    if v is None:
        return "neutral"
    if v > 0:
        return "up"
    if v < 0:
        return "down"
    return "neutral"


def pct_symbol(v):
    if v is None:
        return ""
    if v > 0:
        return "▲"
    if v < 0:
        return "▼"
    return "−"


def render():
    data = load_latest()
    history = load_history(limit=60)

    # 基础数据
    gold = data.get("gold")
    silver = data.get("silver")
    copper = data.get("copper")
    aluminum = data.get("aluminum")
    dxy = data.get("dxy")
    gsr = data.get("gsr")
    cgr = data.get("copper_gold_ratio")
    acr = data.get("aluminum_copper_ratio")
    signals = data.get("signals", {})
    changes = data.get("changes", {})
    updated = data.get("timestamp_beijing", "—")
    source = ", ".join(data.get("source", []))

    quadrant = signals.get("quadrant", "?")
    quadrant_name = signals.get("quadrant_name", "—")
    description = signals.get("description", "")
    risk_level = signals.get("risk_level", "—")
    details = signals.get("details", [])
    actions = signals.get("actions", [])

    # 风险等级颜色
    risk_colors = {
        "低": "#4ade80",
        "中低": "#4ade80",
        "中": "#fbbf24",
        "中高": "#fb923c",
        "高": "#f87171",
    }
    risk_color = risk_colors.get(risk_level, "#9ca3af")

    # 价格卡片数据
    price_cards = []
    for key, label, unit, color in [
        ("gold", "黄金", "USD/oz", "#ffd700"),
        ("silver", "白银", "USD/oz", "#c0c0c0"),
        ("copper", "铜", "USD/lb", "#b87333"),
        ("aluminum", "铝", "USD/lb", "#a1a1aa"),
        ("dxy", "美元指数", "", "#60a5fa"),
    ]:
        val = data.get(key)
        ch = changes.get(key, {})
        price_cards.append({
            "key": key,
            "label": label,
            "unit": unit,
            "color": color,
            "value": f"{val:,.2f}" if val else "—",
            "change": ch,
        })

    # 比率卡片
    ratio_cards = []
    for key, label, desc, threshold in [
        ("gsr", "金银比", "风险偏好温度计", 75),
        ("copper_gold_ratio", "铜金比", "增长 vs 防守", 0.002),
        ("aluminum_copper_ratio", "铝铜比", "供给/能源约束", 0.6),
    ]:
        val = data.get(key)
        ch = changes.get(key, {})
        ratio_cards.append({
            "key": key,
            "label": label,
            "desc": desc,
            "value": f"{val:.4f}" if val else "—",
            "change": ch,
        })

    # 历史数据序列用于图表
    chart_data = []
    for r in history:
        try:
            ts = r.get("beijing", "") or r.get("timestamp", "")[:10]
            chart_data.append({
                "ts": ts,
                "gsr": float(r["gsr"]) if r.get("gsr") else None,
                "cgr": float(r["copper_gold_ratio"]) if r.get("copper_gold_ratio") else None,
                "quadrant": r.get("quadrant", ""),
            })
        except Exception:
            continue

    # 构建 HTML
    html = build_html(
        updated=updated,
        source=source,
        quadrant=quadrant,
        quadrant_name=quadrant_name,
        description=description,
        risk_level=risk_level,
        risk_color=risk_color,
        details=details,
        actions=actions,
        price_cards=price_cards,
        ratio_cards=ratio_cards,
        chart_data=chart_data,
    )

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"站点已生成: {OUTPUT_FILE}")


def build_html(**ctx):
    updated = ctx["updated"]
    source = ctx["source"]
    quadrant = ctx["quadrant"]
    quadrant_name = ctx["quadrant_name"]
    description = ctx["description"]
    risk_level = ctx["risk_level"]
    risk_color = ctx["risk_color"]
    details = ctx["details"]
    actions = ctx["actions"]
    price_cards = ctx["price_cards"]
    ratio_cards = ctx["ratio_cards"]
    chart_data = ctx["chart_data"]

    # 价格卡片 HTML
    price_html = ""
    for c in price_cards:
        ch = c["change"]
        cls = pct_class(ch.get("pct") if ch else None)
        sym = pct_symbol(ch.get("pct") if ch else None)
        pct_str = f"{ch['pct']:+.2f}%" if ch and ch.get("pct") is not None else ""
        val_str = c["value"]
        price_html += f'''
        <div class="card price-card">
            <div class="card-header">
                <span class="dot" style="background:{c['color']}"></span>
                <span class="label">{c['label']}</span>
                <span class="unit">{c['unit']}</span>
            </div>
            <div class="value">{val_str}</div>
            <div class="change {cls}">{sym} {pct_str}</div>
        </div>
        '''

    # 比率卡片 HTML
    ratio_html = ""
    for c in ratio_cards:
        ch = c["change"]
        cls = pct_class(ch.get("pct") if ch else None)
        sym = pct_symbol(ch.get("pct") if ch else None)
        pct_str = f"{ch['pct']:+.2f}%" if ch and ch.get("pct") is not None else ""
        ratio_html += f'''
        <div class="card ratio-card">
            <div class="ratio-label">{c['label']}</div>
            <div class="ratio-value">{c['value']}</div>
            <div class="ratio-desc">{c['desc']}</div>
            <div class="change {cls}">{sym} {pct_str}</div>
        </div>
        '''

    # 象限高亮
    q_active = {"A": "", "B": "", "C": "", "D": ""}
    if quadrant in q_active:
        q_active[quadrant] = "active"

    # 信号详情
    details_html = "".join(f"<li>▸ {d}</li>" for d in details)
    actions_html = "".join(f"<li>{a}</li>" for a in actions)

    # 图表数据 JSON
    chart_json = json.dumps(chart_data, ensure_ascii=False)

    return f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>贵金属信号灯 | Allocation Lab 宏观框架</title>
<style>
:root {{
  --bg: #0b0f19;
  --card: rgba(255,255,255,0.04);
  --border: rgba(255,255,255,0.08);
  --text: #e2e8f0;
  --muted: #94a3b8;
  --accent: #ffd700;
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  line-height: 1.6;
}}
.container {{
  max-width: 960px;
  margin: 0 auto;
  padding: 24px 16px 60px;
}}
header {{
  text-align: center;
  margin-bottom: 28px;
}}
header h1 {{
  margin: 0;
  font-size: 28px;
  letter-spacing: 1px;
}}
header h1 span {{
  color: var(--accent);
}}
.meta {{
  color: var(--muted);
  font-size: 13px;
  margin-top: 6px;
}}

/* 价格卡片 */
.grid-5 {{
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 12px;
  margin-bottom: 20px;
}}
.card {{
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 14px;
  padding: 16px;
  transition: transform .15s;
}}
.card:hover {{ transform: translateY(-2px); }}
.price-card .card-header {{
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}}
.dot {{
  width: 10px; height: 10px;
  border-radius: 50%;
  display: inline-block;
  box-shadow: 0 0 8px currentColor;
}}
.label {{ font-size: 14px; color: var(--muted); }}
.unit {{ font-size: 12px; color: #64748b; margin-left: auto; }}
.price-card .value {{
  font-size: 26px;
  font-weight: 700;
  letter-spacing: 0.5px;
}}
.change {{
  font-size: 13px;
  margin-top: 4px;
  font-variant-numeric: tabular-nums;
}}
.change.up {{ color: #4ade80; }}
.change.down {{ color: #f87171; }}
.change.neutral {{ color: #64748b; }}

/* 比率卡片 */
.grid-3 {{
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 12px;
  margin-bottom: 24px;
}}
.ratio-card {{ text-align: center; padding: 20px 16px; }}
.ratio-label {{ font-size: 15px; color: var(--muted); margin-bottom: 4px; }}
.ratio-value {{ font-size: 32px; font-weight: 700; margin: 6px 0; }}
.ratio-desc {{ font-size: 12px; color: #64748b; }}

/* 四象限 */
.quadrant-section {{ margin-bottom: 24px; }}
.section-title {{
  font-size: 16px;
  color: var(--muted);
  margin-bottom: 12px;
  text-align: center;
}}
.quadrant-grid {{
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
  max-width: 420px;
  margin: 0 auto;
}}
.q {{
  border-radius: 14px;
  padding: 18px 12px;
  text-align: center;
  opacity: 0.35;
  border: 2px solid transparent;
  transition: all .25s;
}}
.q.active {{
  opacity: 1;
  transform: scale(1.04);
  border-color: rgba(255,255,255,0.25);
  box-shadow: 0 10px 30px rgba(0,0,0,0.35);
}}
.q .q-name {{ font-size: 22px; font-weight: 700; }}
.q .q-desc {{ font-size: 12px; margin-top: 4px; opacity: .9; }}
.q-A {{ background: linear-gradient(135deg, #16a34a, #15803d); }}
.q-B {{ background: linear-gradient(135deg, #2563eb, #1d4ed8); }}
.q-C {{ background: linear-gradient(135deg, #ea580c, #c2410c); }}
.q-D {{ background: linear-gradient(135deg, #dc2626, #b91c1c); }}

/* 状态面板 */
.status-card {{
  background: linear-gradient(135deg, rgba(255,215,0,0.08), rgba(255,140,0,0.06));
  border: 1px solid rgba(255,215,0,0.18);
  border-radius: 16px;
  padding: 24px;
  margin-bottom: 24px;
}}
.status-header {{
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 10px;
  margin-bottom: 12px;
}}
.status-title {{
  font-size: 22px;
  font-weight: 700;
  color: var(--accent);
  margin: 0;
}}
.risk-badge {{
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 14px;
  border-radius: 20px;
  font-size: 13px;
  font-weight: 600;
  background: rgba(255,255,255,0.08);
  border: 1px solid var(--border);
}}
.risk-dot {{
  width: 8px; height: 8px;
  border-radius: 50%;
  background: {risk_color};
  box-shadow: 0 0 8px {risk_color};
}}
.status-desc {{
  color: #cbd5e1;
  font-size: 15px;
  margin-bottom: 14px;
}}
.signal-list {{
  list-style: none;
  padding: 0;
  margin: 0;
}}
.signal-list li {{
  padding: 8px 0;
  border-bottom: 1px solid rgba(255,255,255,0.04);
  font-size: 14px;
  color: var(--muted);
}}
.signal-list li:last-child {{ border-bottom: none; }}

/* 图表 */
.chart-wrap {{
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 14px;
  padding: 20px;
  margin-bottom: 24px;
}}
.chart-title {{
  font-size: 14px;
  color: var(--muted);
  margin-bottom: 12px;
}}
svg.chart {{
  width: 100%;
  height: 220px;
}}

/* 解读区 */
.insight {{
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 14px;
  padding: 20px;
  margin-bottom: 24px;
}}
.insight h3 {{
  margin: 0 0 10px;
  font-size: 16px;
  color: var(--text);
}}
.insight p {{
  margin: 0;
  font-size: 14px;
  color: var(--muted);
  line-height: 1.8;
}}

/* footer */
footer {{
  text-align: center;
  color: #475569;
  font-size: 12px;
  margin-top: 40px;
}}
footer a {{ color: #64748b; }}

@media (max-width: 520px) {{
  .grid-5 {{ grid-template-columns: repeat(2, 1fr); }}
  .grid-3 {{ grid-template-columns: 1fr; }}
  header h1 {{ font-size: 22px; }}
}}
</style>
</head>
<body>
<div class="container">
  <header>
    <h1>🥇 <span>贵金属信号灯</span></h1>
    <div class="meta">Allocation Lab 宏观框架 | 数据更新于 {updated}</div>
    <div class="meta" style="font-size:11px;opacity:.7">来源: {source}</div>
  </header>

  <div class="grid-5">
    {price_html}
  </div>

  <div class="grid-3">
    {ratio_html}
  </div>

  <div class="quadrant-section">
    <div class="section-title">📊 四象限市场状态</div>
    <div class="quadrant-grid">
      <div class="q q-A {q_active['A']}">
        <div class="q-name">A · 进攻盘</div>
        <div class="q-desc">铜银同强 · 风险偏好高</div>
      </div>
      <div class="q q-B {q_active['B']}">
        <div class="q-name">B · 防守盘</div>
        <div class="q-desc">金强铜弱 · 避险情绪浓</div>
      </div>
      <div class="q q-C {q_active['C']}">
        <div class="q-name">C · 复杂盘</div>
        <div class="q-desc">金铜同强 · 通胀+不确定</div>
      </div>
      <div class="q q-D {q_active['D']}">
        <div class="q-name">D · 高风险盘</div>
        <div class="q-desc">银强铜弱 · 后半段行情</div>
      </div>
    </div>
  </div>

  <div class="status-card">
    <div class="status-header">
      <div class="status-title">当前状态：{quadrant_name}</div>
      <div class="risk-badge"><span class="risk-dot"></span> 风险等级: {risk_level}</div>
    </div>
    <div class="status-desc">{description}</div>
    <ul class="signal-list">
      {details_html}
    </ul>
  </div>

  <div class="chart-wrap">
    <div class="chart-title">📈 历史走势（近60个交易日）</div>
    <svg class="chart" id="historyChart" viewBox="0 0 800 220" preserveAspectRatio="none"></svg>
  </div>

  <div class="status-card" style="border-color:rgba(255,255,255,0.08);background:linear-gradient(135deg,rgba(96,165,250,0.06),rgba(139,92,246,0.04));">
    <div class="status-header">
      <div class="status-title" style="color:#60a5fa;">🎯 建议动作</div>
    </div>
    <ul class="signal-list">
      {actions_html}
    </ul>
  </div>

  <div class="insight">
    <h3>⚠️ 使用须知</h3>
    <p>
      本工具仅为宏观状态跟踪与数据可视化，不构成任何投资建议。金属价格受多重因素驱动，历史规律不代表未来表现。
      四象限划分基于经验阈值，仅供参考；请结合自身的风险承受能力与资产配置目标独立决策。
    </p>
  </div>

  <footer>
    <p>Generated by 贵金属信号灯 · 数据来自 Yahoo Finance</p>
    <p style="margin-top:4px"><a href="https://mp.weixin.qq.com/s/HigsrOVaJMtpY2M0_oeWJA" target="_blank">参考文章: Allocation Lab 宏观交易框架</a></p>
  </footer>
</div>

<script>
(function(){{
  const data = {chart_json};
  if (!data.length) return;

  // 提取有效序列
  const gsrData = data.map(d => ({{ts: d.ts, v: d.gsr}})).filter(d => d.v != null);
  const cgrData = data.map(d => ({{ts: d.ts, v: d.cgr}})).filter(d => d.v != null);

  function drawLine(series, color, yLabel, offsetY, height) {{
    if (!series.length) return '';
    const vals = series.map(d => d.v);
    const min = Math.min(...vals);
    const max = Math.max(...vals);
    const range = max - min || 1;
    const pad = 20;
    const w = 800;
    const h = height || 90;
    const step = (w - pad*2) / (series.length - 1 || 1);

    let path = `M ${{pad}} ${{h - ((series[0].v - min)/range)*(h-20) - 10}}`;
    series.forEach((d, i) => {{
      const x = pad + i*step;
      const y = h - ((d.v - min)/range)*(h-20) - 10;
      path += ` L ${{x}} ${{y}}`;
    }});

    // 最近点圆点
    const last = series[series.length-1];
    const lx = pad + (series.length-1)*step;
    const ly = h - ((last.v - min)/range)*(h-20) - 10;

    return `
      <text x="${{pad}}" y="${{offsetY + 14}}" fill="#94a3b8" font-size="12">${{yLabel}}</text>
      <path d="${{path}}" fill="none" stroke="${{color}}" stroke-width="2" opacity="0.9" transform="translate(0, ${{offsetY}})"/>
      <circle cx="${{lx}}" cy="${{ly + offsetY}}" r="4" fill="${{color}}"/>
      <text x="${{lx+8}}" y="${{ly + offsetY + 4}}" fill="${{color}}" font-size="11">${{last.v.toFixed(2)}}</text>
    `;
  }}

  const svg = document.getElementById('historyChart');
  svg.innerHTML = `
    ${{drawLine(gsrData, '#fbbf24', '金银比 GSR', 0, 90)}}
    ${{drawLine(cgrData, '#60a5fa', '铜金比 CGR', 110, 90)}}
  `;
}})();
</script>
</body>
</html>
'''


def main():
    render()


if __name__ == "__main__":
    main()
