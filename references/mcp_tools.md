# 麦当劳 MCP Tool 速查与实测返回

- 官方文档：<https://github.com/M-China/mcd-mcp-server>
- 端点：`https://mcp.mcd.cn`（Streamable HTTP）
- 认证：`Authorization: Bearer ${MCD_MCP_TOKEN}`
- 限流：600 次/分钟（超限返回 429；鉴权失败返回 401）

---

## 一、Skill 实际调用的工具（按链路顺序）

### 1. `now-time-info`

- 入参：无
- 返回字段：`timestamp`、`formatted`（`yyyy-MM-dd HH:mm:ss`）、`date`、`dayOfWeek`、`timezone`（`GMT+08:00`）、`offset`
- 用途：作为「明天 / 下次 / 待定」等相对时间的基准。

### 2. `query-nearby-stores`

- 入参：`beType=1`、`searchType=2`、`city`、`keyword`
- 关键返回字段：
  - `storeCode`、`storeName`、`address`、`distance`
  - `businessStatus`、`businessStartTime`、`businessEndTime`
  - `reservation`（是否支持预约）
  - `reservationTimeOptions[]`：`{date, today, reservationOptionText}`，文本形如
    `早餐(06:14至10:15)，午餐(10:44至14:15)，下午茶(14:44至16:45)，夜市(17:14至21:45)`
- ⚠️ 工具描述硬性要求：`reservationTimeOptions` 每一条都必须完整展示给用户，**严禁省略**。
- 实测：餐段之间有约 **29 分钟空档**（10:15→10:44、14:15→14:44、16:45→17:14），即厨房换餐窗口。

### 3. `query-meals`

- 入参：`storeCode`、`orderType=1`、`beType=1`、`reservationDate="yyyy-MM-dd HH:mm"`
- 返回：`categories[]`（分类名 + code + tags）与 `meals{code: {name, image, currentPrice, originalPrice, discountType, canWithOrder}}`
- 实测：传 `2026-10-12 08:00` 返回**早餐菜单**（麦满分系列、厚松饼堡系列、早安营养卷、炒双蛋堡系列、一早现磨、小食甜品/其他、饮品等），**不包含** 巨无霸 / 麦辣鸡腿堡 / 板烧鸡腿堡等正餐产品。
- → 证实「时间 → 餐段菜单」正向链路，且为 MCP 原生支持。

### 4. `query-meal-detail`

- 入参：`code`（餐品编码）
- 返回：套餐组成（单品列表）与**可替换项**（如 中杯可乐 → 无糖可乐、小薯 → 玉米杯）
- 用途：把「组合套餐」拆成「营养单品」以累加营养；并做替换优化。这是「编排」而非「调用」的关键证明。

### 5. `list-nutrition-foods`

- 入参：无
- 返回：`data` 为**字符串文本表格**（非 JSON），共 **160 条**：
  ```
  [160]{productName,nutritionDescription,energyKj,energyKcal,protein,fat,carbohydrate,sodium,calcium}:
    猪柳麦满分,null,1288,308,16,16,24,781,213
    巨无霸,null,2146,513,27,26,42,961,171
  ```
- 用途：营养目标约束的数据源。需由 `scripts/parse_nutrition.py` 解析为 JSON 索引。

### 6. `query-store-coupons`

- 入参：`storeCode`、`orderType`、`beType`；**预约场景必传** `reservationDate`（`yyyy-MM-dd HH:mm`）
- 返回：**当前门店 + 当前订单**实际可用的券（MCP 已代做门店 / 渠道 / 配送方式校验）
- 字段：`title` / `couponId` / `couponCode` / `tradeDateTime`（**有效期**）/
  `products[].productName` / `products[].productCode`
- ✅ 优于 `available-coupons`：后者只返回标题 / 状态 / 图片，**无有效期字段**。
- 🔑 **关键**：`products[].productCode` 与 `query-meals` 的餐品编码**同属一个编码空间**，
  因此「券 → 方案」可做确定性匹配（`scripts/apply_benefits.py::match_coupons`）。
- ⚠️ **边界**：返回字段里**没有优惠面额**。券后价必须由 `calculate-price` 给出，不得估算。
- 实测：演示账号返回 `data: []`（见 `tests/fixtures/coupons_real_empty.json`），必须优雅降级。

### 7. `calculate-price`

- 入参：餐品 + 优惠券信息
- 返回：应付总价（含券后价）
- ⚠️ 这是**唯一权威的金额来源**。券接口不含面额，任何未经此接口的价格都是编造。

### 8. `campaign-calendar`

- 入参：`specifiedDate`（`yyyy-MM-dd`）
- 返回：当日及邻近日期活动列表，**按日期分组**；活动含**起止日期**与**精确到分钟的开售时刻**
  （如「10月8日 10:30 正式开售」）
- 🔑 **关键**：活动是按日聚合的，数量差异极大（实测 10-11 有 12 个、10-12 仅 2 个、10-13 仅 1 个）。
  判断某活动当天是否适用，**必须以它是否出现在预约日那一组里为第一判据**，
  再看活动窗口与餐段是否匹配（`scripts/apply_benefits.py::match_campaigns`）。
- 用途：与推荐的预约时段做交叉，输出**单行**活动提示。不做活动浏览器。

### 9. `create-order`

- 入参：门店、预约时间、餐品、优惠券等
- ⚠️ **仅在用户显式确认方案后调用**，任何情况下不自动下单。

---

## 二、其他可用但未纳入的工具（及原因）

| 工具 | 未纳入原因 |
|---|---|
| `draw-lottery`、`query-lottery-info`、`query-my-prizes` | 积分抽奖是**写操作**、有副作用；与「营养+预约」无关；新账号积分为 0 |
| `mall-*` 系列（麦麦商城商品 / 下单 / 订单）、`query-my-account` | 偏离「营养+预约」主题；新账号无数据，演示无效果 |
| `party-*`（派对 / 品鉴会）、`query-meal-assistance` | 属主题活动 / 团餐扩展能力，非 MVP 核心闭环 |
| `delivery-*`、`query-order`、`order-list`、`cancel-order` | 地址与订单管理为周边能力，当前阶段不纳入主链路 |

---

## 三、已确认的能力边界与工程化补全

1. **营养数据返回的是文本，不是 JSON** → 自写 `scripts/parse_nutrition.py` 做健壮解析（跳表头 → 按逗号切分 → 处理 `null` → 结构化）。
2. **菜单是「组合套餐」，营养表是「单品」** → 必须通过 `query-meal-detail` 拆解后累加。
3. **实测确有匹配缺口**（如菜单「吉士蛋麦满分」营养表中无对应项）→ 精确匹配 → 模糊匹配（去空格 / 括号 / 规格词）→ 标注「暂无官方营养数据」，**绝不编造数值**；核心主餐缺失的方案不进入推荐列表。
4. **MCP 不提供过敏原 / 配料表数据** → 本项目放弃过敏原方向，聚焦 MCP 原生支撑的「营养目标」。
5. **`available-coupons` 不含有效期，但 `query-store-coupons` 含**
   → 改用后者，并自行解析 `tradeDateTime` 的中英文两种格式（`2026-10-08 至 2026-10-21` / `10月8日至10月21日`）过滤过期券。
6. **券接口不返回优惠面额** → 券只用于标记「本方案可享此券」，金额一律由 `calculate-price` 结算并回填，未提供时卡片标注「待结算」。
7. **活动是按日分组的，不是一条连续窗口** → 以「预约日是否在该活动分组内」为第一判据，否则会把当天未开展的活动误报为可用。
8. **餐段之间存在约 29 分钟空档**（实测 10:15→10:44、14:15→14:44、16:45→17:14）→ 必须检出并提示改约，不静默按相邻餐段处理。
9. **早餐饮料轮存在多个 `isDefault=1` 的 choice** → 必须叠加 `quantity>0` 才能取到真实默认单品。
10. **营养表存在重复行**（`小杯玉米杯` ×2、`纯牛奶（盒装）` ×3）→ 索引按名称去重，唯一名数量少于记录数是正常现象。
11. **限流 600 次/分钟** → 营养表与菜单同餐段内只拉一次并复用；反向模式按餐段发起 `query-meals`（单店 4 餐段 = 4 次），远低于阈值。
12. **`query-meals` 随 `storeCode` 变化，但不随日期变化**
    → 实测：同商圈相距 236 m 的两店同日期同时段菜单差异 6 vs 11 项；同店相邻两天（10-12 / 10-13）菜单**完全一致**。
    → 因此多门店择优有数据支撑，但多日排程**不能**声称「每天菜单不同」。
13. **`query-nearby-stores` 不返回已经结束的餐段**
    → 今天（12:39）的 `reservationTimeOptions` 直接不含早餐段。「今天还能否约早餐」
    必须与另一天的餐段集合做差集才能得出（`time_guard.py::missingToday`）。
14. **同一门店不同日期的可约餐段不同**
    → 实测 10-11：午餐/下午茶/夜市；10-12：早餐/午餐/下午茶/夜市。且不同门店的餐段也不同
    （东大桥有 `宵夜(22:14-22:46)`，朝外没有）→ **门店本身就是一个时间约束**。
15. **「无解」通常不是零方案，而是零个「完全达标」的方案**
    → 实测「能量≤400 且蛋白≥30」：17 个可点单元、8 个通过硬约束、0 个完全达标。
    诊断必须区分「硬不可行」（给出被挡原因计数）与「软不可行」（精确定位绑定约束 + 最小放宽量）。
