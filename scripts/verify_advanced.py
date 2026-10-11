#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Verification for the four advanced features.

Run from the repository root:
    python scripts/verify_advanced.py

Exits non-zero on any failure so it can act as a CI gate.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from parse_nutrition import (  # noqa: E402
    parse_nutrition_text, build_index, build_fuzzy_index,
)
from time_guard import guard, segment_states  # noqa: E402
from compare_stores import compare_stores, availability  # noqa: E402
from diagnose import diagnose  # noqa: E402
from plan_week import plan_week  # noqa: E402

FIXTURES = os.path.join(REPO_ROOT, "tests", "fixtures")
FAILURES: list[str] = []


def check(condition: bool, label: str, detail: str = "") -> None:
    if condition:
        print(f"  PASS  {label}")
    else:
        FAILURES.append(label)
        print(f"  FAIL  {label}" + (f" -- {detail}" if detail else ""))


def load_json(name: str) -> Any:
    with open(os.path.join(FIXTURES, name), "r", encoding="utf-8") as f:
        return json.load(f)


def load_text(name: str) -> str:
    with open(os.path.join(FIXTURES, name), "r", encoding="utf-8") as f:
        return f.read()


def main() -> int:
    records = parse_nutrition_text(load_text("nutrition_raw.txt"))
    n_index = build_index(records)
    f_index = build_fuzzy_index(records)
    details = load_json("details_sample.json")
    stores = load_json("stores.json")
    store_menus = load_json("menus_by_store.json")
    date_menus = load_json("menus_by_date.json")

    # ------------------------------------------------------------------ #
    print("\n[1] 时间紧迫度 / 餐段截止感知 (time_guard)")
    # ------------------------------------------------------------------ #
    g_today = guard(stores, "2026-10-11 12:39", "2026-10-11")
    check(g_today.get("status") == "ok", "今天（10-11）返回可解析的餐段状态")
    cur = g_today.get("current") or {}
    check(cur.get("name") == "午餐", "12:39 落在午餐段", str(cur))
    check((cur.get("minutesLeft") or 0) == 96,
          "午餐距 14:15 截止还有 96 分钟", str(cur.get("minutesLeft")))
    # 关键实测事实：接口**不会返回已经结束的餐段**——今天的列表里直接不含早餐，
    # 所以 past 为空，而差异要靠与「明天」的餐段集合做差集才能发现。
    check(not g_today.get("past"),
          "今天的返回列表里不含『已结束』的餐段（MCP 直接不返回过去餐段）",
          str(g_today.get("past")))
    check(all(s["name"] != "早餐" for s in g_today["segments"]),
          "今天的餐段列表里没有早餐")
    check("早餐" in (g_today.get("missingToday") or []),
          "识别出「今天已约不到早餐，但明天可以」",
          str(g_today.get("missingToday")))

    g_tmr = guard(stores, "2026-10-11 12:39", "2026-10-12")
    check(g_tmr.get("current") is None, "未来日期不存在『进行中』的餐段")
    check(len(g_tmr.get("upcoming") or []) == 4,
          "明天有 4 个可约餐段（含早餐）",
          str([s["name"] for s in g_tmr.get("upcoming") or []]))
    check(any(s["name"] == "早餐" and s["state"] == "upcoming"
              for s in g_tmr["segments"]), "明天的早餐段可约")

    g_elapsed = guard(stores, "2026-10-11 12:39", "2026-10-11", "08:00")
    check(g_elapsed.get("targetState") == "elapsed",
          "今天 08:00 被判定为『已经过了』", str(g_elapsed.get("targetState")))

    g_gap = guard(stores, "2026-10-11 12:39", "2026-10-12", "10:30")
    check(g_gap.get("targetState") == "gap", "10:30 落在换餐空档")

    states = segment_states(
        [{"name": "早餐", "start": "06:14", "end": "10:15"}], "12:39", True)
    check(states[0]["state"] == "past", "早餐在 12:39 判定为已结束")

    # ------------------------------------------------------------------ #
    print("\n[2] 多门店横向择优 (compare_stores)")
    # ------------------------------------------------------------------ #
    late = load_json("constraints_late.json")
    cmp_late = compare_stores(store_menus, n_index, f_index, details,
                              late, "2026-10-12 22:30")
    feasible_codes = [f["storeCode"] for f in cmp_late["feasible"]]
    blocked_codes = [b["storeCode"] for b in cmp_late["blocked"]]
    check("1950006" in feasible_codes, "22:30 东大桥店可约（有宵夜段）", str(feasible_codes))
    check("1950121" in blocked_codes, "22:30 朝外店不可约（无宵夜段）", str(blocked_codes))
    check(bool(cmp_late.get("bookabilityNote")), "产出『同一时间不同门店可约性不同』的说明")

    cmp_noon = compare_stores(store_menus, n_index, f_index, details,
                              late, "2026-10-12 12:00")
    check(len(cmp_noon["feasible"]) == 2, "12:00 两家店都可约",
          str([f["storeCode"] for f in cmp_noon["feasible"]]))
    check(cmp_noon["feasible"][0]["distance"] == 226,
          "12:00 场景下最近的朝外店排在首位（无需多走路）")

    avail = availability(store_menus, "脆脆薯条")
    has_map = {a["storeCode"]: a["has"] for a in avail}
    check(has_map.get("1950006") is True, "东大桥有脆脆薯条")
    check(has_map.get("1950121") is False, "朝外没有脆脆薯条")
    avail2 = availability(store_menus, "咖啡阿芙佳朵")
    has_map2 = {a["storeCode"]: a["has"] for a in avail2}
    check(has_map2.get("1950121") is True, "朝外有咖啡阿芙佳朵")
    check(has_map2.get("1950006") is False, "东大桥没有咖啡阿芙佳朵")

    # ------------------------------------------------------------------ #
    print("\n[3] 无解诊断与最小放宽 (diagnose)")
    # ------------------------------------------------------------------ #
    tight = load_json("constraints_tight.json")
    menu_lunch = load_json("menu_lunch_sample.json")
    dg = diagnose(menu_lunch, n_index, f_index, details, tight)
    check(dg.get("feasible") is False, "能量<=400 且 蛋白>=30 被判定为无解")
    check(dg.get("admissibleSize", 0) > 0, "并非『一个方案都没有』，而是没有完全达标的方案")
    binding = {b["key"]: b for b in dg.get("binding") or []}
    check("protein" in binding, "识别出蛋白质是卡住的约束", str(list(binding)))
    pro = binding.get("protein") or {}
    check(pro.get("suggested") == 23.0,
          "蛋白质最小放宽建议为 23 g（板烧鸡腿堡的实际值）", str(pro.get("suggested")))
    check((pro.get("witness") or {}).get("mealName") == "板烧鸡腿堡",
          "放宽后的见证方案是板烧鸡腿堡", str(pro.get("witness")))
    check((pro.get("costPct") or 0) > 0, "给出调整幅度百分比")
    check("板烧鸡腿堡" in (dg.get("advice") or ""), "advice 里点名了具体方案",
          str(dg.get("advice")))

    ok = diagnose(menu_lunch, n_index, f_index, details,
                  load_json("constraints_sample.json"))
    check(ok.get("feasible") is True, "原本可解的目标不被误报为无解")

    # ------------------------------------------------------------------ #
    print("\n[4] 多日排程 (plan_week)")
    # ------------------------------------------------------------------ #
    week = plan_week(date_menus, n_index, f_index, details,
                     load_json("constraints_week.json"))
    check(week["plannedDays"] == 3, "三天都排出了方案", str(week["plannedDays"]))
    check(week["repeatDays"] == 0, "三天没有重样", str(week["repeatDays"]))
    check(week["distinctMeals"] == 3, "共 3 款不重样餐品", str(week["distinctMeals"]))
    names = [d["plan"]["mealName"] for d in week["days"] if d["plan"]]
    check(names == ["麦香鸡", "麦香鱼", "培根蔬萃双层牛堡"],
          "轮换顺序按评分与价格确定且可复现", str(names))
    sodium = [float(d["plan"]["totals"]["sodium"]) for d in week["days"] if d["plan"]]
    check(all(s <= 800 for s in sodium), "每天钠都不超过 800 mg", str(sodium))
    counts = [d["campaignCount"] for d in week["days"]]
    check(len(set(counts)) > 1, "每天的当天活动数不同（2/1/0）", str(counts))

    # ------------------------------------------------------------------ #
    print("\n" + ("=" * 60))
    if FAILURES:
        print(f"RESULT: {len(FAILURES)} FAILED -> " + "; ".join(FAILURES))
        return 1
    print("RESULT: ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
