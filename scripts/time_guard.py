#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Time urgency / segment-deadline awareness.

Measured basis (see ``tests/fixtures/stores.json``): the SAME store returns a
different set of reservable meal segments for different dates. For store
1950121 the payload carries:

    2026-10-11 (today)  : 午餐(10:44-14:15)，下午茶(14:44-16:45)，夜市(17:14-21:45)
    2026-10-12 (tomorrow): 早餐(06:14-10:15)，午餐(10:44-14:15)，下午茶(14:44-16:45)，夜市(17:14-21:45)

**"今天" has no breakfast segment at all** because 早餐 already ended. This is
the single most user-visible fact about the time<->menu coupling, and it is
invisible unless someone computes it: the user asking "帮我预约早餐" at 12:39
must be told that breakfast is gone *for today* but bookable from 06:14 tomorrow.

Note the API never returns segments that have already ended — today's list is
simply shorter. So "breakfast is gone" cannot be read off today's payload
alone; it has to be derived by differencing today's segments against another
day's. That is exactly what ``missingToday`` computes.

Functions
---------
``segment_states``   - classify each segment as past / current / upcoming.
``guard``            - full urgency report for a target date (and optional clock).
``diff_days``        - compare today's segments against another day's, surfacing
                       segments that exist tomorrow but are already gone today.

Usage:
    python scripts/time_guard.py --stores tests/fixtures/stores.json \
        --now "2026-10-11 12:39" --date 2026-10-11
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

from resolve_store import extract_reservable_stores, match_segment  # noqa: E402


def _to_minutes(hhmm: str) -> int:
    if not hhmm:
        return 0
    h, m = hhmm.split(":")[:2]
    return int(h) * 60 + int(m)


def segment_states(segments: list[dict[str, Any]], now_hhmm: str,
                   is_today: bool) -> list[dict[str, Any]]:
    """Classify segments relative to ``now``.

    For a future date every segment is ``upcoming``; for today a segment can be
    ``past`` (already ended), ``current`` (bookable right now) or ``upcoming``.
    ``minutesLeft`` / ``minutesUntil`` are ``None`` for future dates because the
    segment repeats tomorrow and "minutes until" would be misleading.
    """
    now_min = _to_minutes(now_hhmm)
    out: list[dict[str, Any]] = []
    for seg in segments:
        start, end = _to_minutes(seg["start"]), _to_minutes(seg["end"])
        if not is_today:
            state, left, until = "upcoming", None, None
        elif end < now_min:
            state, left, until = "past", 0, None
        elif start <= now_min <= end:
            state, left, until = "current", end - now_min, None
        else:
            state, left, until = "upcoming", None, start - now_min
        out.append({**seg, "state": state, "minutesLeft": left, "minutesUntil": until})
    return out


def _pick_store(stores_payload: dict, store_code: str | None) -> dict | None:
    stores = extract_reservable_stores(stores_payload)
    if not stores:
        return None
    if store_code:
        for s in stores:
            if s.get("storeCode") == store_code:
                return s
    return stores[0]


def _segments_for_date(store: dict, date_str: str) -> list[dict[str, Any]]:
    for opt in store.get("options") or []:
        if opt.get("date") == date_str:
            return opt.get("segments") or []
    return []


def guard(stores_payload: dict, now: str, target_date: str,
          target_hhmm: str | None = None,
          store_code: str | None = None) -> dict[str, Any]:
    """Build the urgency report for ``target_date`` at ``now``.

    ``now`` / ``target_date`` are ``yyyy-MM-dd HH:MM`` / ``yyyy-MM-dd``.
    """
    today = now[:10]
    now_hhmm = now[11:16] if len(now) > 10 else "00:00"
    is_today = target_date == today

    store = _pick_store(stores_payload, store_code)
    if store is None:
        return {"status": "no_store", "message": "附近没有支持预约的门店"}

    segments = _segments_for_date(store, target_date)
    if not segments:
        return {"status": "no_date", "storeCode": store.get("storeCode"),
                "storeName": store.get("storeName"),
                "message": f"该门店 {target_date} 没有返回的预约时段（可能超出可预约范围）"}

    states = segment_states(segments, now_hhmm, is_today)
    current = next((s for s in states if s["state"] == "current"), None)
    upcoming = [s for s in states if s["state"] == "upcoming"]
    past = [s for s in states if s["state"] == "past"]

    target_state = None
    if target_hhmm:
        # 已经结束的餐段不会出现在接口返回的列表里（实测：今天的列表直接不含早餐），
        # 所以「目标时间早于现在」必须优先判定，否则会被误报为「不在营业时段」。
        if is_today and _to_minutes(target_hhmm) < _to_minutes(now_hhmm):
            target_state = "elapsed"
        else:
            hit = match_segment(segments, target_hhmm)
            target_state = hit["status"]

    # What exists on another day but is already unreachable today?
    missing_today: list[str] = []
    if is_today:
        for opt in store.get("options") or []:
            if opt.get("date") == today:
                continue
            other_names = {s["name"] for s in opt.get("segments") or []}
            today_names = {s["name"] for s in segments}
            missing_today = sorted(other_names - today_names)
            break

    return {
        "status": "ok",
        "storeCode": store.get("storeCode"),
        "storeName": store.get("storeName"),
        "date": target_date,
        "isToday": is_today,
        "now": now,
        "segments": states,
        "current": current,
        "upcoming": upcoming,
        "past": past,
        "targetTime": target_hhmm,
        "targetState": target_state,
        "missingToday": missing_today,
    }


def render_guard(g: dict[str, Any]) -> str:
    """Render the urgency report as Markdown."""
    if g.get("status") != "ok":
        return f"> ⚠️ {g.get('message', '无法解析预约时段')}\n"

    lines: list[str] = [
        f"## ⏱ 时间紧迫度 · {g['storeName']}（{g['storeCode']}）· {g['date']}",
        "",
        f"当前时间 {g['now']}（{'今天' if g['isToday'] else '未来日期'}）",
        "",
        "| 餐段 | 时段 | 状态 | 剩余/还有 |",
        "|---|---|---|---|",
    ]
    label = {"past": "已结束", "current": "可约中", "upcoming": "未开始"}
    for s in g["segments"]:
        if s["state"] == "current":
            extra = f"还剩 {s['minutesLeft']} 分钟"
        elif s["state"] == "upcoming" and s["minutesUntil"] is not None:
            extra = f"{s['minutesUntil']} 分钟后开始"
        elif s["state"] == "upcoming":
            extra = "当天可约"
        else:
            extra = "今天已错过"
        lines.append(
            f"| {s['name']} | {s['start']}–{s['end']} | {label[s['state']]} | {extra} |"
        )
    lines.append("")

    cur = g.get("current")
    if cur:
        lines.append(
            f"✅ 现在处于 **{cur['name']}** 时段，距离截止还有 **{cur['minutesLeft']} 分钟**，"
            "现在下单还来得及。"
        )
    else:
        nxt = g.get("upcoming") or []
        if nxt:
            nxt_txt = "、".join(f"{s['name']}（{s['start']}）" for s in nxt[:3])
            lines.append(f"⏳ 当前不在任何可预约餐段内，接下来可约：{nxt_txt}。")
        else:
            lines.append("⛔ 今天剩余时间已无可预约餐段，建议改约明天。")
    lines.append("")

    if g.get("missingToday"):
        lines.append(
            "> 📌 注意：同一家门店「今天」和「明天」的**可预约餐段并不相同**——"
            f"今天已经约不到 {'、'.join(g['missingToday'])}（该餐段已结束）；"
            "如果你要吃的是这一餐，需要改约明天。"
        )
        lines.append("")

    ts = g.get("targetState")
    text = {
        "gap": "该时间落在厨房换餐空档，请改约相邻餐段",
        "closed": "该时间不在门店可预约时段内",
        "elapsed": "该时间今天已经过了",
    }.get(ts or "")
    if text:
        lines.append(f"> ⚠️ {text}")
        lines.append("")

    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="Reservation deadline awareness")
    ap.add_argument("--stores", required=True)
    ap.add_argument("--now", required=True, help="yyyy-MM-dd HH:MM")
    ap.add_argument("--date", default=None, help="target date yyyy-MM-dd")
    ap.add_argument("--time", dest="target_hhmm", default=None, help="target HH:MM")
    ap.add_argument("--store", default=None, help="storeCode")
    args = ap.parse_args()

    with open(args.stores, "r", encoding="utf-8") as f:
        payload = json.load(f)

    g = guard(payload, args.now, args.date or args.now[:10],
              args.target_hhmm, args.store)
    sys.stdout.write(render_guard(g))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
