#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Orchestrator: one utterance in, one reservation card out.

This is the reference implementation of the seven-step workflow in `SKILL.md`
and the single entry point that wires every solver together:

    route        -> decide_mode()
    single       -> plan / plan_group            (forward)
    reverse      -> recommend_slot               (solve for the time)
    week         -> plan_week                    (multi-day rotation)
    cross_store  -> compare_stores               (which store can book it)
    pin          -> candidate_pool + optimize    (scenario D)

Two advanced capabilities are **auto-attached** rather than being separate
commands, because that is precisely when the user needs them:

- `time_guard` — attached whenever the booking date is *today*. The API never
  returns segments that already ended, so "breakfast is gone" is invisible in
  today's payload alone and has to be derived by differencing against another
  day.
- `diagnose` — attached whenever the solver returns zero plans. "没有找到方案"
  is not an answer; the card must say *which* goal is binding and how far it
  has to move.

In production the agent calls the MCP tools and feeds their payloads in; here
the same payloads come from `tests/fixtures/` so the pipeline is reproducible
and testable without a Token.

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

    # cross-store / multi-day need their own menu payloads
        --store-menus tests/fixtures/menus_by_store.json
        --date-menus  tests/fixtures/menus_by_date.json
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
    parse_nutrition_text, build_index, build_fuzzy_index, normalize_name,
)
from parse_request import parse_request  # noqa: E402
from resolve_store import extract_reservable_stores, match_segment  # noqa: E402
from plan_meal import plan, plan_group, candidate_pool, score_plan, _decorate  # noqa: E402
from optimize_replacements import optimize  # noqa: E402
from apply_benefits import attach_benefits  # noqa: E402
from recommend_slot import recommend_slot  # noqa: E402
from time_guard import guard, render_guard  # noqa: E402
from diagnose import (  # noqa: E402
    diagnose, render_diagnosis, needs_diagnosis,
)
from plan_week import plan_week, render_week  # noqa: E402
from compare_stores import compare_stores, render_comparison  # noqa: E402
from render_card import render_cards, render_slot_recommendation  # noqa: E402


def _load_json(path: str | None) -> Any:
    if not path:
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _load_text(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _compose(blocks: list[str]) -> str:
    """Join non-empty Markdown blocks with a blank line."""
    return "\n\n".join(b.strip() for b in blocks if b and b.strip()) + "\n"


# --------------------------------------------------------------------------- #
# Routing
# --------------------------------------------------------------------------- #
def decide_mode(constraints: dict[str, Any], pin: str | None,
                has_store_menus: bool, has_date_menus: bool) -> tuple[str, str | None]:
    """Route the request to one solver, returning ``(mode, degrade_note)``.

    Precedence, highest first:

    1. ``pin`` — explicit CLI override (scenario D).
    2. ``week`` — ``planMode == "week"``. **Checked before ``timeStatus``**:
       a span request has no single time, so `parse_request` marks it
       ``"span"``, and routing on ``timeStatus`` would wrongly send it into
       reverse mode.
    3. ``cross_store`` — ``planMode == "cross_store"``; degrades to reverse
       when no clock time was recognised, because comparing stores at an
       unknown time is meaningless.
    4. ``reverse`` — ``timeStatus == "pending"`` (user asked 「约几点合适」).
    5. ``forward`` — the default: time is fixed, solve the menu.

    A route whose payload is missing degrades to ``forward`` with a note
    instead of crashing.
    """
    if pin:
        return "pin", None

    mode = constraints.get("planMode") or "single"

    if mode == "week":
        if not has_date_menus:
            return "forward", ("识别为多日排程，但缺少多日菜单数据（--date-menus），"
                               "已降级为单日求解")
        return "week", None

    if mode == "cross_store":
        if not has_store_menus:
            return "forward", ("识别为跨门店择优，但缺少多店菜单数据（--store-menus），"
                               "已降级为单店求解")
        if constraints.get("timeStatus") != "explicit":
            return "reverse", ("跨门店择优需要一个具体时间；未识别到时间，"
                               "已先按营养目标反推最优时段")
        return "cross_store", None

    if constraints.get("timeStatus") == "pending":
        return "reverse", None
    return "forward", None


# --------------------------------------------------------------------------- #
# Store / slot resolution
# --------------------------------------------------------------------------- #
def _resolve_slot(stores_payload: dict, target_time: str) -> dict[str, Any]:
    """Pick a store + meal segment for the target time."""
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
                return {"status": "in", "store": store,
                        "slotName": seg["name"],
                        "slotRange": f"({seg['start']}至{seg['end']})"}
            if hit["status"] == "gap":
                return {"status": "gap", "store": store, "gap": hit["gap"]}
    return {"status": "closed", "store": stores[0]}


def _urgency(stores_payload: dict | None, now: datetime,
             target_time: str | None) -> dict[str, Any] | None:
    """Deadline awareness, attached only when the booking is for today.

    Measured fact: the API omits segments that already ended, so today's list
    is simply shorter than tomorrow's. "早餐 已经约不到了" therefore cannot be
    read off today's payload — it has to be derived by differencing.
    """
    if not stores_payload or not target_time:
        return None
    date_str = target_time[:10]
    if date_str != now.strftime("%Y-%m-%d"):
        return None
    g = guard(stores_payload, now.strftime("%Y-%m-%d %H:%M"),
              date_str, target_time[11:16])
    return g if g.get("status") == "ok" else None


def _find_pinned(pool: list[dict[str, Any]], pin: str) -> dict[str, Any] | None:
    """Locate the pinned meal: exact -> normalized -> prefix.

    Deliberately **not** the reverse substring direction. ``pin="薯条"`` must
    not silently resolve to 「脆脆薯条」 — that is the same class of bug already
    fixed in `compare_stores.availability()`, and here it would present the
    user's own stated order as something they did not ask for.
    """
    for c in pool:
        if c["mealName"] == pin:
            return c
    norm = normalize_name(pin)
    for c in pool:
        if normalize_name(c["mealName"]) == norm:
            return c
    for c in pool:
        if normalize_name(c["mealName"]).startswith(norm):
            return c
    return None


# --------------------------------------------------------------------------- #
# Pipeline
# --------------------------------------------------------------------------- #
def run(text: str, now: datetime, stores_payload: dict, menus_payload: dict,
        nutrition_index: dict, fuzzy_index: dict, details: dict,
        coupons: dict | None, campaigns: dict | None,
        top: int = 3, pin: str | None = None,
        store_menus: dict | None = None,
        date_menus: dict | None = None) -> dict[str, Any]:
    """Execute the full pipeline and return the result + rendered Markdown."""
    warnings: list[str] = []
    constraints = parse_request(text, now)
    warnings.extend(constraints.get("warnings") or [])

    mode, degrade = decide_mode(constraints, pin,
                                bool(store_menus), bool(date_menus))
    if degrade:
        warnings.append(degrade)

    people = constraints.get("people") or []
    goals = (people[0].get("goals") if people else {}) or {}
    base = {"mode": mode, "constraints": constraints, "warnings": warnings,
            "urgency": None, "diagnosis": None}

    # ---------- reverse: solve for the time ----------
    if mode == "reverse":
        slot_result = recommend_slot(menus_payload, nutrition_index, fuzzy_index,
                                     details, constraints)
        return {**base, "slotResult": slot_result,
                "markdown": render_slot_recommendation(slot_result["ranking"])}

    # ---------- week: multi-day rotation ----------
    if mode == "week":
        week = plan_week(date_menus, nutrition_index, fuzzy_index,
                         details, constraints, top=1)
        # Coupons/campaigns are per-day, so attach them day by day.
        for day in week["days"]:
            if not day.get("plan"):
                continue
            [enriched] = attach_benefits([day["plan"]], coupons, campaigns,
                                         day["date"], week.get("slot", ""),
                                         day.get("targetTime") or day["date"])
            day["plan"] = enriched
        return {**base, "weekResult": week,
                "markdown": _compose([render_week(week)])}

    # ---------- cross_store: which store can actually book it ----------
    if mode == "cross_store":
        target_time = constraints["targetTime"]
        result = compare_stores(store_menus, nutrition_index, fuzzy_index,
                                details, constraints, target_time, top=top)
        blocks = [render_comparison(result)]
        urgency = _urgency(stores_payload, now, target_time)
        if urgency:
            blocks.insert(0, render_guard(urgency))
        return {**base, "crossStoreResult": result, "urgency": urgency,
                "markdown": _compose(blocks)}

    # ---------- forward / pin: time is fixed ----------
    target_time = constraints.get("targetTime")
    if not target_time:
        reason = "无法解析预约时间"
        return {**base, "mode": "blocked", "markdown": f"> ⚠️ {reason}\n",
                "warnings": warnings + [reason]}

    resolved = _resolve_slot(stores_payload, target_time)
    if resolved["status"] != "in":
        reason = {
            "no_store": "附近没有支持预约的门店",
            "gap": f"该时间落在厨房换餐空档 {resolved.get('gap')}，请改约相邻餐段",
            "closed": "该时间不在门店可预约时段内",
        }.get(resolved["status"], "无法解析预约时段")
        return {**base, "mode": "blocked", "markdown": f"> ⚠️ {reason}\n",
                "warnings": warnings + [reason]}

    store = resolved["store"]
    slot_name = resolved["slotName"]
    full = {**constraints, "storeCode": store.get("storeCode"),
            "storeName": store.get("storeName"), "slotName": slot_name,
            "slotRange": resolved["slotRange"], "targetTime": target_time}

    slot_payload = (menus_payload.get("slots") or {}).get(slot_name)
    if not slot_payload:
        reason = f"夹具中缺少 {slot_name} 时段的菜单"
        return {**base, "mode": "blocked", "markdown": f"> ⚠️ {reason}\n",
                "warnings": warnings + [reason]}

    menu = slot_payload.get("menu") or {}
    header = f"麦麦营养预约规划师 · {store.get('storeName')} · {target_time}"
    urgency = _urgency(stores_payload, now, target_time)

    # scenario D: the user already knows what they want, only optimize it
    if mode == "pin":
        pool = candidate_pool(menu, nutrition_index, fuzzy_index, details)
        target = _find_pinned(pool, pin)
        if target is None:
            reason = f"{slot_name}时段的菜单里没有找到「{pin}」"
            return {**base, "mode": "blocked", "markdown": f"> ⚠️ {reason}\n",
                    "warnings": warnings + [reason]}
        sc = score_plan(target["totals"], goals)
        target = _decorate({**target, "score": sc["score"],
                            "detail": sc["detail"]}, full)
        target["rank"] = 1
        opt = optimize(target, nutrition_index, fuzzy_index, goals, top=3)
        target["swaps"] = opt["swaps"]
        target["swapSkipped"] = len(opt["skipped"])
        if not target.get("complete"):
            warnings.append(f"「{pin}」的部分组成暂无官方营养数据，核算不完整")
        plans = attach_benefits([target], coupons, campaigns,
                                target_time[:10], slot_name, target_time)
        blocks = [render_guard(urgency)] if urgency else []
        blocks.append(render_cards(plans, header=header, warnings=warnings))
        return {**base, "plans": plans, "urgency": urgency,
                "markdown": _compose(blocks)}

    solver = plan_group if len(people) > 1 else plan
    plans = solver(menu, nutrition_index, fuzzy_index, details, full, top=top)

    blocks: list[str] = []
    if urgency:
        blocks.append(render_guard(urgency))

    if not plans:
        # Zero results is exactly when the user needs to know *why*.
        d = diagnose(menu, nutrition_index, fuzzy_index, details, full)
        blocks.append(f"> ⚠️ 该餐段内没有满足目标的组合")
        blocks.append(render_diagnosis(d))
        return {**base, "mode": "blocked", "urgency": urgency, "diagnosis": d,
                "markdown": _compose(blocks), "plans": []}

    # Replacement optimization rides on Top 1 only, to keep the card readable.
    if plans[0].get("replacements") and not plans[0].get("perPerson"):
        opt = optimize(plans[0], nutrition_index, fuzzy_index, goals, top=2)
        if opt["swaps"]:
            plans[0]["swaps"] = opt["swaps"]
            plans[0]["swapSkipped"] = len(opt["skipped"])

    plans = attach_benefits(plans, coupons, campaigns,
                            target_time[:10], slot_name, target_time)
    blocks.append(render_cards(plans, header=header, warnings=warnings))

    # A card listing three plans while the best sits at 90% looks healthy and
    # tells the user nothing. Attach the diagnosis whenever *no* plan fully
    # meets the goals. Group mode is excluded: it already names the diners it
    # had to relax in `relaxedFor`, and a single-person relaxation figure
    # would be meaningless there.
    d: dict[str, Any] | None = None
    if not plans[0].get("perPerson") and needs_diagnosis(plans, goals):
        d = diagnose(menu, nutrition_index, fuzzy_index, details, full)
        blocks.append(render_diagnosis(d))

    goal_line = ("；".join(f"{p.get('name')}：{p.get('goalText', '无特殊目标')}"
                          for p in plans[0].get("perPerson", []))
                 if plans[0].get("perPerson")
                 else "、".join(f"{k}{'≤' if v.get('op') == 'max' else '≥'}{v.get('value'):g}"
                               for k, v in goals.items()) or "无量化目标")

    return {**base, "plans": plans, "urgency": urgency, "diagnosis": d,
            "markdown": _compose(blocks), "goalLine": goal_line}


def main() -> int:
    ap = argparse.ArgumentParser(description="Run an end-to-end booking scenario")
    ap.add_argument("--text", required=True, help="user utterance")
    ap.add_argument("--now", default=None, help="current time 'yyyy-MM-dd HH:MM'")
    ap.add_argument("--stores", required=True)
    ap.add_argument("--menus", required=True, help="menus_by_slot.json")
    ap.add_argument("--nutrition", required=True)
    ap.add_argument("--details", required=True)
    ap.add_argument("--coupons", default=None)
    ap.add_argument("--campaigns", default=None)
    ap.add_argument("--store-menus", default=None,
                    help="menus_by_store.json, required by cross-store mode")
    ap.add_argument("--date-menus", default=None,
                    help="menus_by_date.json, required by multi-day mode")
    ap.add_argument("--top", type=int, default=3)
    ap.add_argument("--pin", default=None,
                    help="pin an existing meal (scenario D) and only optimize its swaps")
    ap.add_argument("--json", action="store_true",
                    help="emit the structured result instead of Markdown")
    ap.add_argument("-o", "--output", default=None, help="write Markdown to this file")
    args = ap.parse_args()

    records = parse_nutrition_text(_load_text(args.nutrition))
    now = (datetime.strptime(args.now, "%Y-%m-%d %H:%M") if args.now else datetime.now())

    result = run(
        args.text, now,
        _load_json(args.stores), _load_json(args.menus),
        build_index(records), build_fuzzy_index(records),
        _load_json(args.details),
        _load_json(args.coupons), _load_json(args.campaigns),
        args.top, args.pin,
        _load_json(args.store_menus), _load_json(args.date_menus),
    )

    if args.json:
        json.dump(result, sys.stdout, ensure_ascii=False, indent=2, default=str)
        sys.stdout.write("\n")
        return 0

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
