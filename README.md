# 🥇 贵金属信号灯

基于 [Allocation Lab 宏观交易框架](https://mp.weixin.qq.com/s/HigsrOVaJMtpY2M0_oeWJA) 的每日贵金属市场状态跟踪工具。
数据每天自动抓取、计算四象限信号，并部署为纯静态网页（无 JS、无后端）。

**在线访问：https://wzxiong.github.io/metal-signal/**

---

## 核心指标

| 指标 | 计算方式 | 含义 |
| --- | --- | --- |
| **金银比 GSR** | 黄金 / 白银 | 风险偏好温度计。上升 = 避险，下降 = 进攻 |
| **铜金比 CGR** | 铜 / 黄金 | 增长 vs 防守。上升 = 复苏/再通胀，下降 = 衰退担忧 |
| **铝铜比 ACR** | 铝 / 铜 | 供给 / 能源约束的辅助观察 |

## 四象限

以"金银比阈值 75"与"铜金比阈值 0.002"切分：

| 象限 | 状态 | 组合 | 特征 |
| --- | --- | --- | --- |
| **A** | 进攻盘 | 银强 + 铜强 | 风险偏好高，商品与股票同涨 |
| **B** | 防守盘 | 金强 + 铜弱 | 避险情绪浓，防守资产占优 |
| **C** | 复杂盘 | 金强 + 铜强 | 通胀 + 不确定性并存 |
| **D** | 高风险盘 | 银强 + 铜弱 | 后半段行情，波动放大 |

> 阈值与象限文案都定义在 `scripts/config.py`，页面、抓取、回填三处共享同一份，
> 不存在"逻辑用 75、文案写 80"的情况。

---

## 数据流

```
Yahoo Finance chart API
        │  scripts/market.py（重试 / 多主机 / 单位换算 / 区间校验）
        ▼
scripts/backfill.py ──► data/history.csv ◄── scripts/fetch_data.py
                          （按交易日去重）         │
                                                   ▼
                                        data/latest.json
                                                   │  scripts/build_site.py
                                                   ▼
                                            docs/index.html
```

## 目录结构

```
.
├── .github/workflows/daily.yml   # 每日抓取 → 构建 → 提交 → 部署
├── data/
│   ├── history.csv               # 每交易日一行（唯一事实来源）
│   └── latest.json               # 最新快照（含信号、涨跌、分位）
├── docs/index.html               # 构建产物，GitHub Pages 直接托管
├── scripts/
│   ├── config.py                 # 品种、阈值、校验区间（唯一配置源）
│   ├── market.py                 # Yahoo 客户端
│   ├── history.py                # 历史读写与旧格式迁移
│   ├── signals.py                # 比率、四象限、分位、风险分
│   ├── fetch_data.py             # 抓取主流程
│   ├── backfill.py               # 历史回填（幂等）
│   └── build_site.py             # 静态页面生成
└── tests/                        # 离线单元测试（55 例，无需联网）
```

---

## 本地运行

需要 Python ≥ 3.10。

```bash
pip install -r requirements.txt

# 1) 回填历史（首次运行，只补缺失的日期）
python -m scripts.backfill

# 2) 抓取最新行情
python -m scripts.fetch_data

# 3) 生成页面
python -m scripts.build_site

# 4) 本地预览
python -m http.server 8080 --directory docs
```

> 若所在网络访问不到 Yahoo（返回 403 人机验证），抓取会失败并保留历史数据；
> GitHub Actions 的 runner 通常可以正常访问。

### 测试

```bash
python -m unittest discover -s tests -t . -v
```

全部测试使用注入的假行情，不联网。覆盖：四象限矩阵与边界、文案与阈值一致性、
旧版 CSV 迁移、Yahoo 响应解析、单日异常复核、降级标记、页面结构与转义。

---

## 数据格式

`data/history.csv`（按 `date` 升序，一天一行）：

```
date,timestamp,gold,silver,copper,aluminum,dxy,gsr,copper_gold_ratio,aluminum_copper_ratio,quadrant
2026-11-02,2026-11-02T01:30:00+00:00,3912.9,50.37,6.53,1.44,101.73,77.68,0.00167,0.2202,B
```

`data/latest.json` 关键字段：

| 字段 | 说明 |
| --- | --- |
| `as_of` | 观察日（交易所交易日，不是抓取时间） |
| `prices` / `ratios` | 本期价格与派生比率 |
| `changes` | 相对**上一个交易日**的涨跌；基准过旧时为空 |
| `baseline` | 对比基准日期、相隔天数、是否可用 |
| `signals` | 象限、风险分、解读文案、历史分位、5/20 日变化 |
| `stale` / `degraded` | 是否降级，以及每个品种的具体原因 |

---

## 数据质量保护

1. **单位与区间校验**：每个品种换算到展示单位后必须落在合理区间
   （如铝 0.4–5 USD/lb），否则判定为数据源异常并沿用上一有效值。
2. **单日波动复核**：与上一交易日相比超出阈值（黄金 25%、白银 35%…）时降级，
   避免某次接口换了单位把历史污染成 `+148096%`。
3. **基准时效**：对比基准超过 5 天则隐藏涨跌幅，不再把半年涨跌当成"日涨跌"。
4. **页面明示**：数据降级、数据滞后、样本不足都会在页面上显示为提示条。

---

## 自动更新

`.github/workflows/daily.yml` 每天北京时间 09:30 运行：

1. 跑单元测试
2. `backfill`（历史不足 120 天时自动补两年日线）
3. 抓取最新行情 → 计算信号
4. 生成静态页面
5. **把 `data/` 和 `docs/` 提交回仓库**（否则历史永远长不起来）
6. 部署到 GitHub Pages

也可以手动触发：Actions → Daily Metal Signal → Run workflow（首次建议勾选 `backfill`）。

> 定时任务在仓库 60 天无提交后会被 GitHub 自动停用。因为每天都会提交数据快照，
> 这个问题顺带被解决了。

---

## 免责声明

本工具仅用于宏观状态跟踪与数据可视化，**不构成任何投资建议**。
金属价格受多重因素驱动，历史规律不代表未来表现；四象限基于经验阈值划分，
请结合自身风险承受能力独立决策。

数据来源：[Yahoo Finance](https://finance.yahoo.com)

## License

[MIT](LICENSE)
