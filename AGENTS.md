# RWA Spot & Perps Market Monitor — Agent Notes

## 项目定位

代币化 RWA（股票 / ETF / 基金 / 商品）市场**产品决策雷达**。追踪现货规模与成交、CEX/DEX 场所格局、
发行商（Ondo / xStocks / bStocks）竞争态势、跨所永续需求，并**自动检测需求异常**
（原先无人交易的产品突然放量）。

R1（PRD v2.0，2026-08-26）是**目标态重构**，不是换皮：数据范围、口径、检测器全部复用，
变的是系统必须回答「发生了什么 / 是否可信 / 意味着什么 / 下一步由谁行动」，
并把结论推进到有人签字的闭环。每条发现走 **发现 → 验证 → 解释 → 行动 → 复盘** 五步，
不服务于这五步中任何一步的页面不进导航。

权威设计文档：根目录 `ARCHITECTURE.md`。改结构前先读。术语以 `CONTEXT.md` 为准。
R1 需求编号与验收标准见 `docs/REQUIREMENTS-R1.md`，实现某条需求的提交请带上编号（如 `ALERT-004`）。

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
- 发布门：`backend/app/services/anomaly/publication.py`（检测结果进入 `alert` 的唯一通道）
- 业务对象：`backend/app/services/workflow/`（告警生命周期、研究任务、发行候选、产品覆盖、数据缺口、审计）
- 版本发布：`backend/app/services/editions/`（Live/冻结版生成、修订版、分享版脱敏）

`workflow/` 与 `editions/` **不是管道层**：它们读分析层产物，永不写 `fact_*`。

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
14. **检测 ≠ 发布**：检测器产出候选，能否被人看到由**发布门**决定
    （tier 合法、单一口径、≥ 约 $50k、数据可验证、evidence 完整；`T*` 另需退出冷启动 + 连续 2 次确认）。
    管理层可见面（首页、邮件摘要）另加 `high`/`critical` 过滤。**永不直接渲染检测器原始输出。**
15. **冻结版不可变**：09:00 / 17:00 HKT 版本一次写定。迟到或更正数据生成带原因与差异的**修订版**，
    被取代的版本仍可读。永不 `UPDATE` edition 行，也不许「修数据」悄悄改掉别人已经引用过的数字。
16. **告警是业务对象，不是通知**：单一 owner、状态机
    （`DETECTED → TENTATIVE/PUBLISHED → CLAIMED → IN_REVIEW → ACTIONED → RESOLVED`，
    可从任意阶段进入 `FALSE_POSITIVE` / `DISMISSED`），每次状态变更、备注、转派写只追加审计。
    关闭必须给出复盘结论或标准化「不处理原因」。数据修复不覆写历史告警，只追加证据版本或产生新告警。
17. **任何分数都不批准任何事**：热力图色阶、象限位置、severity 只用于排序与优先级，不作闸门。
    发行候选在评估门之间移动只能靠有记录的人工决策，UI 也不得暗示相反
    （没有「自动批准」，热力图不得出现跨列合计分）。
18. **证据与反证并列**：任何已发布告警和候选都要显示反面证据
    （Raw/Adjusted 差异、单一场所贡献、池薄、无跨所印证、冷基线）。单边论证会让人停止阅读告警。
19. **只有 `primary_theme` 是互斥的**：份额加总到 100% 与堆叠主题图只能用 `primary_theme`；
    secondary theme 多对多，**永不堆叠、永不求和**。主题定义与映射带版本、理由和确认人，
    历史 edition 必须能按当时生效的定义回放。
20. **可展示数值统一走值对象**：`value, unit, metric_scope, metric_dimension, raw_value, adjusted_value,
    verification_status, observed_at, window, source_count, weight_basis`。
    缺 `weight_basis` 的 ratio 聚合由后端**拒绝**，不给默认值；前端图表组件重复断言同样的不变量。
21. **五种验证状态语义不同**：`VERIFIED` / `PARTIAL` / `NOT_VERIFIED` / `STALE` / `EMPTY`。
    `EMPTY` 是观测到的真零，可以按零陈述；`NOT_VERIFIED` 不得渲染成任何数字；
    `STALE` 阻断新的管理层结论。把它们合并就是第 4 条重犯。
22. **分享版在服务端脱敏**：候选、owner、处理动作、业务备注、发行状态、阈值、未公开源细节
    在 share viewer 的响应里**直接不返回**。前端隐藏不算脱敏。无权限提示不得泄露记录是否存在。
23. **视图状态写进 URL**：scope、edition、as_of、时间窗、筛选、排序、Raw/Adjusted 全部是 URL 状态，
    导出继承同一套外加时区与数据截止时间。别人贴进 PPT 的那张截图，六周后必须还能复现。

## 版本与业务闭环（R1 新增）

- **两个冻结版**：每日 09:00 / 17:00 HKT；Live 版每小时刷新，页面常驻 snapshot 时间、数据年龄与下次刷新。
- **告警工作队列**：认领、转派、备注、确认、判误、搁置、建研究任务、进发行评估、关闭，每个动作有权限校验与审计。
- **发行评估五道门**：发现 → 数据确认 → 小规模验证 → 可行性评估 → 产品委员会。
  每道门有进入条件、降级条件、owner 和产出物；系统只负责发现、排序与保全证据。
- **新产品 1h/6h/24h 生命周期**：每个观察窗口存不可变快照与阶段结论，迟到数据出修订版，不回写已关闭的窗口。
- **通知**：R1 为应用内队列 + 每日邮件摘要（默认 17:15 HKT，冻结版生成后）。
  邮件只含合格告警与待办，`Not verified` 或证据不全的候选不发送。通知只是入口，处理仍回系统并写审计。

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
