# RWA Spot & Perps Market Monitor — 架构设计

> 版本 v3.0 · 2026-08-26（对齐 PRD v2.0《RWA 产品决策雷达》）
> 术语以根目录 `CONTEXT.md` 为准。视觉规范以 `DESIGN.md` 为准（本文不修改该文件）。
> R1 需求编号与验收标准见 `docs/REQUIREMENTS-R1.md`。
> 检测器细则见 `docs/DETECTORS.md`；图表规范见 `docs/DATAVIZ.md`；版式见 `docs/UI-LAYOUT.md`。
> 设计取舍的记录见 `docs/adr/`。业务背景见本文附录 A。

---

## 1. 范围

系统持续采集代币化 RWA（股票 / ETF / 基金 / 商品）在 CEX、DEX 现货与跨所永续市场的公开数据，
以时间序列落库，产出规模与成交排名、场所与发行商竞争格局、需求异常告警，以及每日 xlsx / docx 报告。

R1 在此之上把系统从「市场数据展示」升级为**产品决策雷达**：每条发现都要走完
**发现 → 验证 → 解释 → 行动 → 复盘**，并留下可审计的处理记录。
数据范围、口径类型系统与检测器全部沿用 v2.0；新增的是发布门、告警工作流、版本（edition）与业务对象层。

**范围内**：`rwa_tier ∈ {CORE_RWA, RWA_ADJACENT, SYNTHETIC}` 的标的。

**范围外**：加密原生资产（BTC / ETH / SOL 等，`rwa_tier = NON_RWA`）。
这类资产仅作为 `dim_benchmark` 的参照项存在，不进入任何统计口径、排名或告警。

**非目标**：交易执行、投资建议、法律权利与储备的独立尽调、链上持有人级追踪；
把多种口径合成单一「总分」；用任何分数直接决定发行。

**R1 明确不做**：港股 / 韩国 / 商品的完整 session 日历（仅预留扩展接口）；
以 Polymarket 预测概率驱动 RWA 告警或发行判断（仅作叙事背景）。

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
     │                                 publication.py = 发布门，检测候选进入 alert 的唯一通道
L6 BUSINESS    services/workflow/      告警生命周期 · 研究任务 · 发行候选 · 产品覆盖 · 数据缺口 · 审计
   EDITIONS    services/editions/      Live / 冻结版生成 · 修订版 · 分享版脱敏
     │  L6 由用户动作与调度驱动，不是单向管道的一环；它读 L5 产物与 alert，永不写 fact_*
     ├── L7a API      FastAPI，挂在 settings.normalized_api_base_path 下
     └── L7b REPORT   openpyxl → 22-sheet xlsx；python-docx → 分析报告
                      产物写对象存储或数据库，不落容器文件系统
L8 WEB         React 18 + TS + antd 5 + ECharts
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
| `dim_theme` | `theme_id` | 需求主题：Pre-IPO / 半导体 / 贵金属 / 能源 / 杠杆 ETP / 宽基指数。定义与映射版本化，见 `theme_map` |
| `dim_benchmark` | `benchmark_id` | **软聚合层**：把不同 tier 的 underlying 归到同一经济暴露（SPY ETF 与 S&P 500 指数、GOLD 与 XAU）。仅用于对照展示，**不参与任何求和** |
| `dim_own_product` | `product_id` | **自有产品主表**：我方已发行 / 在研产品，用于覆盖缺口计算。权威系统与维护人在上线前确认 |
| `dim_user` | `user_id` | owner 与角色（`VIEWER` / `ANALYST` / `OWNER` / `ADMIN` / `SHARE_VIEWER`），处理动作的责任主体 |

`theme_map` 是版本化的多对多映射：`(underlying_id, theme_id, is_primary, version, valid_from,
valid_to, confirmed_by, reason)`。**一个 underlying 在任一版本内有且只有一个 `is_primary = True`**——
份额加总到 100% 的图只能用主题主口径，secondary 只用于并列观察。历史 edition 按其 `as_of`
落在哪个版本区间来回放，主题定义变更不会追溯改写昨天的报告。

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
| `fact_launch_window_snapshot` | (asset\|contract) × window × ts | `window ∈ {1h, 6h, 24h}`、各口径观测值、质量标记、跨所出现数、阶段结论。窗口一旦关闭即不可变，迟到数据出修订版 |

### 4.4 辅助表

**数据侧**：`source_registry`（源与 `auth_mode` / `status`）、`fetch_log`（每次采集状态）、
`metric_scope`（口径注册表）、`underlying_map`（版本化映射）、`theme_map`（版本化主题映射）、
`baseline`（分层基线快照）、`data_gap`（可分派的数据缺口队列：问题、影响页面/结论、owner、SLA、修复状态）。

**告警与业务侧**：`alert`、`alert_evidence`（含反证字段）、`alert_action`（认领 / 转派 / 备注 /
确认 / 判误 / 搁置 / 关闭，只追加）、`research_task`、`issuance_candidate`、`candidate_gate_log`
（五道评估门的进入与退出记录）、`product_coverage`（`COVERED` / `CANDIDATE` / `GAP` /
`COMPETITOR_FIRST`）、`audit_log`（谁在什么时候把什么从什么状态改成了什么）。

**版本侧**：`edition`（`LIVE` / `MORNING` / `AFTERNOON`、`as_of`、`generated_at`、`version`、
`is_immutable`）、`edition_revision`（修订原因与前后差异）、`edition_artifact`（导出物与对象存储 URI）。

`alert_action` 与 `audit_log` 都是只追加。**状态不是字段更新出来的**：`alert.current_state`
是动作流的物化视图，可以从 `alert_action` 完整重放。

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
- **持续性**：单快照触发 `confirmation_count = 1`，状态 `TENTATIVE`；连续 2 个快照升 `PUBLISHED`。
- **去重**：同一 `(entity, detector)` 在 24h 冷却窗内合并，`occurrence_count` 累加。
  `occurrence_count`（同一告警重复出现）与 `confirmation_count`（连续确认次数）是两个数，不可互相替代。
- **可解释性**：每条告警落 `alert_evidence`，存原始值、基线、样本量、`market_session`、规则名与反证。

```json
{
  "alert_id": "...",
  "detector": "T2_ColdStartAwakening",
  "family": "time_series",
  "severity": "HIGH",
  "state": "PUBLISHED",
  "confirmation_count": 2,
  "owner_id": null,
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
  "counter_evidence": [
    {"kind": "single_venue", "detail": "97% 成交来自单一场所 Binance"},
    {"kind": "raw_adjusted_gap", "detail": "raw $412k vs adjusted $362k，1 个 pair 被标 stale"}
  ],
  "first_seen": "...", "occurrence_count": 3
}
```

`state` 的完整状态机与迁移规则见 §9.2。检测器不写 `state`，写的是候选；
`state` 由发布门与用户动作产生。

---

## 9. 发布门与告警工作流

### 9.1 发布门

检测器产出的是**候选**，不是告警。`services/anomaly/publication.py` 是候选进入 `alert` 表的唯一通道：

| 闸门 | 条件 | 不通过时 |
|:--|:--|:--|
| 范围 | `rwa_tier ≠ NON_RWA` | 丢弃 |
| 口径 | 证据中 `metric_scope` 单一且合法 | 丢弃并记工程错误 |
| 绝对量 | 相关名义额 ≥ 约 **$50,000** | 丢弃 |
| 数据可验证 | 相关观测非 `NOT_VERIFIED` / `STALE` | 转入 `data_gap`，不发布 |
| 证据完整 | 原值、基线/对照组、样本量、`market_session`、规则名、确认次数齐全 | 不可发布 |
| 冷启动（仅 `T*`） | 同 session 快照 ≥ 14 | 只记录不发布 |
| 连续确认（仅 `T*`） | 1 次 → `TENTATIVE`；2 次 → `PUBLISHED` | 停留在 `TENTATIVE` |

`X*` 横截面族**可即时发布**，但前端文案必须写「同组现状异常」，不得写成「相对历史升温」——
它根本没有读过历史。两族的发布语义分离是硬约束，见 `adr/0005-two-detector-families.md`。

**管理层可见面**（决策首页、每日邮件）在发布门之上再加一层：仅 `high` / `critical`，
且证据完整率必须是 100%。异常雷达工作队列可以看到 `TENTATIVE` 与更低严重度，但要显式标注置信度。

### 9.2 告警状态机

```
DETECTED ──(发布门)──> TENTATIVE ──(第 2 次确认)──> PUBLISHED
                            │                          │
                            └──────────┬───────────────┘
                                       ▼
                                    CLAIMED ──> IN_REVIEW ──> ACTIONED ──> RESOLVED
                                       │
                任意阶段 ──> FALSE_POSITIVE | DISMISSED（必须记录原因）
```

- `DETECTED` 只存在于系统内部，**永不出现在任何界面或 API 响应里**。
- 状态迁移写 `alert_action` + `audit_log`，同事务提交。审计写失败即整个动作失败。
- `RESOLVED` 前必须有复盘：最终结论、是否真实需求、影响了什么决策、误报原因、阈值建议。
- **数据修复不覆写历史告警**：修好数据后要么给原告警追加一个证据版本，要么产生一条新告警。
  昨天基于错误数据发出的告警，历史上确实发生过。
- `FALSE_POSITIVE`（系统判断错了）与 `DISMISSED`（系统对了但业务选择不动）是两回事，
  混为一谈会让误报率这个指标失去意义。

理由见 `adr/0008-alerts-are-business-objects.md`。

### 9.3 从告警到发行候选

`research_task` 与 `issuance_candidate` 都通过 `alert_id` 反向关联到触发它们的告警——
「信号 → 研究任务转化率」这个成功指标只有在这条关联存在时才是可算的。

发行评估五道门（发现 → 数据确认 → 小规模验证 → 可行性评估 → 产品委员会）记在 `candidate_gate_log`，
每道门有进入条件、降级条件、owner 与产出物。

**没有任何分数可以推动一道门。** severity、热力图色阶、四象限位置只用于排序和优先级；
合规、对冲、做市、客户需求与产品委员会是系统之外的独立审批。见 `adr/0009-no-composite-approval-score.md`。

---

## 10. 版本（Edition）与不可变发布

| Edition | 生成 | 可变性 |
|:--|:--|:--|
| `LIVE` | 每小时刷新 | 可变；页面必须显示 snapshot 时间、数据年龄、下次刷新与部分源延迟 |
| `MORNING` | 每日 09:00 HKT 冻结 | **不可变** |
| `AFTERNOON` | 每日 17:00 HKT 冻结 | **不可变** |

- 冻结版一次写定。迟到或更正数据产生 `v2` / `v3` **修订版**，带修订原因与前后差异，
  被取代的版本仍然可读、可访问。**永不 `UPDATE` 已冻结的 edition。**
- 所有页面 URL、导出和分享链接固定 `edition` 与 `as_of`；切换 edition 时保留其它仍然合法的筛选。
- `as_of`（数据截止）与 `generated_at`（生成时刻）分别展示。把两者混为一谈会让报告显得比它的输入更新鲜。
- **分享版**是脱敏后的只读冻结版：服务端裁掉候选、owner、处理动作、业务备注、发行状态、阈值配置
  与未公开源细节；前端隐藏不算脱敏。保留公开市场证据、口径说明、`as_of` 与版本号。

理由见 `adr/0007-editions-and-the-frozen-record.md`。

---

## 11. API

统一挂在 `settings.normalized_api_base_path` 下。

所有响应统一携带上下文元数据：`scope`、`edition`、`as_of`、`generated_at`、`data_age`、
`coverage`、`verification`。**缺任一字段视为契约违规**——没有这些，一张截图六周后无法复现。

```
GET  /api/health

# 决策面
GET  /api/executive                 一句话摘要 + 四问答案 + Top 机会/风险 + 五口径 KPI（严格分列）
GET  /api/editions                  Live 与冻结版列表、修订链、导出链接
GET  /api/editions/{id}             单个版本（含 as_of / generated_at / version / is_immutable）
GET  /api/search                    统一搜索：underlying / asset / issuer / venue / pair / contract / theme

# 告警工作队列
GET  /api/alerts                    队列（status/severity/family/detector/scope/session/owner/theme/quality）
GET  /api/alerts/{id}               单条告警 + 完整证据链 + 反证 + 时间线 + 相邻异常
POST /api/alerts/{id}/actions       认领 / 转派 / 备注 / 确认 / 判误 / 搁置 / 关闭（写审计）
POST /api/alerts/{id}/research-tasks  由告警创建研究任务（保留 alert_id 关联）

# 研究与发行
GET  /api/underlyings/{id}          底层 360 全景
GET  /api/themes                    主题需求（primary / secondary 分列）
GET  /api/themes/versions           主题版本与映射审核记录
GET  /api/coverage                  市场需求 × 自有产品覆盖矩阵（四态）
GET  /api/candidates                发行候选与评估门状态
POST /api/candidates/{id}/gates     推进 / 降级一道评估门（人工决策，写审计）
GET  /api/launch-windows            新产品 1h / 6h / 24h 生命周期快照与阶段结论

# 市场结构（P1 保留页）
GET  /api/scale/categories          五类 + 去重并集（带 is_additive）
GET  /api/spot/venues               场所排名（raw / adjusted 并列）
GET  /api/spot/pairs                交易对下钻（venue / issuer / underlying / tier 筛选）
GET  /api/dex/pools                 DEX 池（含 buys / sells）
GET  /api/issuers                   发行商对比（R1 嵌入用，R2 独立页）
GET  /api/issuers/{id}/venues       发行商 × 场所矩阵
GET  /api/perps/venues              跨所永续（含 HIP-3 perp DEX）
GET  /api/perps/contracts           合约级排名
GET  /api/perps/dexs                HIP-3 permissionless DEX 列表
GET  /api/timeseries                通用时序（entity_type / entity_id / metric / range / session）

# 治理
GET  /api/quality                   源健康 / 实体覆盖 / 映射 / Raw-Adjusted 差异 / 验证 / 基线健康
GET  /api/quality/gaps              数据缺口队列（owner / SLA / 修复状态）
POST /api/quality/gaps/{id}/actions 分派与状态推进（写审计）
GET  /api/reports
GET  /api/reports/{edition_id}/excel
GET  /api/reports/{edition_id}/word
POST /api/reports/generate
```

`/api/underlyings/{id}` 由 v2.0 的 `/api/underlying/{id}` 更名而来（资源名统一复数）。
旧路径保留一个版本作为别名并在响应头标注弃用，之后移除。

`/api/underlyings/{id}` 返回结构：

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

### 11.1 统一数值对象

任何可展示的数值都以同一个信封返回，而不是裸 float：

```json
{
  "value": 362076.0,
  "unit": "USD",
  "metric_scope": "spot_volume",
  "metric_dimension": "flow",
  "raw_value": 362076.0,
  "adjusted_value": 361204.0,
  "verification_status": "VERIFIED",
  "observed_at": "2026-08-26T09:00:00+08:00",
  "window": "24h",
  "source_count": 3,
  "weight_basis": null
}
```

- `metric_dimension = ratio` 时 `weight_basis` **必填**，缺失的 ratio 聚合请求由后端拒绝，不给默认值。
- `verification_status ∈ {VERIFIED, PARTIAL, NOT_VERIFIED, STALE, EMPTY}`。
  `EMPTY` 是观测到的真零，可以按零陈述；`NOT_VERIFIED` 不得渲染成任何数字。
- 后端拒绝非法跨 scope 聚合，前端图表组件再断言一次。图表数据也可能来自前端本地推导，两侧都要做。

### 11.2 权限与分享

五种角色：`VIEWER`（内部只读）、`ANALYST`（备注 / 确认 / 判误 / 建研究任务）、
`OWNER`（认领 / 转派 / 推进 / 关闭）、`ADMIN`（源、阈值、映射、主题审核、权限）、
`SHARE_VIEWER`（脱敏冻结版只读）。

脱敏在**服务端裁字段**，不是前端隐藏；无权限响应不得泄露记录是否存在。

---

## 12. 前端

React 18 + TypeScript + Webpack + antd 5 + framer-motion + lucide-react + **ECharts**。

视觉遵循 `DESIGN.md`（不修改该文件）。图表规范在 `docs/DATAVIZ.md`，其色值全部从 `DESIGN.md` 现有 token 派生；
两文件冲突时以 `DESIGN.md` 的 token 体系为准。版式细则见 `docs/UI-LAYOUT.md`。

页面收敛为 5 个版式模板：

| 模板 | 骨架 | 页面 |
|:--|:--|:--|
| **T1 决策** | 状态条 + 一句话摘要 + 四问卡 + Top 机会/风险 + 五 KPI + 证据图组 + 快速下钻 | 决策首页 |
| **T2 榜单** | 筛选条 + 主图(Hero) + 明细表 | 现货规模 · 场所 · 永续 · 主题需求 |
| **T3 详情** | 实体头 + 按口径分栏指标 + 多图 + 明细表 | Underlying 360 · Perp Contract |
| **T4 队列** | 队列摘要 + 多维筛选 + 工作列表 + 证据/处理抽屉 | 异常雷达 · 数据质量 |
| **T5 版本** | 版本列表 + 摘要 + 修订链 + 导出 | 报告与复盘 |

T3 的「按口径分栏」是硬约束的版式化：不同 `MetricScope` 物理上分在不同卡片内，
使跨口径相加在版面上就不成立。

T4 从 v2.0 的「时间轴流水」升级为**工作队列**：告警是可认领、可推进、可复盘的业务对象，
时间轴只是它的一种排序方式。

导航为左侧 **200px 常驻展开 rail**，按业务闭环分四组：**决策 / 研究 / 市场结构 / 治理**，P0 页面置顶。
顶部全局工具区常驻：统一搜索、scope、edition、时间窗、语言、明暗、用户菜单，
以及当前 snapshot 时间与数据年龄。

**搜索不再是首页主角**（v2.0 的 Greeting Hero + 搜索框已废弃）：首页 Hero 是一句话业务结论，
搜索移入全局工具区，命中直接进入对应实体页。版式细则见 `docs/UI-LAYOUT.md`。

---

## 13. 报告

- **报告从冻结版生成**，不从 Live 生成。每份报告绑定一个 `edition_id`，可回放到当时的 `as_of`。
- **xlsx（22 sheet，openpyxl）**：在原 19 sheet 基础上新增
  `16_HL_HIP3_Contracts`、`17_Liquidity_Quality`、`18_Theme_Demand`；
  `01_Asset_Master` 等表增加 `rwa_tier` 列。
- **docx（python-docx）**：分析报告，含「异常告警摘要」与「处理与复盘」两章。
- **Excel 保持朴素**：无条件格式、无内嵌图表、无合并单元格，保证可直接复制与二次加工。
  可视化只在 Web 端。
- **网页导出继承上下文**：筛选、排序、edition、`as_of`、时区、scope、Raw/Adjusted、验证状态一并带出。
- 产物写对象存储（TOS）或数据库，**不落容器文件系统**。

---

## 14. 调度

APScheduler，时区 HKT。

```
每 15 分钟   headline 快照（Binance TradFi ticker、Hyperliquid metaAndAssetCtxs）
每  1 小时   现货 Top 50 + GeckoTerminal 池 + Hyperliquid perpDexs
             → 刷新 LIVE edition（写 snapshot 时间与数据年龄）
每  6 小时   长尾现货、类别口径、发行商官网产品数
每日 06:30   Alpaca 底层参考价（美股收盘后）
每日 09:00   冻结 MORNING edition（不可变）
每日 17:00   冻结 AFTERNOON edition（不可变）
每日 17:15   生成 xlsx + docx 报告，发送每日邮件摘要（可配置）
每日 03:00   基线重算、冷数据归档、SLA 超时扫描
按需        新产品 1h / 6h / 24h 生命周期窗口快照（以上市时间为锚）
```

冻结任务失败必须告警且**不得静默顺延**：一个没有 09:00 版本的早晨，比一个晚到的 09:00 版本更容易被发现。

---

## 15. 技术栈与目录

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
├── api/routes/              health executive editions search alerts underlyings themes
│                            coverage candidates launch_windows scale spot dex issuers
│                            perps timeseries quality reports
├── core/                    config.py  metrics.py  sessions.py
├── db/                      session.py  base.py
├── models/                  dim_* / fact_* / alert / workflow / edition / registry
├── schemas/                 Pydantic 出入参（含统一数值对象 value.py）
└── services/
    ├── ingest/              coingecko geckoterminal hyperliquid binance alpaca issuer_official
    ├── normalize/           dedup underlying_map tiering quality venue_registry theme_map
    ├── analytics/           rollups concentration baseline
    ├── anomaly/             engine.py scoring.py publication.py detectors/（17 个）
    ├── workflow/            alert_lifecycle.py research_task.py candidate.py coverage.py
    │                        data_gap.py audit.py
    ├── editions/            freeze.py revision.py redaction.py
    ├── report/              excel.py word.py
    └── scheduler.py
```

---

## 16. 交付计划

v2.0 的 P0–P3 已交付管道、口径系统、检测器与首版页面。R1 在其上按**工作流**切分，
依赖关系比技术分层更能决定并行度：

| 工作流 | 主要交付 | 前置 |
|:--|:--|:--|
| **A. 数据契约** | 统一数值对象 · scope 校验 · edition 模型 · evidence/反证字段 · underlying 与 theme 实体版本化 | 数据字典与迁移 |
| **B. 检测与状态机** | 发布门 · 连续确认 · 告警状态机与动作 · 审计 | A |
| **C. 决策首页** | 状态条 · 一句话摘要 · 四问卡 · Top 机会/风险 · 五 KPI · 首批证据图 | A + B |
| **D. 深度研究** | Underlying 360 · 主题需求 · 覆盖缺口矩阵 · 发行候选与评估门 | A |
| **E. 治理** | 数据质量与缺口队列 · 冻结版与修订 · 报告 · 权限 · 分享脱敏 · 邮件摘要 | A + B |
| **F. 联调验收** | 四个端到端场景 · 口径契约测试 · 性能 / 权限 / 可访问性 · 回归 | A–E |

R1 验收场景（沉寂标的放量 / Raw 高但 Adjusted 近零 / 份额迁移 / 新产品 1h-6h-24h）
与逐条需求编号见 `docs/REQUIREMENTS-R1.md`。

**R2**：港股 / 韩国 / 商品 session 日历 · 独立发行商竞争页 · Teams/Slack 通知 ·
Loris 正式接入（取决于 API）· 更完整的客户与渠道验证记录 · 移动端处理优化。

**R3**：阈值自适应建议 · 主题演化 · 跨信号关联 · 组合级覆盖缺口 · 产品机会回测。
即便如此，人工审批与证据可解释性仍然保留——见 `adr/0009-no-composite-approval-score.md`。

---

## 17. 已知边界

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
| 港股 / 韩国 / 商品 session 日历 | ❌ R1 不做 | 仅美股 session 完整；其余标的按 `CLOSED_*` 保守分层，接口预留 |
| Polymarket 预测概率 | ⚠️ 仅背景 | 不进入 RWA 汇总、告警或发行判断，只作叙事参考 |
| 自有产品主表权威系统 | ⚠️ 待确认 | 覆盖缺口计算依赖 `dim_own_product`，其权威来源与维护人在上线前确认 |
| high/critical 精确阈值 | ⚠️ 待校准 | 约 $50k 是当前统一下限；首月回测后按 detector 逐个校准，例外须可追溯 |

### 17.1 上线前业务确认（不阻断设计与开发）

- 正式 owner 名单、角色映射，以及「1 个工作日内认领」这条 SLA 的责任归属。
- 每日邮件的接收组与最终发送时间（当前默认 17:15 HKT）。
- 首月回测后 high / critical 的精确阈值与各 detector 的例外。
- 自有产品主表的权威系统、覆盖状态维护人，以及「竞品先发」的判定口径。
- 分享版的组织级脱敏清单与保留期限。

这五项都不阻断当前设计与开发：它们改的是配置与阈值，不是结构。
但每一项在上线前都必须有名字和日期，否则「谁来处理这条告警」在第一天就没有答案。

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

### A.7 五条不可让步的原则

1. **口径不可相加** —— 代码强制，不靠人记得。
2. **Not verified ≠ 0** —— 取不到就是取不到，UI 灰色占位。
3. **告警必须可解释** —— 每条都能点开看到原始值、基线、样本量、规则名。
4. **冻结版不可变**（R1 新增）—— 更正生成修订版，旧版本仍可读。
   否则「上午那份报告」会变成一个没有确定内容的说法。
5. **没有分数可以批准任何事**（R1 新增）—— 系统负责发现、排序与保全证据；
   合规、对冲、做市、客户需求与产品委员会是独立审批门。

### A.8 R1 为什么是重构而不是改版

v2.0 交付后暴露的不是视觉问题，是结构问题：

- 首页以问候和搜索为 Hero，第一屏回答不了「发生了什么、是否可信、要不要行动」。
- KPI、排名、告警各自成块，读者要自己拼因果——这正是把不同口径数字相加的起点。
- 异常页是技术列表：没有 owner、没有业务备注、没有判误、没有研究任务、没有结论与复盘。
  一条没人负责的告警，第二天就等于没发生过。
- 静态 Top N 太多：看得见「大」，看不见「正在变」。
- 缺统一的 Underlying 360：同一底层的包装、发行商、场所、永续与告警无法在一处闭环。
- 数据质量与业务结论分离：未验证值可能被当成 0，原始量可能掩盖质量调整后的结果。

因此 R1 保留全部数据能力，重组业务链路：首页按四个业务问题组织，
告警升级为有状态机和审计的业务对象，结论固化为版本。

### A.9 需求来源与追溯

- 客户原始需求：《RWA 看板客户需求梳理》，2026-08-18。
- PRD v2.0《RWA 产品决策雷达》，2026-08-26 —— 本轮重构的直接输入，含 25 项已确认决策。
  源文件为 docx，按 `.gitignore` 规则不入库；其规范性内容已拆进本文、`CONTEXT.md`、
  `docs/REQUIREMENTS-R1.md`、`docs/UI-LAYOUT.md` 与 `docs/DATAVIZ.md`。**以仓库内文档为准。**
- 客户现有 Prediction Signal Radar 的 RWA Live 页面及其证据呈现方式。
