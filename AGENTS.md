# RWA Spot & Perps Market Monitor — Agent Notes

## 项目定位

代币化 RWA（股票 / ETF / 基金 / 商品）市场监控系统。追踪现货规模与成交、CEX/DEX 场所格局、
发行商（Ondo / xStocks / bStocks）竞争态势、跨所永续需求，并**自动检测需求异常**
（原先无人交易的产品突然放量）。

权威设计文档：根目录 `ARCHITECTURE.md`。改结构前先读。术语以 `CONTEXT.md` 为准。

## 技术与依赖

- 后端：Python 3.12+、FastAPI、SQLAlchemy 2.0、Alembic
- 前端：React 18 + TypeScript + Webpack、antd 5、ECharts、framer-motion、lucide-react
- Python 包管理：`uv`（根目录 `pyproject.toml` + `uv.lock`）
- Node 包管理：`npm`（根目录与 `frontend/`）
- 调度：APScheduler；报告：openpyxl + python-docx

## 启动与常用命令

- 后端开发：`npm run backend`（`cd backend && uv run uvicorn main:app --reload --port 8025`）
- 前端开发：`npm run frontend`（`cd frontend && npm run dev`）
- 后端测试：`npm run backend:test`
- 格式化：`uv run --group dev black .`
- 类型检查：`uv run --group dev mypy backend`
- 迁移：`npm run backend:migrate` / `npm run backend:revision -- -m "msg"`

## 容器与部署现状

- `compose.yaml`：`backend + frontend + mysql`
- 后端镜像使用 `uv` 安装依赖并通过启动脚本自动执行 Alembic 迁移
- 端口：后端 8025、前端 nginx 8085、MySQL 3307

## 代码位置约定

- 后端薄入口：`backend/main.py`
- 应用装配：`backend/app/main.py`
- 路由注册：`backend/app/api/router.py`
- 业务路由：`backend/app/api/routes/`
- 采集器：`backend/app/services/ingest/`（一个数据源一个模块）
- 检测器：`backend/app/services/anomaly/detectors/`（一个检测器一个文件，文件名带族前缀 `x1_*.py` / `t2_*.py`）

## 数据层分层纪律（必须遵守）

管道严格单向：`ingest → normalize → analytics → anomaly → api/report`

- `ingest/` **只负责取回和原样存储**。不做单位换算、不做去重、不做口径判断。
- 口径转换只在 `normalize/`。聚合只在 `analytics/`。
- `fact_*` 表**只追加不更新**。任何时点的数据都必须可回溯。

## 业务硬约束（必须遵守）

1. **五类口径不可相加**：市值 / 现货成交 / DEX 流动性 / 永续成交 / OI。
   聚合必须走 `safe_sum()`，跨口径求和抛 `MetricScopeViolation`。
2. **`RATIO` 维度指标永不求和**：换手率、买卖比、份额、价差、滑点、funding。
   必须走 `weighted_avg()` 并显式说明权重口径。
3. **CoinGecko 五个类别互相重叠**，只有去重并集行可作总量。用 `is_additive` 标记。
4. **`Not verified` 不等于 `0`**。采集失败写 `NOT_VERIFIED`，绝不写 0。
5. **原始值与质量调整值必须并列展示**，不可只给其一。
6. **基线按 `market_session` 分层**（RTH / PRE / AH / CLOSED_WEEKDAY / CLOSED_WEEKEND / CLOSED_HOLIDAY），
   不按自然日分层，否则每周一必然误报。
7. 统计量用**中位数 + MAD**，不用均值 + 标准差（分布极度右偏）。
8. **告警必须可解释**：每条落 `alert_evidence`（原值、基线、样本量、`market_session`、规则名）。
9. **交易所原始标签原样保留**（`source_underlying_type`），我方分组另存 `analysis_group` 并列，不覆盖。
10. 告警绝对量下限约 $50k，低于此不告警。
11. **`rwa_tier` 是统计口径的闸门**：`CORE_RWA` / `RWA_ADJACENT` / `SYNTHETIC` 在范围内，
    `NON_RWA` 不在。`NON_RWA` 只作 `dim_benchmark` 参照，不进排名、汇总或告警。`dim_benchmark` 永不求和。
12. **两个检测器族不得混用**：横截面族 `X*` 与同组现状比较，不需要基线；
    时序族 `T*` 与自身历史比较，需 ≥14 个同 session 快照。`T*` 不得在冷基线上触发。
13. **生成的 Excel 保持朴素**：无条件格式、无内嵌图表、无合并单元格，保证可复制、可机读。
    视觉表现只在 Web 端。

## 关键约束（基础设施）

- 后端开发与部署**不能依赖任何 PVC**。
- 生成的报告文件、媒体等必须使用云对象存储（当前为 TOS）或直接存入数据库。
- 后端容器内不得保留持久化文件；生产 K8s 环境不提供 PVC。

## 外部数据源与限制

| 源 | 用途 | `auth_mode` | `status` | 限制 |
|:--|:--|:--|:--|:--|
| CoinGecko API | 现货 tickers、类别市值 | `API_KEY` | `ACTIVE` | 免费档约 30 req/min，需令牌桶限流 |
| GeckoTerminal API | DEX 池成交、储备、买卖笔数 | `PUBLIC` | `ACTIVE` | 需分页 |
| **Hyperliquid 官方 API** | **永续主源**：HIP-3 DEX、合约级 OI/funding、`l2Book` | `PUBLIC` | `ACTIVE` | 单端点 `POST /info`，按 `type` 取数 |
| Binance API | bStocks 现货、TradFi 永续 | `PUBLIC` | `ACTIVE` | OI 需 `units × mark` 自算 |
| Alpaca | 美股底层参考价 | `API_KEY` | `ACTIVE` | IEX 非 SIP；**仅在美股开市时段使用** |
| Ondo / xStocks 官网 | 产品主表 | `PUBLIC` | `ACTIVE` | 需抓取；官方产品数 > 聚合器索引数 |
| Loris Tools | 跨所 RWA 永续 | `API_KEY` | `PLANNED` | 公开页仅 Top 25，无合约历史 |
| ASXN Hyperscreener | 流动性指标定义 | `CHALLENGE` | `REFERENCE_ONLY` | Cloudflare Turnstile；**不采集**，仅参照其指标定义 |

新增数据源必须在 `source_registry` 声明 `auth_mode` 与 `status`。`REFERENCE_ONLY` 的源永不进调度。
