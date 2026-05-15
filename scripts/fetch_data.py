#!/usr/bin/env python3
"""
贵金属信号灯 - 每日数据抓取
基于 Allocation Lab 宏观交易框架
"""

import os
import json
import csv
import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path

# 尝试使用 yfinance
try:
    import yfinance as yf
    HAS_YFINANCE = True
except ImportError:
    HAS_YFINANCE = False

# 项目根目录
ROOT = Path(__file__).parent.parent.resolve()
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)

LATEST_FILE = DATA_DIR / "latest.json"
HISTORY_FILE = DATA_DIR / "history.csv"

# 金属配置: (字段名, [候选symbols], 名称, 单位)
METALS = [
    ("gold",   ["GC=F"],       "黄金",   "USD/oz"),
    ("silver", ["SI=F"],       "白银",   "USD/oz"),
    ("copper", ["HG=F"],       "铜",     "USD/lb"),
    ("aluminum", ["ALI=F", "ALM=F", "AL=F"], "铝", "USD/lb"),
]

DXY_SYMBOLS = ["DX-Y.NYB", "UUP"]


def fetch_yfinance(symbols):
    """用 yfinance 获取数据，返回 (price, symbol_used)"""
    for sym in symbols:
        try:
            ticker = yf.Ticker(sym)
            # 优先获取今日数据，设置短超时
            hist = ticker.history(period="2d", interval="1d", timeout=8)
            if not hist.empty:
                price = float(hist["Close"].iloc[-1])
                return round(price, 4), sym
            # fallback 到 fast_info
            try:
                price = ticker.fast_info.get("lastPrice")
                if price:
                    return round(float(price), 4), sym
            except Exception:
                pass
        except Exception as e:
            print(f"  yfinance {sym} failed: {e}")
            continue
    return None, None


def fetch_requests(symbols):
    """用 requests 直连 Yahoo Finance chart API"""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json",
    }
    for sym in symbols:
        try:
            url = f"https://query2.finance.yahoo.com/v8/finance/chart/{sym}"
            resp = requests.get(url, headers=headers, timeout=15)
            if resp.status_code != 200:
                continue
            data = resp.json()
            result = data.get("chart", {}).get("result", [None])[0]
            if not result:
                continue
            meta = result.get("meta", {})
            price = meta.get("regularMarketPrice") or meta.get("previousClose")
            if price:
                return round(float(price), 4), sym
            # 如果 meta 没有，尝试从 prices 数组取最后一个
            timestamps = result.get("timestamp", [])
            prices = result.get("indicators", {}).get("quote", [{}])[0].get("close", [])
            if prices:
                last = [p for p in prices if p is not None][-1]
                return round(float(last), 4), sym
        except Exception as e:
            print(f"  requests {sym} failed: {e}")
            continue
    return None, None


def fetch_price(symbols):
    """先尝试 yfinance，失败则回退到 requests 直连"""
    if HAS_YFINANCE:
        price, used = fetch_yfinance(symbols)
        if price is not None:
            return price, used
    return fetch_requests(symbols)


def fetch_all():
    now = datetime.now(timezone.utc)
    beijing = now.astimezone(timezone(timedelta(hours=8)))

    record = {
        "timestamp": now.isoformat(),
        "timestamp_beijing": beijing.strftime("%Y-%m-%d %H:%M"),
        "source": [],
    }

    print(f"[{beijing.strftime('%Y-%m-%d %H:%M')}] 开始抓取数据...")

    for field, symbols, name, unit in METALS:
        price, used = fetch_price(symbols)
        if price:
            # COMEX 铝期货 ALI=F 报价为 USD/吨，转换为 USD/磅
            if field == "aluminum":
                price = round(price / 2204.62, 4)
            record[field] = price
            record["source"].append(f"{name}({used})")
            print(f"  {name}: {price} {unit}")
        else:
            print(f"  {name}: 获取失败")

    # 美元指数
    dxy_price, dxy_sym = fetch_price(DXY_SYMBOLS)
    if dxy_price:
        record["dxy"] = dxy_price
        record["source"].append(f"美元指数({dxy_sym})")
        print(f"  美元指数: {dxy_price}")
    else:
        print(f"  美元指数: 获取失败")

    # 计算核心比率
    gold = record.get("gold")
    silver = record.get("silver")
    copper = record.get("copper")
    aluminum = record.get("aluminum")

    if gold and silver and silver > 0:
        record["gsr"] = round(gold / silver, 2)
        record["silver_gold_ratio"] = round(silver / gold, 5)

    if copper and gold and gold > 0:
        record["copper_gold_ratio"] = round(copper / gold, 5)

    if aluminum and copper and copper > 0:
        record["aluminum_copper_ratio"] = round(aluminum / copper, 4)

    # 判断象限
    record["signals"] = calculate_signals(record)

    return record


def calculate_signals(data):
    """计算四象限信号"""
    signals = {
        "quadrant": None,
        "quadrant_name": "",
        "description": "",
        "risk_level": "unknown",
        "details": [],
    }

    gsr = data.get("gsr")
    cgr = data.get("copper_gold_ratio")

    if gsr is None or cgr is None:
        signals["details"].append("数据不完整，无法判断象限")
        return signals

    # 核心逻辑：
    # 金银比 GSR = Gold/Silver。GSR 高 = 金强银弱 = 避险 = 防守
    # 铜金比 CGR = Copper/Gold。CGR 高 = 铜强金弱 = 增长 = 周期
    #
    # 四象限（基于文章框架）：
    # A(进攻): 银强(GSR低) + 铜强(CGR高)
    # B(防守): 金强(GSR高) + 铜弱(CGR低)
    # C(复杂): 金强(GSR高) + 铜强(CGR高)  -> 通胀+不确定
    # D(高风险): 银强(GSR低) + 铜弱(CGR低) -> 后半段行情

    gsr_threshold = 75.0
    cgr_threshold = 0.002

    # 金银比解读
    if gsr > 80:
        signals["details"].append(f"金银比 {gsr} > 80，避险情绪浓厚")
    elif gsr < 70:
        signals["details"].append(f"金银比 {gsr} < 70，风险偏好较高")
    else:
        signals["details"].append(f"金银比 {gsr} 处于 70-80 中性区间")

    # 铜金比解读
    if cgr > cgr_threshold:
        signals["details"].append(f"铜金比 {cgr} 偏高，增长预期较强")
    else:
        signals["details"].append(f"铜金比 {cgr} 偏低，增长预期偏弱")

    # 铝铜比辅助
    acr = data.get("aluminum_copper_ratio")
    if acr:
        if acr > 0.6:
            signals["details"].append(f"铝铜比 {acr} 偏高，供给/能源约束因素上升")
        else:
            signals["details"].append(f"铝铜比 {acr} 正常，需求端定价为主")

    # 象限动作建议（基于 Allocation Lab 框架原文，不含具体产品代码）
    actions = []
    if gsr < gsr_threshold and cgr >= cgr_threshold:
        signals["quadrant"] = "A"
        signals["quadrant_name"] = "进攻盘"
        signals["description"] = "铜银同强，风险偏好高，商品与股票往往同涨"
        signals["risk_level"] = "中低"
        actions = [
            "策略风格：适度进攻，关注周期与成长",
            "仓位参考：权益仓位可保持中高，工业金属偏积极",
            "风险提示：交易拥挤度可能上升，注意波动放大与回调",
        ]
    elif gsr >= gsr_threshold and cgr < cgr_threshold:
        signals["quadrant"] = "B"
        signals["quadrant_name"] = "防守盘"
        signals["description"] = "黄金强，铜弱，避险情绪浓，防守板块占优"
        signals["risk_level"] = "中"
        actions = [
            "策略风格：防御为主，增配避险资产",
            "仓位参考：降低风险敞口，提高现金/短债比例",
            "风险提示：股票与商品波动加大，优先保本，关注美元走势",
        ]
    elif gsr >= gsr_threshold and cgr >= cgr_threshold:
        signals["quadrant"] = "C"
        signals["quadrant_name"] = "复杂盘"
        signals["description"] = "金铜同强，通胀+不确定性并存，需均衡应对"
        signals["risk_level"] = "中高"
        actions = [
            "策略风格：均衡配置，不单一押注",
            "仓位参考：保持对冲，黄金与工业金属均配",
            "风险提示：通胀与不确定性并存，做好风险对冲，避免追涨杀跌",
        ]
    elif gsr < gsr_threshold and cgr < cgr_threshold:
        signals["quadrant"] = "D"
        signals["quadrant_name"] = "高风险盘"
        signals["description"] = "银强铜弱，后半段行情特征，波动加大"
        signals["risk_level"] = "高"
        actions = [
            "策略风格：谨慎应对，逐步获利了结",
            "仓位参考：降低杠杆，持有现金等待更清晰信号",
            "风险提示：后半段行情特征，避免追高，准备好止损策略",
        ]
    else:
        signals["quadrant"] = "B"
        signals["quadrant_name"] = "防守盘"
        signals["description"] = "数据边界，倾向防守"
        signals["risk_level"] = "中"
        actions = [
            "策略风格：观望为主",
            "仓位参考：保持中性偏低",
            "风险提示：数据不完整，等待更明确信号",
        ]

    signals["actions"] = actions
    return signals


def load_previous():
    """加载上一次的数据用于计算涨跌"""
    if HISTORY_FILE.exists():
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
            if rows:
                return rows[-1]
    return None


def validate_and_sanitize(record, previous):
    """
    价格合理性校验：如果某金属较历史价格变化幅度过大，
    认为数据源单位/合约出错，丢弃该值并使用历史值回退。
    """
    if not previous:
        return record

    # (字段名, 单日最大允许变化比例)
    thresholds = {
        "gold": 0.25,
        "silver": 0.35,
        "copper": 0.35,
        "aluminum": 0.40,
        "dxy": 0.12,
    }

    for key, max_pct in thresholds.items():
        curr = record.get(key)
        try:
            prev_val = float(previous.get(key, 0)) if previous.get(key) else None
        except (ValueError, TypeError):
            prev_val = None

        if curr is not None and prev_val and prev_val != 0:
            change_pct = abs(curr - prev_val) / prev_val
            if change_pct > max_pct:
                print(f"  ⚠️ 异常值警告: {key} = {curr} (历史 {prev_val}, 变化 {change_pct*100:.1f}%), 超过阈值 {max_pct*100:.0f}%, 丢弃")
                record[key] = None

    # 如果铝被丢弃，连带清除依赖它的比率
    if record.get("aluminum") is None:
        record.pop("aluminum_copper_ratio", None)

    return record


def append_history(record):
    """追加历史记录"""
    flat = {
        "timestamp": record.get("timestamp", ""),
        "beijing": record.get("timestamp_beijing", ""),
        "gold": record.get("gold", ""),
        "silver": record.get("silver", ""),
        "copper": record.get("copper", ""),
        "aluminum": record.get("aluminum", ""),
        "dxy": record.get("dxy", ""),
        "gsr": record.get("gsr", ""),
        "copper_gold_ratio": record.get("copper_gold_ratio", ""),
        "aluminum_copper_ratio": record.get("aluminum_copper_ratio", ""),
        "quadrant": record.get("signals", {}).get("quadrant", ""),
    }
    write_header = not HISTORY_FILE.exists()
    with open(HISTORY_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=flat.keys())
        if write_header:
            writer.writeheader()
        writer.writerow(flat)


def main():
    record = fetch_all()

    # 价格合理性校验（防止数据源单位/合约变更导致异常值污染历史）
    has_data = any(record.get(k) for k in ["gold", "silver", "copper", "aluminum"])
    if has_data:
        prev = load_previous()
        if prev:
            record = validate_and_sanitize(record, prev)
            # 若关键价格被清除，连带清除依赖它的比率与信号
            if record.get("gold") is None or record.get("silver") is None:
                record.pop("gsr", None)
                record.pop("silver_gold_ratio", None)
            if record.get("copper") is None or record.get("gold") is None:
                record.pop("copper_gold_ratio", None)
            if record.get("aluminum") is None or record.get("copper") is None:
                record.pop("aluminum_copper_ratio", None)
            record["signals"] = calculate_signals(record)

    # 如果什么都没抓到，尝试从历史最后一条回退
    if not has_data:
        prev = load_previous()
        if prev:
            print("网络抓取失败，使用历史数据最后一条作为回退...")
            beijing_now = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")
            record = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "timestamp_beijing": beijing_now,
                "source": ["Fallback from history (network unavailable)"],
                "gold": float(prev["gold"]) if prev.get("gold") else None,
                "silver": float(prev["silver"]) if prev.get("silver") else None,
                "copper": float(prev["copper"]) if prev.get("copper") else None,
                "aluminum": float(prev["aluminum"]) if prev.get("aluminum") else None,
                "dxy": float(prev["dxy"]) if prev.get("dxy") else None,
            }
            # 重新计算比率与信号
            gold = record.get("gold")
            silver = record.get("silver")
            copper = record.get("copper")
            aluminum = record.get("aluminum")
            if gold and silver:
                record["gsr"] = round(gold / silver, 2)
                record["silver_gold_ratio"] = round(silver / gold, 5)
            if copper and gold:
                record["copper_gold_ratio"] = round(copper / gold, 5)
            if aluminum and copper:
                record["aluminum_copper_ratio"] = round(aluminum / copper, 4)
            record["signals"] = calculate_signals(record)
        else:
            print("网络抓取失败且无历史数据，生成空记录")

    # 计算较上日涨跌
    prev = load_previous()
    record["changes"] = {}
    if prev:
        for key in ["gold", "silver", "copper", "aluminum", "dxy", "gsr", "copper_gold_ratio"]:
            try:
                curr_v = record.get(key)
                prev_v = float(prev.get(key, 0)) if prev.get(key) else None
                if curr_v is not None and prev_v is not None and prev_v != 0:
                    pct = (curr_v - prev_v) / prev_v * 100
                    record["changes"][key] = {
                        "value": round(curr_v - prev_v, 4),
                        "pct": round(pct, 2),
                    }
            except Exception:
                pass

    # 保存最新数据
    with open(LATEST_FILE, "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=2)

    # 只有当有真实价格且与最后一条不同时才追加历史
    has_real = any(record.get(k) for k in ["gold", "silver", "copper", "aluminum"])
    if has_real:
        # 避免重复追加同一条（同一天同一小时）
        should_append = True
        if prev:
            same = all(
                abs((record.get(k) or 0) - float(prev.get(k) or 0)) < 0.001
                for k in ["gold", "silver", "copper", "aluminum"]
            )
            if same:
                should_append = False
        if should_append:
            append_history(record)
            print("历史数据已更新")
        else:
            print("数据与上条相同，跳过历史追加")

    print("\n数据保存完成:", LATEST_FILE)
    return record


if __name__ == "__main__":
    main()
