# RWA Spot & Perps Market Monitor

代币化 RWA（股票 / ETF / 基金）市场**产品决策雷达** —— 追踪现货规模与成交、CEX/DEX 场所格局、
发行商竞争态势、跨所永续需求，自动检测需求异常，并把异常推进到研究、发行或合作评估的闭环。

## 它回答什么问题

| 问题 | 对应页面 |
|:--|:--|
| 今天最重要的变化是什么？可信吗？要不要动？ | 决策首页 |
| **什么东西突然被买了？谁在跟进？** | **异常雷达** ★ |
| 客户到底想要什么？我们有没有覆盖？ | Underlying 360 · 主题需求 ★ |
| 这个结论能不能发？数据够不够好？ | 数据质量 |
| 上午那份报告到底是哪组数字？ | 报告与复盘（冻结版） |

核心差异化是**异常雷达**：识别「原先没人买的产品突然多了很多人买」这类需求突现信号，
并让每条信号有 owner、有证据与反证、有结论、有复盘——而不只是把当前数字画成图表。

每条发现走 **发现 → 验证 → 解释 → 行动 → 复盘**。

## 文档

| 文件 | 内容 |
|:--|:--|
| [`ARCHITECTURE.md`](./ARCHITECTURE.md) | **权威架构设计** —— 数据模型、异常引擎、发布门、版本、API、交付计划 |
| [`docs/REQUIREMENTS-R1.md`](./docs/REQUIREMENTS-R1.md) | **R1 需求编号与验收标准**（PRD v2.0 的仓库内权威副本） |
| [`DESIGN.md`](./DESIGN.md) | UI 设计系统 |
| [`CLAUDE.md`](./CLAUDE.md) | Claude Code 工作指引 + 业务硬约束 |
| [`AGENTS.md`](./AGENTS.md) | Agent 协作约定 |
| [`CONTEXT.md`](./CONTEXT.md) | 领域术语表（口径、层级、告警状态、版本的唯一定义） |
| [`docs/UI-LAYOUT.md`](./docs/UI-LAYOUT.md) | 页面模板、状态规范、信息层级 |
| [`docs/DATAVIZ.md`](./docs/DATAVIZ.md) | 图表规范与 R1 图表目录 |
| [`docs/DETECTORS.md`](./docs/DETECTORS.md) | 17 个检测器的口径、阈值与发布门 |
| [`docs/adr/`](./docs/adr/) | 设计取舍记录 |

## 技术栈

- **后端** Python 3.12+ · FastAPI · SQLAlchemy 2.0 · Alembic · `uv`
- **前端** React 18 · TypeScript · Webpack 5 · antd 5 · ECharts · react-router 6 · lucide-react
- **数据库** MySQL 8.4（compose）/ SQLite（本地兜底）
- **调度** APScheduler · **报告** openpyxl + python-docx

## 快速开始

```bash
# 后端（无 .env 时落回 sqlite:///./app.db）
npm run backend:migrate    # alembic upgrade head
npm run backend            # http://localhost:8025/api/docs
npm run backend:test       # pytest

# 前端（首次需装依赖；dev server 把 /api 代理到 8025）
cd frontend && npm install
npm run frontend           # http://localhost:3025

# 全栈
docker compose up --build  # 前端 8085 / 后端 8025 / MySQL 3307
```

## 数据源

CoinGecko（现货 tickers + 类别）· GeckoTerminal（DEX 池）· Loris（跨所永续）·
Binance（bStocks 现货 + TradFi 永续）· Alpaca（美股底层参考价）· 发行商官网（产品主表）

## 五条不可让步的原则

1. **口径不可相加** —— 市值 / 现货成交 / DEX 流动性 / 永续成交 / OI 是五类不同指标，
   代码层面强制隔离，跨口径求和抛异常。
2. **`Not verified` ≠ `0`** —— 取不到就是取不到，绝不用 0 填充。
3. **告警必须可解释** —— 每条告警都能点开看到原始值、基线、样本量、规则名与反证。
4. **冻结版不可变** —— 更正生成修订版，旧版本仍可读。
5. **没有分数可以批准任何事** —— 系统负责发现、排序与保全证据，审批在系统之外。

## 状态

v2.0 已实现：全量建表与迁移、口径类型系统、六个采集器、归一化与质量筛选、
横截面 + 时序检测器、xlsx / docx 报告、13 个 API 路由模块，以及首版前端页面。

R1（PRD v2.0，2026-08-26）正在进行：按业务决策闭环重构页面，新增发布门、告警工作流、
版本（edition）与业务对象层。交付计划见 `ARCHITECTURE.md` §16，需求编号见 `docs/REQUIREMENTS-R1.md`。

---
Not investment advice. 数据为可观察市场快照，不代表法律意义上的发行在外资产或审计后储备。
