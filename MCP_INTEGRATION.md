# MCP 集成说明

本项目基于 **麦当劳 MCP（Model Context Protocol）** 开放能力开发。

- 官方 MCP Server 文档：<https://github.com/M-China/mcd-mcp-server>
- 接入端点：`https://mcp.mcd.cn`（Streamable HTTP）
- 认证方式：Header `Authorization: Bearer ${MCD_MCP_TOKEN}`
- 限流：600 次/分钟（超限返回 429）
- 配置模板：见 `mcp-config.example.json`（**仅含环境变量占位符，不含任何真实凭证**）

---

## 一、使用的 MCP Tool 与业务价值

| MCP Tool | 在本项目中的作用 | 对应的约束维度 |
|---|---|---|
| `now-time-info` | 获取服务器当前时间与时区，作为「明天/下次」等相对时间基准 | 时间 |
| `query-nearby-stores` | 查询附近门店、营业时间 `businessStart/EndTime`、`reservation` 与 `reservationTimeOptions` | 时间 |
| `query-meals` | 传 `reservationDate` 拉取**该预约时段**的实时菜单 | 时间 / 菜单 |
| `query-meal-detail` | 拆解套餐组成与**可替换项**，把组合套餐映射为营养单品 | 菜单 / 营养 |
| `list-nutrition-foods` | 获取 160 项餐品营养成分（能量/蛋白质/脂肪/碳水/钠/钙） | 营养 |
| `query-store-coupons` | 获取**当前门店 + 当前订单**实际可用的券（MCP 已代做校验），并返回 `tradeDateTime` 与 `products[].productCode` | 预算 |
| `calculate-price` | 含优惠券算价，输出**唯一的权威实付金额** | 预算 |
| `campaign-calendar` | 查询指定日期的活动、**起止日期**与**精确到分钟的开售时刻**（且**按日分组**） | 活动 |
| `create-order` | 用户显式确认后创建预约订单（**绝不自动调用**） | 闭环 |

> **反向模式**会对 `query-meals` 发起**多次调用**（每个可预约餐段一次），是本 Skill 调用频次最高的模式。实测单店单日 4 个餐段，共 4 次调用，远低于 600 次/分钟的限流。

### `query-meal-detail` 返回结构（实测确认）

`data.rounds[]` 是**选配轮次**（如 主餐轮 / 小食轮 / 饮料轮），每轮含 `choices[]`：

```json
{
  "data": {
    "code": "9900005466",
    "name": "巨无霸三件套",
    "rounds": [
      {
        "id": 1, "name": "新升级巨无霸",
        "choices": [
          {"code":"1100","name":"巨无霸","quantity":1,"isDefault":1,"diffPrice":"+ ¥0"}
        ]
      },
      {
        "id": 2, "name": "选择套餐内小食",
        "choices": [
          {"code":"4810","name":"中薯条","quantity":1,"isDefault":1,"diffPrice":"+ ¥0"},
          {"code":"4437","name":"小杯玉米杯","quantity":0,"isDefault":0,"diffPrice":"- ¥1.5"}
        ]
      },
      {
        "id": 3, "name": "选择套餐内饮料",
      "choices": [
        {"code":"3050","name":"可乐中杯","quantity":1,"isDefault":1,"diffPrice":"+ ¥0"},
        {"code":"3071","name":"无糖可口可乐中杯","quantity":0,"isDefault":0,"diffPrice":"+ ¥0"}
      ]
    }
    ]
  }
}
```

- 每个 round 里 **`isDefault=1` 且 `quantity>0`** 的 choice = 该套餐默认带出的**单品**；
- 其余 choice = **可替换项**，`diffPrice` 为差价（如 中薯条 → 小杯玉米杯 `- ¥1.5`，即可用于「更轻」的替换优化）；
- `supportModify=true` 表示支持特调（去冰 / 不加酱等），本项目 MVP 不主动展开特调选项。

由 `scripts/plan_meal.py::decompose()` 实现抽取。

### `query-store-coupons` 返回结构（实测确认）

按工具声明的展示规则，每条券包含：

| 字段 | 含义 | 本项目用法 |
|---|---|---|
| `title` | 券名 | 卡片展示 |
| `couponId` | 券 ID（分组标识，同一 ID 可对应多个券码） | 去重 |
| `couponCode` | 券码 | 展示「首个（共 N 张）」 |
| `tradeDateTime` | **有效期** | 用 `parse_trade_window()` 解析后过滤过期券 |
| `products[].productName` / `productCode` | **适用商品**及其编码 | **按 `productCode` 与方案精确匹配** |

**关键发现**：`products[].productCode` 与 `query-meals` 的餐品编码处于**同一编码空间**。
这让「券 → 方案」的匹配从模糊的标题比对升级为**确定性匹配**——
组合套餐可用其自身编码（如 `9900005466`）命中，单品则用其单品编码（如 `1450`）命中。
由 `scripts/apply_benefits.py::match_coupons()` 实现。

**关键边界**：返回字段里**没有优惠面额**。因此本项目**不估算券后价**，
一律以 `calculate-price` 的结果为准，未提供时卡片明确标注「待 `calculate-price` 结算」。

> 实测注记：演示账号返回的 `data` 为空数组（见 `tests/fixtures/coupons_real_empty.json`）。
> 这本身就是一个必须优雅降级的真实场景，已由 `scripts/verify_edge_cases.py` 覆盖。

### `campaign-calendar` 的按日分组（实测确认）

传 `specifiedDate=2026-10-12` 会返回相邻多天的活动，且**活动数量按日差异极大**：
2026-10-11 有 12 个，2026-10-12 仅 2 个，2026-10-13 仅 1 个。

这意味着「约哪一天」直接决定能赶上哪些活动——是真实的耦合，也是本项目做
「活动 × 预约时段」单行交叉提示的依据。实现上**必须以按日分组为准**：
某活动未出现在预约日那一组，就视为当天不适用，无论其活动窗口是否覆盖该日。

### 未纳入本项目的能力（及原因）

| 工具 | 未纳入原因 |
|---|---|
| `draw-lottery`、`query-lottery-info`、`query-my-prizes` | 积分抽奖是**写操作**、有副作用；与「营养+预约」无关；新账号积分为 0 |
| `mall-*` 系列（麦麦商城商品/下单/订单）、`query-my-account` | 偏离「营养+预约」主题；新账号无数据，演示无效果 |
| `party-*`（派对/品鉴会）、`query-meal-assistance` | 属主题活动/团餐扩展能力，非 MVP 核心闭环 |
| `delivery-*`、`query-order`、`order-list`、`cancel-order` | 地址与订单管理为周边能力，当前阶段不纳入主链路 |

---

## 二、MCP 调用链路（均为实测确认）

```
now-time-info
  └─ 时间基准（时区 GMT+08:00）

query-nearby-stores (beType=1, searchType=2, city, keyword)
  └─ storeCode / businessStartTime / businessEndTime / reservation
     └─ reservation=true 时返回 reservationTimeOptions（分餐段可约时段）
        实测文本：早餐(06:14至10:15)，午餐(10:44至14:15)，下午茶(14:44至16:45)，夜市(17:14至21:45)

query-meals (storeCode, orderType=1, beType=1, reservationDate="yyyy-MM-dd HH:mm")
  └─ categories[]（分类 + code + tags）与 meals{code:{name, image, currentPrice, originalPrice, discountType}}
     实测：传 08:00 返回早餐菜单，不含巨无霸/麦辣鸡腿堡等正餐产品

query-meal-detail (code)
  └─ 套餐拆单品 + 可替换项（关键：菜单多为组合套餐，营养表为单品）

list-nutrition-foods
  └─ 160 条文本表格 → scripts/parse_nutrition.py 解析为 JSON 索引

   单品匹配营养表（精确 → 模糊 → 缺失标注）
     → 累加整单营养
     → 硬约束过滤 + 软目标打分 → Top3
     → 反向模式：对每个餐段各跑一次 query-meals + 求解器，比较后输出「建议约 X 点」
     → 多人模式：plan_group 为每位成员各选一份，整单受预算约束

query-store-coupons (storeCode, orderType, beType, reservationDate)
  └─ 已校验可用券 + tradeDateTime + products[].productCode
     → 按 productCode 与方案精确匹配（scripts/apply_benefits.py）

calculate-price (含优惠券)
  └─ 券后应付金额（唯一的权威来源；券面额不在券接口返回里，故不估算）

campaign-calendar (specifiedDate)
  └─ 按日分组的活动 → 过滤出预约日 + 匹配餐段 → 卡片内单行提示

create-order（仅用户显式确认后调用）
```

---

## 三、MCP 能力边界与工程化补全

本项目没有简单地堆接口，而是针对 MCP 的实际边界做了工程化设计，这也是 `MCP_INTEGRATION.md` 想重点表达的部分：

1. **营养数据返回的是文本，不是 JSON**
   `list-nutrition-foods` 的 `data` 是字符串文本表格，因此自写 `scripts/parse_nutrition.py` 做健壮解析（跳表头 → 按逗号切分 → 处理 `null` → 结构化）。

2. **菜单是「组合套餐」，营养表是「单品」**
   两者无法直接匹配，必须通过 `query-meal-detail` 把套餐拆解为单品后再累加营养。

3. **实测确有匹配缺口**
   例如菜单里的「吉士蛋麦满分」在营养表中无对应项。降级策略：精确匹配 → 模糊匹配（去空格/括号/规格词）→ 标记「暂无官方营养数据」并标注方案「不完整」，**绝不编造数值**；核心主餐缺失的方案直接不进入推荐列表。

4. **MCP 不提供过敏原 / 配料表数据**
   与「安全」相关的仅有营养成分。因此本项目**放弃过敏原方向**，聚焦于 MCP 原生支撑的「营养目标」，规避数据缺失与合规风险。

5. **`available-coupons` 不含有效期，但 `query-store-coupons` 有**
   实测 `available-coupons` 只返回标题/状态/图片，无法自行推算券期；而
   `query-store-coupons(storeCode)` 同时返回 `tradeDateTime`（有效期）与
   `products[].productCode`，且已由 MCP 完成门店/渠道/配送方式的可用性校验。
   因此本项目以 `query-store-coupons` 为准，并自行解析中英文两种日期格式
   （`2026-10-08 至 2026-10-21` 与 `10月8日至10月21日`）来过滤过期券。

6. **券接口不返回优惠面额**
   无论是 `available-coupons` 还是 `query-store-coupons`，都没有折扣金额字段。
   任何「券后价」若不是来自 `calculate-price` 就属于编造。本项目的处理是：
   券只用于标记「本方案可享此券」，金额留空并明确标注「以 `calculate-price` 结算为准」，
   待 Agent 回填真实金额后再写入卡片。

7. **活动是按日分组的，不是一条连续窗口**
   `campaign-calendar` 返回的是按日期聚合的分组。若只按活动窗口判断，会把
   当天并未开展的活动误报为可用。本项目以「预约日是否在该活动的分组内」为第一判据，
   再叠加活动窗口与餐段匹配。

8. **餐段之间存在约 29 分钟的空档**
   实测 `10:15 → 10:44`、`14:15 → 14:44`、`16:45 → 17:14` 为厨房切换设备的窗口。
   落在空档的预约请求不可下单，必须检出并提示改约，而不是静默按相邻餐段处理。

9. **限流 600 次/分钟**
   营养表与菜单在同一个餐段内只拉取一次并在枚举中复用；反向模式按餐段数发起
   `query-meals`（实测单店 4 个餐段 = 4 次），远低于限流阈值。

---

## 四、安全与合规

- 所有下单动作必须用户**显式确认**，`create-order` 在任何情况下都不自动调用。
- `auto-bind-coupons` 属账户写操作，仅在用户明确同意后调用。
- `draw-lottery` 为写操作且偏离主题，从不调用。
- 营养表述统一为「均衡膳食 / 营养搭配 / 个性化目标」，**不使用「减肥 / 节食 / 断食」**，并设置能量下限保护，拒绝极端低卡目标。
- 仓库不包含任何真实 Token、密钥、密码或个人信息；配置文件仅使用环境变量占位符。
- 项目输出仅供参考，**不构成医疗、营养或其他专业建议**；餐品信息、价格及供应状态以麦当劳官方渠道的实时结果为准。
