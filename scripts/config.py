#!/usr/bin/env python3
"""集中配置：数据路径、品种定义、阈值、校验区间。

设计原则：所有"魔法数字"只在这里出现一次。
抓取（fetch_data）、回填（backfill）、建站（build_site）三个脚本都从这里读，
避免阈值在多处硬编码后互相打架。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta, timezone
from pathlib import Path

# ---------------------------------------------------------------- 路径

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DOCS_DIR = ROOT / "docs"

LATEST_FILE = DATA_DIR / "latest.json"
HISTORY_FILE = DATA_DIR / "history.csv"
OUTPUT_FILE = DOCS_DIR / "index.html"

SITE_URL = "https://wzxiong.github.io/metal-signal/"
REFERENCE_URL = "https://mp.weixin.qq.com/s/HigsrOVaJMtpY2M0_oeWJA"

# ---------------------------------------------------------------- 时间

BEIJING = timezone(timedelta(hours=8))
UTC = timezone.utc

# 磅 / 公吨（铝期货按吨报价，展示按磅）
POUNDS_PER_TONNE = 2204.6226218

# ---------------------------------------------------------------- 历史窗口

# 保留多少个自然日（约两年，够算分位与年度区间）
HISTORY_KEEP_DAYS = 760
# 回填默认拉取的年数
BACKFILL_YEARS = 2
# 历史上少于这个天数就自动回填
BACKFILL_MIN_DAYS = 120
# 分位统计至少需要多少个样本，不足则页面上不显示分位
PERCENTILE_MIN_SAMPLES = 60
# 图表展示最近多少个交易日
CHART_DAYS = 120
# 对比基准最多允许相隔多少天；超过则隐藏涨跌幅（避免把半年涨跌当成日涨跌）
MAX_BASELINE_AGE_DAYS = 5
# 判定为异常单日波动而触发"降级"的阈值（仅在基准足够新时生效）
MAX_DAILY_CHANGE = {
    "gold": 0.25,
    "silver": 0.35,
    "copper": 0.35,
    "aluminum": 0.40,
    "dxy": 0.12,
}


# ---------------------------------------------------------------- 品种

@dataclass(frozen=True)
class Asset:
    """一个可抓取的品种。"""

    key: str
    label: str
    unit: str
    # 依次尝试的 Yahoo symbol，第一个成功即用
    symbols: tuple[str, ...]
    color: str
    # symbol -> 换算除数（Yahoo 报价单位 -> 展示单位）。
    # 没列在这里的 symbol 视为"单位未知"，直接跳过，避免单位错配。
    divisors: dict[str, float] = field(default_factory=dict)
    # 换算后的合理区间（展示单位），超出即判定数据异常
    sane_min: float = 0.0
    sane_max: float = 1e9
    decimals: int = 2


ASSETS: tuple[Asset, ...] = (
    Asset(
        key="gold",
        label="黄金",
        unit="USD/oz",
        symbols=("GC=F",),
        color="#f5c542",
        divisors={"GC=F": 1.0},
        sane_min=500,
        sane_max=20_000,
        decimals=2,
    ),
    Asset(
        key="silver",
        label="白银",
        unit="USD/oz",
        symbols=("SI=F",),
        color="#cbd5e1",
        divisors={"SI=F": 1.0},
        sane_min=5,
        sane_max=500,
        decimals=2,
    ),
    Asset(
        key="copper",
        label="铜",
        unit="USD/lb",
        symbols=("HG=F",),
        color="#d08a4e",
        divisors={"HG=F": 1.0},
        sane_min=1.0,
        sane_max=20,
        decimals=3,
    ),
    Asset(
        key="aluminum",
        label="铝",
        unit="USD/lb",
        symbols=("ALI=F", "ALM=F"),
        color="#9aa4b2",
        # COMEX 铝期货按 USD/公吨报价，展示统一换成 USD/磅
        divisors={"ALI=F": POUNDS_PER_TONNE, "ALM=F": POUNDS_PER_TONNE},
        sane_min=0.4,
        sane_max=5,
        decimals=4,
    ),
    Asset(
        key="dxy",
        label="美元指数",
        unit="",
        symbols=("DX-Y.NYB", "DX=F"),
        color="#5ea3ff",
        divisors={"DX-Y.NYB": 1.0, "DX=F": 1.0},
        sane_min=50,
        sane_max=200,
        decimals=2,
    ),
)

ASSETS_BY_KEY = {a.key: a for a in ASSETS}

# 核心品种：缺失即视为整体降级
CORE_KEYS = ("gold", "silver", "copper")

# ---------------------------------------------------------------- 比率

@dataclass(frozen=True)
class RatioSpec:
    """一个派生比率。

    high_means 用于生成解读文案，确保"高于阈值"和"意味着什么"始终对应，
    不会出现阈值与文案各说各话的情况。
    """

    key: str
    label: str
    desc: str
    decimals: int
    threshold: float
    high_means: str
    low_means: str


RATIOS: tuple[RatioSpec, ...] = (
    RatioSpec(
        key="gsr",
        label="金银比",
        desc="风险偏好温度计",
        decimals=2,
        threshold=75.0,
        high_means="金强银弱，避险情绪偏浓",
        low_means="银强金弱，风险偏好偏高",
    ),
    RatioSpec(
        key="copper_gold_ratio",
        label="铜金比",
        desc="增长 vs 防守",
        decimals=4,
        threshold=0.002,
        high_means="铜强金弱，增长预期偏强",
        low_means="铜弱金强，增长预期偏弱",
    ),
    RatioSpec(
        key="aluminum_copper_ratio",
        label="铝铜比",
        desc="供给 / 能源约束",
        decimals=4,
        threshold=0.6,
        high_means="供给或能源约束上升",
        low_means="需求端定价为主",
    ),
)

RATIOS_BY_KEY = {r.key: r for r in RATIOS}

# 四象限判定的两个轴（用比率阈值定义，避免文字与逻辑不一致）
GSR_THRESHOLD = RATIOS_BY_KEY["gsr"].threshold
CGR_THRESHOLD = RATIOS_BY_KEY["copper_gold_ratio"].threshold

# ---------------------------------------------------------------- 四象限

@dataclass(frozen=True)
class Quadrant:
    code: str
    name: str
    tagline: str
    description: str
    risk_level: str
    risk_score: int  # 0-100，用于风险条
    actions: tuple[str, ...]


QUADRANTS: dict[str, Quadrant] = {
    "A": Quadrant(
        code="A",
        name="进攻盘",
        tagline="铜银同强 · 风险偏好高",
        description="铜银同强，风险偏好高，商品与股票往往同涨。",
        risk_level="中低",
        risk_score=35,
        actions=(
            "策略风格：适度进攻，关注周期与成长",
            "仓位参考：权益仓位可保持中高，工业金属偏积极",
            "风险提示：交易拥挤度可能上升，注意波动放大与回调",
        ),
    ),
    "B": Quadrant(
        code="B",
        name="防守盘",
        tagline="金强铜弱 · 避险情绪浓",
        description="黄金强、铜弱，避险情绪浓，防守资产占优。",
        risk_level="中",
        risk_score=50,
        actions=(
            "策略风格：防御为主，增配避险资产",
            "仓位参考：降低风险敞口，提高现金 / 短债比例",
            "风险提示：股票与商品波动加大，关注美元与利率走向",
        ),
    ),
    "C": Quadrant(
        code="C",
        name="复杂盘",
        tagline="金铜同强 · 通胀+不确定",
        description="金铜同强，通胀与不确定性并存，需要均衡应对。",
        risk_level="中高",
        risk_score=70,
        actions=(
            "策略风格：均衡配置，不单一押注",
            "仓位参考：保持对冲，黄金与工业金属均配",
            "风险提示：通胀与不确定性并存，避免追涨杀跌",
        ),
    ),
    "D": Quadrant(
        code="D",
        name="高风险盘",
        tagline="银强铜弱 · 后半段行情",
        description="银强铜弱，后半段行情特征，波动明显加大。",
        risk_level="高",
        risk_score=85,
        actions=(
            "策略风格：谨慎应对，逐步获利了结",
            "仓位参考：降低杠杆，保留现金等待更清晰信号",
            "风险提示：后半段行情，避免追高，提前设定止损",
        ),
    ),
}

# ---------------------------------------------------------------- 其它

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)

# Yahoo 行情主机，按顺序尝试
YAHOO_HOSTS = (
    "https://query2.finance.yahoo.com",
    "https://query1.finance.yahoo.com",
)
