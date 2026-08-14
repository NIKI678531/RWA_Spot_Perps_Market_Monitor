# RWA Spot & Perps Market Monitor

代币化 RWA（股票 / ETF / 基金）市场监控系统 —— 追踪现货规模与成交、CEX/DEX 场所格局、
发行商竞争态势、跨所永续需求，并自动检测需求异常。

## 它回答什么问题

| 问题 | 对应页面 |
|:--|:--|
| RWA 现在多大？在涨还是在跌？ | Executive |
| 钱在哪个交易所、哪个竞品手里？ | Venues / Issuers |
| **什么东西突然火了？** | **Anomaly Radar** ★ |
| 客户到底想要什么？ | Underlying 360 ★ |

核心差异化是 **Anomaly Radar**：识别「原先没人买的产品突然多了很多人买」这类需求突现信号，
而不只是把当前数字画成图表。

## 文档

| 文件 | 内容 |
|:--|:--|
| [`ARCHITECTURE.md`](./ARCHITECTURE.md) | **权威架构设计** —— 数据模型、异常引擎、API、分期计划 |
| [`DESIGN.md`](./DESIGN.md) | UI 设计系统（CSOP 玻璃拟态金融 UI 规范） |
| [`CLAUDE.md`](./CLAUDE.md) | Claude Code 工作指引 + 业务硬约束 |
| [`AGENTS.md`](./AGENTS.md) | Agent 协作约定 |

## 技术栈

- **后端** Python 3.12+ · FastAPI · SQLAlchemy 2.0 · Alembic · `uv`
- **前端** React 18 · TypeScript · Webpack · antd 5 · framer-motion
- **数据库** MySQL 8.4（compose）/ SQLite（本地兜底）
- **调度** APScheduler · **报告** openpyxl + python-docx

## 快速开始

```bash
# 后端
npm run backend            # http://localhost:8025/api/docs

# 前端
npm run frontend           # http://localhost:3025

# 全栈
docker compose up --build  # 前端 8085 / 后端 8025 / MySQL 3307
```

## 数据源

CoinGecko（现货 tickers + 类别）· GeckoTerminal（DEX 池）· Loris（跨所永续）·
Binance（bStocks 现货 + TradFi 永续）· Alpaca（美股底层参考价）· 发行商官网（产品主表）

## 三条不可让步的原则

1. **口径不可相加** —— 市值 / 现货成交 / DEX 流动性 / 永续成交 / OI 是五类不同指标，
   代码层面强制隔离，跨口径求和抛异常。
2. **`Not verified` ≠ `0`** —— 取不到就是取不到，绝不用 0 填充。
3. **告警必须可解释** —— 每条告警都能点开看到原始值、基线、样本量、规则名。

## 状态

P0 架构设计完成。实现分四期推进，见 `ARCHITECTURE.md` §9。

---
Not investment advice. 数据为可观察市场快照，不代表法律意义上的发行在外资产或审计后储备。
