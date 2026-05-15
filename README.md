# 🥇 贵金属信号灯

基于 Allocation Lab 宏观交易框架的每日贵金属市场状态分析工具。

数据每日自动抓取并部署为静态网页，无需本地服务器即可访问。

---

## 🌐 在线访问

GitHub Pages: **https://wzxiong.github.io/metal-signal/**

---

## 📊 核心指标

| 指标 | 含义 |
|------|------|
| **金银比 (GSR)** | 风险偏好温度计。上升=避险，下降=进攻 |
| **铜金比 (CGR)** | 增长 vs 防守。上升=复苏/再通胀，下降=衰退担忧 |
| **铝铜比 (ACR)** | 供给/能源约束辅助指标 |

## 🎯 四象限

| 象限 | 状态 | 特征 |
|------|------|------|
| A | 进攻盘 | 铜银同强，风险偏好高 |
| B | 防守盘 | 金强铜弱，避险情绪浓 |
| C | 复杂盘 | 金铜同强，通胀+不确定 |
| D | 高风险盘 | 银强铜弱，后半段行情 |

---

## 🚀 本地构建（可选）

```bash
# 安装依赖
pip install -r requirements.txt

# 抓取最新数据
python scripts/fetch_data.py

# 生成静态页面
python scripts/build_site.py

# 本地预览
cd docs && python -m http.server 8080
```

---

## ⚙️ 自动更新

通过 GitHub Actions 每日定时运行（北京时间 09:30 左右）：

1. 从 Yahoo Finance 抓取金、银、铜、铝、美元指数
2. 计算比率与四象限信号
3. 生成静态页面并部署到 GitHub Pages

工作流定义：`.github/workflows/daily.yml`

---

## ⚠️ 免责声明

本工具仅为宏观状态跟踪与数据可视化，**不构成任何投资建议**。
金属价格受多重因素驱动，历史规律不代表未来表现。

数据来源：[Yahoo Finance](https://finance.yahoo.com)

参考文章：[Allocation Lab 宏观交易框架](https://mp.weixin.qq.com/s/HigsrOVaJMtpY2M0_oeWJA)
