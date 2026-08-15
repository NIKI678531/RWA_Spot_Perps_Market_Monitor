# RWA Spot & Perps Market Monitor — 架构设计

> 版本 v1.0 · 2026-08-14
> 数据基线：`RWA_Spot_Perps_Market_Monitor_2026-08-09.xlsx`(19 sheets) + `RWA_Spot_Perps_Market_Analysis_2026-08-09.docx`

---

## 0. 一句话定位

把现在**一次性、人工、快照式**的 RWA 市场盘点，变成**每小时自动采集、带历史序列、能自己报警**的监控系统 —— 回答四个问题：

| # | 老板会问的问题 | 系统给出的答案 |
|:--|:--|:--|
| Q1 | RWA 现在多大？在涨还是在跌？ | Executive KPI + 时间序列（规模/成交/OI 四条独立口径） |
| Q2 | 钱在哪个交易所、哪个竞品手里？ | Venue Ranking + Issuer Ranking + 份额变化 |
| Q3 | 什么东西突然火了？ | **异常雷达（Anomaly Radar）** ← 本系统的核心差异化 |
| Q4 | 客户到底想要什么？ | Underlying 360：按底层证券聚合的真实需求视图 |

---

## 1. 从现状到目标：三个必须解决的结构性缺陷

现有 Excel 已经做得很扎实（口径纪律、质量调整、来源日志都有），但作为"monitor"有三个硬伤：

### 缺陷 1：只有快照，没有历史 → 无法做异常检测

> 你要的「原先没有人买的产品突然多了很多人」，**数学上必须有基线才能定义"突然"**。

单张快照能告诉你 SPCXB 今天成交 $3,620 万，但无法告诉你这是它的日常水平还是暴涨 400%。

**→ 架构决策 A：所有事实表以 `snapshot_ts` 为主键维度做时间序列存储，永不覆盖写。**

### 缺陷 2：没有 `underlying_id` 主键 → 无法回答"客户想要什么"

现在 SPY 这一个底层，散落在至少 6 个地方：

```
SPY (底层证券)
├── SPYB    (bStocks)  → Binance 现货 / PancakeSwap V3 / Uniswap V4 (BSC)
├── SPYx    (xStocks)  → LBank / Raydium (Solana)
├── SPY-ON  (Ondo)     → LBank / MEXC / KCEX
└── SPYUSDT (Binance TradFi 永续)
```

老板问"客户是不是在买标普500"，现在需要人工把 6 行加起来 —— 而且加错了（永续不能和现货相加）。
文档自己在 P1 里点名了这件事：*「建立底层证券映射：同一股票/ETF 的 Ondo、xStocks、bStocks 以及多链包装归并到统一 underlying_id」*。

**→ 架构决策 B：`dim_underlying` 是整个系统的中心维度表，不是可选项。**

### 缺陷 3：口径混淆风险靠"人记得"，没有系统约束

文档反复强调「市值、AUM/TVL、现货成交、永续成交、OI 是五类不同指标，任何汇总页都只能并列，不得相加」。但这是**写在批注里的纪律**，代码层面没有任何东西阻止有人 `SUM()` 一下。

**→ 架构决策 C：口径（metric scope）进入类型系统。** 每个指标带 `scope` 标签，聚合层对跨 scope 相加**直接抛异常**，不是警告。

---

## 2. 系统总览

```
┌────────────────────────────────────────────────────────────────────────┐
│  L1  SOURCES                                                           │
│  CoinGecko  GeckoTerminal  Loris  Binance(Spot/TradFi)  Alpaca  官网    │
└───────────────────────────────┬────────────────────────────────────────┘
                                │ httpx + tenacity 重试 + 令牌桶限流
┌───────────────────────────────▼────────────────────────────────────────┐
│  L2  INGEST   services/ingest/                                         │
│  每个源一个 collector，只做「取回 + 存原样」，不做任何口径转换            │
│  产出 → raw_payload (JSON, 保留原始响应) + fetch_log (状态/耗时/错误)    │
│  ★ 原则：Not verified ≠ 0。取不到就记 NOT_VERIFIED，绝不写 0            │
└───────────────────────────────┬────────────────────────────────────────┘
┌───────────────────────────────▼────────────────────────────────────────┐
│  L3  NORMALIZE   services/normalize/                                   │
│  ① dedup        CoinGecko coin_id 去重（五类目录重叠）                  │
│  ② underlying   ★ 归并到 dim_underlying（SPYB/SPYx/SPY-ON → "SPY"）     │
│  ③ quality      anomaly/stale 筛除 → adjusted_volume（保留 raw 并列）   │
│  ④ venue        场所名归一（"PancakeSwap V3 (BSC)" 有 3 种写法）        │
└───────────────────────────────┬────────────────────────────────────────┘
┌───────────────────────────────▼────────────────────────────────────────┐
│  L4  STORE   MySQL 8.4（compose）/ SQLite（本地兜底）                   │
│  星型模型：6 张维度表 + 7 张事实表（全部按 snapshot_ts 追加）            │
└───────────────────────────────┬────────────────────────────────────────┘
┌───────────────────────────────▼────────────────────────────────────────┐
│  L5  ANALYTICS + ANOMALY   services/analytics/ · services/anomaly/      │
│  rollups(场所/发行商/类别) · concentration(HHI/Top-N) ·                 │
│  baseline(滚动统计, ★工作日/周末分层) → 12 个 detector → 告警评分       │
└───────────────────────────────┬────────────────────────────────────────┘
                ┌───────────────┴───────────────┐
┌───────────────▼──────────────┐  ┌─────────────▼──────────────────────┐
│  L6a  API  FastAPI           │  │  L6b  REPORT  services/report/     │
│  /api/kpi /alerts /venues …  │  │  openpyxl → 19-sheet xlsx          │
└───────────────┬──────────────┘  │  python-docx → 分析报告            │
┌───────────────▼──────────────┐  │  每日 08:00 HKT 推送               │
│  L7  WEB  React + TS + antd  │  └────────────────────────────────────┘
│  8 个页面，DESIGN.md 视觉规范 │
└──────────────────────────────┘
```

---

## 3. 数据模型（星型 schema）

### 3.1 维度表

| 表 | 主键 | 说明 |
|:--|:--|:--|
| `dim_underlying` | `underlying_id` | **中心维度**。底层证券：SPY / TSLA / SPACEX / XAU / CL。字段：`name`、`asset_class`(Equity/ETF/Commodity/FX/Index/PreIPO)、`region`(US/KR/HK)、`isin`、`is_pre_ipo` |
| `dim_issuer` | `issuer_id` | Ondo / xStocks / bStocks / Other。字段：`official_product_count`、`official_url`、`legal_structure_note` |
| `dim_asset` | `asset_id` | 代币。`coin_id`(CoinGecko)、`symbol`、`chain`、`contract_address` → FK `underlying_id` + `issuer_id` |
| `dim_venue` | `venue_id` | 场所。`venue_type`(CEX/DEX)、`chain`、`aliases[]`（解决同名多写法） |
| `dim_perp_contract` | `contract_id` | 永续合约。`exchange`、`symbol` → FK `underlying_id`。`binance_underlying_type` 原样保留 + `analysis_group` 我方分组**并存** |
| `dim_pool` | `pool_id` | DEX 池。`network`、`dex`、`pool_address`、`quote_token`、`is_canonical_quote` |

> **关于 `dim_underlying` 的建立方式**：种子数据从现有 Excel 的 `Underlying names` 列抽取（`05_Spot_Pairs` 已有该字段），再用「符号后缀剥离」规则自动匹配（`SPYB`→`SPY`、`SPYx`→`SPY`、`CRCLON`→`CRCL`），**未命中的进人工审核队列**，不猜。映射表版本化存 `underlying_map`，可回溯。

### 3.2 事实表（全部带 `snapshot_ts`，只追加）

| 表 | 粒度 | 关键字段 | 对应现 Excel |
|:--|:--|:--|:--|
| `fact_asset_snapshot` | asset × ts | price、market_cap、fdv、vol_24h、circ_supply、chg_24h/7d/30d | `01_Asset_Master` |
| `fact_pair_snapshot` | asset × venue × ts | raw_vol、**adjusted_vol**、spread_pct、trust_score、`is_anomaly`、`is_stale` | `05_Spot_Pairs` |
| `fact_venue_snapshot` | venue × ts | raw_vol、adjusted_vol、share、pair_count、underlying_count | `06_Spot_Venues` |
| `fact_pool_snapshot` | pool × ts | reserve_usd、vol_24h、**buys_24h / sells_24h**、tx_count、vol/liq | `06B_DEX_Pools` |
| `fact_perp_venue_snapshot` | exchange × segment × ts | vol_24h、open_interest、symbol_count | `09_Perps_Venues` |
| `fact_perp_contract_snapshot` | contract × ts | vol_24h、oi_units、**oi_usd**、funding_rate、mark、index | `12_TradFi_Contracts` |
| `fact_category_snapshot` | category × ts | asset_count、market_cap、vol_24h | `02_CG_Categories` |

辅助表：`fetch_log`（每次采集的状态/耗时/HTTP码/错误）、`metric_scope`（口径注册表）、`alert`、`alert_evidence`、`underlying_map`。

### 3.3 口径隔离（架构决策 C 的落地）

```python
class MetricScope(StrEnum):
    SPOT_MARKET_CAP   = "spot_market_cap"     # 存量：市值
    SPOT_VOLUME       = "spot_volume"         # 流量：现货成交
    DEX_LIQUIDITY     = "dex_liquidity"       # 存量：池储备/TVL
    PERP_VOLUME       = "perp_volume"         # 流量：永续成交
    PERP_OI           = "perp_oi"             # 存量：未平仓

# 聚合层唯一入口，跨 scope 相加直接炸
def safe_sum(values: Sequence[ScopedValue]) -> ScopedValue:
    scopes = {v.scope for v in values}
    if len(scopes) > 1:
        raise MetricScopeViolation(f"不可相加的口径: {scopes}")
    ...
```

同理，CoinGecko 五个类别之间重叠，只有 `union_deduplicated` 行可用作总量 —— 用 `is_additive: bool` 字段在 `fact_category_snapshot` 上标记，前端渲染时对 `is_additive=False` 的行禁用"合计"。

---

## 4. 异常检测引擎（核心模块）

### 4.1 基线：为什么必须做工作日/周末分层

文档明确警告：*「快照采集于周日。传统金融底层闭市时…建议至少用 5 个工作日 + 2 个周末快照建立基准」*。

RWA 代币 24/7 交易，但底层股票只在美股时段开市。周末成交天然萎缩 —— 如果基线不分层，**每个周一早上都会误报一堆"暴涨"**，系统三天就没人看了。

```python
baseline_key = (entity_id, metric, day_type)   # day_type ∈ {WEEKDAY, WEEKEND, US_HOLIDAY}
```

统计量用 **中位数 + MAD（绝对中位差）** 而非均值+标准差 —— RWA 成交分布极度右偏（Top-10 占 78.2%），均值会被单日尖峰污染，MAD 稳健得多。

```python
robust_z = 0.6745 * (x - median) / mad
```

冷启动期（历史 < 14 个快照）只记录不告警，避免上线首周刷屏。

### 4.2 十二个检测器

**A 组 — 需求突现（直接对应你说的"突然爆量"）**

| # | 检测器 | 触发逻辑 | 业务含义 |
|:--|:--|:--|:--|
| A1 | `VolumeSpike` | `robust_z > 3.5` 且 `adjusted_vol > $50k` | 老产品放量 |
| A2 | **`ColdStartAwakening`** | 过去 14 天 `vol < $1k`，当前 `> $100k` | **「原先没人买，突然一堆人买」← 你的核心诉求** |
| A3 | `NewPairListing` | 出现历史上没有的 (asset, venue) 组合 | 竞品新上架 / 新渠道 |
| A4 | `NewUnderlyingCoverage` | 出现全新 `underlying_id` | 市场在开新赛道（如首个 Pre-IPO） |
| A5 | `BreadthExpansion` | 某发行商 7 天内活跃产品数 +20% | 竞品在铺货 |

**B 组 — 需求方向（DEX 独有，含金量最高）**

| # | 检测器 | 触发逻辑 | 业务含义 |
|:--|:--|:--|:--|
| B1 | **`BuySellImbalance`** | `buys/(buys+sells) > 0.65` 且交易数 > 500 | **净买入 = 真实增量需求**，不是刷量对倒 |
| B2 | `LiquidityDrain` | `reserve_usd` 24h 跌幅 > 40% | 做市商撤退，风险信号 |
| B3 | `VolLiqRatioExtreme` | `vol/liquidity > 20` | 高换手薄池，价格易被打穿 |

> B1 是这套数据里最被低估的金矿。`06B_DEX_Pools` 已经有 `Buys 24h` / `Sells 24h` 两列 —— 成交额只说明"有人在交易"，买卖比才说明"客户在买还是在卖"。

**C 组 — 竞争格局**

| # | 检测器 | 触发逻辑 | 业务含义 |
|:--|:--|:--|:--|
| C1 | `VenueShareShift` | 场所份额变动 > 5pp | 流动性搬家（如 LBank 46.3% 被谁分走） |
| C2 | `IssuerMomentum` | 发行商成交份额 7 日变动 > 8pp | 竞品此消彼长 |
| C3 | `ConcentrationShift` | HHI 或 Top-10 份额变动 > 10% | 市场从集中→分散（或反之） |

**D 组 — 衍生品/定价异常**

| # | 检测器 | 触发逻辑 | 业务含义 |
|:--|:--|:--|:--|
| D1 | `OISurgeNoVolume` | OI `z>3` 但成交 `z<1` | 有人在悄悄建仓（不是日内投机） |
| D2 | `FundingExtreme` | \|funding\| > 历史 p95 | 单边拥挤，情绪极端 |
| D3 | `BasisDislocation` | token价 vs Alpaca 底层价偏离 > 2%（仅美股开市时段） | 定价失效或套利窗口 |

> D3 必须**只在美股开市时段跑**。文档已警告 IEX 非 SIP、周末时点错位 —— 周末跑这个检测器 100% 全是假警报。

### 4.3 告警评分与降噪

```
severity = w1·norm(robust_z) + w2·norm(log10 绝对USD量) + w3·persistence
```

- **绝对量门槛**：低于 $50k 的一律不告警。$500 涨到 $5,000 是 +900%，但没有任何商业意义。
- **持续性确认**：单快照触发标 `TENTATIVE`，连续 2 个快照才升 `CONFIRMED`。
- **去重**：同一 `(entity, detector)` 在 24h 冷却窗内合并为一条，`occurrence_count` 累加。
- **可解释性**：每条告警落 `alert_evidence`，存触发时的原始数值 + 基线 + 用了哪条规则。**老板问"为什么报这个"，点开就能看，不是黑盒。**

告警数据结构：
```json
{
  "alert_id": "...",
  "detector": "ColdStartAwakening",
  "severity": "HIGH",
  "status": "CONFIRMED",
  "entity": {"type": "asset", "id": "spacex-bstocks-tokenized-stock", "symbol": "SPCXB"},
  "underlying_id": "SPACEX",
  "issuer_id": "bStocks",
  "headline_zh": "SPCXB 24h 成交从近 14 日均值 $0.8k 跃升至 $362k",
  "evidence": {
    "current_value": 36207630, "baseline_median": 812, "robust_z": 41.2,
    "day_type": "WEEKEND", "sample_size": 14, "metric_scope": "spot_volume"
  },
  "first_seen": "...", "occurrence_count": 3
}
```

---

## 5. API 设计

统一挂在 `settings.normalized_api_base_path` 下（沿用 DA-Report 约定）。

```
# 概览
GET  /api/kpi/executive             四大 KPI + 环比（严格分列，不合计）
GET  /api/data-quality              各模块 Available/Partial/NotVerified 状态

# 现货
GET  /api/scale/categories          五类 + 去重并集（带 is_additive 标记）
GET  /api/spot/venues               场所排名（raw / adjusted 并列）
GET  /api/spot/pairs                交易对下钻（筛选：venue/issuer/underlying）
GET  /api/dex/pools                 DEX 池（含 buys/sells 比）

# 竞品
GET  /api/issuers                   Ondo/xStocks/bStocks 对比
GET  /api/issuers/{id}/venues       发行商 × 场所矩阵

# 永续
GET  /api/perps/venues              Loris 跨所（all-RWA / stocks 两段）
GET  /api/perps/contracts           Binance TradFi 153 合约

# ★ 核心视图
GET  /api/alerts                    异常告警流（filter: severity/detector/since）
GET  /api/alerts/{id}               单条告警 + 完整证据链
GET  /api/underlying/{id}           ★ 底层证券 360 全景
GET  /api/timeseries                 通用时序（entity_type/entity_id/metric/range）

# 报告
GET  /api/reports                   历史报告列表
GET  /api/reports/{date}/excel      下载 19-sheet 工作簿
GET  /api/reports/{date}/word       下载分析报告
POST /api/reports/generate          手动触发
```

**`/api/underlying/{id}` 的返回结构**（这是回答"客户想要什么"的关键端点）：

```json
{
  "underlying_id": "SPY", "name": "SPDR S&P 500 ETF", "asset_class": "ETF",
  "tokenized_wrappers": [
    {"issuer": "bStocks", "symbol": "SPYB", "market_cap": ..., "adjusted_vol_24h": ...},
    {"issuer": "xStocks", "symbol": "SPYx", ...},
    {"issuer": "Ondo",    "symbol": "SPY-ON", ...}
  ],
  "venue_breakdown": [{"venue": "Binance", "type": "CEX", "adjusted_vol": ...}, ...],
  "perp_exposure": [{"exchange": "Binance", "contract": "SPYUSDT", "vol_24h": ..., "oi_usd": ...}],
  "reference_price": {"source": "Alpaca IEX", "close": 773.16, "as_of": "..."},
  "scope_note": "现货成交与永续成交为不同口径，页面并列展示，不提供合计",
  "active_alerts": [...]
}
```

---

## 6. 前端页面

沿用 `DESIGN.md`（CSOP Gemini/Material You 玻璃拟态）—— React 18 + TS + Webpack + antd 5 + framer-motion + lucide-react。

| # | 页面 | 一屏一焦点的"焦点"是什么 |
|:--|:--|:--|
| 1 | **Executive** | Hero: 四大 KPI 卡 + 右侧异常告警流 |
| 2 | **Anomaly Radar** ★ | 告警时间轴，按 severity 分组，点开看证据链 |
| 3 | Spot Scale | 类别市值 + 去重并集（重叠用 Venn 或堆叠说明，**禁止**画成可相加的饼图） |
| 4 | Venues | CEX vs DEX 排名，raw/adjusted 双列对照 |
| 5 | Issuers | Ondo/xStocks/bStocks 三方对比 + 场所矩阵热力图 |
| 6 | Perps | Loris 跨所 + Binance TradFi 集中度 |
| 7 | **Underlying 360** ★ | 单一底层的全景（现货包装 / 场所 / 永续 / 参考价并列） |
| 8 | Data Quality | 覆盖状态、来源日志、Not verified 清单 |

**图表纪律**（写进 CLAUDE.md 强制约束）：
- 不同 `metric_scope` **禁止**画在同一根 Y 轴上；必须双轴或拆图。
- 有重叠的类别**禁止**用饼图/堆叠柱（视觉上暗示可相加）。
- 数字一律 `typography.numeric`（`tnum`），保证列对齐。
- `Not verified` 渲染为灰色斜杠占位符，**绝不渲染成 0**。

---

## 7. 调度与限流

```
每 15 分钟   headline 快照（Binance TradFi ticker、Loris 首页）—— 便宜且高频有价值
每  1 小时   现货全量（CoinGecko tickers 282 资产）+ GeckoTerminal 池
每  6 小时   类别口径、发行商官网产品数
每日 06:30   Alpaca 底层参考价（美股收盘后）
每日 08:00   ★ 生成 Excel + Word 报告并推送（HKT）
每日 03:00   基线重算、冷数据归档
```

**限流是真实约束**：CoinGecko 免费档约 30 req/min，282 个资产逐个取 tickers ≈ 282 次调用 ≈ 10 分钟。设计上用令牌桶 + `tenacity` 指数退避 + `ETag/If-None-Match` 条件请求；核心资产（Top 50 成交）用 1 小时频率，长尾用 6 小时。付费档（Pro/Analyst）可直接把全量提到 15 分钟。

采集失败 → `fetch_log` 记 `NOT_VERIFIED` + 沿用上一快照并打 `is_carried_forward=True` 标记，**不写 0，不静默**。

---

## 8. 技术栈与目录

| 层 | 选型 |
|:--|:--|
| 后端 | Python 3.12+ · FastAPI · SQLAlchemy 2.0 · Alembic · `uv` 管理 |
| 采集 | httpx · tenacity · curl-cffi（Loris 页面）· BeautifulSoup/lxml |
| 计算 | pandas · numpy（MAD/滚动统计） |
| 调度 | APScheduler |
| 报告 | openpyxl（xlsx）· python-docx（docx） |
| 数据库 | MySQL 8.4（compose）/ SQLite（本地兜底） |
| 前端 | React 18 · TypeScript · Webpack · antd 5 · framer-motion · lucide-react |
| 部署 | Docker Compose · K8s（**无 PVC**，报告存对象存储或 DB） |

```
backend/app/
├── main.py                  create_app() 装配
├── api/routes/              health kpi spot dex issuers perps alerts underlying reports quality
├── core/config.py           pydantic-settings
├── db/                      session.py  base.py
├── models/                  SQLAlchemy ORM（dim_* / fact_* / alert）
├── schemas/                 Pydantic 出入参
└── services/
    ├── ingest/              coingecko geckoterminal loris binance_spot binance_tradfi alpaca issuer_official
    ├── normalize/           dedup underlying_map quality venue_registry
    ├── analytics/           rollups concentration baseline
    ├── anomaly/             engine.py  scoring.py  detectors/（12 个）
    ├── report/              excel.py  word.py
    └── scheduler.py
```

---

## 9. 分期交付

| 阶段 | 内容 | 产出的价值 |
|:--|:--|:--|
| **P0**（1-2 周） | 数据模型 + CoinGecko/GeckoTerminal/Binance 采集 + 时序落库 + Executive/Venues/Issuers 三页 | 现在的 Excel 变成自动日更看板 |
| **P1**（2-3 周） | `dim_underlying` 映射 + Underlying 360 页 + Loris 永续 + 每日 Excel/Word 自动生成 | 能回答"客户想要什么"；老板不用等人做表 |
| **P2**（2-3 周） | **异常引擎 12 个 detector** + Anomaly Radar 页 + 告警推送 | ★ 真正的 "monitor"，你的核心诉求落地 |
| **P3** | 链上持有人/转账/净发行赎回（文档 P2 缺口）、订单簿深度、Loris API | 从"看得见"到"看得深" |

---

## 10. 已知边界（诚实声明，避免过度承诺）

| 事项 | 状态 | 说明 |
|:--|:--|:--|
| Loris 完整合约历史 | ❌ 无 | 公开页仅 Top 25，需 API Key |
| 链上持有人/转账/净申赎 | ❌ 无 | 需链上索引或付费数据（文档已列为 P2） |
| 订单簿深度历史 | ❌ 无 | 各所 API 无历史深度，需自采自存 |
| 法律权利/托管/储备审计 | ⚠️ 部分 | 仅官方描述；独立尽调超出本系统范围 |
| xStocks 覆盖 | ⚠️ 偏低 | 官方 640 产品 vs CoinGecko 索引 113 —— 以官方主表做分母 |
| Alpaca IEX | ⚠️ 非 SIP | 仅方向性校验，不作套利结论 |
| bStocks 性质 | ⚠️ 凭证 | 是凭证化工具，非直接持股，UI 需标注 |

---

## 11. 三条不可让步的原则

1. **口径不可相加** —— 代码强制，跨 `MetricScope` 求和抛异常，不是靠人记得。
2. **Not verified ≠ 0** —— 取不到就是取不到，UI 显示灰色占位，绝不用 0 填充。
3. **告警必须可解释** —— 每条告警都能点开看到原始值、基线、样本量、规则名。不可解释的告警等于噪音。
