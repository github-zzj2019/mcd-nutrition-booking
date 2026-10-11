#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Infeasibility diagnosis and minimal-relaxation advice.

A solver that answers "no plan found" is useless. When a goal set cannot be met
this module answers the three questions a user actually asks:

1. **What blocked it?** — a counted breakdown: nutrition data missing, energy
   below the safety floor, energy above the default ceiling, sodium severely
   over, or budget exceeded.
2. **Which goal is binding?** — the specific goal(s) preventing a 100% score.
3. **What is the cheapest relaxation?** — for each binding goal, the *exact*
   target value at which a plan becomes fully achievable, computed by relaxing
   that one goal while holding every other goal fixed.

The relaxation is exact rather than a grid search: for goal ``k`` we scan only
the candidates that already satisfy **all other** goals, then take the extreme
of ``k``'s field among them (minimum for ``max`` goals, maximum for ``min``
goals). That value is by construction the tightest target still admitting a
solution.

Usage:
    python scripts/diagnose.py \
        --menu tests/fixtures/menu_lunch_sample.json \
        --nutrition tests/fixtures/nutrition_raw.txt \
        --details tests/fixtures/details_sample.json \
        --constraints tests/fixtures/constraints_sample.json --markdown
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from parse_nutrition import (  # noqa: E402
    parse_nutrition_text, build_index, build_fuzzy_index,
)
from plan_meal import (  # noqa: E402
    candidate_pool, hard_filter, score_plan, GOAL_KEYS, GOAL_LABELS,
    DEFAULT_ENERGY_MIN, DEFAULT_ENERGY_MAX, SODIUM_HARD_MULTIPLIER,
)


def _block_reason(c: dict[str, Any], goals: dict[str, dict],
                  budget: float | None) -> str | None:
    """Return why a candidate is inadmissible, or None when it is admissible."""
    if not c["complete"]:
        return "营养数据缺失"
    totals = c["totals"]
    energy = float(totals.get("energyKcal") or 0.0)
    if energy < DEFAULT_ENERGY_MIN:
        return "能量低于安全下限"
    if energy > DEFAULT_ENERGY_MAX:
        return "能量超出默认上限"
    for key, (field, default_op) in GOAL_KEYS.items():
        g = goals.get(key)
        if not g:
            continue
        op = g.get("op", default_op)
        target = float(g["value"])
        actual = float(totals.get(field) or 0.0)
        if op == "max" and key == "sodium" and actual > target * SODIUM_HARD_MULTIPLIER:
            return "钠严重超标"
    if budget is not None and c["price"] > float(budget):
        return "超出预算"
    return None


def all_achieved(totals: dict[str, float], goals: dict[str, dict]) -> bool:
    """True when every goal in ``goals`` is fully achieved (achieved == 1)."""
    for key, (field, default_op) in GOAL_KEYS.items():
        g = goals.get(key)
        if not g:
            continue
        op = g.get("op", default_op)
        target = float(g["value"])
        actual = float(totals.get(field) or 0.0)
        if op == "max" and actual > target + 1e-9:
            return False
        if op == "min" and actual < target - 1e-9:
            return False
    return True


def needs_diagnosis(plans: list[dict[str, Any]], goals: dict[str, dict]) -> bool:
    """Decide whether a diagnosis block should be attached to the card.

    Two cases, not one:

    1. **zero plans** — the obvious one.
    2. **plans exist but none fully meets the goals** — far more common and
       far more misleading. The card looks healthy (「方案 1 / 2 / 3」) while
       the best plan is sitting at 90%. Without a diagnosis the user has no
       way to tell whether that 90% is a rounding artifact or a hard
       conflict, and no idea which goal to relax.

    So the trigger is "no plan fully achieves every goal", not "no plan
    survived the hard filter".
    """
    if not goals:
        return False
    if not plans:
        return True
    return not any(all_achieved(p.get("totals") or {}, goals) for p in plans)


def diagnose(menu: dict, nutrition_index: dict, fuzzy_index: dict,
             details: dict, constraints: dict) -> dict[str, Any]:
    """Diagnose a goal set against one slot's menu."""
    people = constraints.get("people") or []
    goals: dict[str, dict] = (people[0].get("goals") if people else {}) or {}
    budget = constraints.get("budget")

    pool = candidate_pool(menu, nutrition_index, fuzzy_index, details)
    complete = [c for c in pool if c["complete"]]

    blockers: dict[str, int] = {}
    admissible: list[dict[str, Any]] = []
    for c in pool:
        reason = _block_reason(c, goals, budget)
        if reason is None:
            admissible.append(c)
        else:
            blockers[reason] = blockers.get(reason, 0) + 1

    result: dict[str, Any] = {
        "poolSize": len(pool),
        "completeSize": len(complete),
        "admissibleSize": len(admissible),
        "blockers": blockers,
        "goals": goals,
        "budget": budget,
    }

    if not admissible:
        result["feasible"] = False
        result["binding"] = []
        top = sorted(blockers.items(), key=lambda kv: -kv[1])[:2]
        cause = "；".join(f"{k}（{v} 个方案）" for k, v in top) or "未知"
        result["advice"] = (
            f"该餐段共 {len(pool)} 个可点单元，全部被硬约束挡下，主要原因为：{cause}。"
            "建议放宽预算或改约其他餐段；注意本工具不会为了凑出方案而编造营养数据。"
        )
        return result

    scored = sorted(
        ({**c, "score": score_plan(c["totals"], goals)["score"],
          "detail": score_plan(c["totals"], goals)["detail"]} for c in admissible),
        key=lambda p: (-p["score"], p["price"]),
    )
    best = scored[0]

    if all_achieved(best["totals"], goals):
        result["feasible"] = True
        result["best"] = best
        result["binding"] = []
        result["advice"] = "目标可完全达成，无需放宽。"
        return result

    # ---- which goal is binding, and what is the cheapest relaxation? ----
    binding: list[dict[str, Any]] = []
    for key, (field, default_op) in GOAL_KEYS.items():
        g = goals.get(key)
        if not g:
            continue
        op = g.get("op", default_op)
        target = float(g["value"])
        label, unit = GOAL_LABELS.get(key, (key, ""))
        if (best["detail"].get(key) or {}).get("achieved", 1.0) >= 1.0:
            continue

        others = {j: v for j, v in goals.items() if j != key}
        extreme: float | None = None
        witness: dict | None = None
        for c in admissible:
            if not all_achieved(c["totals"], others):
                continue
            v = float(c["totals"].get(field) or 0.0)
            if extreme is None or (op == "min" and v > extreme) or (op == "max" and v < extreme):
                extreme, witness = v, c
        if extreme is None:
            binding.append({
                "key": key, "label": label, "unit": unit, "op": op,
                "target": target, "suggested": None,
                "delta": None, "costPct": None, "witness": None,
                "note": "即使完全放开这一项，其余目标仍无法同时满足",
            })
            continue

        delta = extreme - target
        binding.append({
            "key": key, "label": label, "unit": unit, "op": op,
            "target": target, "suggested": round(extreme, 2),
            "delta": round(delta, 2),
            "costPct": round(abs(delta) / max(abs(target), 1e-6) * 100, 1),
            "witness": {"mealName": witness["mealName"], "price": witness["price"],
                        "items": witness["items"]},
            "note": "",
        })

    binding.sort(key=lambda b: (b["costPct"] is None, b["costPct"] or 0.0))
    result["feasible"] = False
    result["binding"] = binding
    result["best"] = best

    solvable = [b for b in binding if b.get("suggested") is not None]
    if solvable:
        b = solvable[0]
        cmp_text = f"放宽到 {b['suggested']:g}{b['unit']}" if b["op"] == "max" \
            else f"下探到 {b['suggested']:g}{b['unit']}"
        result["advice"] = (
            f"卡住的是「{b['label']}{'≤' if b['op'] == 'max' else '≥'}{b['target']:g}{b['unit']}」。"
            f"把这一项{cmp_text}（调整 {b['costPct']:g}%）即可完全达标，"
            f"对应方案是 **{b['witness']['mealName']}**（¥{b['witness']['price']:g}）。"
        )
    else:
        result["advice"] = (
            "这些目标在该餐段内互相冲突：单独放宽任何一项都无法让其余目标同时成立。"
            "建议改约其他餐段（早餐与正餐是两套互不相交的菜单），或减少同时约束的指标数量。"
        )
    return result


def render_diagnosis(d: dict[str, Any]) -> str:
    """Render the diagnosis as Markdown."""
    lines: list[str] = ["## 🔍 目标诊断：为什么没有完全达标的方案？", ""]

    if d.get("feasible"):
        best = d.get("best") or {}
        lines.append(
            f"✅ 目标可完全达成，最优方案 **{best.get('mealName', '-')}**"
            f"（¥{best.get('price', 0):g}，评分 {best.get('score', 0)}）。"
        )
        lines.append("")
        return "\n".join(lines)

    lines.append(
        f"该餐段共 {d.get('poolSize', 0)} 个可点单元，其中 "
        f"{d.get('completeSize', 0)} 个有完整官方营养数据，"
        f"{d.get('admissibleSize', 0)} 个通过硬约束筛选。"
    )
    lines.append("")

    if d.get("blockers"):
        lines.append("**被挡下的原因分布**")
        lines.append("")
        lines.append("| 原因 | 方案数 |")
        lines.append("|---|---|")
        for reason, count in sorted(d["blockers"].items(), key=lambda kv: -kv[1]):
            lines.append(f"| {reason} | {count} |")
        lines.append("")

    if d.get("binding"):
        lines.append("**卡住的约束与最小放宽建议**（按代价从小到大）")
        lines.append("")
        lines.append("| 约束 | 你的要求 | 放宽到 | 调整幅度 | 对应方案 |")
        lines.append("|---|---|---|---|---|")
        for b in d["binding"]:
            if b.get("suggested") is None:
                lines.append(
                    f"| {b.get('label', b['key'])} | "
                    f"{'≤' if b['op'] == 'max' else '≥'}{b['target']:g}{b.get('unit', '')} | "
                    f"— | — | {b.get('note', '')} |"
                )
                continue
            lines.append(
                f"| {b.get('label', b['key'])} | "
                f"{'≤' if b['op'] == 'max' else '≥'}{b['target']:g}{b.get('unit', '')} | "
                f"{b['suggested']:g}{b.get('unit', '')} | {b['costPct']:g}% | "
                f"{b['witness']['mealName']} ¥{b['witness']['price']:g} |"
            )
        lines.append("")

    if d.get("advice"):
        lines.append(f"> 💡 {d['advice']}")
        lines.append("")

    lines.append(
        "> ℹ️ 营养数据来自麦当劳官方 `list-nutrition-foods`，缺失项一律标注而非估算；"
        "本建议为均衡膳食搭配参考，不构成医疗或临床营养建议。"
    )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="Infeasibility diagnosis")
    ap.add_argument("--menu", required=True)
    ap.add_argument("--nutrition", required=True)
    ap.add_argument("--details", required=True)
    ap.add_argument("--constraints", required=True)
    ap.add_argument("--markdown", action="store_true")
    args = ap.parse_args()

    with open(args.menu, "r", encoding="utf-8") as f:
        menu = json.load(f)
    with open(args.nutrition, "r", encoding="utf-8") as f:
        raw = f.read()
    with open(args.details, "r", encoding="utf-8") as f:
        details = json.load(f)
    with open(args.constraints, "r", encoding="utf-8") as f:
        constraints = json.load(f)

    records = parse_nutrition_text(raw)
    d = diagnose(menu, build_index(records), build_fuzzy_index(records),
                 details, constraints)

    if args.markdown:
        sys.stdout.write(render_diagnosis(d))
    else:
        json.dump(d, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
