# Conneverse Optimizer — V3(当前权威版)

> 单零件推荐。打分标准固定客观;用户只通过第 1 层(硬门槛)和第 3 层(权重)影响结果。
> 常数按真实 eBay 数据校准:主校准集 7,809 item;warranty 门槛在 7,552 unique item(verified 3,809)上校准。
> V3 在已锁的 V2 三层结构上,收敛了门槛与前端。以本文件为准。

---

## V3 相对 V2 的变更摘要

1. **in-stock → 所有 preset 共享的固定门槛**。Budget 不再放行 backorder,四档都必须在库。
2. **件况(新/旧)不再进任何 preset 的默认门槛**。移除 Premium 的 `require_new`;新旧默认只影响质量分。件况**保留为独立可选开关(`require_new`),默认关**,谁要"只收全新"谁手动开。唯一与件况相关的**固定**硬排除是 `For parts (7000)`(件坏了装不了,属功能可用性,非新旧)。
3. **新增统一门槛:warranty ≥ 1 年(TOLERANT)**。所有 preset 共开;只踢"证明不达标"的,缺失/含糊放行。**不做里程**(实测收益为 0)。
4. **默认 preset = Budget**(原 Balanced)。
5. **前端只展示 Budget / Rush 两档**;Balanced / Premium 保留在后端(prewarm 仍算、历史可回放),不在选择器露出。

> 所有 preset 现在**共享同一套固定门槛**,四档只在**权重**上不同。

---

## 一、三层结构

```
第 1 层  硬门槛 / 开关 (0/1)   →  候选 进 / 不进
第 2 层  系统打分 (0–100)      →  price / speed / quality 三个大分(标准固定)
第 3 层  用户权重 (百分比)      →  三个大分加权,排序
```

- **快捷入口**:选 preset(= 第 1 层门槛 + 第 3 层权重 的打包)。V3 前端只露 Budget / Rush,默认 Budget。
- **高级入口**:自由组合门槛 + 权重。
- **独立可选开关**("仅美国"、"X 天硬截止"、"只收全新")不属于任何 preset,默认关,谁需要谁叠加。

---

## 二、第 1 层 · gate【V3】

| gate | 用的 key | 类型 |
| -- | -- | -- |
| For parts 排除 | condition_id==7000 | 固定,不可关(件坏了,与新/旧无关) |
| 卖家最低线 | seller_feedback_pct / count | 固定(好评率≥98% 且 评价数≥100) |
| 在库门槛 | availability_status | **固定,所有 preset 共开**(不接受缺货/backorder) |
| **warranty 最低线** | **warranty_months / warranty_none** | **固定,所有 preset 共开(≥12mo,TOLERANT)** |
| 到货硬截止 | delivery_days_max | 独立可选开关,默认关 |
| 仅美国货 | country | 独立可选开关,不绑 preset,默认关 |
| **件况(只收全新)** | **condition_id(require_new)** | **独立可选开关,不绑 preset,默认关**(开启后 New/New other 之外→砍) |
| fitment | — | 休眠(无数据) |

### 件况处理原则
件况(新/旧)**默认不进任何 preset 的门槛**。"偏新"默认完全交给件况分(New=100 > Used=75)自然降权,任何 preset 都不硬排除二手——避免二手主导品类返空。
但件况**保留为独立可选开关 `require_new`**(默认关):高级入口里谁要"只收全新",打开后非 New/New other 的件被硬排除。这跟"仅美国""X天硬截止"是同一类**可选约束**——不写进任何 preset,按需叠加。
无论开关如何,`For parts (7000)` 永远固定排除(那是"件坏了",非新旧)。

### 在库门槛
`require_in_stock` 所有 preset 固定共开;报保险采购不接受缺货/backorder。

### warranty 门槛(V3 新增,TOLERANT)
- **规则**:命中任一即砍——(a) 卖家**明确声明无保修**(值 None / No / No Warranty);(b) 解析出**时长 < 12 个月**。缺失 / 含糊(Yes/Other/Unspecified) / ≥12mo / Lifetime → **放行**。
- **纯时间**,不解析里程:含里程的取值 381/382 都同时带时间,唯一 mileage-only 是 10,000 mile(够不到门槛),里程 OR 分支收益为 0,不做。
- **解析器排除退货污染**:warranty 原值命中 `return` 关键字(如 "30 Days Return Accepted")当作非保修(→缺失/放行),不误解析成 30 天。
- **门槛 vs 打分分工**:门槛只踢"证明不达标"的;"偏好长保修"由质量分·assurance 表达(缺失→50、明确无→30、≥1年→60、Lifetime→100)。没填保修的件不被踢、但也不加分,自然靠后。
- **为什么 TOLERANT 而非 STRICT**:warranty 是卖家可选自填 aspect,填充率仅 ~62%;STRICT(缺失即砍)会让 verified 只剩 49.5%,且砍掉的 82% 是"没填"而非"保修差",易返空。TOLERANT 留 verified 88.5%,砍的基本是真·短/无保修。

**卖家最低线校准**:统一 98% / 100,仅作安全底线。评价数<100 切底部 4.6%;好评率<98 切底部约 5%。

---

## 三、第 2 层 · 打分算法【V2 已锁,不变】

`quality = 0.50·seller + 0.25·condition + 0.15·assurance + 0.10·popularity`

- **price**:`landed=price+shipping_cost`;`anchor=次低landed`(仅当有价候选≥3 且 最低<0.6×次低)否则最低;`price_score=clamp(100×anchor/landed,0,100)`;缺运费→中性50。
- **speed**:`clamp(100×(D_slow−D)/(D_slow−D_fast),0,100)`,D_fast=2 / D_slow=14;缺失→50。
- **seller**:`60 + (好评率 ≥99.5→+20;99–99.5→+10;98–99→0;<98→−30) + (top_rated→+20)`。
- **condition**(按 conditionId):1000 New=100 / 1500 New other=90 / 2000–2999 Reman=82 / 3000–5999 Used=75 / 6000 Acceptable=60 / None=50 / 7000 门槛排除。
- **assurance**:`0.6·warranty(Lifetime100/≥3yr80/≥1yr60/<1yr40/明确无30/缺失50) + 0.4·returns(≥30天100/接受60/否0/缺失50)`。
- **popularity**:`sold=0→50;>0→50+50×log10(1+sold)/log10(1+50)`,封顶100。
- 缺任一子信号 → 该子分中性 50。

---

## 四、第 3 层 · preset【V3】

| Preset | 场景 | 权重 price/speed/quality | 前端展示 |
| -- | -- | -- | -- |
| **Budget 省钱(默认)** | 越便宜越好(含运费) | 60 / 10 / 30 | ✅ 展示 |
| Rush 急件 | 要快,优先最快到货 | 15 / 60 / 25 | ✅ 展示 |
| Balanced 均衡 | 常规采购 | 35 / 30 / 35 | ⬜ 后端保留,暂不展示 |
| Premium 优质 | 高端/严苛保险 | 15 / 25 / 60 | ⬜ 后端保留,暂不展示 |

- 四档**共享同一套固定门槛**:For parts 排除、卖家 98%/100、在库、warranty ≥12mo(TOLERANT)。只在**权重**上不同。
- 前端只暴露 Budget / Rush,默认 Budget;Balanced / Premium 仍在后端计算(prewarm 四档全算、历史可回放),不在选择器露出。
- Premium 的 `require_new` 已移除,二手件任何档都能进(件况分低、自然靠后)。
- "仅美国""X天硬截止""只收全新(require_new)"是独立可选开关,默认关,任何 preset 可叠加,不写死在 preset 里。

---

## 五、warranty 校准数据(7,552 unique item;verified 3,809)

来源:localizedAspects 的 Manufacturer Warranty 优先、回退 Warranty(命中 4,261 / 回退 444 / 皆无 2,847)。

| bucket | 全集 7,552 | verified 3,809 |
| -- | -- | -- |
| Lifetime | 4.3% | 4.2% |
| ≥3yr | 6.5% | 4.9% |
| 1–3yr | 35.3% | 40.4% |
| <1yr(砍) | 10.4% | 9.1% |
| mileage-only | 0.0%(1条) | 0.0% |
| 含糊文本(放行) | 5.7% | 4.6% |
| 缺失(放行) | 37.7% | 36.7% |
| 明确无 None/No(砍) | 197条 | 90条 |

**门槛存活**:TOLERANT(明确无→砍)全集 86.9% / **verified 88.5%**;STRICT 对比 verified 仅 49.5%(不采用)。
里程:含里程取值 382 条(5.1%),mileage-only 仅 1 条且 <12,000 → OR ≥12,000 mile 分支收益为 0,不做。

---

## 六、落地状态(V3)

- **算法(matcher)**:
  - presets:四档全部 `require_in_stock=True` + `require_warranty=True`;移除 Premium `require_new`(件况默认不 gate)。
  - gates.py:新增 `_gate_warranty`(≥12mo,TOLERANT,含退货污染排除);**保留 `require_new` 作为可选门槛,默认 `False`,不被任何 preset 设置**(仅高级/手动开关触发);保留 For-parts 7000、in-stock、seller 门槛。
  - service.py:`DEFAULT_PRESET = "Budget"`。
  - 解析器:产出 `warranty_months` / `warranty_none`,`return` 关键字排除。
  - 测试:补 warranty gate 用例(明确无/短保修→砍;缺失/含糊→放行;退货污染→放行);补 `require_new` 开关用例(默认关时 Used 放行;开启后 Used→砍、New 放行、For-parts 恒砍)。
- **前端(demo)**:
  - `SHOWN_PRESETS = ["Budget","Rush"]` 驱动选择器;VALID_PRESETS(8名)后端接收不变。
  - 默认选中 Budget;`PartLine.selectedPreset @default("Budget")`(迁移已应用)。
  - pill/chip 在展示层按 SHOWN 过滤;Best overall = Budget & Rush 双 Top1。
  - 遗留 PartLine 老名归一化(sameDayJob→Rush;Balanced/Premium→回落 Budget)。
  - "只收全新"开关暂在高级入口(前端尚未暴露 UI,先留后端能力)。
- **数据**:上线给外部用户前清空 `MatchSearch / Candidate / OptimizerResult` 三表,使库内只剩新规则结果。
- **DB model**:V3 仅 `PartLine.selectedPreset` 列默认值 Balanced→Budget 一处 schema 变更;OptimizerResult / Candidate 结构不变。