# Conneverse Optimizer 完整参考

候选状态、Optimizer 参数、Preset 组合的完整参考文档。

---

## 一、算法架构（三层）

```
┌─ Layer 3: preset ─────────────────────────────┐
│  4 个场景组合 (sameDayJob / costFirst / ...)   │
│  = GatesConfig + ScoringConfig + Weights      │
│  背后就是把 layer 1/2 的所有参数打包起来        │
└────────────────────────────────────────────────┘
              ↓ 参数注入
┌─ Layer 2: config ──────────────────────────────┐
│  GatesConfig (6 个 gate 参数)                  │
│  ScoringConfig (8 个 bonus + 先验)             │
│  Weights (price/quality 权重)                  │
│  16 个参数, 都是命名字段, 可覆盖               │
└────────────────────────────────────────────────┘
              ↓ 被 optimize() 消费
┌─ Layer 1: 函数 ────────────────────────────────┐
│  gate_check(candidate, GatesConfig)            │
│  quality_score(candidate, ScoringConfig)       │
│  price_score(candidate, min_eligible_price)    │
│  optimize(candidates, preset OR configs OR weights)
└────────────────────────────────────────────────┘

```

**关键理解**：Preset 不是独立逻辑，只是 Layer 2 的一份参数组合。同一个 `optimize()` 函数既可以接 preset（Layer 3），也可以直接接 config（Layer 2），这让用户主 UI（选 preset）和 Debug UI（改具体参数）**共用同一套后端**。

---

## 二、候选状态的完整可能性

一条候选从 matcher 出来到最终显示在卡片上，取决于两个独立判定。

### 判定 1: matcher 的 label

由 matcher pipeline 决定，跟 optimizer 无关。


| label  | UI 显示          | 含义                                                   |
| ------ | -------------- | ---------------------------------------------------- |
| `1`    | ✓ **Verified** | matcher 认为 MPN 精确匹配（EXACT_MPN_MATCH 等）               |
| `0`    | **Rejected**   | matcher 认为不匹配（NOISY_NEGATIVE / NGRAM_FITMENT_REJECT） |
| `null` | **Uncertain**  | matcher 判不了（MPN 空 / n-gram 走 review）                 |


**只有** `label=1` **的候选才会进 optimizer 打分。** 其他两类展示在"其他候选"折叠区里。

### 判定 2: optimizer 的处理结果（仅对 label=1）

label=1 的候选进 optimizer 后，只有两种结局：


| 结局           | UI 显示                              | 含义                         |
| ------------ | ---------------------------------- | -------------------------- |
| 通过 gate + 排名 | **Rank N** badge（Rank 1 加 Award ⭐） | 显示 optimizer 排序、总分、价格分、质量分 |
| 被 gate 拒     | **Filtered** badge + reason        | 显示被哪个 gate 拒的原因            |


---

## 三、状态组合矩阵

真实卡片上会看到的状态组合：


| Verified | Rank               | Filtered                | 含义                                      |
| -------- | ------------------ | ----------------------- | --------------------------------------- |
| ✓        | **Rank 1** (Award) | ✗                       | Top pick：MPN 匹配，optimizer 排第一           |
| ✓        | Rank 2-N           | ✗                       | Verified 候选中的次选                         |
| ✓        | ✗                  | ⚠ Filtered              | Verified 但被 gate 拒（Used / CN 卖家 / 差评卖家） |
| ✗        | ✗                  | ✗ (显示 Uncertain)        | matcher 判不了，未走 optimizer                |
| ✗        | ✗                  | ✗ (显示 Rejected/label=0) | matcher 判为不匹配，未走 optimizer              |


---

## 四、Layer 2 参数详解

Optimizer 内部对 label=1 的候选做的判定，全部由这 16 个参数控制。

### GatesConfig — 6 个 gate 参数

每个 gate 单独判定。任何一个不通过 → **Filtered**，reason 就是那个 gate 的失败原因。


| #   | 参数                           | 类型          | 触发的 Filter reason       | 备注                                   |
| --- | ---------------------------- | ----------- | ----------------------- | ------------------------------------ |
| 1   | `allow_used`                 | bool        | `condition:used`        | 允许二手件进 optimizer                     |
| 2   | `require_in_stock`           | bool        | `stock:out_of_stock`    | 缺货直接拒                                |
| 3   | `min_seller_feedback_pct`    | float 0-100 | `seller_feedback:88.5%` | 卖家好评率低于阈值                            |
| 4   | `min_seller_feedback_count`  | int         | `seller_count:96`       | 卖家累计评价数低于阈值                          |
| 5   | `require_domestic`           | bool        | `country:CN`            | 只允许 US 卖家                            |
| 6   | `max_fitment_complaint_rate` | float 0-1   | `fitment_risk:25%`      | 差评里 fitment 问题率上限（eBay 拿不到该数据，实际不启用） |


### ScoringConfig — 8 个 scoring 参数

Gate 通过的候选按这些参数打质量分（0-100）。


| #   | 参数                        | 默认   | 影响                       |
| --- | ------------------------- | ---- | ------------------------ |
| 7   | `seller_pct_prior`        | 98.0 | Bayesian 收缩的先验值          |
| 8   | `seller_pct_pseudo`       | 1000 | 先验 pseudo-count（越大越拉向先验） |
| 9   | `warranty_year_1_bonus`   | +5   | ≥1 年保修加分                 |
| 10  | `warranty_year_3_bonus`   | +10  | ≥3 年 追加（累计 +15）          |
| 11  | `warranty_lifetime_bonus` | +15  | Lifetime 追加（累计 +30）      |
| 12  | `top_rated_bonus`         | +5   | eBay Top Rated Seller    |
| 13  | `returns_bonus`           | +5   | 支持退货且窗口 ≥30 天            |
| 14  | `sold_qty_bonus`          | +5   | 累计销量 ≥100                |


### Weights — 2 个权重参数

Gate 通过的候选做最终排名：

```
total = w_price × price_score + w_quality × quality_score

```


| #   | 参数                | 说明    |
| --- | ----------------- | ----- |
| 15  | `weights.price`   | 价格分权重 |
| 16  | `weights.quality` | 质量分权重 |


内部会归一化：`w_price / (price + quality) + w_quality / (price + quality) = 1.0`

---

## 五、Preset 与参数的映射

四个 preset 就是把上面 16 个参数按场景配好。**未列出的参数用默认值。**


| 参数                           | sameDayJob | costFirst | qualityFirst | scheduled |
| ---------------------------- | ---------- | --------- | ------------ | --------- |
| `allow_used`                 | false      | false     | false        | false     |
| `require_in_stock`           | true       | true      | true         | **false** |
| `min_seller_feedback_pct`    | **97**     | 95        | **98**       | 95        |
| `min_seller_feedback_count`  | **100**    | 50        | **500**      | 50        |
| `require_domestic`           | **true**   | false     | **true**     | false     |
| `max_fitment_complaint_rate` | 0.15       | 0.25      | 0.10         | 0.15      |
| `weights.price`              | 40         | **80**    | **15**       | 55        |
| `weights.quality`            | 60         | 20        | **85**       | 45        |


Scoring 里的 8 个 bonus 参数所有 preset 都用默认，暂时没差别。

---

## 六、场景与用户意图对照


| Preset           | 用户场景         | 结果特点                                  |
| ---------------- | ------------ | ------------------------------------- |
| **sameDayJob**   | 车在架子上今天要修完   | 严过滤：拒 CN / 拒小卖家 / seller 卡 97%；质量优先   |
| **costFirst**    | 客户不急，越便宜越好   | 宽过滤：CN 也接受；价格权重 80/20                 |
| **qualityFirst** | 高端客户 / 大保险公司 | 最严过滤：seller 卡 98% × 500 好评；质量权重 85/15 |
| **scheduled**    | 计划采购，可以等     | 允许缺货（backorder），CN 也可以，价格质量均衡         |


---

## 七、Filter reason 完整语义参考

用户看到 `Filtered: xxx` 时如何理解：


| Filter reason         | 意思           | 用户可能的响应                                          |
| --------------------- | ------------ | ------------------------------------------------ |
| `condition:used`      | 二手件被拒        | 切 preset 也没用（4 个 preset 都拒 Used）；未来可加"接受 Used"开关 |
| `stock:out_of_stock`  | 缺货           | 切到 `scheduled` preset 可以接受 backorder             |
| `seller_feedback:XX%` | 卖家好评率低       | 换到 `costFirst` 或 `scheduled`（放宽到 95%）            |
| `seller_count:N`      | 卖家评价数少       | 除 `qualityFirst` 都是 50 或 100，看具体数字               |
| `country:CN`          | 非美国卖家        | 换到 `costFirst` 或 `scheduled`（允许海外）               |
| `fitment_risk:XX%`    | fitment 抱怨率高 | 目前不启用                                            |


---

## 八、`optimize()` 参数注入的三种方式

Layer 1 的 `optimize()` 函数接口：

```python
optimize(
    candidates,
    preset=None,           # 传 Preset 或 preset name, 从 Layer 3 打包一次
    gates=None,            # 覆盖 preset 里的 gates 
    scoring=None,          # 覆盖 preset 里的 scoring
    weights_price=None,    # 覆盖 preset 里的 weights.price
    weights_quality=None,  # 覆盖 preset 里的 weights.quality
)

```

**三种用法**：


| 场景                       | 用法                                                                          |
| ------------------------ | --------------------------------------------------------------------------- |
| **用户主页选 preset**         | `optimize(cands, preset="sameDayJob")`                                      |
| **Debug 页从 preset 出发微调** | `optimize(cands, preset="sameDayJob", gates=my_gates)`                      |
| **Debug 页从零构造**          | `optimize(cands, gates=g, scoring=s, weights_price=40, weights_quality=60)` |


这个设计让 preset 和显式参数**天然共存**——preset 兜底，任何一层可以被覆盖。方案 A 的 `/api/rerank` 接口可以直接支持这三种，前端传什么后端跑什么。

---

## 九、切换 preset 的开销

**不需要重调 eBay，也不需要新建 MatchSearch 记录。**

原因：optimizer 是纯函数——candidate 原始数据（从 eBay 抓来的 seller / condition / country 等）已经存在 `MatchSearch.rawResponse` 里。切换 preset 只是用不同参数**重新跑一次 optimizer**，同一份候选数据换视角。

```
[eBay API] → matcher → 拿到候选原始数据
                        ↓
                  MatchSearch.rawResponse (JSON, 已存)
                        ↓
                  Candidate 表 (每条 + label + optimizer 结果)
                        ↓
              [切换 preset] 从 rawResponse 拿原始数据
                        ↓
                  重跑 optimizer, 得到新的 rank / filtered
                        ↓
              [不落库] 直接返回给前端展示

```

**为什么不落库**：同一个 PartLine 每次切 preset 都新建 MatchSearch 会污染数据；用户切 preset 是"视角切换"，不是"新搜索"。

**只有原始搜索（点 Search eBay）才写库。** 切 preset 只在返回体里用不同参数重算。