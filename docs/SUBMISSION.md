# 报名提交（D14）

报名入口：<https://github.com/M-China/mcd-developer-innovation-challenge/issues>
（官方仓库已迁移：原 `M-China-Official/...` → 现 `M-China/...`，旧地址会 301 重定向）

---

## 一、官方要求的格式（原文照抄）

```text
Issue 标题：自定义报名标题
Issue 正文：
【参赛申请】
项目名称：{项目名称}
项目地址：{项目 GitHub 仓库地址}
项目简介：{项目简介}
```

官方对校验逻辑的两条明确警告（`README.md` 第 134–136 行原文）：

> - **报名 Issue 的【标题】和【正文】之间暂不要使用换行符，如【项目简介】和【项目简介内容】之间暂不要使用换行符**
> - **项目地址链接暂不要使用 Markdown 格式组织文本**

实操含义：

1. 每个字段的**标签与内容必须写在同一行**——`项目名称：xxx` 不能拆成 `项目名称：` 换行再写 `xxx`。
   （GitHub Issue 的 title 与 body 本就是两个独立字段，所以这条约束的是正文内部的标签行。）
2. `项目地址` 必须贴**裸 URL**，不能写成 `[xxx](url)` 或 `<url>`。
3. `项目简介` 整段压成**一行**，中间不换行。

---

## 二、待提交内容（可直接复制）

### Issue 标题

```text
麦麦营养预约规划师：给营养目标，反推「该约几点」
```

### Issue 正文

```text
【参赛申请】
项目名称：麦麦营养预约规划师（mcd-nutrition-booking）
项目地址：https://github.com/github-zzj2019/mcd-nutrition-booking
项目简介：基于麦当劳 MCP 的营养预约规划 Skill。核心洞察是麦当劳菜单按餐段切换（早餐/正餐/下午茶/夜市，实测换餐空档约 29 分钟），因此「约几点」直接决定「能吃到什么营养」——本项目把这条耦合做成双向求解。正向：给定预约时间，在该餐段实时菜单内求满足营养目标的最优组合，并匹配当前门店当前订单可用的券、提示时段内的活动；反向：给定营养目标，遍历各餐段分别求解后输出「建议约几点」（实测「蛋白质≥25g、能量≤600kcal、钠≤800mg」的最优解落在早餐 06:14，评分 3.00，而正餐时段最优仅 2.96 且蛋白质只到 96%）。实际调用 now-time-info、query-nearby-stores、query-meals（传 reservationDate 拉取该时段菜单）、query-meal-detail（把组合套餐拆解为营养单品）、list-nutrition-foods、query-store-coupons、calculate-price、campaign-calendar 共 8 个 Tool，构成时间/菜单/营养/预算/活动五维约束。另含四个进阶能力：多门店择优（实测同商圈相距 236 米的两家店，22:30 一家可约、一家已过营业）、餐段截止感知（接口不返回已结束餐段，必须与另一日做差集才能发现「今天约不到早餐，明天 06:14 起才可以」）、多日排程（实测相邻两天菜单完全一致，故主动放弃「菜单每天不同」的假设，改建在可约餐段、当天活动数与跨天不重样上）、无解诊断（区分硬不可行与软不可行，精确定位绑定约束并算出最小放宽量，如「蛋白质下探到 23g 即可达标」）。所有营养数据取自官方 list-nutrition-foods 并可与真实菜单逐项匹配，匹配不到即标注「暂无官方营养数据」并移出推荐，绝不编造数值；券后价一律以 calculate-price 为准，不做估算；create-order 仅在用户显式确认后调用，任何情况下不自动下单。
```

> 复制时请确认「项目简介：」之后的内容是**完整的一行**。若编辑器自动折行，只是视觉换行，不影响；但只要出现真实回车，就会被校验逻辑判为失败。

---

## 三、两种提交方式

### 方式 A：手动提交（推荐，无需任何凭据）

1. 打开 <https://github.com/M-China/mcd-developer-innovation-challenge/issues>
2. 点 **New issue**，把上面「Issue 标题」粘进标题框，「Issue 正文」粘进正文框
3. 点 **Submit new issue**
4. 等几秒刷新页面——官方机器人 `M-China-Official` 会回复：

   > 您的作品已成功参赛，您可以通过官方项目的排行榜.md查看排名

   看到这条即为**报名成功**；若回复的是失败原因，改完可直接再开一个 Issue 重提。

### 方式 B：用 GitHub Token 自动提交

**Token 必须满足两个条件，缺一不可**（这是本次实际踩到的坑）：

| 配置项 | 要求 | 踩坑记录 |
|---|---|---|
| Repository permissions → **Issues** | **Read and write** | ⚠️ 只勾了 `Contents` 或 `Metadata` 的话，建 Issue 会返回 `403 Resource not accessible by personal access token`，**连自己名下的仓库也建不了** |
| Repository access | **All repositories**（至少包含 Public） | Fine-grained PAT 默认限定在「Only select repositories」，够不到 `M-China` 组织下的仓库 |

生成路径：<https://github.com/settings/personal-access-tokens> → **Fine-grained token** → 按上表勾两项 → 生成后**立即使用，用完 Revoke**。

> 官方仓库已从 `M-China-Official/...` 迁移到 **`M-China/mcd-developer-innovation-challenge`**，
> 旧地址会自动 301 重定向，但 API 提交时请直接用新地址。

---

## 四、提交前的自检清单

| 检查项 | 状态 | 说明 |
|---|---|---|
| 仓库为 Public | ✅ | `private=false` |
| 创建时间在 2025-12-25 ~ 2026-10-25 | ✅ | 创建于 `2026-10-11T01:29:33Z` |
| `README.md` | ✅ | 文件名不可改动 |
| `CONTEST_DECLARATION.md` | ✅ | **文件名与内容均不可改动**，已用官方原件 |
| `MCP_INTEGRATION.md` | ✅ | 文件名不可改动 |
| 源代码 | ✅ | `SKILL.md` + `scripts/` |
| `workbuddy.md` | ✅ | WorkBuddy 专项奖励必需 |
| `mcp-config.example.json` | ✅ | 仅环境变量占位符，无真实凭证 |
| 项目名称/内容无违规敏感内容 | ✅ | 措辞统一为「均衡膳食 / 营养搭配」，无「减肥 / 节食」表述 |

---

## 五、提交后的预期

- **报名成功**：官方机器人 `M-China-Official` 会在该 Issue 下回复：
  > 您的作品已成功参赛，您可以通过官方项目的排行榜.md查看排名

  （已核对近期他人 Issue #415 / #421 / #422 的实际回复，措辞一致，可据此判定成功。）
- **报名失败**：系统同样会回复失败原因；按提示修改后可**重新提交 Issue**。
- 报名成功后，官方定时采集**公开 Star 数据**，Star > 0 才进入排行榜（`RANKING.md`）。
- Star 定格时间：**2026-10-26 00:00**。提交收货信息截止：**2026-11-14**。
