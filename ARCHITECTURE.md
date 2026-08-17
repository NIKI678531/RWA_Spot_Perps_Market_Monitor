# RWA Spot & Perps Market Monitor — 架构设计

> 版本 v2.0 · 2026-08-17
> 术语以根目录 `CONTEXT.md` 为准。视觉规范以 `DESIGN.md` 为准（本文不修改该文件）。
> 检测器细则见 `docs/DETECTORS.md`；图表规范见 `docs/DATAVIZ.md`；版式见 `docs/UI-LAYOUT.md`。
> 设计取舍的记录见 `docs/adr/`。业务背景见本文附录 A。

---

## 1. 范围

系统持续采集代币化 RWA（股票 / ETF / 基金 / 商品）在 CEX、DEX 现货与跨所永续市场的公开数据，
以时间序列落库，产出规模与成交排名、场所与发行商竞争格局、需求异常告警，以及每日 xlsx / docx 报告。

**范围内**：`rwa_tier ∈ {CORE_RWA, RWA_ADJACENT, SYNTHETIC}` 的标的。

**范围外**：加密原生资产（BTC / ETH / SOL 等，`rwa_tier = NON_RWA`）。
这类资产仅作为 `dim_benchmark` 的参照项存在，不进入任何统计口径、排名或告警。

**非目标**：交易执行、投资建议、法律权利与储备的独立尽调、链上持有人级追踪。

---

## 2. 数据源注册表

每个源在 `source_registry` 表中注册，采集器按 `auth_mode` 选择接入方式，按 `status` 决定是否调度。

| 源 | 覆盖 | `auth_mode` | `status` | 限制 |
|:--|:--|:--|:--|:--|
| CoinGecko | 现货资产主表、类别市值、tickers | `API_KEY`（可空） | `ACTIVE` | 免费档约 30 req/min；五个类别互相重叠 |
| GeckoTerminal | DEX 池储备、成交、买卖笔数 | `PUBLIC` | `ACTIVE` | 需分页；池覆盖不完整 |
| **Hyperliquid 官方 API** | **永续主源**：HIP-3 perp DEX 列表、合约级 mark/OI/funding、L2 订单簿、成交流 | `PUBLIC` | `ACTIVE` | 无速率文档，实测宽松；`POST /info` 单端点多 `type` |
| Binance | bStocks 现货、TradFi 永续 ticker | `PUBLIC` | `ACTIVE` | OI 需 `units × mark` 自算；原始 `EQUITY` 标签须原样保留 |
| Alpaca | 美股底层参考价 | `API_KEY` | `ACTIVE` | IEX 非 SIP；仅美股开市时段使用 |
| Ondo / xStocks / bStocks 官网 | 发行商产品主表 | `PUBLIC` | `ACTIVE` | 需抓取；官方产品数 > 聚合器索引数 |
| Loris Tools | 跨所永续聚合 | `API_KEY` | `PLANNED` | 公开页仅 Top 25，无合约历史 |
| ASXN Hyperscreener | 流动性对比方法论 | `CHALLENGE` | `REFERENCE_ONLY` | 见下 |

### 2.1 `auth_mode = CHALLENGE`

指接口受人机验证（如 Cloudflare Turnstile）保护，需要浏览器侧交互令牌才能访问。

`hyperscreener.asxn.xyz` 的全部接口返回 `403 VERIFICATION_REQUIRED`，要求 `X-Verification-Token` 请求头。
已实测 `curl-cffi` 的 `chrome124` / `chrome120` / `safari17_0` 三种 TLS 指纹伪装，全部被拒。

该源被标记为 `REFERENCE_ONLY`：**不做自动采集**，仅采用其滑点档位与深度带的指标定义，
数据由 Hyperliquid `l2Book` 自行计算。理由与替代方案见 `docs/adr/0004-challenge-source-degradation.md`。

`REFERENCE_ONLY` 的源保留在注册表中且不被调度，用于阻止后续开发重复投入探测成本。

---

## 3. 管道分层

严格单向，跨层调用视为架构违规：

```
L1 SOURCES
     │  httpx + tenacity 指数退避 + 令牌桶限流 + ETag 条件请求
L2 INGEST      services/ingest/        一源一 collector
     │  只做「取回 + 原样存储」。不换算单位、不去重、不判口径。
     │  产出 raw_payload(JSON) + fetch_log(状态/耗时/HTTP码/错误)
     │  取不到 → 记 NOT_VERIFIED，绝不写 0
L3 NORMALIZE   services/normalize/
     │  ① dedup           CoinGecko coin_id 跨类别去重
     │  ② underlying      归并到 dim_underlying（SPYB / SPYx / SPY-ON → SPY）
     │  ③ tiering         判定 rwa_tier，NON_RWA 在此被隔离出统计口径
     │  ④ quality         anomaly / stale 筛除 → adjusted_volume（raw 并存）
     │  ⑤ venue           场所名归一（同一 DEX 有多种写法）
L4 STORE       MySQL 8.4 / SQLite
     │  星型模型；fact_* 仅追加，永不 UPDATE
L5 ANALYTICS   services/analytics/     rollups · concentration(HHI/TopN) · baseline
   ANOMALY     services/anomaly/       engine + 17 detectors + scoring
     │
     ├── L6a API      FastAPI，挂在 settings.normalized_api_base_path 下
     └── L6b REPORT   openpyxl → 22-sheet xlsx；python-docx → 分析报告
                      产物写对象存储或数据库，不落容器文件系统
L7 WEB         React 18 + TS + antd 5 + ECharts
```

---

## 4. 数据模型

### 4.1 口径类型系统

五类指标各自独立，互相不可相加。口径不是注释，是类型：

```python
class MetricDimension(StrEnum):
    STOCK = "stock"    # 时点存量
    FLOW  = "flow"     # 窗口流量
    RATIO = "ratio"    # 比率，永不可相加

class MetricScope(StrEnum):
    SPOT_MARKET_CAP = "spot_market_cap"   # STOCK
    SPOT_VOLUME     = "spot_volume"       # FLOW
    DEX_LIQUIDITY   = "dex_liquidity"     # STOCK
    PERP_VOLUME     = "perp_volume"       # FLOW
    PERP_OI         = "perp_oi"           # STOCK
```

聚合唯一入口是 `safe_sum()`，跨 scope 求和抛 `MetricScopeViolation`。
`RATIO` 维度的指标（换手率、买卖比、份额、滑点、funding）**在任何情况下都不得求和**，
必须走 `weighted_avg()` 并显式给出权重口径。图表侧由 `assert_same_axis()` 拒绝把 STOCK 与 FLOW 放上同一 Y 轴。

### 4.2 维度表

| 表 | 主键 | 关键字段 |
|:--|:--|:--|
| `dim_underlying` | `underlying_id` | **中心维度**。`name`、`asset_class`(Equity/ETF/Commodity/FX/Index/PreIPO)、`region`、`isin`、`is_pre_ipo`、`theme_id` |
| `dim_issuer` | `issuer_id` | `official_product_count`、`official_url`、`legal_structure_note` |
| `dim_asset` | `asset_id` | `coin_id`、`symbol`、`chain`、`contract_address`、**`rwa_tier`** → FK `underlying_id` + `issuer_id` |
| `dim_venue` | `venue_id` | `venue_type`(CEX/DEX/PERP_DEX)、`chain`、`aliases[]` |
| `dim_perp_contract` | `contract_id` | `exchange`、`perp_dex`(HIP-3)、`symbol`、`source_underlying_type`（原样）、`analysis_group`（我方）→ FK `underlying_id` |
| `dim_pool` | `pool_id` | `network`、`dex`、`pool_address`、`quote_token`、`is_canonical_quote` |
| `dim_theme` | `theme_id` | 需求主题：Pre-IPO / 半导体 / 贵金属 / 能源 / 杠杆 ETP / 宽基指数 |
| `dim_benchmark` | `benchmark_id` | **软聚合层**：把不同 tier 的 underlying 归到同一经济暴露（SPY ETF 与 S&P 500 指数、GOLD 与 XAU）。仅用于对照展示，**不参与任何求和** |

`rwa_tier` 四层判定：

| 值 | 含义 | 入统计口径 |
|:--|:--|:--|
| `CORE_RWA` | 有底层资产托管或凭证支持的代币化证券 / 商品 | ✅ |
| `RWA_ADJACENT` | 与 RWA 相关但非直接代币化（发行商代币、RWA 协议代币） | ✅（单独列示） |
| `SYNTHETIC` | 无底层托管的合成敞口（永续、合成资产） | ✅（仅永续口径） |
| `NON_RWA` | 加密原生 | ❌ 仅作 `dim_benchmark` 参照 |

`dim_underlying` 的建立：种子从现有工作簿 `Underlying names` 列抽取，再按符号后缀剥离规则自动匹配
（`SPYB`→`SPY`、`SPYx`→`SPY`、`CRCLON`→`CRCL`）。**未命中的进人工审核队列，不猜**。
映射版本化存 `underlying_map`，可回溯。

### 4.3 事实表

全部带 `snapshot_ts`，**只追加，永不 UPDATE**。

| 表 | 粒度 | 关键字段 |
|:--|:--|:--|
| `fact_asset_snapshot` | asset × ts | price、market_cap、fdv、vol_24h、circ_supply、chg_24h/7d/30d |
| `fact_pair_snapshot` | asset × venue × ts | raw_vol、adjusted_vol、spread_pct、trust_score、is_anomaly、is_stale |
| `fact_venue_snapshot` | venue × ts | raw_vol、adjusted_vol、share、pair_count、underlying_count |
| `fact_pool_snapshot` | pool × ts | reserve_usd、vol_24h、buys_24h、sells_24h、tx_count |
| `fact_perp_venue_snapshot` | exchange × segment × ts | vol_24h、open_interest、symbol_count |
| `fact_perp_contract_snapshot` | contract × ts | vol_24h、oi_units、oi_usd、funding_rate、mark、index |
| `fact_category_snapshot` | category × ts | asset_count、market_cap、vol_24h、**is_additive** |

### 4.4 辅助表

`source_registry`（源与 `auth_mode` / `status`）、`fetch_log`（每次采集状态）、`metric_scope`（口径注册表）、
`underlying_map`（版本化映射）、`baseline`（分层基线快照）、`alert`、`alert_evidence`。

---

## 5. 采集与降级

- 失败或限流 → `fetch_log` 记 `NOT_VERIFIED`，沿用上一快照并标 `is_carried_forward = True`。**不写 0，不静默。**
- `NOT_VERIFIED` 沿调用链传播：`safe_sum()` 遇到未验证输入会跳过它并把结果标为未验证，前端渲染灰色占位。
- CoinGecko 分层刷新：成交 Top 50 资产每小时，长尾每 6 小时。付费档可整体提到 15 分钟。
- Hyperliquid 通过单一 `POST /info` 端点按 `type` 取数：`perpDexs` 列举 HIP-3 DEX，
  `metaAndAssetCtxs` 取合约级上下文，`l2Book` 取深度。合约级历史由本系统自采自存。

---

## 6. 归一化

- **去重**：CoinGecko 的 Tokenized Stock / Tokenized ETF / Ondo / xStocks / bStocks 五个类别按构造重叠。
  只有去重并集行是有效总量；其余行 `is_additive = False`，API 与图表都必须尊重该标记。
- **质量调整**：带 `anomaly` / `stale` 标记的交易对排除出 adjusted，保留在 raw。
  两者始终并列输出，不得只给其一。
- **价格一致性**：同一 `underlying_id` 下各代币化包装的价格若偏离超过阈值，
  该映射进人工审核队列而非直接归并（`GOLD` / `GOLDJM` / `GLDMINE` 是不同标的；`SKHX` 与 `SKHY` 价格相差约 7 倍）。

---

## 7. 分析层

- **rollups**：按 venue / issuer / underlying / theme / category 聚合，每个聚合结果携带 `MetricScope`。
- **concentration**：HHI 与 Top-N 份额，按 venue、issuer、contract 三个轴计算。
- **baseline**：按 `(entity_id, metric, market_session)` 三元组分层的滚动中位数与 MAD。

`market_session` 取代早期设计的 `day_type`，取值：

| 值 | 含义 |
|:--|:--|
| `RTH` | 美股常规交易时段 |
| `PRE` | 盘前 |
| `AH` | 盘后 |
| `CLOSED_WEEKDAY` | 工作日闭市 |
| `CLOSED_WEEKEND` | 周末 |
| `CLOSED_HOLIDAY` | 美股假日 |

RWA 代币 24/7 交易，底层证券不是。仅按工作日 / 周末二分不足以区分盘中与盘后
——两者成交量结构差异与工作日 / 周末的差异同量级。

统计量用**中位数 + MAD**：

```
robust_z = 0.6745 × (x − median) / mad
```

分布极度右偏（Top-10 合约占 Binance TradFi 成交 78.2%），均值会吸收掉本该被检测的尖峰。
历史不足 14 个同 session 快照时只记录不告警。

---

## 8. 异常检测

17 个检测器，一个文件一个，在 `engine.py` 注册。分两族：

- **横截面族（X1–X7）**：与同组的当前状态比较。不需要历史基线，上线当天即可运行。
- **时序族（T1–T10）**：与自身的历史比较。需要 ≥14 个同 `market_session` 快照。

两族抓的是不同现象：长期低量的标的突然放量是时序异常；从未被关注但换手率畸高的新标的是横截面异常。
完整规格与阈值见 `docs/DETECTORS.md`。

### 8.1 评分与降噪

```
severity = w1·norm(robust_z) + w2·norm(log10 绝对USD量) + w3·persistence
```

- **绝对量门槛**：低于约 $50k 名义额一律不告警。$500 → $5,000 是 +900%，无商业意义。
- **持续性**：单快照触发标 `TENTATIVE`，连续 2 个快照升 `CONFIRMED`。
- **去重**：同一 `(entity, detector)` 在 24h 冷却窗内合并，`occurrence_count` 累加。
- **可解释性**：每条告警落 `alert_evidence`，存原始值、基线、样本量、`market_session`、规则名。

```json
{
  "alert_id": "...",
  "detector": "T2_ColdStartAwakening",
  "family": "time_series",
  "severity": "HIGH",
  "status": "CONFIRMED",
  "entity": {"type": "asset", "id": "spacex-bstocks-tokenized-stock", "symbol": "SPCXB"},
  "underlying_id": "SPACEX",
  "issuer_id": "bStocks",
  "theme_id": "PRE_IPO",
  "headline_zh": "SPCXB 24h 成交从近 14 个快照中位数 $812 跃升至 $362,076",
  "evidence": {
    "current_value": 362076, "baseline_median": 812, "robust_z": 41.2,
    "market_session": "CLOSED_WEEKEND", "sample_size": 14,
    "metric_scope": "spot_volume", "rule": "dormancy<=1e3 & current>=1e5 & ratio>=0.9"
  },
  "first_seen": "...", "occurrence_count": 3
}
```

---

## 9. API

统一挂在 `settings.normalized_api_base_path` 下。

```
GET  /api/health
GET  /api/kpi/executive             五口径 KPI + 环比，严格分列
GET  /api/data-quality              各模块 Available / Partial / NotVerified

GET  /api/scale/categories          五类 + 去重并集（带 is_additive）
GET  /api/spot/venues               场所排名（raw / adjusted 并列）
GET  /api/spot/pairs                交易对下钻（venue / issuer / underlying / tier 筛选）
GET  /api/dex/pools                 DEX 池（含 buys / sells）

GET  /api/issuers                   发行商对比
GET  /api/issuers/{id}/venues       发行商 × 场所矩阵

GET  /api/perps/venues              跨所永续（含 HIP-3 perp DEX）
GET  /api/perps/contracts           合约级排名
GET  /api/perps/dexs                HIP-3 permissionless DEX 列表

GET  /api/themes                    主题需求排名
GET  /api/alerts                    告警流（severity / family / detector / since）
GET  /api/alerts/{id}               单条告警 + 完整证据链
GET  /api/underlying/{id}           底层证券 360 全景
GET  /api/timeseries                通用时序（entity_type / entity_id / metric / range）

GET  /api/reports
GET  /api/reports/{date}/excel
GET  /api/reports/{date}/word
POST /api/reports/generate
```

`/api/underlying/{id}` 返回结构：

```json
{
  "underlying_id": "SPY", "name": "SPDR S&P 500 ETF", "asset_class": "ETF",
  "rwa_tier": "CORE_RWA", "theme_id": "BROAD_INDEX",
  "tokenized_wrappers": [
    {"issuer": "bStocks", "symbol": "SPYB", "market_cap": 0, "adjusted_vol_24h": 0}
  ],
  "venue_breakdown": [{"venue": "Binance", "type": "CEX", "adjusted_vol": 0}],
  "perp_exposure": [{"exchange": "Hyperliquid", "perp_dex": "xyz", "contract": "SPY", "vol_24h": 0, "oi_usd": 0}],
  "reference_price": {"source": "Alpaca IEX", "close": 773.16, "as_of": "..."},
  "benchmark_peers": [{"benchmark_id": "SP500", "members": ["SPY", "SPX"]}],
  "scope_note": "现货成交与永续成交为不同口径，页面并列展示，不提供合计",
  "active_alerts": []
}
```

---

## 10. 前端

React 18 + TypeScript + Webpack + antd 5 + framer-motion + lucide-react + **ECharts**。

视觉遵循 `DESIGN.md`（不修改该文件）。图表规范在 `docs/DATAVIZ.md`，其色值全部从 `DESIGN.md` 现有 token 派生；
两文件冲突时以 `DESIGN.md` 的 token 体系为准。版式细则见 `docs/UI-LAYOUT.md`。

10 个页面收敛为 4 个版式模板：

| 模板 | 骨架 | 页面 |
|:--|:--|:--|
| **T1 概览** | 液态背景 + Greeting Hero + KPI 带 + 双栏(2fr/1fr) | Overview |
| **T2 榜单** | 筛选条 + 主图(Hero) + 明细表 | Spot Scale · Venues · Issuers · Perps · Themes |
| **T3 详情** | 实体头 + 指标卡组 + 按口径分栏多图 + 明细表 | Underlying 360 · Perp Contract |
| **T4 流水** | 时间轴 + 右侧证据抽屉 | Anomaly Radar · Data Quality |

T3 的「按口径分栏」是硬约束的版式化：不同 `MetricScope` 物理上分在不同卡片内，
使跨口径相加在版面上就不成立。

导航为左侧 72px 图标 rail，分四组：总览 / 市场 / 永续 / 需求 / 运维。
顶栏常驻数据时间戳。

---

## 11. 报告

- **xlsx（22 sheet，openpyxl）**：在原 19 sheet 基础上新增
  `16_HL_HIP3_Contracts`、`17_Liquidity_Quality`、`18_Theme_Demand`；
  `01_Asset_Master` 等表增加 `rwa_tier` 列。
- **docx（python-docx）**：分析报告，新增「异常告警摘要」一章。
- **Excel 保持朴素**：无条件格式、无内嵌图表、无合并单元格，保证可直接复制与二次加工。
  可视化只在 Web 端。
- 产物写对象存储（TOS）或数据库，**不落容器文件系统**。

---

## 12. 调度

APScheduler，时区 HKT。

```
每 15 分钟   headline 快照（Binance TradFi ticker、Hyperliquid metaAndAssetCtxs）
每  1 小时   现货 Top 50 + GeckoTerminal 池 + Hyperliquid perpDexs
每  6 小时   长尾现货、类别口径、发行商官网产品数
每日 06:30   Alpaca 底层参考价（美股收盘后）
每日 08:00   生成 xlsx + docx 报告并推送
每日 03:00   基线重算、冷数据归档
```

---

## 13. 技术栈与目录

| 层 | 选型 |
|:--|:--|
| 后端 | Python 3.12+ · FastAPI · SQLAlchemy 2.0 · Alembic · `uv` |
| 采集 | httpx · tenacity · curl-cffi · BeautifulSoup / lxml |
| 计算 | pandas · numpy |
| 调度 | APScheduler |
| 报告 | openpyxl · python-docx |
| 数据库 | MySQL 8.4（compose）/ SQLite（本地兜底） |
| 前端 | React 18 · TypeScript · Webpack · antd 5 · ECharts · framer-motion · lucide-react |
| 部署 | Docker Compose · K8s（**无 PVC**） |

```
backend/app/
├── main.py                  create_app() 装配
├── api/routes/              health kpi spot dex issuers perps themes alerts underlying reports quality
├── core/                    config.py  metrics.py  sessions.py
├── db/                      session.py  base.py
├── models/                  dim_* / fact_* / alert / registry
├── schemas/                 Pydantic 出入参
└── services/
    ├── ingest/              coingecko geckoterminal hyperliquid binance alpaca issuer_official
    ├── normalize/           dedup underlying_map tiering quality venue_registry
    ├── analytics/           rollups concentration baseline
    ├── anomaly/             engine.py scoring.py detectors/（17 个）
    ├── report/              excel.py word.py
    └── scheduler.py
```

---

## 14. 分期交付

各期以「可独立产出一份完整交付物」为切分依据，而非技术分层。

| 期 | 内容 | 期末交付物 |
|:--|:--|:--|
| **P0** | 全量建表 · 口径类型系统 · CoinGecko + Hyperliquid 采集 · underlying 映射 + rwa_tier · 22-sheet xlsx 导出 · 前端 T1 + T2 · **末尾启动定时快照** | 自动复刻并扩展现有工作簿 |
| **P1** | 全量调度 · `market_session` 分层基线 · `fetch_log` / NOT_VERIFIED 全链路 · **横截面族 X1–X7** · 数据质量页 | 首次可看趋势；异常检测首批上线 |
| **P2** | **时序族 T1–T10** · `alert` + `alert_evidence` · 告警页与证据抽屉 · docx 报告 | 核心检测能力完整 |
| **P3** | 流动性质量 / 滑点（依赖 `l2Book` 积累）· 主题需求排名 · `dim_benchmark` 对照 · Loris · CEX/DEX 补充源 | 深度分析 |

定时快照必须在 P0 末即启动。基线需要 14 个同 session 快照，晚开一天，P2 就要多等一天。

---

## 15. 已知边界

| 事项 | 状态 | 说明 |
|:--|:--|:--|
| ASXN Hyperscreener | ❌ 不采集 | Cloudflare Turnstile；已降级为方法论参照源 |
| Loris 完整合约历史 | ❌ 无 | 公开页仅 Top 25，需 API Key |
| 链上持有人 / 转账 / 净申赎 | ❌ 无 | 需链上索引或付费数据 |
| 订单簿深度历史 | ⚠️ 自采 | 各所无历史深度接口；Hyperliquid `l2Book` 需自采自存，故滑点分析在 P3 |
| 法律权利 / 托管 / 储备审计 | ⚠️ 部分 | 仅官方描述，独立尽调超出本系统范围 |
| xStocks 覆盖 | ⚠️ 偏低 | 官方 640 产品 vs CoinGecko 索引 113；以官方主表做分母 |
| Alpaca IEX | ⚠️ 非 SIP | 仅方向性校验，不作套利结论 |
| bStocks 性质 | ⚠️ 凭证 | 凭证化工具而非直接持股，UI 须标注 |

---

## 附录 A · 业务背景与设计依据

本附录记录系统为何是现在这个形态。正文描述系统是什么，此处记录为什么。

### A.1 业务动机

系统服务于发行加密 / RWA ETF 前的市场调研：判断市场热度、识别真实客户需求、
选择产品线、了解竞品与场所格局。因此系统的重心不在「行情展示」而在
「**哪些标的正在被买**」——需求异常检测是核心差异化能力，其余模块是它的上下文。

### A.2 数据基线

设计基于 `RWA_Spot_Perps_Market_Monitor_2026-08-09.xlsx`（19 sheets）与
`RWA_Spot_Perps_Market_Analysis_2026-08-09.docx`。该数据集为**单次人工快照**，
采集于 2026-08-09（周日）。

### A.3 三个结构性缺陷及对应决策

**缺陷 1：只有快照，没有历史。** 「原先没人买的产品突然有人买」在数学上必须有基线才能定义「突然」。
单张快照能说明 SPCXB 当日成交 $362k，但不能说明这是常态还是暴涨。
→ 所有事实表以 `snapshot_ts` 做时间序列存储，永不覆盖写。

**缺陷 2：没有 `underlying_id` 主键。** SPY 这一个底层散落在至少 6 处：
`SPYB`（bStocks，Binance / PancakeSwap / Uniswap）、`SPYx`（xStocks，LBank / Raydium）、
`SPY-ON`（Ondo，LBank / MEXC / KCEX）、`SPYUSDT`（Binance TradFi 永续）。
回答「客户是否在买标普 500」需要人工把 6 行相加——且加错了，永续不能与现货相加。
→ `dim_underlying` 是中心维度表，不是可选项。

**缺陷 3：口径纪律只写在批注里。** 原文档反复强调五类指标只能并列不得相加，
但代码层面没有任何东西阻止 `SUM()`。
→ 口径进入类型系统，跨 scope 求和直接抛异常。

### A.4 数据本身给出的商业结论

以下结论来自基线数据集，是「重心放在需求检测」这一判断的依据：

- **永续需求约为现货的 1.7 倍**：永续成交 $4.44bn vs 现货成交 $2.62bn。
  只看现货会低估市场真实热度。
- **需求集中在「散户平时买不到的东西」而非蓝筹**：SpaceX Pre-IPO（SPCX）占 Binance TradFi 成交 28.2%，
  SPCXB 占 Binance bStocks 现货 52.8%；其后是 SK Hynix、黄金、SanDisk、原油、SOXL。
  Apple / Microsoft 一类标的成交并不突出。
- **成交极度集中**：Top-10 合约占 Binance TradFi 成交 78.2%，单一合约占 28.2%。
  这直接决定统计量必须用中位数 + MAD 而非均值 + 标准差。
- **原始成交与质量调整成交可以差三个数量级**：Native (BSC) 原始成交约 $29.3mn，
  质量调整后约 $216——19 个交易对中 17 个被标记。两者必须并列展示。

### A.5 术语纠正

原始数据集中的 `anomaly` / `stale` 标记是 **CoinGecko 的数据质量标记**，
表示该交易对报价可疑或陈旧，与本系统要检测的**需求异常**是两个概念。
系统内前者称 `quality_flag`，后者称 `alert`。见 `CONTEXT.md`。

### A.6 一次意外发现

Loris 数据中按 OI 排名第一的场所 `Trade[XYZ]` 是 Hyperliquid 的 HIP-3 permissionless perp DEX。
Hyperliquid 官方 API 免费、无鉴权地提供其完整合约级数据。
这使早期设计中「Loris 完整合约历史 ❌ 无，需 API Key」这一最大缺口大部分被填补，
并导致永续主源从 Loris 改为 Hyperliquid 官方 API。见 `docs/adr/0003-hyperliquid-as-primary-perp-source.md`。

### A.7 三条不可让步的原则

1. **口径不可相加** —— 代码强制，不靠人记得。
2. **Not verified ≠ 0** —— 取不到就是取不到，UI 灰色占位。
3. **告警必须可解释** —— 每条都能点开看到原始值、基线、样本量、规则名。
