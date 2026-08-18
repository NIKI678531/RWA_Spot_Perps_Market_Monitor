# 跨场所永续改由五家交易所直采，不依赖 Loris 聚合页

工作簿 `09_Perps_Venues` / `10_Perps_Summary` 两张表来自 `loris.tools/rwa` 的公开页面。
实测该页面数字全部由前端渲染，抓 HTML 只能得到场所名称，拿不到任何成交额与持仓量；
其真实后端为 `api.loris.tools`，已定位到 `/rwa/exchanges`、`/rwa/aggregates-timeseries`、
`/markets/symbols` 三个接口，但全部返回 `401 Missing API key`。
即便拿到 key，公开视图也只有 Top 25、无合约级明细、无历史。

决定：跨场所永续数据改由交易所自己的公开行情接口直采，覆盖 OKX、Bybit、Gate.io、MEXC、
Bitget 五家；Loris 保留在注册表中，`status = PLANNED`、`auth_mode = API_KEY`，
采集器已写好，等 `loris_api_key` 配置到位即可启用。

## Considered Options

- **标记 Loris 为 `REFERENCE_ONLY`。** 被否：`REFERENCE_ONLY` 的语义是"评估后决定不采"
  （见 ADR 0004 的 ASXN：那堵墙无法绕过，且数据本身在范围外）。Loris 没有任何一点被否，
  它只是缺一把钥匙，给了钥匙立刻可用。写成 `REFERENCE_ONLY` 会让后来者以为此路不通。
- **用无头浏览器渲染 Loris 页面。** 被否：与 ADR 0004 同一条理由——生产 K8s 无 PVC，
  且这类方案对上游改版极度脆弱。这条约束不因换了一个站点而改变。
- **只接一家交易所。** 被否：单一场所的永续视图无法回答"这个需求是不是全市场的"，
  而这正是横截面检测器（`X*` 族）唯一的输入。同侪组只有一个成员时，横截面比较不成立。
- **买聚合器席位。** 被否：五家的行情接口都不要 key，一次采集 1–2 个请求；
  覆盖度比任何聚合器的 Top 25 更完整，成本为零。

## Consequences

- 五家场所的合约命名各不相同（`AAPL-USDT-SWAP` / `AAPLUSDT` / `AAPLX_USDT` /
  `AAPLSTOCK_USDT`），去计价后缀的语法逐场所定义。**绝不做前缀匹配**：MEXC 的
  `HOODRAT_USDT` 是 meme 币，前缀匹配会把它认成 Robinhood。
- 持仓量的单位五家三种口径：OKX/Bybit 直接给美元，Gate/MEXC 给张数需乘合约乘数，
  Bitget 给币本位需乘价格。任一因子缺失时整个乘积记为 `NULL` 而非 0
  ——缺一个因子得到的不是"很小的持仓"，而是"未知的持仓"。
- 每个场所写两行 `fact_perp_venue_snapshot`：`segment = all` 与 `segment = stock`。
  **stock 是 all 的子集，两行不可相加**，API 与图表都不得跨 `segment` 求和。
- 五家合计列出 3,000 余个合约，其中绝大多数是加密原生标的。解析在写库之前完成，
  只有能落到 `dim_underlying` 已有标的上的合约才建维度行，否则
  `dim_perp_contract` 与待审队列会被上千个范围外符号淹没（域规则 11）。
- `underlying_map` 新增 `STOCK` 后缀规则，且必须排在最前——后缀规则按长度降序匹配，
  短规则先命中会切错字符数、提出一个根本没人上市的标的。
