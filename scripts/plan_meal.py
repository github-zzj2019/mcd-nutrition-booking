#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Core constraint solver: nutrition goals x reservation time slot.

The agent (WorkBuddy) calls the McDonald's MCP tools, then feeds the returned
payloads into this module:

  - `query-meals`      -> ``menu``          (categories + meals map)
  - `query-meal-detail`-> ``details``       (code -> raw detail response)
  - `list-nutrition-foods` (parsed via ``parse_nutrition.py``) -> ``nutrition_index`` / ``fuzzy_index``

``plan()`` then:
  1. Walks every purchasable meal in the slot's menu.
  2. Decomposes combos (套餐) into single items via `query-meal-detail` rounds.
  3. Sums nutrition per single item (exact -> fuzzy -> missing).
  4. Applies hard constraints (energy safety bounds, severe sodium breach, budget).
  5. Scores soft goal achievement and returns the Top-N plans.

Run from the repository root:
    python scripts/plan_meal.py \
        --menu tests/fixtures/menu_lunch_sample.json \
        --nutrition tests/fixtures/nutrition_raw.txt \
        --details tests/fixtures/details_sample.json \
        --constraints tests/fixtures/constraints_sample.json
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
import sys
from typing import Any, Iterable

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

# --------------------------------------------------------------------------- #
# Per-meal safe bounds (see references/nutrition_reference.md)
# --------------------------------------------------------------------------- #
DEFAULT_ENERGY_MIN = 300.0    # 安全下限：拒绝极端低卡目标
DEFAULT_ENERGY_MAX = 900.0    # 默认整餐能量上限
SODIUM_HARD_MULTIPLIER = 1.5  # 仅当钠 > 目标 * 1.5 才硬拦截

# goal key -> (nutrition field, default operator)
GOAL_KEYS: dict[str, tuple[str, str]] = {
    "energy": ("energyKcal", "max"),
    "protein": ("protein", "min"),
    "fat": ("fat", "max"),
    "carbohydrate": ("carbohydrate", "max"),
    "sodium": ("sodium", "max"),
    "calcium": ("calcium", "min"),
}

TOTAL_FIELDS = ("energyKcal", "protein", "fat", "carbohydrate", "sodium", "calcium")

GOAL_LABELS: dict[str, tuple[str, str]] = {
    "energy": ("能量", "kcal"),
    "protein": ("蛋白质", "g"),
    "fat": ("脂肪", "g"),
    "carbohydrate": ("碳水", "g"),
    "sodium": ("钠", "mg"),
    "calcium": ("钙", "mg"),
}


def goal_text(goals: dict[str, dict]) -> str:
    """Render a goal set as human-readable Chinese (e.g. ``能量≤600kcal、钠≤800mg``)."""
    parts: list[str] = []
    for key, g in (goals or {}).items():
        label, unit = GOAL_LABELS.get(key, (key, ""))
        default_op = GOAL_KEYS.get(key, ("", "max"))[1]
        op = "≤" if g.get("op", default_op) == "max" else "≥"
        parts.append(f"{label}{op}{float(g.get('value', 0)):g}{unit}")
    return "、".join(parts) if parts else "无特殊目标"

try:  # package context
    from .parse_nutrition import normalize_name  # type: ignore
except ImportError:  # script context
    from parse_nutrition import normalize_name  # type: ignore


# --------------------------------------------------------------------------- #
# Nutrition lookup / aggregation
# --------------------------------------------------------------------------- #
def lookup(item_name: str, index: dict[str, dict], fuzzy_index: dict[str, dict]) -> dict[str, Any]:
    """Exact -> fuzzy -> missing lookup for a single item name."""
    if item_name in index:
        return {"status": "exact", "record": index[item_name]}
    key = normalize_name(item_name)
    if key and key in fuzzy_index:
        return {"status": "fuzzy", "record": fuzzy_index[key]}
    return {"status": "missing", "record": None}


def accumulate(items: Iterable[str], nutrition_index: dict[str, dict],
               fuzzy_index: dict[str, dict]) -> dict[str, Any]:
    """Sum nutrition over a list of single-item names."""
    totals = {k: 0.0 for k in TOTAL_FIELDS}
    missing: list[str] = []
    for name in items:
        hit = lookup(name, nutrition_index, fuzzy_index)
        rec = hit["record"]
        if rec is None:
            missing.append(name)
            continue
        for k in TOTAL_FIELDS:
            v = rec.get(k)
            if v is not None:
                totals[k] += float(v)
    return {"totals": totals, "missing": missing, "complete": not missing}


def score_plan(totals: dict[str, float], goals: dict[str, dict],
               weights: dict[str, float] | None = None) -> dict[str, Any]:
    """Score a plan against goals (soft objectives). Higher is better."""
    weights = weights or {k: 1.0 for k in GOAL_KEYS}
    score = 0.0
    detail: dict[str, Any] = {}
    for key, (field, default_op) in GOAL_KEYS.items():
        g = goals.get(key)
        if not g:
            continue
        op = g.get("op", default_op)
        target = float(g["value"])
        actual = float(totals.get(field) or 0.0)
        if op == "max":
            achieved = 1.0 if actual <= target else max(
                0.0, 1.0 - (actual - target) / max(target, 1e-6)
            )
        else:  # min
            achieved = 1.0 if actual >= target else actual / max(target, 1e-6)
        detail[key] = {"target": target, "actual": actual, "op": op,
                       "achieved": round(achieved, 4)}
        score += weights.get(key, 1.0) * achieved
    return {"score": round(score, 4), "detail": detail}


def hard_filter(totals: dict[str, float], goals: dict[str, dict]) -> bool:
    """Return True when the plan is admissible under hard constraints."""
    energy = float(totals.get("energyKcal") or 0.0)
    if energy < DEFAULT_ENERGY_MIN:
        return False
    if energy > DEFAULT_ENERGY_MAX:
        return False
    for key, (field, default_op) in GOAL_KEYS.items():
        g = goals.get(key)
        if not g:
            continue
        op = g.get("op", default_op)
        target = float(g["value"])
        actual = float(totals.get(field) or 0.0)
        # 钠采用「相对最优」：仅严重超标才硬拦截，其余交给打分惩罚
        if op == "max" and key == "sodium" and actual > target * SODIUM_HARD_MULTIPLIER:
            return False
    return True


def fully_achieved(detail: dict[str, Any], goals: dict[str, dict]) -> bool:
    """True when every goal in ``goals`` is met at 100% by a scored candidate.

    ``detail`` is the per-goal breakdown produced by `score_plan`. This is a
    different question from `hard_filter`: the hard filter only rejects energy
    and sodium *extremes*, so a ``protein>=40`` goal leaves every candidate
    admissible while none of them actually reaches 40 g. Code that infers
    "the goal is unreachable" from "nothing passed the hard filter" therefore
    reports success for a goal it never met.
    """
    for key in (goals or {}):
        d = (detail or {}).get(key) or {}
        if float(d.get("achieved", 0.0)) < 1.0:
            return False
    return True


# --------------------------------------------------------------------------- #
# MCP payload extraction
# --------------------------------------------------------------------------- #
def _unwrap(payload: dict) -> dict:
    """Accept either the full MCP response or just its `data` object."""
    if isinstance(payload, dict) and "data" in payload:
        return payload.get("data") or {}
    return payload or {}


def extract_meals(menu_payload: dict) -> dict[str, dict]:
    """Return the ``{code: {name, currentPrice, ...}}`` map from `query-meals`."""
    return _unwrap(menu_payload).get("meals") or {}


def extract_categories(menu_payload: dict) -> list[dict]:
    """Return the category list from `query-meals`."""
    return _unwrap(menu_payload).get("categories") or []


def decompose(detail_payload: dict) -> dict[str, Any]:
    """Extract default single items + replacement options from `query-meal-detail`.

    The detail response contains ``data.rounds[]``; each round is a selection
    step (e.g. main / side / drink) and carries ``choices[]``. A choice with
    ``isDefault=1`` and ``quantity>0`` is the item the combo ships with; the
    remaining choices are replacement options with a ``diffPrice``.
    """
    data = _unwrap(detail_payload)
    rounds = data.get("rounds") or []

    default_items: list[str] = []
    replacements: list[dict[str, Any]] = []

    for rnd in rounds:
        round_name = rnd.get("name", "")
        choices = rnd.get("choices") or []

        default_choice = None
        for c in choices:
            if c.get("isDefault") == 1 and (c.get("quantity") or 0) > 0:
                default_choice = c
                break
        if default_choice is None and choices:
            default_choice = choices[0]

        if default_choice:
            default_items.append(default_choice.get("name", ""))

        for c in choices:
            if c is default_choice:
                continue
            replacements.append({
                "round": round_name,
                "from": default_choice.get("name", "") if default_choice else "",
                "to": c.get("name", ""),
                "code": c.get("code", ""),
                "diffPrice": c.get("diffPrice", "+ ¥0"),
            })

    return {
        "code": data.get("code", ""),
        "name": data.get("name", ""),
        "defaultItems": [i for i in default_items if i],
        "replacements": replacements,
    }


def build_details_index(details: dict[str, dict]) -> dict[str, dict]:
    """Normalize the ``{code: raw detail response}`` map into decompositions.

    Non-dict entries (e.g. a ``_note`` metadata key in fixtures) are skipped so
    the map can safely carry documentation alongside payloads.
    """
    idx: dict[str, dict] = {}
    for code, resp in (details or {}).items():
        if not isinstance(resp, dict):
            continue
        idx[code] = decompose(resp)
    return idx


# --------------------------------------------------------------------------- #
# Enumeration helper (kept for custom-combo expansion, D9+)
# --------------------------------------------------------------------------- #
def enumerate_combos(mains: list[dict], sides: list[dict],
                     drinks: list[dict]) -> Iterable[tuple]:
    """Yield ``(main, side|None, drink|None)`` combinations."""
    side_opts: list[Any] = list(sides) + [None]
    drink_opts: list[Any] = list(drinks) + [None]
    return itertools.product(mains, side_opts, drink_opts)


# --------------------------------------------------------------------------- #
# Core solver
# --------------------------------------------------------------------------- #
def candidate_pool(menu: dict, nutrition_index: dict, fuzzy_index: dict,
                   details: dict) -> list[dict[str, Any]]:
    """Turn one slot's menu into nutrition-resolved candidate meals.

    Every purchasable unit is decomposed to single items, its nutrition summed,
    and its completeness recorded. Plans with unmatchable core items are marked
    ``complete=False`` so callers can drop them rather than fabricate values.
    """
    meals = extract_meals(menu)
    details_idx = build_details_index(details)

    pool: list[dict[str, Any]] = []
    for code, meal in meals.items():
        if code in details_idx:
            dec = details_idx[code]
            items = dec["defaultItems"]
            repl = dec["replacements"]
        else:
            items = [meal.get("name", "")]
            repl = []

        acc = accumulate(items, nutrition_index, fuzzy_index)
        try:
            price = float(meal.get("currentPrice") or 0)
        except (TypeError, ValueError):
            price = 0.0

        pool.append({
            "code": code,
            "mealName": meal.get("name", ""),
            "items": items,
            "totals": acc["totals"],
            "price": price,
            "missing": acc["missing"],
            "complete": acc["complete"],
            "replacements": repl,
        })
    return pool


def _decorate(plan: dict[str, Any], constraints: dict) -> dict[str, Any]:
    """Attach store/slot context and the human-readable reason to a plan."""
    plan["slotName"] = constraints.get("slotName", "")
    plan["slotRange"] = constraints.get("slotRange", "")
    plan["storeName"] = constraints.get("storeName", "")
    plan["storeCode"] = constraints.get("storeCode", "")
    plan["targetTime"] = constraints.get("targetTime")
    plan["slotSource"] = constraints.get("slotSource", "user")
    plan["reason"] = _build_reason(plan)
    return plan


def plan(menu: dict, nutrition_index: dict, fuzzy_index: dict,
         details: dict, constraints: dict, top: int = 3) -> list[dict[str, Any]]:
    """Solve for the chosen time slot and return the Top-N reservation plans.

    Single-diner mode: the first person's goals drive the ranking. Use
    :func:`plan_group` when more than one person has distinct goals.
    """
    people = constraints.get("people") or []
    goals: dict[str, dict] = (people[0].get("goals") if people else {}) or {}
    budget = constraints.get("budget")
    if len(people) > 1:
        return plan_group(menu, nutrition_index, fuzzy_index, details,
                          constraints, top)

    candidates: list[dict[str, Any]] = []
    for c in candidate_pool(menu, nutrition_index, fuzzy_index, details):
        if not c["complete"]:
            continue
        if budget is not None and c["price"] > float(budget):
            continue
        if not hard_filter(c["totals"], goals):
            continue
        sc = score_plan(c["totals"], goals)
        candidates.append({**c, "score": sc["score"], "detail": sc["detail"]})

    # Rank: score desc, then price asc (cheaper is better on a tie)
    candidates.sort(key=lambda p: (-p["score"], p["price"]))
    results = [_decorate(c, constraints) for c in candidates[:top]]
    for i, c in enumerate(results, start=1):
        c["rank"] = i
    return results


def plan_group(menu: dict, nutrition_index: dict, fuzzy_index: dict,
               details: dict, constraints: dict, top: int = 3,
               branch: int = 8) -> list[dict[str, Any]]:
    """Multi-diner goal intersection: pick one meal per person.

    Each diner gets their own meal (two people may legitimately order the same
    thing). For every person we keep the ``branch`` best-scoring candidates and
    then enumerate the cartesian product, keeping only combinations whose total
    price respects the budget. With at most a handful of diners this stays well
    inside a few thousand combinations while remaining deterministic.
    """
    people = constraints.get("people") or []
    if len(people) <= 1:
        return plan(menu, nutrition_index, fuzzy_index, details, constraints, top)

    budget = constraints.get("budget")
    pool = [c for c in candidate_pool(menu, nutrition_index, fuzzy_index, details)
            if c["complete"]]

    per_person_pool: list[list[dict[str, Any]]] = []
    relaxed: list[str] = []
    for person in people:
        goals = person.get("goals") or {}
        scored: list[dict[str, Any]] = []
        for c in pool:
            if not hard_filter(c["totals"], goals):
                continue
            sc = score_plan(c["totals"], goals)
            scored.append({**c, "score": sc["score"], "detail": sc["detail"],
                           "goalText": goal_text(goals)})
        if not scored:
            # Nothing survives the hard constraints for this diner: fall back
            # to the whole pool instead of failing the entire request.
            relaxed.append(person.get("name", "?"))
            scored = [{**c, "score": score_plan(c["totals"], goals)["score"],
                       "detail": score_plan(c["totals"], goals)["detail"],
                       "goalText": goal_text(goals)} for c in pool]
        elif goals and not any(fully_achieved(c["detail"], goals) for c in scored):
            # Everything is admissible but nothing actually reaches the goal.
            # Without this branch the card showed 小王 at 70% with no hint that
            # his goal was unmet anywhere in this slot.
            relaxed.append(person.get("name", "?"))
        scored.sort(key=lambda p: (-p["score"], p["price"]))
        per_person_pool.append(scored[:branch])

    combos: list[tuple[float, float, list[dict[str, Any]]]] = []
    for combo in itertools.product(*per_person_pool):
        total_price = sum(c["price"] for c in combo)
        if budget is not None and total_price > float(budget):
            continue
        total_score = sum(c["score"] for c in combo)
        combos.append((total_score, total_price, list(combo)))
    combos.sort(key=lambda t: (-t[0], t[1]))

    totals_keys = TOTAL_FIELDS
    results: list[dict[str, Any]] = []
    for score, price, combo in combos[:top]:
        totals = {k: sum(c["totals"].get(k) or 0.0 for c in combo) for k in totals_keys}
        missing = sorted({m for c in combo for m in c["missing"]})
        results.append(_decorate({
            "perPerson": [
                {
                    "name": people[i].get("name", f"成员{i + 1}"),
                    "goalText": c["goalText"],
                    "mealName": c["mealName"],
                    "code": c["code"],
                    "items": c["items"],
                    "price": c["price"],
                    "score": c["score"],
                    "detail": c["detail"],
                }
                for i, c in enumerate(combo)
            ],
            "totals": totals,
            "price": round(price, 2),
            "score": round(score, 4),
            "detail": {},
            "missing": missing,
            "complete": not missing,
            "relaxedFor": relaxed,
        }, constraints))

    for i, c in enumerate(results, start=1):
        c["rank"] = i
    return results


def _build_reason(plan: dict[str, Any]) -> str:
    """Compose the 'why this time slot' explanation for a plan card."""
    totals = plan.get("totals") or {}
    kcal = round(float(totals.get("energyKcal") or 0), 0)
    sodium = round(float(totals.get("sodium") or 0), 0)
    protein = round(float(totals.get("protein") or 0), 1)
    slot = plan.get("slotName") or "该餐段"
    rng = plan.get("slotRange") or ""

    # 时间由用户指定 vs 由求解器反推，措辞必须区分，否则会让人误以为系统在替他改时间
    if plan.get("slotSource") == "recommended":
        lead = f"推荐约在{slot}{rng}，该餐段的实时菜单里"
    else:
        lead = f"你指定的时间落在{slot}{rng}，该餐段的实时菜单里"

    if plan.get("perPerson"):
        names = "、".join(p.get("name", "?") for p in plan["perPerson"])
        bits = [f"{lead}，为 {names} 分别选出的组合，整单约 {kcal:g} kcal、"
                f"蛋白 {protein:g} g、钠 {sodium:g} mg"]
    else:
        bits = [f"{lead}，本组合整单约 {kcal:g} kcal、蛋白 {protein:g} g、钠 {sodium:g} mg"]

    if not plan.get("complete"):
        bits.append("（部分餐品暂无官方营养数据，核算不完整）")
    # relaxedFor is rendered as a prominent callout on the card itself, so
    # repeating it inside this sentence would just be noise.

    tail = "这是该餐段内按每位成员的目标算出的整体更优解。" if plan.get("perPerson") \
        else "这是该餐段内按你的目标算出的相对更优解。"
    return "；".join(bits) + "。" + tail


def _load_text(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _load_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def main() -> int:
    ap = argparse.ArgumentParser(description="McDonald's nutrition booking solver")
    ap.add_argument("--menu", required=True, help="query-meals JSON payload")
    ap.add_argument("--nutrition", required=True, help="raw nutrition text table")
    ap.add_argument("--details", required=True, help="{code: query-meal-detail response}")
    ap.add_argument("--constraints", required=True, help="constraints JSON")
    ap.add_argument("--top", type=int, default=3)
    args = ap.parse_args()

    from parse_nutrition import (  # noqa: PLC0415
        parse_nutrition_text, build_index, build_fuzzy_index,
    )

    records = parse_nutrition_text(_load_text(args.nutrition))
    nutrition_index = build_index(records)
    fuzzy_index = build_fuzzy_index(records)

    results = plan(
        _load_json(args.menu),
        nutrition_index,
        fuzzy_index,
        _load_json(args.details),
        _load_json(args.constraints),
        args.top,
    )
    json.dump(results, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
