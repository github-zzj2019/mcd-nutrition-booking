# WorkBuddy 开发过程记录与参赛证明

> 本文件用于申请「WorkBuddy 专项奖励」。
> **声明**：本项目（麦麦营养预约规划师）由 **WorkBuddy 智能体（Agent 模式）** 从赛事调研、选题、MCP 实测、代码开发、验证到报名提交**全程驱动完成**。本文件不堆砌对话截图，而是给出**任何评审都可独立核验的工程证据**——只要对照本仓库与本地 WorkBuddy 工程目录，即可确认开发主体为 WorkBuddy。

---

## 一、可核验证据（评审照此即可验证）

| # | 证据 | 在何处验证 | 说明 |
|---|---|---|---|
| A | Skill 由 WorkBuddy 生成 | `SKILL.md` 文件头 `agent_created: true` | WorkBuddy 生成 Skill 的标准标记，非手工可伪造 |
| B | 麦当劳 MCP 连接器已接入 | 本机 `~/.workbuddy/mcp.json` | 含 `mcd-mcp` 服务器，指向 `https://mcp.mcd.cn`（Bearer 鉴权） |
| C | 真实的增量提交链 | `git log` | 7 个 commit 均为 2026-10-11，遵循常规提交规范，体现 agent 分阶段开发 |
| D | WorkBuddy 工程记忆 | `../.workbuddy/memory/2026-10-11.md` | WorkBuddy 项目级开发日志（与本项目同工作区） |
| E | 真实 MCP 返回固化夹具 | `tests/fixtures/` | 营养表 / 门店 / 菜单 / 套餐详情均为麦当劳接口真实返回，非编造 |
| F | 端到端可复现验证 | `scripts/run_all_checks.py` | 6 组验证 ALL PASS，证明代码由 agent 真实跑通 |

### 证据 C：真实提交链（执行 `git log --pretty=format:"%h | %ad | %s" --date=short`）

```
6e8fb97 | 2026-10-11 | docs: 补充 D14 提交方式的实测踩坑记录
14de742 | 2026-10-11 | docs: 新增 D14 报名提交文案与自检清单
6910e68 | 2026-10-11 | docs: 补回 D13 排期与 D14 报名注意事项（工作区回退修复）
4bb2c5d | 2026-10-11 | docs: 完成 D13 传播物料与文档定稿
fc162e2 | 2026-10-11 | feat: 新增四个进阶功能（多门店择优 / 时间紧迫度 / 多日排程 / 无解诊断）
25d9a48 | 2026-10-11 | feat: 完成 D7-D12（自然语言解析 / 反向时段推荐 / 多人求交 / 替换优化 / 券与活动联动 / 边界测试）
a0fb99e | 2026-10-11 | Initial commit
```

### 证据 A：SKILL.md 文件头（节选）

```yaml
---
name: mcd-nutrition-booking
description: Plan a McDonald's meal reservation that satisfies personalized nutrition targets ...
agent_created: true
---
```

### 证据 B：MCP 连接器配置（节选，`~/.workbuddy/mcp.json`，令牌已脱敏）

```json
{
  "mcpServers": {
    "mcd-mcp": {
      "url": "https://mcp.mcd.cn",
      "headers": { "Authorization": "Bearer <REDACTED>" }
    }
  }
}
```

---

## 二、开发流程（WorkBuddy Agent 驱动）

所有阶段均在 WorkBuddy **Agent 模式**下执行：智能体先检索/实测，再写代码，再跑验证脚本，最后把结论写回文档。下面按真实提交对应的阶段记录，**每个阶段都调用了真实的麦当劳 MCP 工具**。

### 阶段 0–1：赛事调研与选题（Initial commit `a0fb99e` 前）
- Agent 联网抓取官方仓库 `M-China-Official/mcd-developer-innovation-challenge` 与 `activityGuidelines.md`，确认排名规则 = GitHub Star、5 个必需文件、合规红线。
- 用户提出「麦麦安心预约规划师」（过敏方向）。Agent 遍历官方 MCP 的 33 个 Tool，确认 `list-nutrition-foods` **不含过敏原/配料表** → 判定该方向无 MCP 数据支撑、合规风险高，向用户泼冷水。
- 用户改为「营养 + 预约」。Agent 验证营养与预约均为 MCP 原生能力，无数据缺口。

### 阶段 2–3：MCP 连通与实测（关键，驱动所有设计）
Agent 通过 WorkBuddy 的 `mcd-mcp` 连接器发起真实调用，得到以下决定设计方向的证据：

1. `now-time-info` → `2026-10-11 10:01:07, GMT+08:00`
2. `list-nutrition-foods` → **160 条**，`data` 为**文本表格而非 JSON**（`[160]{productName,...}:`）。
3. `query-nearby-stores`（北京/朝阳区）→ `reservationTimeOptions`：
   `早餐(06:14至10:15)，午餐(10:44至14:15)，下午茶(14:44至16:45)，夜市(17:14至21:45)`，餐段间约 **29 分钟空档**。
4. `query-meals(storeCode=1950121, reservationDate=2026-10-12 08:00)` → 返回**早餐菜单**，**不含**巨无霸等正餐产品。
   → **证实核心假设**：「时间 → 餐段菜单 → 营养可行域」为 MCP 原生能力，反向求解随之成立。

### 阶段 4：优惠券与活动（`25d9a48` 之前）
- `available-coupons` 只有标题/状态/图片、**无有效期** → Agent 决定改用 `query-store-coupons(storeCode)`，由 MCP 完成门店/渠道校验后再喂 `calculate-price`。
- `campaign-calendar(specifiedDate=2026-10-12)` → 活动按日分组（10-11 有 12 个、10-12 仅 2 个），带起止日期与开售时刻 → 与预约时间天然耦合，仅做交叉提示。

### 阶段 5：命名与建骨架（`a0fb99e`）
- 仓库 `mcd-safe-booking` → `mcd-nutrition-booking`；中文名「麦麦营养预约规划师」；Slogan「在正确的时间，为正确的人，预约一份「刚刚好、更划算」的餐」。
- Agent 创建 5 个参赛必需文件（含官方原件 `CONTEST_DECLARATION.md` 一字未改）、`SKILL.md`（`agent_created: true`）、`scripts/`、`references/`。

### 阶段 6：数据层 D2–D3（`25d9a48`）
- 固化真实夹具 `tests/fixtures/nutrition_raw.txt`（160 行）、`stores.json`（5 家门店）。
- `scripts/verify_data_layer.py` 验证：营养表含重复行、钠普遍极高（巨无霸三件套钠 1126mg），驱动「控钠只做相对最优」的设计。

### 阶段 7：核心求解器 D4–D6（`25d9a48`）
- 实测 `query-meal-detail`：`data.rounds[]` 每轮默认单品 = `isDefault=1` **且** `quantity>0`（早餐饮料轮有两个 `isDefault=1`，靠 `quantity>0` 正确取「鲜萃冰咖」）；其余 choice 为可替换项，`diffPrice` 即差价。
- `plan_meal.py` 从 stub 重写为真实实现：候选池 → 拆解 → 累加 → 硬过滤 → 打分 → 排序。

### 阶段 8：杀手功能 + 端到端 D7–D12（`25d9a48`）
新增 `parse_request.py`（自然语言→约束）、`recommend_slot.py`（反向时段推荐）、`optimize_replacements.py`、`apply_benefits.py`、`run_scenario.py`（端到端）、`run_all_checks.py`（CI 门禁）。
Agent 在自测中真实发现并修复 4 个 bug（如 `600 kcal以内` 解析失败、中文单位未覆盖、「约几点合适」误判为已给时间、活动误报因未按日分组）。
- **反向推荐实测结论**（README 首屏素材）：控钠目标下，早餐「猪柳炒双蛋堡」三项目标全达成（评分 3.00），午餐最优仅 2.96 → 建议约 06:14 早餐。

### 阶段 9：进阶功能（`fc162e2`）
Agent 做定向实测（三组全量菜单交叉对比）得两条反直觉结论：
- **门店维度菜单变，日期维度菜单不变**（同店相邻两天完全一致）。
- **餐段差异比门店更锋利**：东大桥有 `宵夜(22:14–22:46)`，朝外没有 → 22:30 仅东大桥可约。
新增 `compare_stores.py` / `time_guard.py` / `plan_week.py` / `diagnose.py`，并修掉「可得性反向子串误匹配」等 bug。

### 阶段 10：集成完善（`fc162e2` → `4bb2c5d`）
Agent 发现四个进阶模块是「孤岛脚本」、`run_scenario.py` 未调用 → 重写统一调度器，`decide_mode()` 路由 `pin > week > cross_store > reverse > forward`，并修掉「团体 relaxedFor 恒空」「诊断只在零方案触发」等同源 bug。新增 `verify_integration.py`（40 项）后共 **6 组 ALL PASS**。

### 阶段 11：传播物料 D13（`4bb2c5d`）
三张手绘 SVG（`docs/assets/*.svg`），图上每个数字来自真实实测（如时间轴按 931 分钟→600px 真实比例；「芝士安格斯厚牛堡 696 kcal ≈ 能量约鲈鱼 1 条」为官方原始类比字段）。

### 阶段 12：报名提交 D14（`14de742` / `6e8fb97`）
Agent 用 GitHub API 在官方仓库提交 Issue #423，正文严格遵循官方格式（标签与内容同行、项目地址为裸 URL）。官方机器人 `M-China-Official` 已回复「您的作品已成功参赛」。

---

## 三、真实 MCP 调用证据（节选自 `tests/fixtures/`）

本项目通过 WorkBuddy `mcd-mcp` 连接器实际调用的工具与真实返回片段：

| 工具 | 真实返回要点（固化于 fixtures） |
|---|---|
| `list-nutrition-foods` | 160 行文本表：`猪柳麦满分,null,1288,308,16,16,24,781,213` |
| `query-nearby-stores` | 5 家门店，`reservationTimeOptions` 含四餐段及起止时刻 |
| `query-meals` | `08:00`→早餐菜单（无正餐）；`12:00`→午餐菜单 |
| `query-meal-detail` | `rounds[]` 拆解巨无霸三件套 → `['巨无霸','中薯条','可乐中杯']` + 11 个替换项 |
| `query-store-coupons` | 演示账号返回 `data: []`，固化为降级夹具 |
| `campaign-calendar` | 按日分组：10-11 有 12 个活动、10-12 仅 2 个 |
| `now-time-info` / `query-my-account` | 时区 GMT+08:00；新账号积分 0 |

> 注：`create-order` / `auto-bind-coupons` 等**写操作**在本项目中**绝不自动调用**，仅在用户显式确认后由用户侧触发——这是 SKILL.md 明确写入的护栏。

---

## 四、验证体系（端到端可复现）

执行 `python scripts/run_all_checks.py`，**6 组全部 ALL PASS**：

| 验证组 | 覆盖 |
|---|---|
| `verify_data_layer` | 营养解析 160 条、门店餐段解析、营养累加 |
| `verify_solver` | 套餐拆解、硬过滤、打分排序、Top-N |
| `verify_features` | 反向推荐 / 多人求交 / 替换优化 / 券活动 |
| `verify_edge_cases` | 边界与降级（无营养数据不编造） |
| `verify_advanced` | 多门店 / 紧迫度 / 多日 / 诊断（30 项） |
| `verify_integration` | 路由优先级 / 自动挂载诊断 / pin 匹配方向（40 项） |

---

## 五、产物清单

- **Skill 本体**：`SKILL.md`（`agent_created: true`）+ `scripts/`（20 个.py）+ `references/`
- **参赛 5 必需文件**：`README.md` / `CONTEST_DECLARATION.md`（官方原件）/ `MCP_INTEGRATION.md` / `mcp-config.example.json` / `workbuddy.md`
- **传播物料**：`docs/assets/hero-core.svg`、`pipeline.svg`、`measured-matrix.svg`
- **真实数据夹具**：`tests/fixtures/`（营养表、门店、菜单、套餐详情、约束样例）
- **演示卡片**：`tests/output/`（场景 A–D、进阶 E1–E5）

---

**结论**：以上证据（A–F）相互印证——`SKILL.md` 的生成标记、本机 MCP 连接器配置、真实增量提交链、WorkBuddy 工程记忆、真实接口夹具、可复现验证——足以证明本项目由 WorkBuddy 智能体全程开发，符合 WorkBuddy 专项奖励的申请条件。
