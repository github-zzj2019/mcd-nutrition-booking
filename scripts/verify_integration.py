#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Integration checks: the orchestrator really reaches every capability.

`SKILL.md` advertises four advanced capabilities (deadline awareness,
infeasibility diagnosis, multi-day scheduling, cross-store arbitration), but
they used to be standalone scripts that the end-to-end path never invoked —
the wiring was documented, not implemented. These checks pin that wiring so it
cannot silently rot again.

They also cover the two failure shapes that matter:

- **zero plans** triggers a diagnosis;
- **plans that all fall short** triggers one too. The second is the common
  case and used to produce a card that looked healthy while the best plan sat
  at 90%.

Run from the repo root:  python scripts/verify_integration.py
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from parse_nutrition import (  # noqa: E402
    parse_nutrition_text, build_index, build_fuzzy_index,
)
from run_scenario import run, decide_mode, _find_pinned  # noqa: E402

FIX = os.path.join(REPO_ROOT, "tests", "fixtures")
NOW = datetime(2026, 10, 11, 10, 0)      # a Sunday-ish reference instant
TODAY = "2026-10-11"
TOMORROW = "2026-10-12"

PASS = 0
FAIL = 0


def check(cond: bool, label: str, extra: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {label}")
    else:
        FAIL += 1
        print(f"  FAIL  {label}" + (f"  <- {extra}" if extra else ""))


def _load(name: str):
    path = os.path.join(FIX, name)
    with open(path, "r", encoding="utf-8") as f:
        return f.read() if name.endswith(".txt") else json.load(f)


def setup() -> dict:
    records = parse_nutrition_text(_load("nutrition_raw.txt"))
    return {
        "index": build_index(records),
        "fuzzy": build_fuzzy_index(records),
        "stores": _load("stores.json"),
        "menus": _load("menus_by_slot.json"),
        "details": _load("details_sample.json"),
        "coupons": _load("coupons_sample.json"),
        "campaigns": _load("campaigns_sample.json"),
        "store_menus": _load("menus_by_store.json"),
        "date_menus": _load("menus_by_date.json"),
    }


def go(c: dict, text: str, *, now=NOW, pin=None,
       store_menus=True, date_menus=True, stores=True):
    return run(
        text, now,
        c["stores"] if stores else {},
        c["menus"], c["index"], c["fuzzy"], c["details"],
        c["coupons"], c["campaigns"], 3, pin,
        c["store_menus"] if store_menus else None,
        c["date_menus"] if date_menus else None,
    )


# --------------------------------------------------------------------------- #
def check_routing(c: dict) -> None:
    print("【1】模式路由（planMode 必须优先于 timeStatus）")
    cons_week = {"planMode": "week", "timeStatus": "span"}
    check(decide_mode(cons_week, None, True, True)[0] == "week",
          "多日意图 → week，不被 span 状态误判为反向模式")

    cons_cs = {"planMode": "cross_store", "timeStatus": "explicit"}
    check(decide_mode(cons_cs, None, True, True)[0] == "cross_store",
          "跨店意图 + 明确时间 → cross_store")

    cons_cs_p = {"planMode": "cross_store", "timeStatus": "pending"}
    mode, note = decide_mode(cons_cs_p, None, True, True)
    check(mode == "reverse" and note is not None,
          "跨店意图 + 无时间 → 降级为 reverse 并给出说明", str(note))

    mode, note = decide_mode(cons_week, None, True, False)
    check(mode == "forward" and note is not None,
          "week 但缺多日菜单 → 降级为 forward 并说明原因", str(note))

    mode, note = decide_mode(cons_cs, None, False, True)
    check(mode == "forward" and note is not None,
          "cross_store 但缺多店菜单 → 降级为 forward 并说明原因", str(note))

    check(decide_mode({"planMode": "single", "timeStatus": "pending"},
                      None, True, True)[0] == "reverse",
          "「约几点合适」 → reverse")
    check(decide_mode({"planMode": "single", "timeStatus": "explicit"},
                      None, True, True)[0] == "forward",
          "明确时间 → forward")
    check(decide_mode({"planMode": "week", "timeStatus": "span"},
                      "巨无霸三件套", True, True)[0] == "pin",
          "--pin 优先级最高，压过多日意图")


def check_diagnosis(c: dict) -> None:
    print("【2】诊断自动挂载（零方案 + 无一达标 两种失败形态）")

    # 零方案：预算 1 元，所有餐品价格都高于它，硬过滤后一个不剩。
    r = go(c, "明天中午12点，预算1元以内，控制在600 kcal以内")
    check(r["mode"] == "blocked" and r["diagnosis"] is not None,
          "零方案时自动挂载诊断", r["mode"])
    check("被挡下的原因分布" in (r["markdown"] or ""),
          "零方案卡片含阻塞原因分布")

    # 无一达标：有 3 个方案，但最好的也只到 90%。
    r = go(c, "明天中午12点，能量控制在400 kcal以内，蛋白质30g以上")
    check(len(r.get("plans") or []) > 0, "该目标确实有方案（非空）",
          str(len(r.get("plans") or [])))
    check(r["diagnosis"] is not None,
          "有方案但无一达标时同样挂载诊断（关键修复）")
    check("最小放宽建议" in (r["markdown"] or ""),
          "卡片含最小放宽建议表")

    # 完全达标：不该有诊断噪音。
    r = go(c, "明天中午12点在公司楼下吃，帮我控制在600 kcal以内")
    check(r["diagnosis"] is None, "完全达标时不挂载诊断（无噪音）")

    # 团体模式：用 relaxedFor 表达，不套用单人放宽建议。
    r = go(c, "明天中午我和两个同事吃饭，我在控钠，小王增肌蛋白40g以上，"
              "小李随便，预算120")
    check((r.get("plans") or [{}])[0].get("perPerson") is not None,
          "多人场景走团体求解")
    check(r["diagnosis"] is None,
          "团体模式不附加单人诊断（避免误导的放宽量）")

    # Assert on "the diner is named on the card", not on the exact wording —
    # the callout text has been revised once already.
    relaxed = (r.get("plans") or [{}])[0].get("relaxedFor") or []
    check(bool(relaxed),
          "团体求解识别出目标无法完全满足的成员", str(relaxed))
    check(bool(relaxed) and all(n in (r["markdown"] or "") for n in relaxed),
          "被放宽的成员在卡片显著位置被点名", str(relaxed))


def check_urgency(c: dict) -> None:
    print("【3】紧迫度自动挂载（仅当预约日是今天）")
    r = go(c, "今天中午12点在公司楼下吃，帮我控制在600 kcal以内")
    check(r["urgency"] is not None, "预约日为今天 → 挂载紧迫度")
    check("时间紧迫度" in (r["markdown"] or ""), "卡片含紧迫度区块")

    r = go(c, "明天中午12点在公司楼下吃，帮我控制在600 kcal以内")
    check(r["urgency"] is None, "预约日为明天 → 不挂载（避免噪音）")
    check("时间紧迫度" not in (r["markdown"] or ""),
          "明天卡片不含紧迫度区块")


def check_pin(c: dict) -> None:
    print("【4】pin 匹配方向（禁止反向子串）")
    check(_find_pinned([{"mealName": "脆脆薯条"}, {"mealName": "薯条"}],
                       "薯条")["mealName"] == "薯条",
          "精确项存在时优先命中精确项")
    check(_find_pinned([{"mealName": "脆脆薯条"}], "薯条") is None,
          "pin=薯条 不误匹配 脆脆薯条（与 compare_stores 同类缺陷）")
    check(_find_pinned([{"mealName": "巨无霸三件套"}, {"mealName": "巨无霸"}],
                       "巨无霸")["mealName"] == "巨无霸",
          "精确优先于前缀项")
    check(_find_pinned([{"mealName": "巨无霸三件套"}], "巨无霸") is not None,
          "无精确项时前缀兜底仍可用")

    r = go(c, "明天中午还是点巨无霸三件套，但想控制在800 kcal、钠1000毫克以内",
           pin="巨无霸三件套")
    check(r["mode"] == "pin" and r.get("plans"), "端到端 pin 模式可执行")
    check(bool((r.get("plans") or [{}])[0].get("swaps")),
          "pin 方案附上了替换优化建议")


def check_cross_store(c: dict) -> None:
    print("【5】跨店择优（无解给出下一个可约时刻）")
    r = go(c, "明天晚上22点哪家店还能约到600 kcal以内的")
    result = r.get("crossStoreResult") or {}
    check(not result.get("feasible"), "22:00 确实无门店可约")
    nxt = result.get("nearestBookable")
    check(nxt is not None, "全部门店不可约时给出最近可约时刻")
    if nxt:
        check(nxt["start"] > "22:00",
              "推荐时刻晚于请求时刻", nxt["start"])
        check("改约到" in (r["markdown"] or ""),
              "卡片给出「改约到 X 点」的可执行建议")
        check(nxt["storeName"] == "麦当劳北京东大桥餐厅",
              "推荐的是唯一有宵夜段的东大桥店", nxt["storeName"])

    r = go(c, "明天晚上22点半哪家店还能约到600 kcal以内的")
    result = r.get("crossStoreResult") or {}
    check(bool(result.get("feasible")), "22:30 有门店可约")
    if result.get("feasible"):
        best = result["feasible"][0]
        check(best["slotName"] == "宵夜",
              "命中东大桥的宵夜餐段", best["slotName"])
        check(len(result.get("blocked") or []) > 0,
              "同时列出不可约门店，体现同一时间不同门店可约性不同")


def check_week(c: dict) -> None:
    print("【6】多日排程（跨天去重 + 钠提示）")
    r = go(c, "接下来三天中午帮我安排一下，都在600 kcal以内")
    week = r.get("weekResult") or {}
    check(r["mode"] == "week", "多日意图端到端跑通")
    check(week.get("plannedDays", 0) >= 2, "至少两天有方案")
    check(week.get("distinctMeals", 0) == week.get("plannedDays", -1),
          "跨天去重生效：每天不重样",
          f"{week.get('distinctMeals')} vs {week.get('plannedDays')}")
    check("🧂" in (r["markdown"] or ""),
          "周计划对超参考值的钠给出提示（与单卡一致）")


def main() -> int:
    c = setup()
    check_routing(c)
    check_diagnosis(c)
    check_urgency(c)
    check_pin(c)
    check_cross_store(c)
    check_week(c)

    print()
    print(f"RESULT: {'ALL PASS' if FAIL == 0 else 'SOME FAILED'} "
          f"({PASS} passed, {FAIL} failed)")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
