#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Verify the core solver (D4-D6) against real MCP payload fixtures.

Checks:
  1. `decompose()` correctly extracts default single items from
     `query-meal-detail` rounds for 巨无霸三件套 (lunch) and
     猪柳蛋麦满分套餐 (breakfast).
  2. Replacement options are surfaced (e.g. 中薯条 -> 小杯玉米杯).
  3. `plan()` ranks the lunch menu under scenario A constraints
     (energy <= 600 kcal, sodium <= 800 mg) and returns Top-3.
  4. Plans with unmatchable items are dropped rather than fabricated.

Run from the repository root:
    python scripts/verify_solver.py
"""

from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from parse_nutrition import (  # noqa: E402
    parse_nutrition_text,
    build_index,
    build_fuzzy_index,
)
from plan_meal import decompose, plan  # noqa: E402

FIXTURES = os.path.join(REPO_ROOT, "tests", "fixtures")

_ok = True


def check(cond: bool, msg: str) -> None:
    global _ok  # noqa: PLW0603
    print(("  PASS  " if cond else "  FAIL  ") + msg)
    _ok = _ok and cond


def load_json(name: str) -> Any:  # noqa: F405
    with open(os.path.join(FIXTURES, name), "r", encoding="utf-8") as f:
        return json.load(f)


def load_text(name: str) -> str:
    with open(os.path.join(FIXTURES, name), "r", encoding="utf-8") as f:
        return f.read()


def main() -> int:
    # ---- data layer (already verified, rebuilt here for the solver) ----
    records = parse_nutrition_text(load_text("nutrition_raw.txt"))
    nutrition_index = build_index(records)
    fuzzy_index = build_fuzzy_index(records)
    print(f"nutrition records: {len(records)} (unique: {len(nutrition_index)})\n")

    details = load_json("details_sample.json")
    menu = load_json("menu_lunch_sample.json")
    constraints = load_json("constraints_sample.json")

    # ---- 1) decompose lunch combo ----
    print("=== 1) decompose 巨无霸三件套 (lunch) ===")
    dec = decompose(details["9900005466"])
    check(dec["name"] == "巨无霸三件套", f"name = {dec['name']}")
    check(dec["defaultItems"] == ["巨无霸", "中薯条", "可乐中杯"],
          f"defaultItems = {dec['defaultItems']}")
    check(len(dec["replacements"]) > 0,
          f"{len(dec['replacements'])} replacement option(s) surfaced")
    side_swap = [r for r in dec["replacements"] if r["to"] == "小杯玉米杯"]
    check(bool(side_swap), "replacement 中薯条 -> 小杯玉米杯 detected")
    if side_swap:
        print(f"        {side_swap[0]['round']}: {side_swap[0]['from']} -> "
              f"{side_swap[0]['to']} ({side_swap[0]['diffPrice']})")

    # ---- 2) decompose breakfast combo ----
    print("\n=== 2) decompose 猪柳蛋麦满分套餐 (breakfast) ===")
    dec_b = decompose(details["9900003458"])
    check(dec_b["defaultItems"] == ["猪柳蛋麦满分", "脆薯饼", "鲜萃冰咖"],
          f"defaultItems = {dec_b['defaultItems']}")

    # ---- 3) plan() under scenario A ----
    print("\n=== 3) plan() — 场景A：明天 12:00，整单 <=600kcal，钠 <=800mg ===")
    results = plan(menu, nutrition_index, fuzzy_index, details, constraints, top=3)
    check(len(results) > 0, f"returned {len(results)} plan(s)")
    for p in results:
        t = p["totals"]
        print(f"\n  方案 {p['rank']}｜{p['mealName']}  ¥{p['price']}")
        print(f"    组成：{', '.join(p['items'])}")
        print(f"    营养：{t['energyKcal']:g} kcal / 蛋白 {t['protein']:g} g / "
              f"钠 {t['sodium']:g} mg")
        print(f"    评分：{p['score']}  完整：{p['complete']}")
        if p["missing"]:
            print(f"    缺失营养数据：{p['missing']}")
        print(f"    理由：{p['reason']}")

    # ---- 4) no fabrication ----
    print("\n=== 4) 缺失项不编造 ===")
    dropped = [p for p in plan(menu, nutrition_index, fuzzy_index,
                                details, constraints, top=999)
               if not p["complete"]]
    check(len(dropped) == 0,
          f"{len(dropped)} incomplete plan(s) leaked into results (expect 0)")

    print("\nRESULT:", "ALL PASS" if _ok else "SOME FAILED")
    return 0 if _ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
