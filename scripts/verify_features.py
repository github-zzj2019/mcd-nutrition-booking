#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Verify the D9-D11 killer features against real MCP payload fixtures.

Checks:
  1. **Reverse slot recommendation** — given nutrition goals and no time, the
     solver ranks every reservable meal segment and recommends a booking time.
  2. **Group goal intersection** — one meal per diner, budget respected, every
     diner's goals scored and reported individually.
  3. **Replacement optimization** — `query-meal-detail` swap options are replayed
     and only goal-improving swaps are kept; swaps without official nutrition
     data are skipped, never fabricated.
  4. **Guardrails** — extreme low-calorie targets are rejected, and plans with
     unmatchable items never leak into results.

Run from the repository root:
    python scripts/verify_features.py
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
    parse_nutrition_text, build_index, build_fuzzy_index,
)
from plan_meal import (  # noqa: E402
    plan, plan_group, candidate_pool, hard_filter, DEFAULT_ENERGY_MIN,
)
from recommend_slot import recommend_slot  # noqa: E402
from optimize_replacements import optimize, parse_diff_price  # noqa: E402

FIXTURES = os.path.join(REPO_ROOT, "tests", "fixtures")

_ok = True


def check(cond: bool, msg: str) -> None:
    global _ok  # noqa: PLW0603
    print(("  PASS  " if cond else "  FAIL  ") + msg)
    _ok = _ok and cond


def load_json(name: str) -> dict:
    with open(os.path.join(FIXTURES, name), "r", encoding="utf-8") as f:
        return json.load(f)


def load_text(name: str) -> str:
    with open(os.path.join(FIXTURES, name), "r", encoding="utf-8") as f:
        return f.read()


def main() -> int:
    records = parse_nutrition_text(load_text("nutrition_raw.txt"))
    nutrition_index = build_index(records)
    fuzzy_index = build_fuzzy_index(records)
    details = load_json("details_sample.json")
    slots = load_json("menus_by_slot.json")
    lunch_menu = slots["slots"]["午餐"]["menu"]

    # ------------------------------------------------------------------ #
    print("=== 1) 反向时段推荐（场景B：目标已知，时间未知）===")
    constraints_b = load_json("constraints_reverse.json")
    result = recommend_slot(slots, nutrition_index, fuzzy_index, details, constraints_b)
    ranking = result["ranking"]
    check(len(ranking) >= 2, f"{len(ranking)} 个餐段给出可行解")
    top = ranking[0]
    print(f"    结论：{result['conclusion']}")
    for r in ranking:
        b = r["bestPlan"]
        t = b["totals"]
        print(f"      {r['slotName']}{r['slotRange']:>14}  {b['mealName']:<12}"
              f" {t['energyKcal']:>6.0f}kcal  蛋白{t['protein']:>4.0f}g  "
              f"钠{t['sodium']:>5.0f}mg  评分 {r['score']}")
    check(top["slotName"] == "早餐",
          f"最优餐段 = {top['slotName']}（早餐时段确实能吃到更高的蛋白/能量比）")
    check(top["score"] >= ranking[-1]["score"],
          f"排序单调：top {top['score']} >= last {ranking[-1]['score']}")
    check(bool(top.get("reason")) and "建议" in top["reason"],
          "生成了『为什么这个时间』的解释")
    print(f"    理由：{top['reason']}")

    # ------------------------------------------------------------------ #
    print("\n=== 2) 多人目标求交（场景C：3人、目标各异、预算120）===")
    constraints_c = {
        "storeCode": "1950121", "storeName": "麦当劳北京朝外餐厅",
        "slotName": "午餐", "slotRange": "(10:44至14:15)",
        "targetTime": "2026-10-12 12:00",
        "budget": 120,
        "people": [
            {"name": "我", "goals": {"energy": {"op": "max", "value": 600},
                                     "sodium": {"op": "max", "value": 800}}},
            {"name": "小王", "goals": {"protein": {"op": "min", "value": 40}}},
            {"name": "小李", "goals": {}},
        ],
    }
    group = plan_group(lunch_menu, nutrition_index, fuzzy_index, details,
                       constraints_c, top=3)
    check(len(group) > 0, f"返回 {len(group)} 个团体方案")
    if group:
        g0 = group[0]
        check(len(g0["perPerson"]) == 3,
              f"方案含 {len(g0['perPerson'])} 位成员的独立组合")
        for p in g0["perPerson"]:
            print(f"      {p['name']:<4}（{p['goalText']:<18}）→ {p['mealName']:<12} ¥{p['price']:g}"
                  f"  评分 {p['score']}")
        check(g0["price"] <= 120, f"总价 ¥{g0['price']:g} 未超预算 ¥120")
        names = [p["name"] for p in g0["perPerson"]]
        check(names == ["我", "小王", "小李"], f"成员顺序与输入一致：{names}")
        check(g0.get("complete") is True, "团体方案营养核算完整（无缺失项）")
        # 小王拿到的应是蛋白最高的一档
        wang = [p for p in g0["perPerson"] if p["name"] == "小王"][0]
        check(wang["score"] > 0, f"小王拿到高蛋白组合（达成度 {wang['score']}）")

    # ------------------------------------------------------------------ #
    print("\n=== 3) 替换优化（场景D：巨无霸三件套求更轻搭配）===")
    check(parse_diff_price("- ¥1.5") == -1.5, "diffPrice 解析：- ¥1.5 -> -1.5")
    check(parse_diff_price("+ ¥9") == 9.0, "diffPrice 解析：+ ¥9 -> 9.0")

    pool = candidate_pool(lunch_menu, nutrition_index, fuzzy_index, details)
    target = next((c for c in pool if c["mealName"] == "巨无霸三件套"), None)
    check(target is not None, "在候选池中找到 巨无霸三件套")
    if target:
        base_items = target["items"]
        print(f"    原始组成：{' + '.join(base_items)}")
        print(f"    原始营养：{target['totals']['energyKcal']:.0f} kcal / "
              f"蛋白 {target['totals']['protein']:.0f} g / 钠 {target['totals']['sodium']:.0f} mg")
        from plan_meal import score_plan  # noqa: PLC0415
        goals_d = {"energy": {"op": "max", "value": 800},
                   "sodium": {"op": "max", "value": 1000}}
        target["score"] = score_plan(target["totals"], goals_d)["score"]
        target["detail"] = score_plan(target["totals"], goals_d)["detail"]
        opt = optimize(target, nutrition_index, fuzzy_index, goals_d, top=3)
        print(f"    基线评分：{opt['baseScore']}")
        for s in opt["swaps"]:
            print(f"      {s['round']}：{s['from']} → {s['to']}（{s['diffPrice']}）"
                  f"  评分 {opt['baseScore']} → {s['newScore']}  {s['deltaText']}")
        check(len(opt["swaps"]) > 0, f"找到 {len(opt['swaps'])} 个有改善的替换")
        if opt["swaps"]:
            best_swap = opt["swaps"][0]
            check(best_swap["scoreDelta"] > 0,
                  f"最优替换提升评分 +{best_swap['scoreDelta']}")
            check(best_swap["to"] == "小杯玉米杯",
                  f"最优替换命中 中薯条 → {best_swap['to']}")
        print(f"    因无官方营养数据而跳过的替换：{len(opt['skipped'])} 个")
        for s in opt["skipped"]:
            print(f"      - {s['to']}：{s['reason']}")

    # ------------------------------------------------------------------ #
    print("\n=== 4) 护栏 ===")
    extreme = {"energy": {"op": "max", "value": 100}}
    leaked = [c for c in pool
              if c["complete"] and hard_filter(c["totals"], extreme)
              and c["totals"]["energyKcal"] < DEFAULT_ENERGY_MIN]
    check(len(leaked) == 0, f"极端低卡目标下泄漏 {len(leaked)} 个方案（期望 0）")
    low_energy = {"totals": {"energyKcal": 120, "protein": 3, "fat": 2,
                             "carbohydrate": 10, "sodium": 100, "calcium": 10}}
    check(hard_filter(low_energy["totals"], {}) is False,
          f"能量 {DEFAULT_ENERGY_MIN:g} kcal 安全下限生效")
    fabricated = [c for c in pool if not c["complete"]]
    print(f"    候选池中营养数据不完整的餐品：{len(fabricated)} 个"
          f"（如 {fabricated[0]['mealName'] if fabricated else '-'}），均不进入推荐")
    plans_a = plan(lunch_menu, nutrition_index, fuzzy_index, details,
                   load_json("constraints_sample.json"), top=99)
    check(all(p["complete"] for p in plans_a),
          f"{len(plans_a)} 个最终方案全部完整，无编造数值")

    print("\nRESULT:", "ALL PASS" if _ok else "SOME FAILED")
    return 0 if _ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
