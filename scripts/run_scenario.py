#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""End-to-end scenario runner: one utterance in, one reservation card out.

This is the reference implementation of the seven-step workflow in `SKILL.md`:

    1. parse_request      -> constraint object
    2. resolve_store      -> store + meal segment + gap detection
    3. query-meals        -> slot-specific real-time menu (fixture here)
    4. list-nutrition + query-meal-detail -> decompose combos, sum nutrition
    5. plan_meal          -> hard filter, score, rank, group intersection
    6. apply_benefits     -> coupons + campaign cross-hint
    7. render_card        -> Markdown card

In production the agent calls the MCP tools and feeds their payloads in; here
the same payloads come from `tests/fixtures/` so the pipeline is reproducible
and testable without hitting the API.

Usage:
    python scripts/run_scenario.py \
        --text "明天中午12点在公司楼下吃，帮我控制在600 kcal以内" \
        --now "2026-10-11 10:00" \
        --stores tests/fixtures/stores.json \
        --menus tests/fixtures/menus_by_slot.json \
        --nutrition tests/fixtures/nutrition_raw.txt \
        --details tests/fixtures/details_sample.json \
        --coupons tests/fixtures/coupons_sample.json \
        --campaigns tests/fixtures/campaigns_sample.json \
        -o tests/output/scenario_A_card.md
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from typing import Any

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from parse_nutrition import (  # noqa: E402
    parse_nutrition_text, build_index, build_fuzzy_index,
)
from parse_request import parse_request  # noqa: E402
from resolve_store import extract_reservable_stores, match_segment  # noqa: E402
from plan_meal import plan, plan_group, candidate_pool, score_plan, _decorate  # noqa: E402
from optimize_replacements import optimize  # noqa: E402
from apply_benefits import attach_benefits  # noqa: E402
from recommend_slot import recommend_slot  # noqa: E402
from render_card import render_cards, render_slot_recommendation  # noqa: E402


def _load_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _load_text(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _resolve_slot(stores_payload: dict, target_time: str) -> dict[str, Any]:
    """Pick a store + meal segment for the target time.

    Returns ``{"store":..., "slotName":..., "slotRange":..., "status":...}``.
    """
    stores = extract_reservable_stores(stores_payload)
    if not stores:
        return {"status": "no_store", "store": None}

    date_str = target_time[:10]
    hhmm = target_time[11:16]

    for store in stores:
        for opt in store.get("options") or []:
            if opt.get("date") != date_str:
                continue
            segs = opt.get("segments") or []
            if not segs:
                continue
            hit = match_segment(segs, hhmm)
            if hit["status"] == "in":
                seg = hit["segment"]
                return {
                    "status": "in",
                    "store": store,
                    "slotName": seg["name"],
                    "slotRange": f"({seg['start']}至{seg['end']})",
                }
            if hit["status"] == "gap":
                return {"status": "gap", "store": store, "gap": hit["gap"]}
    return {"status": "closed", "store": stores[0]}


def run(text: str, now: datetime, stores_payload: dict, menus_payload: dict,
        nutrition_index: dict, fuzzy_index: dict, details: dict,
        coupons: dict | None, campaigns: dict | None,
        top: int = 3, pin: str | None = None) -> dict[str, Any]:
    """Execute the full pipeline and return plans + the rendered Markdown."""
    warnings: list[str] = []
    constraints = parse_request(text, now)
    warnings.extend(constraints.get("warnings") or [])

    people = constraints.get("people") or []
    goals = (people[0].get("goals") if people else {}) or {}

    # ---------- reverse mode: solve for the time ----------
    if constraints.get("timeStatus") == "pending":
        slot_result = recommend_slot(menus_payload, nutrition_index, fuzzy_index,
                                     details, constraints)
        markdown = render_slot_recommendation(slot_result["ranking"])
        return {"mode": "slot", "constraints": constraints,
                "slotResult": slot_result, "markdown": markdown,
                "warnings": warnings}

    # ---------- forward mode: time is fixed ----------
    target_time = constraints["targetTime"]
    resolved = _resolve_slot(stores_payload, target_time)
    if resolved["status"] != "in":
        reason = {
            "no_store": "附近没有支持预约的门店",
            "gap": f"该时间落在厨房换餐空档 {resolved.get('gap')}，请改约相邻餐段",
            "closed": "该时间不在门店可预约时段内",
        }.get(resolved["status"], "无法解析预约时段")
        return {"mode": "blocked", "constraints": constraints,
                "markdown": f"> ⚠️ {reason}\n",
                "warnings": warnings + [reason]}

    store = resolved["store"]
    slot_name = resolved["slotName"]
    full_constraints = {
        **constraints,
        "storeCode": store.get("storeCode"),
        "storeName": store.get("storeName"),
        "slotName": slot_name,
        "slotRange": resolved["slotRange"],
        "targetTime": target_time,
    }

    slot_payload = (menus_payload.get("slots") or {}).get(slot_name)
    if not slot_payload:
        reason = f"夹具中缺少 {slot_name} 时段的菜单"
        return {"mode": "blocked", "constraints": full_constraints,
                "markdown": f"> ⚠️ {reason}\n", "warnings": warnings + [reason]}

    menu = slot_payload.get("menu") or {}
    header = (f"麦麦营养预约规划师 · {store.get('storeName')} · {target_time}")

    # 场景D：用户已锁定常点的那款，只求更均衡的替换方案
    if pin:
        pool = candidate_pool(menu, nutrition_index, fuzzy_index, details)
        target = next((c for c in pool
                       if c["mealName"] == pin or pin in c["mealName"]), None)
        if target is None:
            reason = f"{slot_name}时段的菜单里没有找到「{pin}」"
            return {"mode": "blocked", "constraints": full_constraints,
                    "markdown": f"> ⚠️ {reason}\n", "warnings": warnings + [reason]}
        sc = score_plan(target["totals"], goals)
        target = _decorate({**target, "score": sc["score"],
                            "detail": sc["detail"]}, full_constraints)
        target["rank"] = 1
        opt = optimize(target, nutrition_index, fuzzy_index, goals, top=3)
        target["swaps"] = opt["swaps"]
        target["swapSkipped"] = len(opt["skipped"])
        if not target.get("complete"):
            warnings.append(f"「{pin}」的部分组成暂无官方营养数据，核算不完整")
        plans = attach_benefits([target], coupons, campaigns,
                                target_time[:10], slot_name, target_time)
        return {"mode": "plan", "constraints": full_constraints, "plans": plans,
                "markdown": render_cards(plans, header=header, warnings=warnings),
                "warnings": warnings}

    solver = plan_group if len(people) > 1 else plan
    plans = solver(menu, nutrition_index, fuzzy_index, details,
                   full_constraints, top=top)
    if not plans:
        reason = "该餐段内没有满足目标的组合，建议放宽目标或改约其他餐段"
        return {"mode": "blocked", "constraints": full_constraints,
                "markdown": f"> ⚠️ {reason}\n", "warnings": warnings + [reason]}

    # 替换优化只挂在 Top1 上，避免卡片信息过载
    if plans and plans[0].get("replacements") and not plans[0].get("perPerson"):
        opt = optimize(plans[0], nutrition_index, fuzzy_index, goals, top=2)
        if opt["swaps"]:
            plans[0]["swaps"] = opt["swaps"]
            plans[0]["swapSkipped"] = len(opt["skipped"])

    plans = attach_benefits(plans, coupons, campaigns,
                            target_time[:10], slot_name, target_time)

    goal_line = "；".join(
        f"{p.get('name')}：{p.get('goalText', '无特殊目标')}" for p in plans[0].get("perPerson", [])
    ) if plans[0].get("perPerson") else "、".join(
        f"{k}{'≤' if v.get('op') == 'max' else '≥'}{v.get('value'):g}"
        for k, v in goals.items()
    ) or "无量化目标"

    markdown = render_cards(plans, header=header, warnings=warnings)
    return {"mode": "plan", "constraints": full_constraints, "plans": plans,
            "markdown": markdown, "warnings": warnings, "goalLine": goal_line}


def main() -> int:
    ap = argparse.ArgumentParser(description="Run an end-to-end booking scenario")
    ap.add_argument("--text", required=True, help="user utterance")
    ap.add_argument("--now", default=None, help="current time 'yyyy-MM-dd HH:MM'")
    ap.add_argument("--stores", required=True)
    ap.add_argument("--menus", required=True)
    ap.add_argument("--nutrition", required=True)
    ap.add_argument("--details", required=True)
    ap.add_argument("--coupons", default=None)
    ap.add_argument("--campaigns", default=None)
    ap.add_argument("--top", type=int, default=3)
    ap.add_argument("--pin", default=None,
                    help="pin an existing meal (scenario D) and only optimize its swaps")
    ap.add_argument("-o", "--output", default=None, help="write Markdown to this file")
    args = ap.parse_args()

    records = parse_nutrition_text(_load_text(args.nutrition))
    now = (datetime.strptime(args.now, "%Y-%m-%d %H:%M") if args.now else datetime.now())

    result = run(
        args.text, now,
        _load_json(args.stores), _load_json(args.menus),
        build_index(records), build_fuzzy_index(records),
        _load_json(args.details),
        _load_json(args.coupons) if args.coupons else None,
        _load_json(args.campaigns) if args.campaigns else None,
        args.top, args.pin,
    )

    if args.output:
        out_path = os.path.join(REPO_ROOT, args.output)
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(result["markdown"])
        print(f"[written] {out_path}")
    else:
        sys.stdout.write(result["markdown"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
