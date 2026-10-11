#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Multi-day scheduling: plan a run of days without repeating yourself.

Honest framing (measured, see ``tests/fixtures/menus_by_date.json``)
-------------------------------------------------------------------
A natural assumption is "the menu changes every day, so a week planner is
trivially interesting". **Measurement refuted that**: store 1950121 returns an
*identical* lunch menu for 2026-10-12 and 2026-10-13 — same categories, same
codes, same prices.

So the week planner is NOT built on menu drift. It is built on the three
variables that *do* change day to day, all of them time-shaped:

1. **Reservable segments differ by date.** Measured: 2026-10-11 (today) has no
   早餐 segment at all because 早餐 already ended, while 2026-10-12 does.
2. **Campaigns differ by date.** Measured via `campaign-calendar`: 10-11 has
   12 campaigns, 10-12 has 2, 10-13 has 1.
3. **Rotation itself is the user's real requirement.** Nobody wants the same
   burger five days running; cross-day de-duplication is a real constraint the
   single-day solver cannot express.

``plan_week`` solves each day in order, excluding codes already used earlier,
and falls back (with an explicit ``repeated`` flag) only when a day would
otherwise have no plan at all.

Usage:
    python scripts/plan_week.py \
        --date-menus tests/fixtures/menus_by_date.json \
        --nutrition tests/fixtures/nutrition_raw.txt \
        --details tests/fixtures/details_sample.json \
        --constraints tests/fixtures/constraints_sample.json --markdown
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date as _date
from typing import Any

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from parse_nutrition import (  # noqa: E402
    parse_nutrition_text, build_index, build_fuzzy_index,
)
from plan_meal import plan  # noqa: E402

WEEKDAY_CN = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def _filtered_menu(menu_payload: dict, exclude: set[str]) -> dict:
    """Return a copy of the menu with ``exclude`` codes removed."""
    data = (menu_payload or {}).get("data") or {}
    meals = data.get("meals") or {}
    kept = {k: v for k, v in meals.items() if k not in exclude}
    return {"data": {**data, "meals": kept}}


def plan_week(date_menus: dict, nutrition_index: dict, fuzzy_index: dict,
              details: dict, constraints: dict, top: int = 1) -> dict[str, Any]:
    """Solve one plan per day, rotating so no dish repeats while possible."""
    store_code = date_menus.get("storeCode")
    store_name = date_menus.get("storeName")
    slot = date_menus.get("slot", "午餐")

    used: set[str] = set()
    days: list[dict[str, Any]] = []

    for date_str in sorted(date_menus.get("dates") or {}):
        entry = date_menus["dates"][date_str]
        segments = entry.get("segments") or []
        lunch_seg = next((s for s in segments if s["name"] == slot), None)
        slot_range = f"({lunch_seg['start']}至{lunch_seg['end']})" if lunch_seg else ""

        local = {
            **constraints,
            "storeCode": store_code,
            "storeName": store_name,
            "slotName": slot,
            "slotRange": slot_range,
            "targetTime": f"{date_str} {lunch_seg['start']}" if lunch_seg else date_str,
        }

        # Prefer a dish we have not used earlier in the run.
        plans = plan(_filtered_menu(entry.get("menu") or {}, used),
                     nutrition_index, fuzzy_index, details, local, top=top)
        repeated = False
        if not plans:
            plans = plan(entry.get("menu") or {}, nutrition_index, fuzzy_index,
                         details, local, top=top)
            repeated = True

        chosen = plans[0] if plans else None
        if chosen:
            used.add(chosen["code"])

        try:
            wd = WEEKDAY_CN[_date.fromisoformat(date_str).weekday()]
        except ValueError:
            wd = "-"

        days.append({
            "date": date_str,
            "weekday": wd,
            "menuSource": entry.get("menuSource", "measured"),
            "campaignCount": entry.get("campaignCount", 0),
            "slotRange": slot_range,
            "repeated": repeated,
            "plan": chosen,
        })

    planned = [d for d in days if d["plan"]]
    result = {
        "storeCode": store_code,
        "storeName": store_name,
        "slot": slot,
        "days": days,
        "plannedDays": len(planned),
        "repeatDays": sum(1 for d in days if d["repeated"]),
        "distinctMeals": len({d["plan"]["code"] for d in planned}),
        "totalPrice": round(sum(d["plan"]["price"] for d in planned), 2),
        "avgSodium": (round(sum(d["plan"]["totals"].get("sodium", 0) for d in planned)
                            / max(len(planned), 1), 0) if planned else 0.0),
        "avgEnergy": (round(sum(d["plan"]["totals"].get("energyKcal", 0) for d in planned)
                            / max(len(planned), 1), 0) if planned else 0.0),
    }
    return result


def render_week(result: dict[str, Any]) -> str:
    """Render the multi-day plan as a Markdown table."""
    lines: list[str] = [
        f"## 🗓 多日排程 · {result.get('storeName', '-')} · {result.get('slot', '-')}",
        "",
        "| 日期 | 星期 | 推荐组合 | 能量 | 蛋白 | 钠 | 价格 | 当天活动 | 是否重样 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for d in result["days"]:
        p = d["plan"]
        if not p:
            lines.append(f"| {d['date']} | {d['weekday']} | — | — | — | — | — "
                         f"| {d['campaignCount']} 个 | — |")
            continue
        t = p.get("totals") or {}
        repeat = "⚠️ 是" if d["repeated"] else "否"
        source = "" if d["menuSource"] == "measured" else " *"
        lines.append(
            f"| {d['date']}{source} | {d['weekday']} | {p.get('mealName', '-')} | "
            f"{float(t.get('energyKcal', 0)):.0f} kcal | {float(t.get('protein', 0)):.0f} g | "
            f"{float(t.get('sodium', 0)):.0f} mg | ¥{p.get('price', 0):g} | "
            f"{d['campaignCount']} 个 | {repeat} |"
        )
    lines.append("")

    if any(d["menuSource"] != "measured" for d in result["days"]):
        lines.append(
            "> \\* 标 \\* 的日期菜单未单独实测，沿用与相邻日期一致的已实测菜单"
            "（相邻日期菜单实测无差异）。"
        )
        lines.append("")

    lines.append(
        f"**汇总**：{result['plannedDays']}/{len(result['days'])} 天有达标方案，"
        f"共 {result['distinctMeals']} 款不重样，合计 ¥{result['totalPrice']:g}，"
        f"日均 {result['avgEnergy']:.0f} kcal、钠 {result['avgSodium']:.0f} mg。"
    )
    lines.append("")

    if result["repeatDays"]:
        lines.append(
            f"> ⚠️ 有 {result['repeatDays']} 天因剩余可选餐品已耗尽而被迫重复，"
            "说明该目标在这个餐段里的可行域很窄。"
        )
        lines.append("")

    counts = {d["date"]: d["campaignCount"] for d in result["days"]}
    if len(set(counts.values())) > 1:
        detail = "、".join(f"{k} {v} 个" for k, v in counts.items())
        lines.append(
            f"> 🎉 **约哪一天会改变能赶上哪些活动**（实测 `campaign-calendar` 按日分组）：{detail}。"
        )
        lines.append("")

    lines.append(
        "> ℹ️ 实测发现：同一门店相邻两天的**菜单完全一致**，因此多日排程的差异不来自菜单，"
        "而来自「每天的可预约餐段」「当天的活动」以及「跨天不重样」本身。"
    )
    lines.append("")
    lines.append(
        "> ℹ️ 营养数据来自麦当劳官方 `list-nutrition-foods`，为单餐参考值；"
        "本工具提供均衡膳食搭配建议，不构成医疗或临床营养建议。"
    )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="Multi-day meal scheduling")
    ap.add_argument("--date-menus", required=True)
    ap.add_argument("--nutrition", required=True)
    ap.add_argument("--details", required=True)
    ap.add_argument("--constraints", required=True)
    ap.add_argument("--markdown", action="store_true")
    args = ap.parse_args()

    with open(args.date_menus, "r", encoding="utf-8") as f:
        date_menus = json.load(f)
    with open(args.nutrition, "r", encoding="utf-8") as f:
        raw = f.read()
    with open(args.details, "r", encoding="utf-8") as f:
        details = json.load(f)
    with open(args.constraints, "r", encoding="utf-8") as f:
        constraints = json.load(f)

    records = parse_nutrition_text(raw)
    result = plan_week(date_menus, build_index(records), build_fuzzy_index(records),
                       details, constraints)

    if args.markdown:
        sys.stdout.write(render_week(result))
    else:
        json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
