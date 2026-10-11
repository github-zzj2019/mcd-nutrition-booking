---
name: mcd-nutrition-booking
description: Plan a McDonald's meal reservation that satisfies personalized nutrition targets (energy, protein, fat, carbohydrate, sodium, calcium) and budget, by solving the two-way constraint between the reservation time slot and the menu segment. This skill should be used when a user wants to order or reserve McDonald's food for a specific time and for one or more people, with nutrition goals, coupon usage, or campaign windows to consider. It orchestrates the McDonald's MCP tools (nearby stores, per-slot real-time menus, meal details, nutrition data, store coupons, price calculation, campaign calendar) to output a ranked reservation plan card, and never places an order without explicit user confirmation.
agent_created: true
---

# 麦麦营养预约规划师 (McDonald's Nutrition Booking Planner)

## Purpose

Turn a natural-language request such as "明天中午12点约了朋友在公司楼下吃饭，帮我控制在600 kcal以内" into a **reservation plan card**: which store, which time slot, which meal combination, the aggregated nutrition, the price after coupons, running-campaign hints, and the reason for choosing that time.

The core value is solving the **two-way constraint between time and nutrition**:

```
正向：预约时间 → 决定可点餐段菜单 → 决定营养可行域
反向：营养目标 → 反推最优餐段 → 推荐预约时间
```

## When to use

Trigger this skill when the user wants to **order or reserve** McDonald's food (dine-in, takeaway, delivery) AND mentions any of:

- a **time** (today/tomorrow, a specific date or clock time)
- a **store / place**
- **multiple people**
- **nutrition goals** (energy/kcal, protein, fat, carbohydrate, sodium, calcium — e.g. 控卡 / 控钠 / 增肌)
- **budget / coupons**

Do NOT use for: pure nutrition lookup without ordering intent; browsing the campaign calendar alone; 积分抽奖 or 麦麦商城 (out of scope); placing an order without explicit confirmation.

## Workflow

### Step 1 — Clarify and normalize the request

Call `mcp__mcd-mcp__now-time-info` to get the current server time and timezone.
Run `scripts/parse_request.py --text "<utterance>" --now "<yyyy-MM-dd HH:MM>"` to obtain a
**constraint object**:

- `timeStatus`: `explicit` when a clock time was given; `pending` when the user asks
  「约几点合适」or states goals without a time — **pending means reverse mode**
- `targetTime` (may be null)
- `city` / `keyword` (store location)
- `people`: list of `{name, goals}`, where goals are bounds such as `energy<=600`, `protein>=30`, `sodium<=800`
- `budget` (optional upper bound)
- `warnings`: parse caveats that must be surfaced to the user

The parser understands Chinese units (kcal/大卡/卡, g/克, mg/毫克) and negation forms
(「别超过」「不想超过」), and rejects physiologically implausible per-meal targets.

If critical information is still missing, ask a clarifying question before proceeding.

### Step 2 — Resolve the store and time slots

Call `mcp__mcd-mcp__query-nearby-stores` with `beType=1`, `searchType=2`, `city`, `keyword`.

- Keep stores with `reservation=true`.
- **Present every entry of `reservationTimeOptions` to the user verbatim; never omit or summarize them.**
- Run `scripts/resolve_store.py` to parse `reservationTimeOptions` into structured meal segments (e.g. `早餐 06:14–10:15`) and match the target time to a segment.
- If the target time falls in a segment gap (~29 min kitchen switching window), tell the user and suggest the nearest slot.

### Step 3 — Pull the real-time menu for the target slot

Call `mcp__mcd-mcp__query-meals` with `storeCode`, `orderType=1`, `beType=1`, and `reservationDate="yyyy-MM-dd HH:mm"` (the chosen slot's time).
Verify the returned categories match the expected segment (e.g. the breakfast menu must not contain 巨无霸). This confirms the forward link.

### Step 4 — Attach nutrition data

Call `mcp__mcd-mcp__list-nutrition-foods`. Run `scripts/parse_nutrition.py` to convert the returned **text table** into a structured index keyed by product name.
For each candidate meal (usually combo sets), call `mcp__mcd-mcp__query-meal-detail` with the meal `code` to **decompose the combo into single items**, then match each single item against the nutrition index and sum them.

Matching policy: exact name → fuzzy (strip spaces / brackets / spec words) → **missing**.

When an item is missing: mark it "暂无官方营养数据", mark the whole plan "不完整", and **never fabricate values**. If a core main item is missing, drop the plan from the recommendation list.

### Step 5 — Solve constraints and rank

Run `scripts/plan_meal.py` with the menu, nutrition index, and constraint object.

- Hard filters: energy range (with a lower safety bound of 300 kcal), a severe-sodium
  breach ceiling, and the budget upper bound.
- Soft scoring: weighted achievement of each target; ties break on lower price.
- Output Top 3 plans.

Three modes share this solver:

- **Reverse mode** (`timeStatus == "pending"`) — pull the menu for **every** reservable
  segment and run `scripts/recommend_slot.py`. It scores each segment's best plan and
  outputs 「建议约 X 点」 together with a comparison against the runner-up slot.
- **Group mode** (more than one person with distinct goals) — `plan_meal.plan_group()`
  assigns one meal per diner and maximises total achievement under the shared budget.
  Two people may receive the same meal; that is legitimate. If a diner has no fully
  satisfying meal in this segment, relax for that diner and say so on the card.
- **Replacement optimization** — `scripts/optimize_replacements.py` replays each swap
  from `query-meal-detail`, keeps only those that improve the score, and **skips** swaps
  whose target item has no official nutrition record (never fabricate a value). Attach
  the suggestions to the Top 1 plan.

### Step 6 — Add coupons and campaign hints

Call `mcp__mcd-mcp__query-store-coupons` with `storeCode` (+ `orderType`, `beType`, and
`reservationDate` for reservations) to get coupons **already validated** by MCP for this
store and order type. Never compute coupon validity yourself.

Its payload is richer than `available-coupons`: it carries `tradeDateTime` (validity
window), `couponCode`, and `products[].productCode` — and that product code lives in the
**same code space as `query-meals`**. Run `scripts/apply_benefits.py` to match coupons to
plans deterministically by code instead of fuzzy title matching.

**Critical boundary**: the coupon payload contains **no discount amount**. Never invent
one. Obtain the payable amount from `mcp__mcd-mcp__calculate-price` and pass it in via
the `price_map`; without it the card must say the amount is pending. Call
`mcp__mcd-mcp__auto-bind-coupons` **only with explicit user consent**.

Call `mcp__mcd-mcp__campaign-calendar` with the reservation date. It groups campaigns
**by day**, so a campaign absent from the reservation date's list is not running that day
regardless of its window. If a campaign is active and its segment matches the booking,
append **one line** to the card. Do not build a campaign browser.

### Step 7 — Render the plan card and confirm

Run `scripts/render_card.py` to render the Markdown plan card (combination, nutrition
totals, goal-achievement table, price, coupon line, campaign hint, and the
「为什么选这个时间」 explanation). `scripts/run_scenario.py` runs steps 1–7 end to end
and writes the card to a file.

Card rules that must hold:

- Distinguish **who chose the time**. When the user specified it, say
  「你指定的时间落在…」; when the solver recommended it, say「推荐约在…」. Never imply
  the tool overrode the user's time.
- When total sodium exceeds the per-meal reference (700 mg, i.e. 1/3 of the 2000 mg
  daily limit), append the sodium advisory. McDonald's items are sodium-heavy; the
  honest framing is "best available in this segment", not "low sodium".
- Group cards must show **each diner's** achievement, not just the group total.

**Never call `create-order` unless the user explicitly confirms the exact plan.** On confirmation, call `mcp__mcd-mcp__create-order` with the resolved store, slot, items and coupon.

## Guardrails

- **Order placement**: only after explicit user confirmation of the final plan. Never auto-order.
- **`auto-bind-coupons`**: only with explicit user consent (it is an account write operation).
- **`draw-lottery`**: a write operation with side effects and out of scope — never call it.
- **Wording**: use 「均衡膳食 / 营养搭配 / 个性化目标」. **Never use 「减肥 / 节食 / 断食」**; enforce a lower energy bound and reject extreme low-calorie targets.
- **Missing data**: explicitly state "暂无官方营养数据" instead of guessing or fabricating.
- **Rate limit**: 600 req/min; reuse cached menu and nutrition-table pulls during enumeration.

## Bundled resources

- `scripts/parse_request.py` — utterance → constraint object (incl. reverse-mode detection).
- `scripts/resolve_store.py` — store time options → structured segments; gap detection.
- `scripts/parse_nutrition.py` — nutrition text table → exact + fuzzy indexes.
- `scripts/plan_meal.py` — combo decomposition, hard filter, scoring, ranking, group mode.
- `scripts/recommend_slot.py` — reverse mode: rank segments, recommend a booking time.
- `scripts/optimize_replacements.py` — swap evaluation driven by `query-meal-detail`.
- `scripts/apply_benefits.py` — coupon matching by `productCode` + campaign cross-hint.
- `scripts/render_card.py` — Markdown plan card renderer.
- `scripts/run_scenario.py` — end-to-end pipeline; accepts `--pin <meal>` for scenario D.
- `scripts/run_all_checks.py` — runs every verification suite; non-zero on failure.
- `references/mcp_tools.md` — MCP tool cheat sheet and measured return fields.
- `references/nutrition_reference.md` — dietary reference intakes used for per-meal targets.
- `tests/fixtures/` — real MCP payloads, so every result is reproducible without a Token.

Verify a change with `python scripts/run_all_checks.py` before shipping it.
