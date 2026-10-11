# WorkBuddy 对话上下文（参赛证明）

> 本文件对应官方活动规则 `activityGuidelines.md` 对 `workbuddy.md` 的要求：
> 「如参与 WorkBuddy 专项奖励的需提交本文件，**WorkBuddy 对话上下文，用于联动活动**」。
> 下方为**本项目在 WorkBuddy 智能体（Agent 模式）中从选题到报名的真实开发对话**，
> 用于证明项目由腾讯 WorkBuddy 真实开发（规则："参赛项目须真实使用腾讯 WorkBuddy 智能体"）。
> 对话中用户的原始指令逐字保留；涉及 GitHub Token 等敏感信息已脱敏；agent 侧的实测结论均来自麦当劳 MCP 真实接口返回。

---

## 对话记录

**用户（消息 1）**：你现在是一名程序员，你想参加麦当劳和 WorkBuddy 合作的麦当劳程序员创意开发大赛，请先了解大赛介绍。

**WorkBuddy**：联网检索并抓取官方仓库 `M-China-Official/mcd-developer-innovation-challenge` 及其 `activityGuidelines.md`、`RANKING.md`、`M-China/mcd-mcp-server`。确认核心规则：作品须是基于麦当劳 MCP 的 Skill、开源到个人 Public 仓库、按 Star 排名（前 100 进榜，Star=0 不进榜）；须含 5 个文件（README / CONTEST_DECLARATION / MCP_INTEGRATION / mcp-config.example.json / workbuddy.md）；报名截止 2026-10-25 23:59；合规红线禁止"宣扬不健康饮食方式"。

---

**用户（消息 2）**：先思考一下选题，我有一个想法是「麦麦安心预约规划师」（过敏朋友场景，时间+安全约束）。

**WorkBuddy**：遍历官方 MCP 的 33 个 Tool，确认与"安全"相关的只有 `list-nutrition-foods`（能量/蛋白质/脂肪/碳水/钠/钙），**不含过敏原与配料表**。判定"自动过滤含花生/乳制品餐品"无法用 MCP 直接实现，且过敏数据出错合规风险高，建议调整方向。

---

**用户（消息 3）**：去掉过敏原这个话题，修改为营养目标呢，营养+预定是否可行？

**WorkBuddy**：验证营养与预约均为 MCP 原生能力，无数据缺口。提出关键假设——"菜单是否随预约时间变化"，这决定两者是简单叠加还是真正咬合。写入 `docs/PROPOSAL.md` 立项方案，核心洞察定为"时间 ↔ 营养 双向约束"。

---

**用户（消息 4）**：1、2 都已经配置了，现在先明确一下选题以及意义，开发思路等。

**WorkBuddy**：通过 WorkBuddy 的 `mcd-mcp` 连接器实测，证实核心假设——
- `query-meals(storeCode=1950121, reservationDate=2026-10-12 08:00)` 返回**早餐菜单**，不含巨无霸等正餐；
- `query-nearby-stores` 返回 `reservationTimeOptions`：`早餐(06:14至10:15)，午餐(10:44至14:15)，下午茶(14:44至16:45)，夜市(17:14至21:45)`，餐段间约 29 分钟空档。
→ "时间 → 餐段菜单 → 营养可行域"为 MCP 原生能力，反向求解（营养目标反推时段）随之成立。

---

**用户（消息 5）**：增加优惠券和活动是不是更好？

**WorkBuddy**：实测 `available-coupons`（只有标题/状态/图片、无有效期）与 `campaign-calendar`（按日分组，10-11 有 12 个活动、10-12 仅 2 个）。决策：✅ 加优惠券（预算维度，用 `query-store-coupons` + `calculate-price`）；⚠️ 活动仅做"活动窗口 × 预约时间"交叉提示，不做活动浏览器（避免与官方示例撞车、稀释核心卖点）；❌ 不加积分抽奖/麦麦商城。

---

**用户（消息 6）**：刚加了优惠卷和活动，题目还需要修改吗？github 仓库叫什么最好？

**WorkBuddy**：选题不改（核心仍是"营养目标驱动的预约规划"）。中文名保留「麦麦营养预约规划师」；Slogan 微调「在正确的时间，为正确的人，预约一份『刚刚好、更划算』的餐」。建议仓库名 `mcd-safe-booking` → **`mcd-nutrition-booking`**（弃用 `safe`，不引入 `coupon`/`campaign` 以免稀释记忆点）。GitHub 改名保留 Star、旧地址自动重定向。

---

**用户（消息 7）**：已经修改了 github 仓库，并重新 clone 到目录下 \mcd-nutrition-booking，请正式建仓库骨架。

**WorkBuddy**：创建 5 个参赛必需文件（`CONTEST_DECLARATION.md` 为官方原件原样复制，一字未改）、`SKILL.md`（`agent_created: true`）、`scripts/`、`references/`。完成 Initial commit。

---

**用户（消息 8）**：继续下一步。
**用户（消息 9）**：继续完成下一步。

**WorkBuddy**：D2–D3 数据层——固化真实夹具 `tests/fixtures/nutrition_raw.txt`（160 行）、`stores.json`（5 家门店）；`verify_data_layer.py` 验证：营养表含重复行、钠普遍极高（巨无霸三件套钠 1126mg）。D4–D6 核心求解器——实测 `query-meal-detail`：`rounds[]` 默认单品 = `isDefault=1` 且 `quantity>0`；`plan_meal.py` 从 stub 重写为真实实现（候选池→拆解→累加→硬过滤→打分→排序）。

---

**用户（消息 10）**：继续完成所有开发，直到 D12。

**WorkBuddy**：D7–D12 杀手功能——新增 `parse_request.py`（自然语言→约束）、`recommend_slot.py`（反向时段推荐）、`optimize_replacements.py`、`apply_benefits.py`、`run_scenario.py`（端到端）、`run_all_checks.py`（CI 门禁）。自测修复 4 个真实 bug（如 `600 kcal以内` 解析失败、中文单位未覆盖、"约几点合适"误判为已给时间、活动误报因未按日分组）。反向推荐实测：控钠目标下早餐"猪柳炒双蛋堡"三项目标全达成（评分 3.00），午餐最优仅 2.96 → 建议约 06:14 早餐。完成 `25d9a48`。

---

**用户（消息 11）**：再增加完成进阶功能。

**WorkBuddy**：定向实测（三组全量菜单交叉对比）得反直觉结论——门店维度菜单变、日期维度菜单不变；餐段差异比门店更锋利（东大桥有 `宵夜(22:14–22:46)`，朝外没有）。新增 `compare_stores.py` / `time_guard.py` / `plan_week.py` / `diagnose.py`，修掉"可得性反向子串误匹配"等 bug。完成 `fc162e2`。

---

**用户（消息 12）**：检查一遍整理功能是否完善，如果不完善则继续完善。

**WorkBuddy**：发现四个进阶模块是"孤岛脚本"、`run_scenario.py` 未调用 → 重写统一调度器，`decide_mode()` 路由 `pin > week > cross_store > reverse > forward`；修掉"团体 relaxedFor 恒空""诊断只在零方案触发"等同源 bug。新增 `verify_integration.py`（40 项）后共 **6 组 ALL PASS**。

---

**用户（消息 13）**：完成 D13。

**WorkBuddy**：三张手绘 SVG（`docs/assets/hero-core.svg`、`pipeline.svg`、`measured-matrix.svg`），图上每个数字来自真实实测（时间轴按 931 分钟→600px 真实比例；"芝士安格斯厚牛堡 696 kcal ≈ 能量约鲈鱼 1 条"为官方原始类比字段）。完成 `4bb2c5d`。

---

**用户（消息 14）**：完成 d14。

**WorkBuddy**：编写 `docs/SUBMISSION.md`（官方格式正文 + 自检清单）。记录报名翻车点：简介标题与正文间不换行、项目地址不用 Markdown 链接格式。

---

**用户（消息 15 / 16）**：请继续完成未完成的任务。（429 限流后重发）

**WorkBuddy**：重试推送代码到 GitHub（此前因 429 限流中断），确认远程与本地一致。

---

**用户（消息 17）**：github 的 token 已经给你，请提交 issue。

**WorkBuddy**：收到。准备用 GitHub API 在官方仓库 `M-China/mcd-developer-innovation-challenge` 创建报名 Issue。

---

**用户（消息 18）**：`github_pat_11ANQ…`（fine-grained PAT，已脱敏）

**WorkBuddy**：使用该 fine-grained PAT 提交，返回 **403**（缺少 `Issues: read and write` 权限），提交失败。

---

**用户（消息 19）**：`ghp_cejAc…`（classic PAT，已脱敏，含 repo scope）

**WorkBuddy**：改用 classic PAT，成功创建报名 Issue **#423**（标题「麦麦营养预约规划师 · 参赛申请」），正文严格遵循官方格式（标签与内容同行、项目地址为裸 URL）。官方机器人 `M-China-Official` 已回复「您的作品已成功参赛」。

---

**用户（消息 20）**：（轮询官方机器人回复时用户中断）

**WorkBuddy**：稍后独立核验 Issue #423 评论，确认官方机器人 `M-China-Official`（COLLABORATOR）回复：`您的作品已成功参赛，您可以通过官方项目的排行榜.md查看排名`（comment id 6106461004）。

---

## 真实性佐证（评审可独立核验）

| # | 证据 | 验证位置 |
|---|---|---|
| A | Skill 由 WorkBuddy 生成 | `SKILL.md` 文件头 `agent_created: true` |
| B | 麦当劳 MCP 连接器已接入 | 本机 `~/.workbuddy/mcp.json` → `mcd-mcp` 指向 `https://mcp.mcd.cn`（Bearer 鉴权） |
| C | 真实增量提交链 | `git log`：`a0fb99e` → `25d9a48` → `fc162e2` → `4bb2c5d` → `6910e68` → `14de742` → `6e8fb97`，均 2026-10-11 |
| D | WorkBuddy 工程记忆 | `../.workbuddy/memory/2026-10-11.md` |
| E | 真实 MCP 返回固化夹具 | `tests/fixtures/`（营养表/门店/菜单/套餐详情，均为接口真实返回） |
| F | 端到端可复现验证 | `python scripts/run_all_checks.py` → 6 组 ALL PASS |

> 说明：本对话记录由 WorkBuddy 智能体在其 Agent 会话中据实还原，敏感凭证（GitHub Token）已脱敏，未写入任何公开文件。如需最原始的逐字聊天导出，可在 WorkBuddy 客户端中将本会话导出后覆盖本文件。
