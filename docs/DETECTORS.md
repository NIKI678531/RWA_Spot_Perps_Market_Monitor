# 异常检测器规格

> 配套 `ARCHITECTURE.md` §8。术语以 `CONTEXT.md` 为准。
> 分族依据见 `adr/0005-two-detector-families.md`。

17 个检测器，一个文件一个，在 `services/anomaly/engine.py` 注册。
文件名带族前缀：`x1_cross_sectional_turnover.py`、`t2_cold_start_awakening.py`。

## 0. 全局闸门

任何检测器的输出在进入 `alert` 之前必须通过下列全部闸门：

| 闸门 | 规则 | 理由 |
|:--|:--|:--|
| 范围 | `rwa_tier ≠ NON_RWA` | 加密原生资产不在统计口径内 |
| 绝对量 | 相关名义额 ≥ **$50,000** | $500 → $5,000 是 +900%，无商业意义 |
| 口径 | 证据中的 `metric_scope` 必须单一 | 跨口径比较无意义 |
| 持续性 | 单快照 → `TENTATIVE`；连续 2 个快照 → `CONFIRMED` | 抑制单点抖动 |
| 冷却 | 同 `(entity, detector)` 24h 内合并，`occurrence_count` 累加 | 抑制刷屏 |
| 证据 | 必须写 `alert_evidence` | 不可解释的告警等于噪音 |

时序族额外要求：同 `market_session` 历史 ≥ **14** 个快照，否则只记录不告警。
横截面族额外要求：对照组样本 ≥ **5**，否则跳过该实体。

评分：

```
severity = 0.5·norm(robust_z) + 0.3·norm(log10(绝对USD量)) + 0.2·persistence
```

分档：`LOW < 0.35 ≤ MEDIUM < 0.6 ≤ HIGH < 0.85 ≤ CRITICAL`。

---

## 1. 横截面族（X1–X7）

与同组实体的**当前状态**比较。不读基线，无冷启动依赖，**P1 上线**。

对照组默认按 `(asset_class, rwa_tier)` 划分；池类检测器按 `(network, quote_token)` 划分。
组内统计量同样用中位数 + MAD。

### X1 `CrossSectionalTurnover` — 换手率横截面离群

| 项 | 值 |
|:--|:--|
| 输入 | `vol_24h`（`SPOT_VOLUME`）、`market_cap`（`SPOT_MARKET_CAP`） |
| 派生 | `turnover = vol_24h / market_cap`（`RATIO`，不可求和） |
| 触发 | 组内 `robust_z(turnover) > 3.5` |
| 附加门槛 | `vol_24h ≥ $50k`；`market_cap ≥ $250k`（避免微市值放大分母噪音） |
| 业务含义 | 一个没人讨论、也没有历史可比的标的，正在被异常密集地交易 |

冷启动期最重要的检测器：它不需要任何历史即可回答「谁正在被买」。

### X2 `BuySellImbalance` — 买卖失衡

| 项 | 值 |
|:--|:--|
| 输入 | `buys_24h`、`sells_24h`、`vol_24h`（DEX 池） |
| 派生 | `buy_ratio = buys / (buys + sells)`（`RATIO`） |
| 触发 | `buy_ratio > 0.65`（净买入）或 `< 0.35`（净卖出） |
| 附加门槛 | `buys + sells ≥ 500` 笔；`vol_24h ≥ $50k` |
| 业务含义 | 成交额只说明有人在交易；买卖比才说明客户在买还是在卖 |

DEX 是唯一能分离买卖方向的数据源。这是判断真实增量需求（而非对倒刷量）的唯一直接证据。

### X3 `VolLiqRatioExtreme` — 高换手薄池

| 项 | 值 |
|:--|:--|
| 输入 | `vol_24h`（`SPOT_VOLUME`）、`reserve_usd`（`DEX_LIQUIDITY`） |
| 派生 | `vol_liq = vol_24h / reserve_usd`（`RATIO`） |
| 触发 | `vol_liq > 20` |
| 附加门槛 | `reserve_usd ≥ $50k` |
| 业务含义 | 池薄而换手高，价格易被单笔打穿；这类成交量的信息含量低，须在解读时降权 |

### X4 `NewPairListing` — 新交易对

| 项 | 值 |
|:--|:--|
| 输入 | `(asset_id, venue_id)` 组合 |
| 触发 | 该组合在历史 `fact_pair_snapshot` 中不存在 |
| 附加门槛 | 首次观测 `adjusted_vol ≥ $50k` |
| 业务含义 | 竞品新上架或开辟新渠道 |

### X5 `NewUnderlyingCoverage` — 新底层覆盖

| 项 | 值 |
|:--|:--|
| 输入 | `underlying_id` |
| 触发 | 出现历史上不存在的 `underlying_id`，且已通过人工映射审核 |
| 附加门槛 | 无（新赛道本身即信号，但严重度按首日成交定档） |
| 业务含义 | 市场在开新赛道 —— 例如首个 Pre-IPO 标的上线 |

未通过人工映射审核的新符号不触发本检测器，只进审核队列。避免把映射失败误报为新赛道。

### X6 `PriceConsistency` — 同底层跨包装价格不一致

| 项 | 值 |
|:--|:--|
| 输入 | 同一 `underlying_id` 下各 `asset` 的 `price` |
| 派生 | 组内价格相对中位数的偏离率（`RATIO`） |
| 触发 | 任一包装偏离组中位数 > **2%** |
| 附加门槛 | 组内 ≥ 2 个包装且各自 `vol_24h ≥ $50k` |
| 业务含义 | 要么存在套利窗口，要么**映射错了** |

后者是主要用途。基线数据集中 `SKHX` 与 `SKHY` 价格相差约 7 倍、
`GOLD` / `GOLDJM` / `GLDMINE` 是三个不同标的——本检测器把这类映射错误在进入报表前拦下。
触发时同时向人工审核队列写一条映射复核任务。

### X7 `BasisDislocation` — 代币价与底层价基差

| 项 | 值 |
|:--|:--|
| 输入 | `asset.price`、Alpaca 底层参考价 |
| 派生 | `basis = (token_price − ref_price) / ref_price`（`RATIO`） |
| 触发 | `|basis| > 2%` |
| 附加门槛 | **仅 `market_session = RTH`**；`vol_24h ≥ $50k` |
| 业务含义 | 定价失效或套利窗口 |

`market_session ≠ RTH` 时必须跳过。Alpaca 走 IEX 而非 SIP，
且闭市时段代币价与最后一笔股价的时点天然错位——在非 RTH 运行本检测器 100% 是假警报。
结论只作方向性参考，不作套利判断。

---

## 2. 时序族（T1–T10）

与实体**自身历史**比较。需通过冷启动闸门（同 `market_session` ≥14 个快照），**P2 上线**。

### T1 `VolumeSpike` — 成交放量

| 项 | 值 |
|:--|:--|
| 输入 | `adjusted_vol`（`SPOT_VOLUME`） |
| 触发 | `robust_z > 3.5` |
| 附加门槛 | `adjusted_vol ≥ $50k` |
| 业务含义 | 已有需求的产品放量 |

用 adjusted 而非 raw：raw 会把质量标记的对倒量当成需求。

### T2 `ColdStartAwakening` — 沉睡唤醒 ★

| 项 | 值 |
|:--|:--|
| 输入 | `adjusted_vol`（`SPOT_VOLUME`）+ 完整回看窗口 |
| 触发 | 回看窗口内 **≥90%** 的观测 ≤ **$1,000**，且当前 ≥ **$100,000** |
| 附加门槛 | 已含在触发条件中 |
| 业务含义 | **原先没有人买的产品现在突然多了很多人买** —— 本系统的核心诉求 |

刻意**不用百分比变化**表达。$200 → $4,000 是 +1900% 且毫无意义；
「曾经沉睡」与「现在有量」是两个独立条件，必须分别成立。

90% 而非 100%：单个不具代表性的打印不应使一条本质静默的序列失去资格。

与 T1 的边界：已经在交易的标的跳涨归 T1。两者分开，
是为了不让「涨了 3 倍」和「从零开始」在告警流里显示为同一类事件。

### T3 `BreadthExpansion` — 发行商铺货

| 项 | 值 |
|:--|:--|
| 输入 | 发行商名下 `adjusted_vol > 0` 的活跃 asset 数 |
| 触发 | 7 日活跃产品数 **+20%** |
| 附加门槛 | 净增 ≥ **3** 个产品（避免小基数放大） |
| 业务含义 | 竞品在扩产品线 |

### T4 `ThemeRotation` — 主题轮动

| 项 | 值 |
|:--|:--|
| 输入 | 主题级 `adjusted_vol` 份额（`RATIO`） |
| 触发 | 7 日份额变动 ≥ **5pp** |
| 附加门槛 | 该主题绝对成交 ≥ **$1mn** |
| 业务含义 | 需求在主题之间迁移 —— 直接对应选品决策 |

份额是 `RATIO`，只能通过 `weighted_avg()` 计算，权重口径为主题内 `adjusted_vol`。

### T5 `LiquidityDrain` — 流动性撤离

| 项 | 值 |
|:--|:--|
| 输入 | `reserve_usd`（`DEX_LIQUIDITY`） |
| 触发 | 24h 跌幅 > **40%** |
| 附加门槛 | 跌前 `reserve_usd ≥ $250k` |
| 业务含义 | 做市商撤退，风险信号 |

### T6 `VenueShareShift` — 场所份额迁移

| 项 | 值 |
|:--|:--|
| 输入 | 场所 `adjusted_vol` 份额（`RATIO`） |
| 触发 | 份额变动 > **5pp** |
| 附加门槛 | 该场所 `adjusted_vol ≥ $500k` |
| 业务含义 | 流动性搬家 |

### T7 `IssuerMomentum` — 发行商此消彼长

| 项 | 值 |
|:--|:--|
| 输入 | 发行商 `adjusted_vol` 份额（`RATIO`） |
| 触发 | 7 日份额变动 > **8pp** |
| 附加门槛 | 该发行商 `adjusted_vol ≥ $500k` |
| 业务含义 | 竞品格局变化 |

### T8 `ConcentrationShift` — 集中度变化

| 项 | 值 |
|:--|:--|
| 输入 | HHI、Top-10 份额（`RATIO`），分别按 venue / issuer / contract 三个轴 |
| 触发 | 任一指标相对变动 > **10%** |
| 附加门槛 | 轴上实体数 ≥ 5 |
| 业务含义 | 市场从集中走向分散，或反之 |

### T9 `OISurgeNoVolume` — 静默建仓

| 项 | 值 |
|:--|:--|
| 输入 | `oi_usd`（`PERP_OI`）、`vol_24h`（`PERP_VOLUME`） |
| 触发 | `robust_z(oi) > 3` **且** `robust_z(vol) < 1` |
| 附加门槛 | `oi_usd ≥ $50k` |
| 业务含义 | 有人在建立持仓而非日内投机 —— 方向性观点，信息含量高于单纯放量 |

两个输入分属不同 `MetricScope`，各自独立打分，**不合成单一数值**。

### T10 `FundingExtreme` — 资金费率极端

| 项 | 值 |
|:--|:--|
| 输入 | `funding_rate`（`RATIO`） |
| 触发 | `|funding|` > 该合约自身历史 **p95** |
| 附加门槛 | `oi_usd ≥ $250k` |
| 业务含义 | 单边拥挤，情绪极端 |

用自身历史分位数而非全市场分位数：不同标的的资金费率中枢差异极大。

---

## 3. 排期外的指标

**滑点与深度带质量**（`SlippageDeterioration`）在本版本中**不是检测器**，
而是 P3 的展示指标。它依赖 Hyperliquid `l2Book` 的历史积累，
而各交易所均不提供历史深度接口，需自采自存。指标定义参照 ASXN Hyperscreener
（见 `adr/0004-challenge-source-degradation.md`），数据自算。
待积累足够历史后再评估是否升级为时序族检测器。
