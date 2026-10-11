#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Attach coupons and campaign hints to a plan card (D12).

Coupons
-------
`query-store-coupons(storeCode)` returns coupons that are **already validated**
for this store + order type, and — unlike `available-coupons` — it carries
``tradeDateTime`` (validity window) and ``products[].productCode``. That product
code lives in the same code space as `query-meals`, so a coupon can be matched
to a plan deterministically instead of by fuzzy title matching.

**Important boundary**: the coupon payload contains **no discount amount**.
This module therefore never invents one. It reports *which* coupon applies and
leaves the actual payable amount to `calculate-price`. If the agent supplies a
``price_map`` of ``{plan code: payable}`` obtained from `calculate-price`, the
amount is attached verbatim; otherwise ``payable`` stays ``None`` and the card
says the amount is pending.

Campaigns
---------
Measured: 2026-10-11 carries 12 campaigns, 2026-10-12 only 2 — the day you book
changes what you can catch. Campaigns are surfaced as a **single line** on the
card, never as a browsable list (that would dilute the core time<->nutrition
value and collide with the official "活动推荐助手" example).

Usage:
    python scripts/apply_benefits.py \
        --plans plans.json \
        --coupons tests/fixtures/coupons_sample.json \
        --campaigns tests/fixtures/campaigns_sample.json \
        --date 2026-10-12 --slot 早餐 --time "2026-10-12 08:00"
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime
from typing import Any

ISO_DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
# 10月8日 / 10月21日
CN_DATE_RE = re.compile(r"(\d{1,2})\s*月\s*(\d{1,2})\s*日")

NO_COUPON_NOTE = "当前账号在该门店暂无可用券（已通过 query-store-coupons 校验，非本工具臆测）"
PENDING_PRICE_NOTE = "券后应付金额需由 calculate-price 计算，本工具不做估算"


# --------------------------------------------------------------------------- #
# Date window parsing
# --------------------------------------------------------------------------- #
def _to_date(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def parse_trade_window(text: str, fallback_year: int | None = None) -> tuple[date | None, date | None]:
    """Parse ``tradeDateTime`` into ``(start, end)``.

    Handles ``2026-10-08 至 2026-10-21`` and ``10月8日至10月21日`` (Chinese form
    is resolved against ``fallback_year``).
    """
    if not text:
        return None, None
    iso = ISO_DATE_RE.findall(text)
    if len(iso) >= 2:
        start = _to_date(int(iso[0][0]), int(iso[0][1]), int(iso[0][2]))
        end = _to_date(int(iso[-1][0]), int(iso[-1][1]), int(iso[-1][2]))
        return start, end
    if len(iso) == 1:
        start = _to_date(int(iso[0][0]), int(iso[0][1]), int(iso[0][2]))
        return start, None

    cn = CN_DATE_RE.findall(text)
    if cn:
        year = fallback_year or date.today().year
        parsed = [_to_date(year, int(m), int(d)) for m, d in cn]
        parsed = [p for p in parsed if p]
        if len(parsed) >= 2:
            return parsed[0], parsed[-1]
        if len(parsed) == 1:
            return parsed[0], None
    return None, None


def is_valid_on(coupon: dict[str, Any], on_date: date | str,
                fallback_year: int | None = None) -> bool:
    """Return True when the coupon's window covers ``on_date``."""
    if isinstance(on_date, str):
        try:
            on_date = datetime.strptime(on_date[:10], "%Y-%m-%d").date()
        except ValueError:
            return True  # 日期无法解析时不擅自拦截

    if not isinstance(on_date, date):
        return True

    year = on_date.year
    start, end = parse_trade_window(coupon.get("tradeDateTime", ""), fallback_year or year)
    if start and on_date < start:
        return False
    if end and on_date > end:
        return False
    return True


# --------------------------------------------------------------------------- #
# Coupon matching
# --------------------------------------------------------------------------- #
def _plan_codes(plan: dict[str, Any]) -> set[str]:
    """Every product code a plan touches: the combo itself and each diner's pick."""
    codes: set[str] = set()
    if plan.get("code"):
        codes.add(str(plan["code"]))
    for p in plan.get("perPerson") or []:
        if p.get("code"):
            codes.add(str(p["code"]))
    return codes


def match_coupons(plan: dict[str, Any], coupons_payload: dict | list,
                  on_date: date | str | None = None) -> dict[str, Any]:
    """Match validated coupons to a plan by ``products[].productCode``.

    Returns ``{"matched": [...], "expiredSkipped": n}``. Coupons outside their
    ``tradeDateTime`` window are counted, never matched.
    """
    raw = coupons_payload.get("data") if isinstance(coupons_payload, dict) else coupons_payload
    raw = raw or []
    codes = _plan_codes(plan)
    names = set(plan.get("items") or [])
    for p in plan.get("perPerson") or []:
        names.update(p.get("items") or [])

    matched: list[dict[str, Any]] = []
    expired_skipped = 0
    for c in raw:
        if not isinstance(c, dict):
            continue
        if on_date and not is_valid_on(c, on_date):
            expired_skipped += 1
            continue
        hit = None
        for prod in c.get("products") or []:
            if str(prod.get("productCode", "")) in codes:
                hit = f"商品编码 {prod.get('productCode')} 命中"
                break
            if prod.get("productName") in names:
                hit = f"适用商品 {prod.get('productName')}"
                break
        if hit:
            matched.append({
                "couponId": c.get("couponId"),
                "title": c.get("title"),
                "couponCode": c.get("couponCode"),
                "tradeDateTime": c.get("tradeDateTime"),
                "matchedOn": hit,
            })
    return {"matched": matched, "expiredSkipped": expired_skipped}


def match_campaigns(campaigns_payload: dict | list, target_date: str | date,
                    slot_name: str = "", target_time: str = "") -> list[dict[str, Any]]:
    """Return campaigns whose window covers the reservation date (and slot)."""
    if isinstance(campaigns_payload, dict):
        raw = campaigns_payload.get("campaigns") or []
    else:
        raw = campaigns_payload or []

    if isinstance(target_date, date):
        target_date = target_date.isoformat()

    active: list[dict[str, Any]] = []
    for c in raw:
        if not isinstance(c, dict):
            continue

        # `campaign-calendar` groups campaigns by day; a campaign absent from the
        # target day's list is not running that day, regardless of its window.
        days = c.get("days") or []
        if days and target_date and target_date not in days:
            continue

        start, end = parse_trade_window(c.get("window", ""),
                                        int(target_date[:4]) if target_date else None)
        if start and target_date and target_date < start.isoformat():
            continue
        if end and target_date and target_date > end.isoformat():
            continue
        slots = c.get("slots") or []
        if slots and slot_name and slot_name not in slots:
            continue
        active.append({
            "title": c.get("title"),
            "window": c.get("window"),
            "launchTime": c.get("launchTime"),
            "slotMatched": bool(slots and slot_name in slots),
        })
    return active


def campaign_hint(active: list[dict[str, Any]], max_lines: int = 1) -> str | None:
    """Compose the single cross-hint line for the card."""
    if not active:
        return None
    # 优先展示与当前餐段绑定的活动，这正是「时间 × 活动」的交叉价值
    ordered = sorted(active, key=lambda c: not c.get("slotMatched"))
    bits: list[str] = []
    for c in ordered[:max_lines]:
        win = f"（{c['window']}）" if c.get("window") else ""
        tail = "，正好覆盖你预约的这个餐段" if c.get("slotMatched") else ""
        bits.append(f"你预约的时段内「{c['title']}」进行中{win}{tail}")
    return "；".join(bits)


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def attach_benefits(plans: list[dict[str, Any]], coupons_payload: dict | list | None,
                    campaigns_payload: dict | list | None,
                    target_date: str | date | None = None,
                    slot_name: str = "", target_time: str = "",
                    price_map: dict[str, float] | None = None) -> list[dict[str, Any]]:
    """Attach ``coupon`` / ``payable`` / ``campaignHint`` / notes onto each plan.

    ``price_map`` maps a plan's product code to the payable amount returned by
    `calculate-price`. Without it the card stays honest and marks the amount
    as pending rather than guessing a discount.
    """
    coupons_raw = None
    if isinstance(coupons_payload, dict):
        coupons_raw = coupons_payload.get("data")
    elif isinstance(coupons_payload, list):
        coupons_raw = coupons_payload
    has_coupons = bool(coupons_raw)

    active_campaigns = match_campaigns(campaigns_payload or [], target_date or "",
                                       slot_name, target_time)
    hint = campaign_hint(active_campaigns)

    out: list[dict[str, Any]] = []
    for plan in plans:
        p = dict(plan)
        result = match_coupons(p, coupons_payload or {}, target_date)
        matched = result["matched"]
        if result["expiredSkipped"]:
            p["expiredCouponsSkipped"] = result["expiredSkipped"]

        if matched:
            p["coupon"] = {
                "name": matched[0]["title"],
                "couponId": matched[0]["couponId"],
                "discount": "以 calculate-price 结算为准",
                "matchedOn": matched[0]["matchedOn"],
            }
            p["couponCount"] = len(matched)
        else:
            p["coupon"] = None
            p["couponCount"] = 0

        payable = None
        if price_map:
            key = str(p.get("code") or "")
            if key and key in price_map:
                payable = price_map[key]
        if payable is not None:
            p["payable"] = round(float(payable), 2)
        else:
            p["payable"] = None
            p["priceNote"] = PENDING_PRICE_NOTE if matched else NO_COUPON_NOTE

        if hint:
            p["campaignHint"] = hint
        p["activeCampaigns"] = active_campaigns
        out.append(p)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Attach coupons and campaign hints")
    ap.add_argument("--plans", required=True, help="plans JSON from plan_meal.py")
    ap.add_argument("--coupons", default=None, help="query-store-coupons payload")
    ap.add_argument("--campaigns", default=None, help="structured campaign-calendar payload")
    ap.add_argument("--date", default=None, help="reservation date yyyy-MM-dd")
    ap.add_argument("--slot", default="", help="meal segment name, e.g. 早餐")
    ap.add_argument("--time", default="", help="reservation time 'yyyy-MM-dd HH:mm'")
    ap.add_argument("--prices", default=None, help="JSON {code: payable} from calculate-price")
    args = ap.parse_args()

    plans = json.load(open(args.plans, "r", encoding="utf-8"))
    if isinstance(plans, dict):
        plans = [plans]
    coupons = json.load(open(args.coupons, "r", encoding="utf-8")) if args.coupons else None
    campaigns = json.load(open(args.campaigns, "r", encoding="utf-8")) if args.campaigns else None
    price_map = json.load(open(args.prices, "r", encoding="utf-8")) if args.prices else None

    result = attach_benefits(plans, coupons, campaigns, args.date, args.slot,
                             args.time, price_map)
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
