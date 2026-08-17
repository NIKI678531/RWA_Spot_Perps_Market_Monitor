# 数据可视化规范

> `DESIGN.md` 是权威视觉规范，**本文件不得修改它**，也**不得引入 `DESIGN.md` 之外的新色值**。
> 本文只补 `DESIGN.md` 未覆盖的图表层。两者冲突时以 `DESIGN.md` 的 token 体系为准。
> 版式见 `UI-LAYOUT.md`；口径约束见根目录 `CLAUDE.md`「Chart rules」。

图表库：**ECharts**。主题通过一份集中的 theme 对象注入，组件内不写死任何色值。

---

## 1. 色板

### 1.1 分类色板（最多 9 系列）

全部取自 `DESIGN.md` front matter 中的既有 token，按可区分度排序：

| 序号 | Token | 值 |
|:--|:--|:--|
| 1 | `colors.primary` | `#2361AD` |
| 2 | `colors.cat-marketing` | `#722ED1` |
| 3 | `colors.cat-trading` | `#52C41A` |
| 4 | `colors.cat-pcs` | `#FAAD14` |
| 5 | `colors.cat-data` | `#1890FF` |
| 6 | `colors.error` | `#F5222D` |
| 7 | `colors.accent` | `#60A5FA` |
| 8 | `colors.primary-hover` | `#1A4E8A` |
| 9 | `colors.cat-system` | `#6B7280` |

**上限 9 系列是硬规则。** 超过 9 项时收敛为 **Top 8 + 「其他」**，
「其他」固定使用 `colors.text-disabled`（`#94A3B8`）。
人眼无法在一张图上可靠区分 12 种颜色；把长尾塞进图例只会让图更难读，不会让信息更多。

`cat-*` token 在 `DESIGN.md` 中承载业务分类语义，禁止在 badge 之外借用——
本规范中它们仅作为**图表系列色**使用，不得与 badge 的分类含义混淆，
因此图表系列色**必须始终配图例**，不得依赖「颜色即分类」的记忆。

### 1.2 序列色板（热力图、深度带）

由 `colors.primary-container` → `colors.primary` → `colors.primary-hover` 三点插值：

```
#E7EEF8 → #2361AD → #1A4E8A
```

### 1.3 涨跌与语义色

- 上涨 / 正向：`colors.success`（`#52C41A`）
- 下跌 / 负向：`colors.error`（`#F5222D`）
- 中性 / 无变化：`colors.text-secondary`

沿用 `DESIGN.md` 的语义色定义（绿=正、红=负，欧美惯例）。
这与中国内地市场「红涨绿跌」的惯例相反，因此**任何含涨跌着色的图表必须显示图例**，
不得依赖读者的直觉。

语义色仅用于数值符号，**不用于表示 `MetricScope`**——口径靠分图或分轴表达，不靠颜色。

### 1.4 未验证态

`Not verified` 使用 `colors.text-disabled`（`#94A3B8`）+ 45° 斜纹填充，**高度按该系列平均值渲染**，
并在 tooltip 中标注「未验证」。

**绝不渲染成 0 高度的柱**：零高度在视觉上等同于「观测到了零」，而未验证是「没有观测到」。

---

## 2. 图表类型选择

| 要回答的问题 | 用 | 禁止 |
|:--|:--|:--|
| 谁最大 / 排名 | 横向条形图（降序） | 饼图 |
| 随时间怎么变 | 折线图；单口径 | 面积堆叠（重叠类别） |
| 构成（**互斥**类别） | 堆叠条形 | — |
| 构成（**重叠**类别，如 CoinGecko 五类） | 并列条形 + 独立的「去重并集」条 | **饼图 / 堆叠图** |
| 两个口径的关系 | 双轴折线或散点 | 单轴双系列 |
| 场所 × 发行商矩阵 | 热力图 | 3D 柱 |
| 分布 | 箱线图 / 直方图 | 平均值单柱 |
| 订单簿深度 | 阶梯面积图 | 平滑曲线 |

### 2.1 重叠类别的硬规则

CoinGecko 的 Tokenized Stock / Tokenized ETF / Ondo / xStocks / bStocks 按构造互相重叠。
饼图与堆叠图的形状本身就在断言「各部分之和等于整体」，对重叠类别是错误陈述。

带 `is_additive = false` 的数据集：

- 前端拒绝渲染为饼图 / 堆叠图（在图表组件层校验，抛错而非静默降级）
- 必须并列展示「各类别」与「去重并集」两组数值
- 图例下方固定附一行说明：**类别之间存在重叠，仅并集行可作总量**

### 2.2 双轴规则

- 不同 `MetricScope` **禁止**共用一根 Y 轴。
- STOCK 与 FLOW 共图时必须双轴，且两根轴的标题都要写明口径全称
  （「现货成交额（24h，USD）」而非「成交额」）。
- 双轴图必须让两个系列使用**不同的图形类型**（例如柱 + 线），
  防止读者把两条同型曲线的交叉点误读为有意义的事件。
- 超过两个口径 → 拆图，不加第三根轴。

---

## 3. 轴、网格与坐标

| 元素 | 规范 |
|:--|:--|
| 轴线 | `colors.border`（35% alpha），1px |
| 网格线 | 仅水平方向，`colors.border` 的 50% 透明度，1px，虚线 `[3, 3]` |
| 轴标签 | `typography.label-sm`（12px），`colors.text-secondary` |
| 轴标题 | `typography.body-sm`（13px），`colors.text-secondary`，**必须含口径与单位** |
| 数值标签 | `typography.numeric`（Roboto Mono，`tnum`），`colors.text` |
| 零基线 | 条形图 Y 轴**必须**从 0 起。截断基线会放大差异，在金融图表中等同于误导 |
| 对数轴 | 允许，但必须在轴标题标注 `(log)`，且不与线性轴同图 |

金额缩写统一：`$1.2K / $34.5M / $6.7B`，保留 1 位小数，中英文界面一致。

---

## 4. 交互与动效

全部引用 `DESIGN.md` 的 `dur-* / ease-*` 词汇表，不自造：

| 交互 | 时长 / 曲线 |
|:--|:--|
| 图表入场 | `dur-base` (300ms) + `ease-emphasized`；多系列 stagger `60ms × i`，上限 600ms |
| tooltip 显隐 | `dur-fast` (200ms) + `ease-standard` |
| hover 高亮 | `dur-micro` (120ms) |
| 数据刷新过渡 | `dur-base`，形变过渡而非重绘（禁止状态突变） |
| 刷选 / 缩放 | `dur-fast` |

- **禁止循环动画**：无自动播放、无轮播、无常驻脉冲。
- `@media (prefers-reduced-motion: reduce)` 下全部动效降级为 0ms 直接呈现。
- 加载态使用骨架屏（图表区域为带 shimmer 的灰块），**不用 spinner**。

tooltip 使用 `components.popover` 的 token（`surface-strong` + `rounded.xl` + `padding 8px`），
内容必须包含：实体名、数值（`tnum`）、口径全称、`snapshot_ts`。未验证项标注「未验证」。

---

## 5. 无障碍

- 颜色不得作为唯一编码：折线同时用线型（实/虚/点划）区分，条形同时用直接数值标签。
- 文本与背景对比度 ≥ WCAG AA（4.5:1）。
- 每张图配一个可读的 `aria-label`，内容为「图表类型 + 度量 + 口径 + 时间范围」。
- 每张图提供「查看数据表」入口，输出与图表同源的表格。

---

## 6. 组件层校验

图表封装组件在渲染前执行下列断言，失败时抛错而非降级：

```ts
assertSingleScope(series)        // 单轴多系列必须同 MetricScope
assertSameAxis(scopes)           // STOCK 与 FLOW 不得共轴
assertAdditive(data, chartType)  // is_additive=false 时禁止 pie / stacked
assertSeriesLimit(series)        // 分类系列 ≤ 9，超出须先收敛为 Top 8 + 其他
```

后端 `app/core/metrics.py` 里的 `assert_same_axis()` 是同一规则的服务端实现。
两侧都做，是因为图表数据也可能来自前端本地推导。
