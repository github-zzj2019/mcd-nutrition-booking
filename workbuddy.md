# WorkBuddy 开发过程记录

> 本项目**全程使用腾讯 WorkBuddy** 开发（含 MCP 连接器配置、Skill 设计、代码编写与文档生成）。
> 本文件保留完整开发对话，用于申请「WorkBuddy 专项奖励」。

---

## 阶段 0：明确目标

**用户**：你现在是一名程序员，想参加麦当劳和 WorkBuddy 合作的麦当劳程序员创意开发大赛，请先了解大赛介绍。

**动作**：通过 WorkBuddy 联网检索与抓取，确认唯一权威渠道为麦当劳 GitHub 官方账号 `M-China-Official/mcd-developer-innovation-challenge`，并抓取活动规则 `activityGuidelines.md` 与 MCP Server 文档 `M-China/mcd-mcp-server`。

**结论**：
- 作品必须是**基于麦当劳 MCP 能力**开发的 Skill，开源到个人 **Public** GitHub 仓库，按 **Star 数**排名（前 100 名进榜，Star 为 0 不进榜）。
- 必须包含 5 个文件：`README.md`、`CONTEST_DECLARATION.md`（**官方原件，一字不可改**）、`MCP_INTEGRATION.md`、`mcp-config.example.json`、`workbuddy.md`。
- 报名时间：2026-10-09 10:30 → **2026-10-25 23:59**；2026-10-26 00:00 定榜；11-14 前提交兑奖信息。
- 合规红线：内容**禁止「宣扬不健康饮食方式」**。

---

## 阶段 1：选题提出（过敏原方向）

**用户**：提出「麦麦安心预约规划师」，定位「在正确的时间，为正确的人，预约一份安全的餐」。场景为「明天中午12点约了过敏体质的朋友，TA 对花生和乳制品过敏」，Skill 需同时处理时间约束与安全约束。

**核实**：WorkBuddy 遍历官方 MCP Server 的 33 个 Tool，确认与「安全」相关的只有 `list-nutrition-foods`（能量/蛋白质/脂肪/碳水/钠/钙），**不含过敏原与配料表**。

**结论（泼冷水）**：「自动过滤含花生/乳制品餐品」无法用 MCP 直接实现；且过敏数据一旦出错可能送人进医院，合规风险极高。

---

## 阶段 2：去掉过敏原，改为「营养 + 预约」

**用户**：去掉过敏原，修改为营养目标，营养+预定是否可行？

**验证**：确认营养与预约均为 MCP 原生能力，无数据缺口。进一步提出关键假设——「菜单是否随预约时间变化」，这决定了两者是简单叠加还是真正咬合。

---

## 阶段 3：配置 MCP 并实测（关键）

**用户**：1、2 都已经配置了（Token 与连接器均已就绪）。

**实测返回（真实数据）**：

1. `now-time-info` → `formatted: "2026-10-11 10:01:07"`，时区 `GMT+08:00`。
2. `query-my-account` → 可用积分 0（新账号）。
3. `list-nutrition-foods` → **160 条**，`data` 为**字符串文本表格**（非 JSON）：
   ```
   [160]{productName,nutritionDescription,energyKj,energyKcal,protein,fat,carbohydrate,sodium,calcium}:
     猪柳麦满分,null,1288,308,16,16,24,781,213
     巨无霸,null,2146,513,27,26,42,961,171
   ```
   `nutritionDescription` 含官方生活化类比（「能量约鲈鱼 1 条」「能量约猕猴桃 2 个」）→ 极具传播力。
4. `query-my-coupons` → 空（新账号）。
5. `query-nearby-stores`（北京 / 朝阳区）→ 返回 `storeCode`、`businessStartTime/EndTime`、`reservation=true`、`reservationTimeOptions`：
   ```
   早餐(06:14至10:15)，午餐(10:44至14:15)，下午茶(14:44至16:45)，夜市(17:14至21:45)
   ```
   餐段之间约 **29 分钟空档**（厨房换餐窗口），可作为产品提示点。
6. `query-meals`（`storeCode=1950121`，`reservationDate=2026-10-12 08:00`）→ 返回**早餐菜单**（麦满分系列、厚松饼堡系列、早安营养卷、炒双蛋堡系列、一早现磨等），**巨无霸、麦辣鸡腿堡、板烧鸡腿堡等正餐产品完全不在返回列表中**。

→ **核心假设证实**：「时间 → 餐段菜单 → 营养可行域」正向链路成立，且为 MCP 原生支持（`query-meals` 的 `reservationDate` 参数）。反向链路（营养目标 → 反推时段）由此成立。

---

## 阶段 4：增加优惠券与活动

**用户**：增加优惠券和活动是不是更好？

**实测**：

- `available-coupons` → 返回 6 张可领券（免费脆薯饼、麦旋风买一送一、9.9 元冰美式等），但字段只有**标题 + 状态 + 图片**，**无有效期字段** → 修正原计划：不自算「券的有效时段」，改用 `query-store-coupons(storeCode)`，由 MCP 完成门店/渠道/配送方式校验后再喂给 `calculate-price`。
- `campaign-calendar`（`specifiedDate=2026-10-12`）→ 返回三天活动（10-11 有 12 个、10-12 有 2 个、10-13 有 1 个），活动带**起止日期**（「10月8日至10月21日」）与**精确到分钟的开售时刻**（「10:30 正式开售」）→ 与预约时间天然耦合。

**决策**：

- ✅ **加**：优惠券（预算维度，`query-store-coupons` + `calculate-price` + 经同意的 `auto-bind-coupons`）
- ⚠️ **只加交叉提示**：活动（仅「活动窗口 × 预约时间」单行提示，不做活动浏览器，避免稀释核心卖点、避免与官方示例「活动推荐助手」撞车）
- ❌ **不加**：积分抽奖（`draw-lottery` 为写操作、有副作用）、麦麦商城（偏离主题）

**判断标准**：只纳入能跟「时间」咬合的维度。这个项目的卖点是「时间 ↔ 营养 双向约束」，不是一个麦当劳功能大全。

---

## 阶段 5：命名与仓库决策

**用户**：刚加了优惠卷和活动，题目还需要修改吗？github 仓库叫什么最好？

**决策**：

- **选题不改**：核心仍是「营养目标驱动的预约规划」，卖点是「时间 ↔ 营养 双向约束求解」。优惠券与活动只是把约束模型从四维补到五维的**从属约束**，因其咬合时间才纳入，不改变问题本质。
- **中文名保留**：`麦麦营养预约规划师`（"营养"必须在前，这是差异化钩子）。
- **Slogan 微调**：「在正确的时间，为正确的人，预约一份「**刚刚好、更划算**」的餐。」副线：按时段求解营养目标 → 自动匹配当前订单可用券 → 提示预约时段内正在进行的活动。
- **仓库名**：`mcd-safe-booking` → **`mcd-nutrition-booking`**。弃用 `safe`（来自已废弃的过敏原方向，现在是误导，且未体现"营养"）；不加入 `coupon`/`campaign`（避免稀释记忆点）。保留 `mcd-` 前缀便于赛事检索。GitHub 改名保留 Star、旧地址自动重定向。
- 用户已重命名 GitHub 仓库并 clone 到 `mcd-nutrition-booking/`。

---

## 阶段 6：建仓库骨架（当前）

按 `docs/PROPOSAL.md` 的 6.1 交付形态创建目录结构与全部文件：

- `SKILL.md`：frontmatter（`name` / `description` / `agent_created: true`）+ 七步工作流 + 护栏 + 资源索引。
- `scripts/`：`parse_nutrition.py`、`resolve_store.py`、`plan_meal.py`、`render_card.py`。
- `references/`：`mcp_tools.md`、`nutrition_reference.md`。
- 5 个参赛必需文件：`README.md`、`CONTEST_DECLARATION.md`（**官方原件原样复制，未做任何改动**）、`MCP_INTEGRATION.md`、`mcp-config.example.json`、`workbuddy.md`。
- `docs/PROPOSAL.md`：立项方案（已从旧目录迁移）。

---

---

## 阶段 7：数据层（D2–D3）

用真实 MCP 返回固化夹具，写 `scripts/verify_data_layer.py` 全量验证：

- `tests/fixtures/nutrition_raw.txt`：`list-nutrition-foods` 的完整 160 行原文。
- `tests/fixtures/stores.json`：`query-nearby-stores`（北京/朝阳区）的 5 家门店。
- 验证 14 项全部 PASS。过程中发现两个真实数据特征：
  1. **营养表存在重复行**（`小杯玉米杯` 2 次、`纯牛奶（盒装）` 3 次），按名称建索引会少于 160 条——
     是我断言写错了，不是解析错误，已修正为「唯一名数量 ≤ 记录数」。
  2. **钠普遍极高**：巨无霸三件套光钠就 1126 mg，占成人每日上限 56%。
     这坐实了「控钠只能做相对最优，不能承诺绝对达标」的设计。

---

## 阶段 8：核心求解器（D4–D6）

实测 `query-meal-detail` 坐实了套餐拆解结构。关键坑：

> **早餐饮料轮里有两个 `isDefault=1` 的选项**（`鲜萃冰咖` quantity=1、`大杯鲜萃咖啡` quantity=0）。
> 只看 `isDefault` 会取错，必须同时判断 `quantity>0`。

`plan_meal.py` 从 TODO stub 重写为真实实现：候选池 → 拆解 → 累加 → 硬过滤 → 打分 → 排序。
另修掉一个自测发现的问题：夹具里的 `_note` 说明字段会被 `build_details_index` 当作编码遍历而崩溃，
已加非 dict 跳过。

---

## 阶段 9：杀手功能 + 端到端（D7–D12）

**自然语言解析**（新增，MVP 唯一缺口）：`scripts/parse_request.py`。
踩到的三个真实 bug 及修复：

1. `600 kcal以内` 没识别——正则的负向预查 `(?![a-zA-Z\u4e00-\u9fa5])` 把中文「以」也排除了，改为 `(?![a-zA-Z0-9])`。
2. 「我」被重复识别——`帮我看看` 里的「我」被当成用餐者，加负向后顾 `(?<![帮给替和跟与带请让])` 并按姓名合并。
3. 「约几点合适」被误判为已给时间——新增 `ASK_TIME_RE`，命中即进入反向模式。
4. 中文单位未覆盖——`800毫克`、`25克` 无法识别，已把 `mg|毫克`、`g|克` 及否定式（`别超过`/`不想超过`）纳入。

**反向时段推荐**：拉取早餐 08:00 / 午餐 12:00 / 下午茶 15:30 三个真实菜单建立
`menus_by_slot.json`，跑出结论「建议预约 06:14（早餐），猪柳炒双蛋堡，三项目标全达成；
午餐最优只到 96%」——这是 README 首屏的核心素材。

**多人目标求交**：`plan_group()` 每人一份餐（允许相同），按总评分降序、总价升序，
受整单预算约束。实测「我控钠 / 小王增肌 40g / 小李随便，预算 120」→ 总价 ¥59。

**替换优化**：`optimize_replacements.py` 实测巨无霸三件套 →
`中薯条 → 小杯玉米杯（-¥1.5，能量 -236、钠 -164）`，评分 1.69 → 2.00；
同时 4 个替换项因无官方营养数据被**明确跳过**而非编造。

**券与活动联动**：实测发现 `query-store-coupons` 返回 `tradeDateTime` 与 `products[].productCode`，
推翻了原「券无有效期」的假设；券后价因接口无面额字段而一律交给 `calculate-price`。
活动匹配踩到一个 bug：只按窗口判断会把当天未开展的活动误报，
修正为**以 `campaign-calendar` 的按日分组为第一判据**。

**验证体系**：`verify_data_layer` / `verify_solver` / `verify_features` / `verify_edge_cases`
四组，由 `scripts/run_all_checks.py` 一键运行，**全部 ALL PASS**。
并产出 `tests/output/` 下场景 A/B/C/D 四张演示卡片。

---

## 关键实测结论备忘

| 结论 | 证据 |
|---|---|
| 菜单随预约时间动态返回 | `query-meals` 传 `08:00` 返回早餐菜单，不含正餐产品 |
| 门店返回分餐段可约时段 | `reservationTimeOptions` 文本含早餐/午餐/下午茶/夜市及起止时刻 |
| 餐段间有约 29 分钟空档 | 10:15→10:44、14:15→14:44、16:45→17:14 |
| 营养数据是文本非 JSON，共 160 条 | `list-nutrition-foods` 返回 `[160]{...}:` 文本表格 |
| ~~`available-coupons` 无有效期字段~~ → **已修正** | `available-coupons` 确实只有标题/状态/图片；但 `query-store-coupons` **有** `tradeDateTime` 与 `products[].productCode` |
| 券的 `productCode` 与菜单编码同空间 | 可确定性匹配「券 → 方案」，无需模糊比对 |
| 券接口不返回优惠面额 | 券后价只能来自 `calculate-price`，本项目不估算 |
| 活动按日分组，数量差异极大 | 10-11 有 12 个、10-12 仅 2 个、10-13 仅 1 个 |
| 活动带起止日期与开售时刻 | `campaign-calendar` 返回「10月8日 10:30 正式开售」 |
| 菜单是组合套餐、营养表是单品 | 需 `query-meal-detail` 拆解后累加 |
| 存在营养匹配缺口 | 如「吉士蛋麦满分」营养表中无对应项 |

---

**声明**：以上开发过程均在 WorkBuddy 中完成，MCP 调用记录与返回数据均来自麦当劳 MCP 真实接口实测。
