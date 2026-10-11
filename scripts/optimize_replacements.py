#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Replacement optimization driven by `query-meal-detail`.

Every combo returned by `query-meal-detail` exposes ``rounds[].choices[]``: the
choice with ``isDefault=1`` and ``quantity>0`` is what the combo ships with, and
the remaining choices are **swap options** carrying a ``diffPrice`` (e.g.
中薯条 -> 小杯玉米杯 ``- ¥1.5``).

This module replays each single swap against the user's goals and keeps only
those that measurably improve goal achievement without breaking the hard
constraints. It never invents a swap's nutrition: when the alternative item has
no official nutrition record the swap is skipped and reported as such.

Usage:
    python scripts/optimize_replacements.py \
        --plan plan.json \
        --nutrition tests/fixtures/nutrition_raw.txt \
        --goals '{"energy":{"op":"max","value":600}}'
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import Any

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from parse_nutrition import (  # noqa: E402
    parse_nutrition_text, build_index, build_fuzzy_index,
)
from plan_meal import (  # noqa: E402
    accumulate, score_plan, hard_filter, GOAL_LABELS,
)

DIFF_PRICE_RE = re.compile(r"([+-])\s*¥\s*(\d+(?:\.\d+)?)")


def parse_diff_price(text: str) -> float:
    """Parse ``"- ¥1.5"`` -> ``-1.5``; unparsable text is treated as 0."""
    m = DIFF_PRICE_RE.search(text or "")
    if not m:
        return 0.0
    value = float(m.group(2))
    return -value if m.group(1) == "-" else value


def _delta_text(before: dict[str, float], after: dict[str, float],
                keys: list[str]) -> str:
    parts: list[str] = []
    for key in keys:
        label, _ = GOAL_LABELS.get(key, (key, ""))
        field = {"energy": "energyKcal"}.get(key, key)
        d = float(after.get(field) or 0) - float(before.get(field) or 0)
        if abs(d) < 1e-6:
            continue
        parts.append(f"{label} {d:+.0f}")
    return f"（{'、'.join(parts)}）" if parts else ""


def optimize(plan: dict[str, Any], nutrition_index: dict, fuzzy_index: dict,
             goals: dict[str, dict], top: int = 3) -> dict[str, Any]:
    """Evaluate every single swap on a plan and return the improving ones."""
    items: list[str] = list(plan.get("items") or [])
    replacements: list[dict[str, Any]] = plan.get("replacements") or []
    base_totals = plan.get("totals") or {}
    base_score = float(plan.get("score") or 0.0)
    base_price = float(plan.get("price") or 0.0)

    fields = ["energyKcal", "protein", "fat", "carbohydrate", "sodium", "calcium"]
    improving: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for repl in replacements:
        src = repl.get("from", "")
        dst = repl.get("to", "")
        if not src or not dst or src not in items:
            continue

        new_items = list(items)
        new_items[new_items.index(src)] = dst
        acc = accumulate(new_items, nutrition_index, fuzzy_index)

        if not acc["complete"]:
            # 无可信营养数据：明确跳过，绝不编造替代品的数值
            skipped.append({"round": repl.get("round", ""), "to": dst,
                            "reason": "该替换项暂无官方营养数据"})
            continue

        new_totals = acc["totals"]
        if not hard_filter(new_totals, goals):
            continue

        new_score = float(score_plan(new_totals, goals)["score"])
        price_delta = parse_diff_price(repl.get("diffPrice", ""))
        new_price = round(base_price + price_delta, 2)

        if new_score <= base_score + 1e-9:
            continue

        improving.append({
            "round": repl.get("round", ""),
            "from": src,
            "to": dst,
            "code": repl.get("code", ""),
            "diffPrice": repl.get("diffPrice", ""),
            "priceDelta": price_delta,
            "newPrice": new_price,
            "scoreDelta": round(new_score - base_score, 4),
            "newScore": round(new_score, 4),
            "newTotals": new_totals,
            "deltaText": _delta_text(base_totals, new_totals,
                                     [k for k in GOAL_LABELS if k in goals]),
            "newItems": new_items,
        })

    improving.sort(key=lambda s: (-s["scoreDelta"], s["priceDelta"]))
    return {
        "baseScore": base_score,
        "basePrice": base_price,
        "baseTotals": {k: base_totals.get(k) for k in fields},
        "swaps": improving[:top],
        "skipped": skipped,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Combo replacement optimizer")
    ap.add_argument("--plan", required=True, help="a plan JSON from plan_meal.py")
    ap.add_argument("--nutrition", required=True, help="raw nutrition text table")
    ap.add_argument("--goals", default="{}", help='JSON goals, e.g. \'{"energy":{"op":"max","value":600}}\'')
    ap.add_argument("--top", type=int, default=3)
    args = ap.parse_args()

    records = parse_nutrition_text(open(args.nutrition, "r", encoding="utf-8").read())
    plan = json.load(open(args.plan, "r", encoding="utf-8"))
    if isinstance(plan, list):
        plan = plan[0] if plan else {}

    result = optimize(plan, build_index(records), build_fuzzy_index(records),
                      json.loads(args.goals), args.top)
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
