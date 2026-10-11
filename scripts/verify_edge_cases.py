#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Boundary / degradation tests (D12).

Every case here is a scenario the skill can realistically hit in production:
an account with no coupons, a coupon outside its validity window, a menu whose
items have no official nutrition record, a budget below the cheapest item, a
reservation time that lands in the kitchen switching gap, and malformed MCP
payloads. The contract being protected is: **degrade explicitly, never fabricate
and never crash.**

Run from the repository root:
    python scripts/verify_edge_cases.py
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from parse_nutrition import (  # noqa: E402
    parse_nutrition_text, build_index, build_fuzzy_index,
)
from plan_meal import plan, candidate_pool  # noqa: E402
from resolve_store import parse_time_options, match_segment, extract_reservable_stores  # noqa: E402
from parse_request import parse_request  # noqa: E402
from apply_benefits import (  # noqa: E402
    attach_benefits, match_coupons, match_campaigns, parse_trade_window,
    is_valid_on, NO_COUPON_NOTE, PENDING_PRICE_NOTE,
)

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

    base_constraints = {
        "storeCode": "1950121", "storeName": "麦当劳北京朝外餐厅",
        "slotName": "午餐", "slotRange": "(10:44至14:15)",
        "targetTime": "2026-10-12 12:00", "budget": None,
        "people": [{"name": "我", "goals": {"energy": {"op": "max", "value": 600},
                                            "sodium": {"op": "max", "value": 800}}}],
    }

    # ------------------------------------------------------------------ #
    print("=== 1) 优惠券降级 ===")
    plans = plan(lunch_menu, nutrition_index, fuzzy_index, details,
                 base_constraints, top=3)
    check(len(plans) > 0, f"基线场景产出 {len(plans)} 个方案")

    empty_coupons = load_json("coupons_real_empty.json")
    out = attach_benefits(plans, empty_coupons, None, "2026-10-12", "午餐")
    check(all(p["coupon"] is None for p in out), "空券列表不误报匹配")
    check(out[0].get("priceNote") == NO_COUPON_NOTE,
          "给出明确的降级话术（而非静默略过）")
    check(all(p["payable"] is None for p in out), "无 calculate-price 结果时不编造券后价")

    print("\n=== 2) 券有效期过滤 ===")
    sample_coupons = load_json("coupons_sample.json")
    res = match_coupons({"code": "4437", "items": ["玉米杯"], "perPerson": []},
                        sample_coupons, "2026-10-12")
    check(res["expiredSkipped"] == 1, f"已过期券被拦截 {res['expiredSkipped']} 张")
    check(len(res["matched"]) == 0, "过期券未进入匹配结果")

    res2 = match_coupons({"code": "1450", "items": ["麦香鸡"], "perPerson": []},
                         sample_coupons, "2026-10-12")
    check(len(res2["matched"]) == 1 and res2["matched"][0]["couponId"] == "C1001",
          "按 productCode 精确命中 麦香鸡 -> C1001")

    combo_res = match_coupons({"code": "9900005466", "items": ["巨无霸", "中薯条", "可乐中杯"],
                               "perPerson": []}, sample_coupons, "2026-10-12")
    check(len(combo_res["matched"]) == 1,
          "套餐按其自身编码命中券（9900005466 -> C1002）")

    check(parse_trade_window("2026-10-08 至 2026-10-21")[0].isoformat() == "2026-10-08",
          "解析 ISO 有效期窗口")
    cn = parse_trade_window("10月8日至10月21日", 2026)
    check(cn[0].isoformat() == "2026-10-08" and cn[1].isoformat() == "2026-10-21",
          "解析中文有效期窗口（补全年份）")
    check(is_valid_on({"tradeDateTime": "2026-10-08 至 2026-10-21"}, "2026-10-12") is True,
          "窗口内日期判定为可用")
    check(is_valid_on({"tradeDateTime": "2026-09-01 至 2026-09-30"}, "2026-10-12") is False,
          "窗口外日期判定为不可用")

    print("\n=== 3) 券后价只来自 calculate-price ===")
    with_prices = attach_benefits(plans, sample_coupons, None, "2026-10-12", "午餐",
                                  price_map={plans[0]["code"]: 15.9})
    check(with_prices[0]["payable"] == 15.9,
          f"提供了 calculate-price 结果时按原值回填（¥{with_prices[0]['payable']}）")
    check(with_prices[1]["payable"] is None and with_prices[1]["priceNote"] is not None,
          "未提供的方案保持待定并给出说明")

    # ------------------------------------------------------------------ #
    print("\n=== 4) 活动 × 预约时段 交叉提示 ===")
    campaigns = load_json("campaigns_sample.json")
    breakfast_active = match_campaigns(campaigns, "2026-10-12", "早餐")
    check(any(c["slotMatched"] for c in breakfast_active),
          "早餐时段命中绑定早餐的活动")
    lunch_active = match_campaigns(campaigns, "2026-10-12", "午餐")
    check(not any(c["slotMatched"] for c in lunch_active),
          "午餐时段不误报仅适用早餐的活动")
    print(f"    10-12 早餐命中：{[c['title'] for c in breakfast_active]}")
    out_b = attach_benefits(plans, empty_coupons, campaigns, "2026-10-12", "午餐")
    check(out_b[0].get("campaignHint") is None,
          "无匹配活动时不产生空提示行（已按 campaign-calendar 的按日聚合过滤）")
    out_bf = attach_benefits(plans, empty_coupons, campaigns, "2026-10-12", "早餐")
    check(bool(out_bf[0].get("campaignHint")),
          "早餐时段产生单行活动提示")
    print(f"    提示：{out_bf[0].get('campaignHint')}")
    check(out_bf[0]["campaignHint"].count("；") == 0,
          "活动提示限定为单行，不做活动列表")

    # ------------------------------------------------------------------ #
    print("\n=== 5) 求解器边界 ===")
    check(plan({}, nutrition_index, fuzzy_index, details, base_constraints) == [],
          "空菜单返回空列表而非报错")
    unknown_menu = {"data": {"meals": {"99999999": {"name": "不存在的餐品",
                                                    "currentPrice": "1"}}}}
    check(plan(unknown_menu, nutrition_index, fuzzy_index, details,
               base_constraints) == [],
          "餐品无官方营养数据时不产出方案（不编造）")
    tiny_budget = {**base_constraints, "budget": 1}
    check(plan(lunch_menu, nutrition_index, fuzzy_index, details, tiny_budget) == [],
          "预算低于最低价时返回空而非越界")
    pool = candidate_pool(lunch_menu, nutrition_index, fuzzy_index, details)
    incomplete = [c for c in pool if not c["complete"]]
    check(len(incomplete) > 0, f"真实菜单中确有 {len(incomplete)} 个套餐营养数据不完整")
    check(all(c["missing"] for c in incomplete if not c["complete"]),
          "不完整方案均记录了缺失项名称")

    print("\n=== 6) 门店餐段边界 ===")
    segs = parse_time_options("早餐(06:14至10:15)，午餐(10:44至14:15)，"
                              "下午茶(14:44至16:45)，夜市(17:14至21:45)")
    check(len(segs) == 4, f"解析出 {len(segs)} 个餐段")
    check(match_segment(segs, "12:00")["status"] == "in", "12:00 命中午餐")
    gap = match_segment(segs, "10:30")
    check(gap["status"] == "gap" and gap["gap"] == ["10:15", "10:44"],
          f"10:30 落在厨房换餐空档 {gap.get('gap')}")
    check(match_segment(segs, "23:00")["status"] == "closed", "23:00 判定为打烊")
    stores = extract_reservable_stores(load_json("stores.json"))
    check(len(stores) > 0, f"从真实门店返回中提取 {len(stores)} 家可预约门店")

    print("\n=== 7) 自然语言解析降级 ===")
    vague = parse_request("帮我点个麦当劳", datetime(2026, 10, 11, 10, 0))
    check(vague["timeStatus"] == "pending", "无时间信息时进入反向模式")
    check(any("营养目标" in w for w in vague["warnings"]), "明确提示未识别到营养目标")
    nonsense = parse_request("asdf", datetime(2026, 10, 11, 10, 0))
    check(nonsense["timeStatus"] == "pending" and not nonsense["budget"],
          "无意义输入不崩溃，安全降级")
    implausible = parse_request("明天中午控制在5 kcal以内", datetime(2026, 10, 11, 10, 0))
    check(implausible["people"][0]["goals"].get("energy") is None,
          "生理上不合理的 5 kcal 目标被拒绝")
    check(any("合理区间" in w for w in implausible["warnings"]), "给出被拒绝的原因")

    print("\n=== 8) 脏数据健壮性 ===")
    dirty = parse_nutrition_text(
        "[3]{productName,nutritionDescription,energyKj,energyKcal,protein,fat,carbohydrate,sodium,calcium}:\n"
        "  正常餐品,null,100,50,2,1,5,10,3\n"
        "  \n"
        "  缺字段行,1,2\n"
        "  ,null,1,2,3,4,5,6,7\n"
    )
    check(len(dirty) == 1, f"畸形行被跳过，保留 {len(dirty)} 条有效记录")
    check(dirty[0]["productName"] == "正常餐品", "有效记录解析正确")
    dirty_details = {"_note": "metadata string", "9900005466": details["9900005466"]}
    from plan_meal import build_details_index  # noqa: PLC0415
    idx = build_details_index(dirty_details)
    check(len(idx) == 1 and "9900005466" in idx,
          "details 映射中的非 dict 说明字段被安全跳过")

    print("\nRESULT:", "ALL PASS" if _ok else "SOME FAILED")
    return 0 if _ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
