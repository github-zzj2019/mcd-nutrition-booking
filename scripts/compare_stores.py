#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Cross-store arbitration: treat the *store* as part of the time constraint.

Measured basis (``tests/fixtures/menus_by_store.json``)
------------------------------------------------------
Two stores 236 m apart in the same Beijing block return **different menus** for
the same slot, and — more importantly — **different reservable segments**:

    1950121 朝外  (226 m) : 早餐/午餐/下午茶/夜市
    1950006 东大桥 (462 m) : 早餐/午餐/下午茶/夜市/**宵夜(22:14-22:46)**

So "晚上 22:30 想约一顿" is infeasible at the nearest store and feasible one
block away. That is a *time* constraint expressed through the store dimension,
and it is invisible unless the solver looks across stores.

Menus also differ on individual items (东大桥 has 脆脆薯条; 朝外 has 抹茶/咖啡
阿芙佳朵 and 浓缩咖啡 — all four resolve against the official nutrition table),
which makes "which nearby store still has the thing I want" a real query.

Honest caveat recorded in the fixture: the two stores share ~95% of the menu,
so under a loose goal the *best* dish is usually the same. The genuine value is
(a) which stores are bookable at the requested time and (b) item availability.

Functions
---------
``availability``  - which nearby stores carry a named item (with price).
``compare_stores``- solve the goal at every store, rank by feasibility/score.

Usage:
    python scripts/compare_stores.py \
        --store-menus tests/fixtures/menus_by_store.json \
        --nutrition tests/fixtures/nutrition_raw.txt \
        --details tests/fixtures/details_sample.json \
        --constraints tests/fixtures/constraints_compare.json \
        --time "2026-10-12 22:30" --markdown
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
    parse_nutrition_text, build_index, build_fuzzy_index, normalize_name,
)
from resolve_store import match_segment  # noqa: E402
from plan_meal import extract_meals, plan, score_plan  # noqa: E402


def _to_minutes(hhmm: str) -> int | None:
    """Convert 'HH:MM' to minutes past midnight; None when unparseable."""
    try:
        h, m = (hhmm or "").split(":")
        return int(h) * 60 + int(m)
    except (ValueError, AttributeError):
        return None


def nearest_bookable(store_menus: dict, hhmm: str) -> dict[str, Any] | None:
    """Earliest segment start across all stores that is still ahead of ``hhmm``.

    "Every nearby store is closed at 22:00" is accurate but not actionable —
    the user asked for a time and deserves the next moment any of them can
    take the booking. This is computed from the same ``reservationTimeOptions``
    already on the store entries, so it costs **no extra MCP call**, and it is
    exactly the kind of answer the store dimension exists to give: 东大桥 can
    book 宵夜 from 22:14 while 朝外 simply has no such segment.
    """
    target = _to_minutes(hhmm)
    if target is None:
        return None

    best: dict[str, Any] | None = None
    for code, entry in (store_menus.get("stores") or {}).items():
        for seg in entry.get("segments") or []:
            start = _to_minutes(seg.get("start") or "")
            if start is None or start <= target:
                continue
            if best is None or start < best["startMinutes"]:
                best = {
                    "startMinutes": start,
                    "start": seg.get("start"),
                    "end": seg.get("end"),
                    "slotName": seg.get("name"),
                    "storeCode": code,
                    "storeName": entry.get("storeName", code),
                    "distance": entry.get("distance"),
                }
    return best


def _item_matches(item_name: str, name: str) -> bool:
    """Decide whether a menu entry ``name`` is the item the user asked for.

    Deliberately *not* a two-way substring test: ``"薯条" in "脆脆薯条"`` is
    true but is the wrong answer when the user asked for 脆脆薯条 and the store
    only carries plain 薯条. Matching is exact, normalized-exact, or a prefix
    match (``巨无霸`` -> ``巨无霸三件套``), never the reverse direction.
    """
    if not name:
        return False
    if item_name == name:
        return True
    if normalize_name(item_name) == normalize_name(name):
        return True
    return name.startswith(item_name)


def availability(store_menus: dict, item_name: str) -> list[dict[str, Any]]:
    """Report which stores carry ``item_name`` (exact / normalized / prefix)."""
    out: list[dict[str, Any]] = []
    for code, entry in (store_menus.get("stores") or {}).items():
        meals = extract_meals(entry.get("menu") or {})
        hit = None
        for mcode, meal in meals.items():
            name = meal.get("name", "")
            if _item_matches(item_name, name):
                hit = {"code": mcode, "name": name,
                       "price": float(meal.get("currentPrice") or 0)}
                break
        out.append({
            "storeCode": code,
            "storeName": entry.get("storeName", code),
            "distance": entry.get("distance"),
            "has": hit is not None,
            "item": hit,
        })
    out.sort(key=lambda s: (not s["has"], s["distance"] or 0))
    return out


def render_availability(rows: list[dict[str, Any]], item_name: str) -> str:
    """Render item availability across stores as Markdown."""
    lines = [f"## 🏪 「{item_name}」附近的门店可得性", ""]
    lines.append("| 门店 | 距离 | 有无 | 价格 |")
    lines.append("|---|---|---|---|")
    for r in rows:
        price = f"¥{r['item']['price']:g}" if r["item"] else "—"
        lines.append(
            f"| {r['storeName']}（{r['storeCode']}） | {r['distance']} m | "
            f"{'✅ 有' if r['has'] else '❌ 无'} | {price} |"
        )
    lines.append("")

    have = [r for r in rows if r["has"]]
    if not have:
        lines.append(f"> ⚠️ 附近门店的该时段菜单里都没有「{item_name}」。")
    elif len(have) < len(rows):
        nearest = have[0]
        missing = [r for r in rows if not r["has"]]
        lines.append(
            f"> 📌 最近的 {missing[0]['storeName']}（{missing[0]['distance']} m）**没有**这一款；"
            f"需要多走 {abs((nearest['distance'] or 0) - (missing[0]['distance'] or 0))} m "
            f"到 {nearest['storeName']}（{nearest['distance']} m）才点得到。"
        )
    lines.append("")
    return "\n".join(lines)


def compare_stores(store_menus: dict, nutrition_index: dict, fuzzy_index: dict,
                   details: dict, constraints: dict, target_time: str,
                   top: int = 3) -> dict[str, Any]:
    """Solve the same goal set at every store and rank the outcome."""
    hhmm = target_time[11:16] if len(target_time) > 10 else "12:00"
    feasible: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []

    for code, entry in (store_menus.get("stores") or {}).items():
        segments = entry.get("segments") or []
        hit = match_segment(segments, hhmm)
        base = {
            "storeCode": code,
            "storeName": entry.get("storeName", code),
            "distance": entry.get("distance"),
            "segments": segments,
        }
        if hit["status"] != "in":
            reason = ("该时间落在厨房换餐空档 " + "→".join(hit.get("gap") or [])
                      if hit["status"] == "gap"
                      else f"该时间不在该店可预约时段内（最晚 {segments[-1]['end'] if segments else '-'}）")
            blocked.append({**base, "reason": reason})
            continue

        seg = hit["segment"]
        local = {
            **constraints,
            "storeCode": code,
            "storeName": entry.get("storeName", code),
            "slotName": seg["name"],
            "slotRange": f"({seg['start']}至{seg['end']})",
            "targetTime": target_time,
        }
        plans = plan(entry.get("menu") or {}, nutrition_index, fuzzy_index,
                     details, local, top=top)
        if not plans:
            blocked.append({**base, "reason": "该店该餐段内没有满足目标的组合"})
            continue

        feasible.append({
            **base,
            "slotName": seg["name"],
            "slotRange": f"({seg['start']}至{seg['end']})",
            "bestPlan": plans[0],
            "alternatives": len(plans),
            "score": plans[0].get("score", 0.0),
            "prices": [p.get("price", 0.0) for p in plans],
        })

    feasible.sort(key=lambda f: (-f["score"], f["distance"] or 0))
    blocked.sort(key=lambda b: b["distance"] or 0)

    result: dict[str, Any] = {
        "targetTime": target_time,
        "feasible": feasible,
        "blocked": blocked,
        "best": feasible[0] if feasible else None,
    }

    # ---- trade-off narrative -------------------------------------------
    if feasible and blocked:
        # 只有一家能约时不存在权衡，说成「无需权衡」会掩盖真正的信息
        best = feasible[0]
        result["tradeoff"] = (
            f"该时间只有 {best['storeName']}（{best['distance']} m）可约，"
            f"其余 {len(blocked)} 家已过营业时段——这里没有权衡余地，只有唯一解。"
        )
    elif feasible:
        best = feasible[0]
        nearest = min(feasible, key=lambda f: f["distance"] or 0)
        if best["storeCode"] != nearest["storeCode"]:
            result["tradeoff"] = (
                f"最近的 {nearest['storeName']}（{nearest['distance']} m）也能约，"
                f"但 {best['storeName']}（{best['distance']} m，多走 "
                f"{abs((best['distance'] or 0) - (nearest['distance'] or 0))} m）"
                f"的最优方案达成度更高（{best['score']} vs {nearest['score']}）。"
            )
        else:
            result["tradeoff"] = "最优门店同时也是最近的门店，无需权衡。"
    elif blocked:
        nxt = nearest_bookable(store_menus, hhmm)
        if nxt:
            result["nearestBookable"] = nxt
            result["tradeoff"] = (
                f"所有 {len(blocked)} 家门店在 {hhmm} 都不可约；最近的可约时间是 "
                f"{nxt['start']}（{nxt['slotName']}，{nxt['storeName']}，"
                f"{nxt['distance']} m）。"
            )
        else:
            result["tradeoff"] = "所有附近门店都无法在该时间满足该目标，且当天已无更晚的餐段。"

    if blocked and feasible:
        names = "、".join(b["storeName"] for b in blocked[:2])
        result["bookabilityNote"] = (
            f"{names} 在该时间不可约或无法达成目标——**同一时间不同门店的可预约性并不相同**。"
        )
    return result


def render_comparison(result: dict[str, Any]) -> str:
    """Render the cross-store comparison as Markdown."""
    lines: list[str] = [
        f"## 🏪 多门店择优 · 预约时间 {result.get('targetTime', '-')}",
        "",
    ]
    if result.get("feasible"):
        lines.append("| 门店 | 距离 | 命中餐段 | 最优方案 | 评分 | 价格 | 达标方案数 |")
        lines.append("|---|---|---|---|---|---|---|")
        for f in result["feasible"]:
            best = f["bestPlan"]
            lines.append(
                f"| {f['storeName']}（{f['storeCode']}） | {f['distance']} m | "
                f"{f['slotName']}{f['slotRange']} | {best.get('mealName', '-')} | "
                f"{f['score']} | ¥{best.get('price', 0):g} | {f['alternatives']} |"
            )
        lines.append("")
    else:
        lines.append("> ⚠️ 没有门店能在该时间满足该目标。")
        lines.append("")

    if result.get("blocked"):
        lines.append("**不可约 / 无解的门店**")
        lines.append("")
        lines.append("| 门店 | 距离 | 原因 |")
        lines.append("|---|---|---|")
        for b in result["blocked"]:
            lines.append(f"| {b['storeName']}（{b['storeCode']}） | {b['distance']} m | {b['reason']} |")
        lines.append("")

    if result.get("tradeoff"):
        lines.append(f"**权衡**：{result['tradeoff']}")
        lines.append("")

    nxt = result.get("nearestBookable")
    if nxt:
        lines.append(
            f"> ⏭ 改约到 **{nxt['start']}** 即可：{nxt['storeName']}"
            f"（{nxt['distance']} m）的{nxt['slotName']}餐段 "
            f"{nxt['start']}–{nxt['end']}。该结论直接来自门店预约时段，"
            "不需要额外查询。"
        )
        lines.append("")

    if result.get("bookabilityNote"):
        lines.append(f"> 📌 {result['bookabilityNote']}")
        lines.append("")

    lines.append(
        "> ℹ️ 同商圈门店菜单重合度很高（实测约 95%），宽松目标下各家最优餐品通常相同；"
        "门店择优的真实价值在于「该时间哪家可约」与「某款特定餐品哪家还有」。"
    )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="Cross-store arbitration")
    ap.add_argument("--store-menus", required=True)
    # 仅在 --avail 模式下可省略：可得性只查菜单，不需要营养表与求解器。
    ap.add_argument("--nutrition")
    ap.add_argument("--details")
    ap.add_argument("--constraints")
    ap.add_argument("--time", default="2026-10-12 12:00")
    ap.add_argument("--avail", default=None, help="query item availability instead")
    ap.add_argument("--markdown", action="store_true")
    args = ap.parse_args()

    if not args.avail and not all([args.nutrition, args.details, args.constraints]):
        ap.error("--nutrition / --details / --constraints are required unless --avail is used")

    with open(args.store_menus, "r", encoding="utf-8") as f:
        store_menus = json.load(f)

    # 可得性只查菜单，不需要营养表与求解器，优先返回以免强制传参。
    if args.avail:
        rows = availability(store_menus, args.avail)
        sys.stdout.write(render_availability(rows, args.avail)
                         if args.markdown
                         else json.dumps(rows, ensure_ascii=False, indent=2) + "\n")
        return 0

    with open(args.nutrition, "r", encoding="utf-8") as f:
        raw = f.read()
    with open(args.details, "r", encoding="utf-8") as f:
        details = json.load(f)
    with open(args.constraints, "r", encoding="utf-8") as f:
        constraints = json.load(f)

    records = parse_nutrition_text(raw)
    result = compare_stores(store_menus, build_index(records),
                            build_fuzzy_index(records), details,
                            constraints, args.time)
    if args.markdown:
        sys.stdout.write(render_comparison(result))
    else:
        json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
