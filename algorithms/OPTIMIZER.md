# Conneverse Optimizer V2 — 设计定稿

> 单零件推荐。打分标准固定客观,用户只通过第 1 层(硬约束)和第 3 层(权重)影响结果。
> 常数按 7,809 个真实 eBay item 校准(2026-07)。

---

## 一、字段清单(现有的 key + 用在哪)

`Candidate` 上所有字段,以及每个字段在 V2 里的去向。图例:✅ 已用 / ⬜ 有数据但没用 / 💤 拿不到(恒空)。

| 字段 | 数据 | 用在哪 |
| ---- | ---- | ---- |
| `item_id` | ✅ | 标识,不打分 |
| `title` | ✅ | 标识,不打分 |
| `price` | ✅ 100% | **价格分**(landed) |
| `shipping_cost` | ✅ 94.8% | **价格分**(landed;缺失=None,不当免运) |
| `condition_id` | ✅ ~100% | **第1层**(7000排除 / require_new / allow_used) + **质量分·件况** |
| `condition`(字符串) | ✅ | 兼容保留;打分统一用 `condition_id` |
| `availability_status` | ✅ | **第1层**(在库门槛) |
| `available_qty` | ⬜ 79.8% | 未用(可留"仅剩 X 件"提示) |
| `sold_qty` | ✅ 100% | **质量分·热度** |
| `seller_feedback_pct` | ✅ ~100% | **第1层**(卖家好评率,最低线) + **质量分·seller** |
| `seller_feedback_count` | ✅ ~100% | **第1层**(卖家累计评价数,最低线);不进打分 |
| `top_rated` | ✅ 40% true | **质量分·seller**(主区分信号) |
| `delivery_days_max` | ✅ 91.2% | **第1层**(Rush 截止) + **速度分** |
| `delivery_days_min` | ⬜ | 未用(速度分只取 max) |
| `returns_accepted` | ✅ | **质量分·保障** |
| `return_period_days` | ✅ | **质量分·保障** |
| `warranty_years` | ✅ ~51% 可解析 | **质量分·保障** |
| `country` | ✅ | **第1层**(仅美国,Premium 启用) |
| `brand` | ⬜ | park(字段混杂:Unbranded/车厂名/店名混在一起,不可用) |
| `product_rating` | 💤 | 预留,恒 None |
| `product_review_count` | 💤 | 预留,恒 None |
| `fitment_complaint_rate` | 💤 | 预留,需爬评论+NLP |
| `fitment_review_sample` | 💤 | 预留,恒 None |
| `review_recency` | 💤 | 预留,恒 None |
| `is_self_hosted_rating` | 💤 | 预留,恒 False |
| `raw` | ✅ | 调试兜底 |

- `top_rated` 的 **40% true** 是取值分布(该字段人人都有),不是填充率。
- `warranty_years` 的 **~51% 可解析**:62.9% 有 warranty 字段,但含 "Yes/None/Other" 等非数字,真正能解析出年限的约 51%。
- 其余百分比都指**填充率**(有值的 item 占比)。

**打分实际用到的 10 个字段**:price、shipping_cost、condition_id、sold_qty、seller_feedback_pct、top_rated、delivery_days_max、warranty_years、returns_accepted、return_period_days。

---

## 二、三层结构

```
第 1 层  硬门槛 / 开关 (0/1)   →  候选 进 / 不进
第 2 层  系统打分 (0–100)      →  price / speed / quality 三个大分(标准固定)
第 3 层  用户权重 (百分比)      →  三个大分加权,排序
```

- **快捷入口**:选 preset(= 第 1 层开关 + 第 3 层权重 的打包)
- **高级入口**:自由组合第 1 层开关 + 第 3 层权重

---

## 三、第 2 层 · 打分算法【已定稿】

### 1. price

```
landed       = price + shipping_cost
anchor       = 次低landed  若 最低landed < 0.6 × 次低landed  否则 最低landed   (离群保护)
price_score  = clamp(100 × anchor / landed, 0, 100)
```

- 缺运费(5.2%,大件 freight)→ landed=None → 中性 50,不当锚
- price≤0 → 0;单候选 → 100

| 卖家 | 标价 | 运费 | landed | price_score |
| -- | -- | -- | -- | -- |
| A | 74 | 0 | 74 | 100 |
| B | 60 | 20 | 80 | 92.5 |
| C | 120 | 0 | 120 | 61.7 |
| D | 200 | 缺失 | — | 中性 50 |

### 2. speed

```
D            = delivery_days_max
speed_score  = clamp(100 × (D_slow − D) / (D_slow − D_fast), 0, 100)
默认 D_fast=2, D_slow=14
```

- 缺失(9%)→ 中性 50;急件由第 1 层 gate 滤掉

| 到货天数 | 4 | 6 | 11 | 无预估 |
| -- | -- | -- | -- | -- |
| speed_score | 83 | 67 | 25 | 中性 50 |

### 3. quality

```
quality_score = 0.50·seller + 0.25·condition + 0.15·assurance + 0.10·popularity
```

**① seller(50%)** — 好评率挤在 98.5–100,主区分靠 top_rated

```
seller = 60 + (好评率 ≥99.5→+20; 99–99.5→+10; 98–99→0; <98→−30) + (top_rated→+20)
```

**② condition(25%)** — 按 conditionId

| conditionId | 桶 | 分 |
| -- | -- | -- |
| 1000 | New | 100 |
| 1500 | New other | 90 |
| 2000–2999 | Reman/Refurb/Like New | 82 |
| 3000–5999 | Used | 75 |
| 6000 | Acceptable | 60 |
| None | Unknown | 50 |
| 7000 For parts | 第1层排除 | — |

**③ assurance(15%)**

```
warranty: Lifetime→100 / ≥3yr→80 / ≥1yr→60 / <1yr→40 / 明确无→30 / 缺失→50   × 0.6
returns:  ≥30天→100 / 接受(窗口未知)→60 / 否→0 / 缺失→50                       × 0.4
```

**④ popularity(10%)** — sold_qty 中位=0,故 0 当"未知"

```
sold_qty=0 → 50;  >0 → 50 + 50 × log10(1+sold)/log10(1+50),封顶 100
```

**缺任一子信号 → 该子分给中性 50。**

| | seller | condition | assurance | popularity | 质量分 |
| -- | -- | -- | -- | -- | -- |
| X:99.8%·Top·New·3yr+30天退货·售300 | 100 | 100 | 88 | 100 | 98 |
| Y:99.0%·非Top·Used·无保修·退货窗口未知·售0 | 70 | 75 | 54 | 50 | 67 |

---

## 四、第 1 层 · gate【草案,待锁】

| gate | 用的 key | 类型 |
| -- | -- | -- |
| For parts 排除 | condition_id==7000 | 固定,不可关 |
| 卖家最低线 | seller_feedback_pct / count | 固定(**好评率≥98% 且 评价数≥100**) |
| 在库门槛 | availability_status | preset 拨 |
| 只收新件 | condition_id (require_new) | preset 拨 |
| 允许二手 | condition_id (allow_used) | preset 拨 |
| 到货硬截止 | delivery_days_max | preset 拨(仅 Rush) |
| 仅美国货 | country | preset 拨(**仅 Premium**) |
| fitment | — | 休眠(无数据) |

**卖家最低线校准(v4 数据)**:仅作安全底线(挡近乎零记录的新号),不偏好大卖家。
- 评价数 <100 切底部 **4.6%**(旧值 50 只切 3.2%,太松;500 切 11.7%,太狠)
- 好评率 <98 切底部约 **5%**(旧值 95% 几乎切不到人)

---

## 五、第 3 层 · preset【草案,待锁】

| Preset | 场景 | gate | 权重 price/speed/quality |
| -- | -- | -- | -- |
| Rush 急件 | 今明两天必须到 | in_stock, max_delivery=3天 | 15 / 60 / 25 |
| Balanced 均衡(默认) | 常规采购 | in_stock | 35 / 30 / 35 |
| Budget 省钱 | 不急越便宜越好 | 允许 backorder + 允许二手 | 60 / 10 / 30 |
| Premium 优质 | 高端/严苛保险 | in_stock, require_new, **仅美国** | 15 / 25 / 60 |

**待确认**:Premium 的卖家门槛要不要单独抬高(比通用底线 98%/100 更严)。

---

## 六、数据校准记录(7,809 item)

| 字段 | 填充率 | 关键分布 |
| -- | -- | -- |
| price | 100% | 中位 $74,p90 $310 |
| shipping_cost | 94.8% | 免运 80.4%,非零中位 $20 |
| delivery_days_max | 91.2% | 天数中位 5 / p75 9 / p90 11 |
| sold_qty | 100% | 中位 0,p90 39,p95 105 |
| seller_feedback_pct | ~100% | 挤在 98.5–100(p5=98.6) |
| seller_feedback_count | ~100% | p5=116 / p10=397 / p25=1,816 / 中位=10,179 / p75=57,961;<50=3.2% <100=4.6% <500=11.7% |
| top_rated | — | 40% true |
| condition | ~100% | New 77% / Used 20% / New other 2% |
| warranty_years | 62.9%(可解析 ~51%) | 多为 1–3 年 |