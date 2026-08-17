# 永续主源选 Hyperliquid 官方 API，而非 Loris

早期设计以 Loris Tools 作为跨所永续的主源，并把「Loris 完整合约历史 ❌ 无，需 API Key」
列为系统最大的已知缺口。核对基线数据时发现，Loris 中按 OI 排名第一的场所 `Trade[XYZ]`
是 Hyperliquid HIP-3 permissionless perp DEX，而 Hyperliquid 官方 API 免费、无鉴权地提供
其完整合约级数据。

决定：永续主源改为 Hyperliquid 官方 API（`POST /info`，按 `type` 取 `perpDexs`、
`metaAndAssetCtxs`、`l2Book`）。Loris 降为 `PLANNED`，仅在拿到 API Key 后作为跨所补充。

## Considered Options

- **继续以 Loris 为主源并购买 API Key。** 被否：付费换来的仍是聚合视图，而我们需要的是合约级明细；
  Hyperliquid 免费提供且粒度更细。
- **抓取 Loris 公开页。** 被否：仅 Top 25，无合约历史，且抓取结果无法回溯校验。
- **两者并行为对等主源。** 被否：两个源的口径与合约命名不一致，并行会引入需要长期维护的对账负担，
  收益仅是冗余。

## Consequences

- 永续覆盖范围从「Loris 索引到的场所」变为「Hyperliquid 上的全部 HIP-3 DEX + Binance TradFi」。
  非 Hyperliquid、非 Binance 的永续场所暂不覆盖，此限制记在架构文档已知边界中。
- 合约级历史由本系统自采自存，因此可用的历史深度从系统上线之日起算。
- 订单簿深度同理需自采，滑点与流动性质量分析因此排在 P3。
