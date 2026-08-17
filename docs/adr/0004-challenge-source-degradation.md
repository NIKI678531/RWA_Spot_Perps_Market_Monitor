# CHALLENGE 类源不自动采集，降级为方法论参照

`hyperscreener.asxn.xyz` 曾被列为新增数据源。实测其全部接口返回
`403 VERIFICATION_REQUIRED`，要求由 Cloudflare Turnstile 颁发的 `X-Verification-Token`；
`curl-cffi` 的 `chrome124` / `chrome120` / `safari17_0` 三种 TLS 指纹伪装均被拒。
同时，该站的流动性对比页覆盖的是 BTC / ETH / SOL，而本系统的范围已确定为严格 RWA
——即便能取到，数据本身也在范围外。

决定：引入 `auth_mode = CHALLENGE` 作为源注册表的一等属性，
并把 ASXN 标记为 `status = REFERENCE_ONLY`：不调度、不采集，仅采用其滑点档位与深度带的
**指标定义**，数据由 Hyperliquid `l2Book` 自行计算。

## Considered Options

- **人工注入 token。** 被否：Turnstile 令牌有效期以分钟计，人工注入无法支撑定时采集。
- **无头浏览器（Playwright）解验证。** 被否：生产 K8s 不提供 PVC，浏览器镜像体积与
  运行时状态都与该约束冲突；且此类方案对上游风控变更极度脆弱，维护成本长期存在。
- **从注册表中删除该源。** 被否：删掉之后没有任何记录说明它为何不在，
  后续开发会重新花时间探测同一堵墙。

## Consequences

- `REFERENCE_ONLY` 成为一种常规状态而非例外：任何被评估后决定不采集的源都留在注册表中，
  带着它被拒的原因。
- 滑点与深度指标改由自采数据计算，因此依赖 `l2Book` 历史的积累，排期在 P3。
