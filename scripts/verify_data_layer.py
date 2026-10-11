#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Verify the data layer against real MCP return payloads.

Checks:
  1. `parse_nutrition.py` correctly parses the full 160-record nutrition text
     table (including null / non-null `nutritionDescription`).
  2. `resolve_store.py` correctly extracts reservable stores and parses
     `reservationTimeOptions` into structured meal segments.
  3. `plan_meal.accumulate` sums a realistic combo, and missing items are
     flagged instead of fabricated.

Run from the repository root:
    python scripts/verify_data_layer.py
"""

from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)  # import sibling scripts

from parse_nutrition import (  # noqa: E402
    parse_nutrition_text,
    build_index,
    build_fuzzy_index,
    normalize_name,
    lookup,
)
from resolve_store import (  # noqa: E402
    parse_time_options,
    extract_reservable_stores,
    match_segment,
)
from plan_meal import accumulate  # noqa: E402

FIXTURES = os.path.join(REPO_ROOT, "tests", "fixtures")
NUTRITION_FIXTURE = os.path.join(FIXTURES, "nutrition_raw.txt")
STORES_FIXTURE = os.path.join(FIXTURES, "stores.json")

_ok = True


def check(cond: bool, msg: str) -> None:
    global _ok  # noqa: PLW0603
    print(("  PASS  " if cond else "  FAIL  ") + msg)
    _ok = _ok and cond


def main() -> int:
    print("=== 1) Nutrition parser (full 160-record payload) ===")
    with open(NUTRITION_FIXTURE, "r", encoding="utf-8") as f:
        raw = f.read()
    records = parse_nutrition_text(raw)
    check(len(records) == 160, f"parsed {len(records)} records (expected 160)")

    index = build_index(records)
    fuzzy = build_fuzzy_index(records)
    unique = len({r["productName"] for r in records})
    check(len(index) == unique,
          f"exact index built: {len(index)} unique / {len(records)} records "
          f"({len(records) - unique} duplicate line(s) collapsed)")

    # Spot checks against the real payload.
    gig = lookup("巨无霸", index, fuzzy)
    check(gig["status"] == "exact" and gig["record"]["energyKcal"] == 513.0,
          "巨无霸 exact match, energyKcal=513")

    double_sausage = lookup("双层猪柳蛋麦满分", index, fuzzy)
    check(double_sausage["status"] == "exact"
          and double_sausage["record"]["protein"] == 29.0
          and double_sausage["record"]["sodium"] == 1210.0,
          "双层猪柳蛋麦满分 exact, protein=29, sodium=1210")

    # nutritionDescription non-null case must be preserved.
    pure = lookup("纯悦", index, fuzzy)
    check(pure["status"] == "exact"
          and pure["record"]["nutritionDescription"] == "能量约清水1杯",
          "纯悦 nutritionDescription preserved")

    # Missing item must be flagged, never fabricated.
    missing = lookup("吉士蛋麦满分", index, fuzzy)
    check(missing["status"] in ("missing", "fuzzy"),
          f"吉士蛋麦满分 status={missing['status']} (no fabrication)")

    print()
    print("=== 2) Accumulate a realistic combo ===")
    combo = accumulate(["巨无霸", "中薯条", "无糖可口可乐中杯"], index, fuzzy)
    totals = combo["totals"]
    check(abs(totals["energyKcal"] - (513 + 289 + 0)) < 1e-6,
          f"combo energy = {totals['energyKcal']} (513+289+0)")
    check(abs(totals["protein"] - (27 + 4 + 0)) < 1e-6,
          f"combo protein = {totals['protein']} (27+4+0)")
    check(abs(totals["sodium"] - (961 + 165 + 35)) < 1e-6,
          f"combo sodium = {totals['sodium']} (961+165+35)")
    check(combo["complete"] is True, "combo complete (all items matched)")

    print()
    print("=== 3) Store / segment resolver ===")
    with open(STORES_FIXTURE, "r", encoding="utf-8") as f:
        stores_payload = json.load(f)
    stores = extract_reservable_stores(stores_payload)
    check(len(stores) == 5, f"extracted {len(stores)} reservable stores (expected 5)")

    first = stores[0]
    check(first["storeCode"] == "1950121" and first["storeName"] == "麦当劳北京朝外餐厅",
          f"first store = {first['storeCode']} / {first['storeName']}")

    tomorrow = [o for o in first["options"] if o["date"] == "2026-10-12"][0]
    names = [s["name"] for s in tomorrow["segments"]]
    check(names == ["早餐", "午餐", "下午茶", "夜市"],
          f"segments parsed: {names}")

    # Match a time inside lunch.
    m = match_segment(tomorrow["segments"], "12:00")
    check(m["status"] == "in" and m["segment"]["name"] == "午餐",
          f"12:00 -> in / {m['segment']['name'] if m['segment'] else None}")

    # 10:30 falls in the kitchen switching gap (10:15 -> 10:44).
    g = match_segment(tomorrow["segments"], "10:30")
    check(g["status"] == "gap", f"10:30 -> {g['status']} (gap {g.get('gap')})")

    # East bridge store has an extra 宵夜 segment.
    east = [s for s in stores if s["storeCode"] == "1950006"][0]
    east_tmr = [o for o in east["options"] if o["date"] == "2026-10-12"][0]
    east_names = [s["name"] for s in east_tmr["segments"]]
    check("宵夜" in east_names, f"东大桥店 extra segment detected: {east_names}")

    print()
    print("RESULT:", "ALL PASS" if _ok else "SOME FAILED")
    return 0 if _ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
