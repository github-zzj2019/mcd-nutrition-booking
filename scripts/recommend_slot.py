#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Reverse mode: solve for the *time* instead of the meal.

This is the sharpest differentiator of the skill. A nutrition-lookup tool
answers "what should I eat"; this module answers **"what time should I book"**
by running the constraint solver once per reservable meal segment and comparing
the best achievable plan in each.

The two-way constraint being exploited:

    正向：预约时间 -> 决定可点餐段菜单 -> 决定营养可行域
    反向：营养目标 -> 反推最优餐段 -> 推荐预约时间

Measured basis: `query-meals` takes a ``reservationDate`` and returns a
segment-specific menu (breakfast 08:00 vs lunch 12:00 differ completely), while
`query-nearby-stores` exposes the bookable segments per day.

Usage:
    python scripts/recommend_slot.py \
        --slots tests/fixtures/menus_by_slot.json \
        --nutrition tests/fixtures/nutrition_raw.txt \
        --details tests/fixtures/details_sample.json \
        --constraints tests/fixtures/constraints_reverse.json \
        --markdown
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from parse_nutrition import (  # noqa: E402
    parse_nutrition_text,
    build_index,
    build_fuzzy_index,
)
from plan_meal import plan, plan_group, GOAL_LABELS  # noqa: E402


def _load_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _load_text(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _fmt(value: Any, unit: str) -> str:
    if value is None:
        return "暂无数据"
    return f"{float(value):g} {unit}"


def _goal_line(detail: dict[str, Any]) -> str:
    """Render the achieved-goals summary, e.g. 蛋白 100% / 钠 100%."""
    parts: list[str] = []
    for key, d in (detail or {}).items():
        label, _unit = GOAL_LABELS.get(key, (key, ""))
        parts.append(f"{label} {round(float(d.get('achieved', 0)) * 100)}%")
    return "、".join(parts) if parts else "无量化目标"


def _build_slot_reason(top: dict[str, Any], runner_up: dict[str, Any] | None) -> str:
    """Explain why this slot beats the others, with real numbers."""
    best = top.get("bestPlan") or {}
    t = best.get("totals") or {}
    bits = [
        f"{top['slotName']}{top.get('slotRange', '')}的菜单里，"
        f"「{best.get('mealName', '-')}」整单 "
        f"{_fmt(t.get('energyKcal'), 'kcal')}、蛋白 {_fmt(t.get('protein'), 'g')}、"
        f"钠 {_fmt(t.get('sodium'), 'mg')}，"
        f"{_goal_line(best.get('detail'))}"
    ]
    if runner_up:
        rbest = runner_up.get("bestPlan") or {}
        rt = rbest.get("totals") or {}
        bits.append(
            f"{runner_up['slotName']}时段最优的「{rbest.get('mealName', '-')}」为 "
            f"{_fmt(rt.get('energyKcal'), 'kcal')}、蛋白 {_fmt(rt.get('protein'), 'g')}、"
            f"钠 {_fmt(rt.get('sodium'), 'mg')}，{_goal_line(rbest.get('detail'))}"
        )
    return "；".join(bits) + f"。因此建议把预约时间定在{top['slotName']}时段。"


def recommend_slot(slots_payload: dict, nutrition_index: dict, fuzzy_index: dict,
                   details: dict, constraints: dict) -> dict[str, Any]:
    """Run the solver per meal segment and rank the segments.

    ``slots_payload`` is ``{"storeCode","storeName","date","slots":{name: {...}}}``
    where each slot carries ``slotRange``, ``suggestTime`` and ``menu``
    (a raw `query-meals` payload).
    """
    store_code = slots_payload.get("storeCode") or constraints.get("storeCode") or ""
    store_name = slots_payload.get("storeName") or constraints.get("storeName") or ""
    people = constraints.get("people") or []
    use_group = len(people) > 1

    feasible: list[dict[str, Any]] = []
    infeasible: list[dict[str, Any]] = []

    for slot_name, slot in (slots_payload.get("slots") or {}).items():
        slot_constraints = {
            **constraints,
            "slotName": slot_name,
            "slotRange": slot.get("slotRange", ""),
            "storeCode": store_code,
            "storeName": store_name,
            "targetTime": slot.get("suggestTime"),
            "slotSource": "recommended",
        }
        solver = plan_group if use_group else plan
        plans = solver(slot.get("menu") or {}, nutrition_index, fuzzy_index,
                       details, slot_constraints, top=1)
        if not plans:
            infeasible.append({"slotName": slot_name,
                               "slotRange": slot.get("slotRange", ""),
                               "suggestTime": slot.get("suggestTime"),
                               "reason": "该餐段内没有满足目标的组合"})
            continue
        feasible.append({
            "slotName": slot_name,
            "slotRange": slot.get("slotRange", ""),
            "suggestTime": slot.get("suggestTime"),
            "bestPlan": plans[0],
            "score": plans[0].get("score", 0.0),
            "price": plans[0].get("price", 0.0),
        })

    # Rank: goal achievement first, then price (cheaper wins a tie).
    feasible.sort(key=lambda r: (-r["score"], r["price"]))
    if feasible:
        runner_up = feasible[1] if len(feasible) > 1 else None
        feasible[0]["reason"] = _build_slot_reason(feasible[0], runner_up)

    return {
        "mode": "slot",
        "storeCode": store_code,
        "storeName": store_name,
        "date": slots_payload.get("date"),
        "goals": [{"name": p.get("name"), "goalText": (p.get("goals") or {})}
                  for p in people],
        "ranking": feasible,
        "infeasible": infeasible,
        "conclusion": (
            f"建议预约 {feasible[0]['suggestTime']}（{feasible[0]['slotName']}"
            f"{feasible[0]['slotRange']}）"
            if feasible else
            "所有可预约餐段均无解，建议放宽营养目标或改选其他门店"
        ),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Reverse time-slot recommendation")
    ap.add_argument("--slots", required=True, help="menus_by_slot.json")
    ap.add_argument("--nutrition", required=True, help="raw nutrition text table")
    ap.add_argument("--details", required=True, help="{code: query-meal-detail response}")
    ap.add_argument("--constraints", required=True, help="constraints JSON (no targetTime)")
    ap.add_argument("--markdown", action="store_true", help="render the card instead of JSON")
    args = ap.parse_args()

    records = parse_nutrition_text(_load_text(args.nutrition))
    result = recommend_slot(
        _load_json(args.slots),
        build_index(records),
        build_fuzzy_index(records),
        _load_json(args.details),
        _load_json(args.constraints),
    )

    if args.markdown:
        from render_card import render_slot_recommendation  # noqa: PLC0415
        sys.stdout.write(render_slot_recommendation(result["ranking"]))
        if result["infeasible"]:
            sys.stdout.write("\n**无解餐段**："
                             + "、".join(f"{s['slotName']}{s['slotRange']}"
                                        for s in result["infeasible"])
                             + "\n")
    else:
        json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
