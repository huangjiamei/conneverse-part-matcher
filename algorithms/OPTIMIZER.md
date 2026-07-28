```markdown
# Conneverse Optimizer New

> 针对**单零件推荐**范围。打分标准固定客观,用户只通过第 1 层(硬约束)和第 3 层(权重)影响结果。

## 一、算法三层结构

```
第 1 层  硬门槛 / 开关 (0/1)   →  候选 进 / 不进
第 2 层  系统打分 (0–100)      →  每个大分客观打分(标准固定,不随用户变)
第 3 层  用户权重 (百分比)      →  各大分占多少,加权出总排名
```

- **快捷入口**:提供多个presets(= 第 1 层开关 + 第 3 层权重 的打包)
- **高级入口**:用户自由组合第 1 层开关 + 第 3 层权重(先支持"一次性覆盖")
- 两个入口都跑同一套第 2 层打分


### 第 1 层 · 硬门槛 / 开关(决定"进不进")

| key                     | 作用                                       | 类型          |
| ----------------------- | ---------------------------------------- | ----------- |
| `availability_status`   | 能不能等 backorder(在库开关)                     | 用户开关(默认在库)  |
| `condition`             | 要不要只收新件(require_new)                     | 用户开关(默认只新件) |
| `seller_feedback_pct`   | 通用最低信誉线                                  | 固定门槛        |
| `seller_feedback_count` | 通用最低评价数线                                 | 固定门槛        |
| `delivery_days_min/max` | "X 天内必须到"硬截止;急件下没有到货预估的也一并过滤(设了才启用) | 可选门槛        |
| `country`               | "仅美国货"合规要求                               | 可选开关(默认关)   |


### 第 2 层 · 打分(每个大分 0–100)

| 大分      | key                                                                                                                                       |
| ------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| **价格分** | `price`、`shipping_cost`                                                                                                                   |
| **速度分** | `delivery_days_min`、`delivery_days_max`                                                                                                   |
| **质量分** | `seller_feedback_pct`、`seller_feedback_count`、`top_rated`、`warranty_years`、`returns_accepted`、`return_period_days`、`condition`、`sold_qty` |


### 第 3 层 · 权重(不消费候选字段)

| 参数               | 说明    |
| ---------------- | ----- |
| `weight_price`   | 价格分占比 |
| `weight_speed`   | 速度分占比 |
| `weight_quality` | 质量分占比 |


---

## 二、算法数据说明

### 1. 跨层的 key(既在第 1 层又在第 2 层)

先卡下限、线上再比高低,不是重复:

- `condition` → 第 1 层(可选 require_new)+ 第 2 层(件况分,New / New other / Used 递减扣分)
- `seller_feedback_pct` / `seller_feedback_count` → 第 1 层(最低线)+ 第 2 层(信誉分)
- `delivery_days_min/max` → 第 1 层(硬截止,可选)+ 第 2 层(速度分)

### 2. 不参与三层的 2 个字段

- `available_qty` → 可选"仅剩 X 件"提示,先不用(单件采购下无打分意义)
- `brand` → (现在brand有的存储的是店铺名称)

### 3. 预留口子(旧算法考虑过、现在 eBay 拿不到的打分数据)

| key                      | 原打算衡量        | 现状                    |
| ------------------------ | ------------ | --------------------- |
| `product_rating`         | 产品星级(0–5)     | 恒 None,eBay 只给卖家级信誉   |
| `product_review_count`   | 产品评价数        | 恒 None                |
| `fitment_complaint_rate` | 装车抱怨率(0–1)    | 恒 None,需爬评论 + NLP     |
| `fitment_review_sample`  | 抱怨率样本量       | 恒 None                |
| `review_recency`         | 评论时效性(0–100) | 恒 None                |
| `is_self_hosted_rating`  | 是否卖家自站评论     | 恒 False,eBay 全平台评论    |


---


## 三、打分算法

> 常数均按 7,809 个真实 eBay item 校准(2026-07)。每个大分 0–100,标准固定,不随 preset 变。

### 1. price

```
landed       = price + shipping_cost                          # 到手总价
anchor       = 次低landed   若 最低landed < 0.6 × 次低landed     # 离群保护
             = 最低landed   否则
price_score  = clamp(100 × anchor / landed, 0, 100)           # 越便宜越高,最便宜=100
```

- 数据:80.4% 免运(landed=标价);非零运费中位 $20、p90 $160(大件 freight)
- **缺运费处理**:5.2% 的件无 shippingOptions(freight/自提)→ 运费**按未知,不当 0**,不让它当便宜锚(否则大件假装便宜)
- 边界:price≤0 → 0 分沉底;单候选 → 100

**例子**

4 个卖家(D 是大件、无 shippingOptions):

| 卖家 | 标价 | 运费 | landed | price_score |
| -- | -- | ---- | ------ | ----------- |
| A  | 74 | 0    | 74     | 100×74÷74 = **100** |
| B  | 60 | 20   | 80     | 100×74÷80 = **92.5** |
| C  | 120| 0    | 120    | 100×74÷120 = **61.7** |
| D  | 200| 缺失 | —      | **中性 50**(不当便宜锚) |

最低 landed=74,次低 80,74 > 0.6×80 → 无离群,anchor=74。注意 B 标价最低($60)但含运费后并不便宜。


### 2. speed

```
D            = delivery_days_max                              # 保守取较晚界
speed_score  = clamp(100 × (D_slow − D) / (D_slow − D_fast), 0, 100)
默认: D_fast = 2 天,  D_slow = 14 天
```

- 数据:91.2% 有到货预估 → 速度分成立;到货天数 中位 5 / p75 9 / p90 11 / max 74(backorder 长尾)
- 校准:D_slow=14 → 5天=75、9天=42、11天=25、≥14天=0(长尾归零)
- **缺失(9%)**:不急 → 中性 50;急件由第 1 层 gate 滤掉。急件可把 D_slow 压到 ~7 让排序更偏快

**例子(D_fast=2, D_slow=14)**

| 卖家 | 到货天数 | speed_score |
| -- | ---- | ----------- |
| A  | 4 天  | (14−4)÷12 = **83** |
| B  | 6 天  | (14−6)÷12 = **67** |
| C  | 11 天 | (14−11)÷12 = **25** |
| D  | 无预估 | **中性 50**(急件则被 gate 滤掉) |


### 3. quality

```
quality_score = 0.50·seller + 0.25·condition + 0.15·assurance + 0.10·popularity
```

**① seller(50%)** — 好评率几乎无区分度(挤在 98.5–100),主区分靠 top_rated(40% 为 true)
```
seller = 60
       + (好评率 ≥99.5 → +20;  99–99.5 → +10;  98–99 → 0;  <98 → −30)
       + (top_rated → +20)
       clamp[0,100]
```

**② condition(25%)** — 按 conditionId 映射(见上表)。数据:New 76.6% / Used 20.1%

**③ assurance(15%)**
```
warranty: Lifetime→100 / ≥3yr→80 / ≥1yr→60 / <1yr→40 / 明确无→30 / 缺失·无法解析→50   占 0.6
returns:  ≥30天→100 / 接受但窗口未知→60 / 不接受→0 / 缺失→50                          占 0.4
assurance = 0.6×warranty + 0.4×returns
```
- 数据:约 51% 的件能解析出保修年限;多为 1–3 年

**④ popularity(10%)** — sold_qty 中位=0,故 0 当"未知"不当"最差"
```
sold_qty = 0  → 50(未知/冷门,中性)
sold_qty > 0  → 从 50 对数上升到 100,约 50 件封顶
```
- 数据:半数件 sold_qty=0,长尾 p90=39 / p95=105 → 饱和阈值 ~50

**缺任一子信号 → 该子分给中性 50。**

**例子**

| | seller | condition | assurance | popularity | **质量分** |
|--|--|--|--|--|--|
| **X**:99.8%·TopRated·New·3年保修+30天退货·售300 | 100 | 100 | 88 | 100 | **98** |
| **Y**:99.0%·非Top·Used·无保修·退货窗口未知·售0 | 70 | 75 | 54 | 50 | **67** |

- **X** 算式:0.50×100 + 0.25×100 + 0.15×88 + 0.10×100 = **98.2**
- **Y** 算式:0.50×70 + 0.25×75 + 0.15×54 + 0.10×50 = **66.8**
- 子分拆解看得出:X 的 seller 满分(top_rated 顶上去),Y 是二手中端卖家但**没被打死**(67 分),符合"Used 可接受不淘汰"的设计
```

