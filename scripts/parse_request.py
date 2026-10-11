#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Parse a natural-language Chinese request into a structured constraint object.

The McDonald's MCP tools only accept structured parameters, so the first step of
the workflow is turning an utterance such as::

    明天中午12点约了朋友在公司楼下吃饭，帮我控制在600 kcal以内

into::

    {
      "rawText": "...",
      "timeStatus": "explicit",
      "targetTime": "2026-10-12 12:00",
      "city": null,
      "keyword": "公司楼下",
      "people": [{"name": "我", "goals": {"energy": {"op": "max", "value": 600}}}],
      "budget": null,
      "warnings": []
    }

When the user states nutrition goals but no time (reverse mode), ``timeStatus``
is ``"pending"`` and ``targetTime`` is ``null`` — the caller should then run
``scripts/recommend_slot.py`` to solve for the best reservation slot.

Usage:
    python scripts/parse_request.py --text "明天中午12点..."
    python scripts/parse_request.py --text "..." --now "2026-10-11 10:00"
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta
from typing import Any

# --------------------------------------------------------------------------- #
# Dictionaries
# --------------------------------------------------------------------------- #
DATE_WORDS: dict[str, int] = {
    "今天": 0, "今日": 0, "当天": 0,
    "明天": 1, "明日": 1,
    "后天": 2,
}

# Longest-first so that "晚上" wins over "晚".
PERIOD_HOURS: list[tuple[str, int]] = [
    ("凌晨", 2), ("清晨", 6), ("早上", 8), ("早晨", 8), ("上午", 10),
    ("中午", 12), ("午间", 12), ("下午", 15), ("傍晚", 18), ("晚上", 19),
    ("夜里", 21), ("夜宵", 22), ("宵夜", 22),
]

CN_NUMBERS: dict[str, int] = {
    "一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}

# Keyword -> goals applied when the user expresses an intent without numbers.
KEYWORD_GOALS: list[tuple[str, dict[str, dict[str, Any]]]] = [
    ("增肌", {"protein": {"op": "min", "value": 30}}),
    ("高蛋白", {"protein": {"op": "min", "value": 30}}),
    ("蛋白够", {"protein": {"op": "min", "value": 30}}),
    ("控钠", {"sodium": {"op": "max", "value": 800}}),
    ("少盐", {"sodium": {"op": "max", "value": 800}}),
    ("低钠", {"sodium": {"op": "max", "value": 800}}),
    ("控糖", {"carbohydrate": {"op": "max", "value": 60}}),
    ("低糖", {"carbohydrate": {"op": "max", "value": 60}}),
    ("少糖", {"carbohydrate": {"op": "max", "value": 60}}),
    ("控卡", {"energy": {"op": "max", "value": 500}}),
    ("低卡", {"energy": {"op": "max", "value": 500}}),
    ("少油", {"fat": {"op": "max", "value": 25}}),
    ("补钙", {"calcium": {"op": "min", "value": 300}}),
]

NAME_TOKENS = (
    "小王", "小李", "小张", "小赵", "小陈", "小刘", "小周", "小孙",
    "老王", "老李", "老张",
    "我",
)

# 「帮我 / 给我 / 替我」里的「我」不是一位用餐者，需排除。
NAME_RE = re.compile(
    "(?<![帮给替和跟与带请让])(" + "|".join(re.escape(n) for n in NAME_TOKENS) + ")"
)

# 「约几点合适」表示时间本身是待求解的未知量。
ASK_TIME_RE = re.compile(r"(几点|什么时候|什么时间|哪个时段|哪个餐段|何时)")

# --------------------------------------------------------------------------- #
# Intent routing: which solver should handle this request?
# --------------------------------------------------------------------------- #
# These two intents select an *advanced* solver instead of the default
# single-slot one. They are deliberately narrow: a multi-day request must not
# fire on 「两个同事」and a cross-store request must not fire on 「哪家餐厅」.
#
#   连续三天 / 接下来 3 天 / 这周   -> scripts/plan_week.py
#   哪家店 / 换个店 / 附近门店      -> scripts/compare_stores.py

MULTI_DAY_RE = [
    re.compile(r"(?:连续|接下来|未来|往后|之后)\s*([0-9一两二三四五六七八九十]+)\s*(?:天|日)"),
    re.compile(r"([0-9一两二三四五六七八九十]+)\s*(?:天|日)\s*(?:的)?"
               r"(?:午餐|早餐|晚餐|午饭|晚饭|计划|安排|排程|菜单|餐)"),
]
WEEK_RE = re.compile(r"(这周|本周|一周|一星期|这个礼拜|整个星期)")

CROSS_STORE_RE = re.compile(
    r"(哪家店|哪家门店|哪个店|哪一家|哪店|换个店|换家店|换一家|"
    r"附近(?:的)?(?:门店|店)|所有门店|各家门店|各个门店|哪(?:里|儿)能(?:约|点))"
)

# --------------------------------------------------------------------------- #
# Time parsing
# --------------------------------------------------------------------------- #
def _parse_clock(text: str) -> tuple[int, int] | None:
    """Extract an explicit clock time from the text."""
    # 12:30 / 08:00
    m = re.search(r"(\d{1,2})\s*[:：]\s*(\d{2})", text)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        if 0 <= h <= 23 and 0 <= mi <= 59:
            return h, mi
    # 12点半
    m = re.search(r"(\d{1,2})\s*点\s*半", text)
    if m:
        h = int(m.group(1))
        if 0 <= h <= 23:
            return h, 30
    # 12点
    m = re.search(r"(\d{1,2})\s*点(?:钟)?", text)
    if m:
        h = int(m.group(1))
        if 0 <= h <= 23:
            return h, 0
    return None


def _parse_period(text: str) -> int | None:
    """Extract a coarse period hint (e.g. 中午 -> 12)."""
    for word, hour in PERIOD_HOURS:
        if word in text:
            return hour
    return None


def _parse_date_offset(text: str) -> int | None:
    """Return the day offset for 今天/明天/后天; None when unspecified."""
    for word, offset in DATE_WORDS.items():
        if word in text:
            return offset
    return None


def parse_time(text: str, now: datetime) -> dict[str, Any]:
    """Resolve the target datetime from an utterance.

    Returns ``{"status": "explicit"|"pending", "targetTime": str|None,
    "note": str|None}``.
    """
    clock = _parse_clock(text)
    offset = _parse_date_offset(text)

    if clock is None and (ASK_TIME_RE.search(text) or offset is None):
        return {"status": "pending", "targetTime": None,
                "note": "时间未定，进入反向时段推荐模式（按营养目标反推最优餐段）"}

    if clock is None:
        period = _parse_period(text)
        hour = period if period is not None else 12
        minute = 0
    else:
        hour, minute = clock

    day = (now + timedelta(days=(offset or 0))).date()
    target = datetime.combine(day, datetime.min.time()).replace(hour=hour, minute=minute)

    # "明天中午" said at 13:00 would resolve to a past instant; keep it simple and
    # only warn instead of silently shifting a day.
    note = None
    if offset == 0 and target < now:
        note = f"目标时间 {target:%Y-%m-%d %H:%M} 早于当前时间，已按原样保留，请与用户确认"

    return {"status": "explicit", "targetTime": target.strftime("%Y-%m-%d %H:%M"), "note": note}


# --------------------------------------------------------------------------- #
# Nutrition goal parsing
# --------------------------------------------------------------------------- #
ENERGY_RE = [
    # 600 kcal 以内 / 513大卡 —— 数字在前，单位在后
    re.compile(r"(\d+(?:\.\d+)?)\s*(?:kcal|Kcal|KCAL|大卡|千卡|卡路里|卡)(?![a-zA-Z0-9])"),
    # 控制在 600 / 热量 600 —— 单位在前，数字在后
    re.compile(r"(?:kcal|Kcal|大卡|千卡|卡路里|热量|能量|卡)\s*(?:控制在|不超过|低于|以内|以下|之内|小于|≤|<=|max)?\s*(\d+(?:\.\d+)?)"),
    # 600 以内（上下文已含热量/卡）
    re.compile(r"(\d+(?:\.\d+)?)\s*(?:以内|以下|之内)\s*(?:kcal|大卡|卡)"),
]
# 中文口语里的否定式限定词（「别超过」「不想超过」「不要超过」）同样表示上限。
MAX_PREFIX = (r"(?:控制在|不超过|不超过|低于|以内|以下|之内|小于|也别超过|别超过|"
              r"不要超过|不想超过|≤|<=)?")
MIN_PREFIX = r"(?:要|达到|超过|不少于|至少|吃够|≥|>=|以上)?"

PROTEIN_RE = [
    re.compile(rf"蛋白(?:质)?\s*{MIN_PREFIX}\s*(\d+(?:\.\d+)?)\s*(?:g|克)", re.I),
    re.compile(r"(\d+(?:\.\d+)?)\s*(?:g|克)\s*(?:以上)?蛋白"),
]
SODIUM_RE = [
    re.compile(rf"钠\s*{MAX_PREFIX}\s*(\d+(?:\.\d+)?)\s*(?:mg|毫克)", re.I),
    re.compile(r"(\d+(?:\.\d+)?)\s*(?:mg|毫克)\s*钠"),
]
FAT_RE = [
    re.compile(rf"脂肪\s*{MAX_PREFIX}\s*(\d+(?:\.\d+)?)\s*(?:g|克)"),
]
CARB_RE = [
    re.compile(rf"碳水(?:化合物)?\s*{MAX_PREFIX}\s*(\d+(?:\.\d+)?)\s*(?:g|克)"),
]
CALCIUM_RE = [
    re.compile(rf"钙\s*{MIN_PREFIX}\s*(\d+(?:\.\d+)?)\s*(?:mg|毫克)"),
]

# Sanity bounds: reject values that are physiologically implausible for one meal.
GOAL_BOUNDS: dict[str, tuple[float, float]] = {
    "energy": (200.0, 1500.0),
    "protein": (5.0, 120.0),
    "fat": (3.0, 120.0),
    "carbohydrate": (5.0, 200.0),
    "sodium": (100.0, 5000.0),
    "calcium": (20.0, 1500.0),
}


def _first_match(patterns: list[re.Pattern], text: str) -> float | None:
    for pat in patterns:
        m = pat.search(text)
        if m:
            try:
                return float(m.group(1))
            except (TypeError, ValueError):
                continue
    return None


def parse_goals(text: str) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Extract numeric nutrition goals plus keyword-derived goals."""
    goals: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []

    candidates = [
        ("energy", ENERGY_RE, "max"),
        ("protein", PROTEIN_RE, "min"),
        ("sodium", SODIUM_RE, "max"),
        ("fat", FAT_RE, "max"),
        ("carbohydrate", CARB_RE, "max"),
        ("calcium", CALCIUM_RE, "min"),
    ]
    for key, patterns, op in candidates:
        val = _first_match(patterns, text)
        if val is None:
            continue
        low, high = GOAL_BOUNDS[key]
        if val < low or val > high:
            warnings.append(f"{key} 目标值 {val:g} 超出单餐合理区间 [{low:g}, {high:g}]，已忽略")
            continue
        goals[key] = {"op": op, "value": val}

    for keyword, g in KEYWORD_GOALS:
        if keyword in text:
            for key, spec in g.items():
                # An explicit numeric goal always beats a keyword default.
                goals.setdefault(key, dict(spec))

    return goals, warnings


# --------------------------------------------------------------------------- #
# People parsing
# ------------------------------------------------------------------- #
def parse_people(text: str) -> tuple[list[dict[str, Any]], list[str]]:
    """Split the utterance per person and attach each one's goals."""
    warnings: list[str] = []
    matches = list(NAME_RE.finditer(text))
    if not matches:
        goals, w = parse_goals(text)
        warnings.extend(w)
        return [{"name": "我", "goals": goals}], warnings

    # Merge repeated mentions of the same person (「我」常被说多次)。
    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for i, m in enumerate(matches):
        name = m.group(1)
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        segment = text[m.start():end]
        goals, w = parse_goals(segment)
        warnings.extend(w)
        if name not in merged:
            merged[name] = {"name": name, "goals": {}}
            order.append(name)
        for key, spec in goals.items():
            merged[name]["goals"].setdefault(key, spec)

    people = [merged[n] for n in order]

    # Cross-check against an explicit headcount: 我和两个同事 = 3 人。
    head = _parse_headcount(text)
    if head is not None and head != len(people):
        warnings.append(
            f"文本提到 {head} 人，识别出 {len(people)} 位具名成员，"
            f"其余 {max(0, head - len(people))} 位沿用「{people[0]['name']}」的目标"
        )
        while len(people) < head:
            people.append({"name": f"成员{len(people) + 1}", "goals": dict(people[0]["goals"])})
    return people, warnings


def _parse_headcount(text: str) -> int | None:
    """Return the number of diners implied by the utterance."""
    # 我和两个同事 / 我跟三位朋友 -> 1 + N
    m = re.search(r"我\s*(?:和|跟|与|带)\s*([0-9一两二三四五六七八九十]+)\s*(?:个|位)?\s*(?:同事|朋友|同学|人)", text)
    if m:
        return 1 + _to_int(m.group(1))
    # 我和两个同事（无「我」）-> 同上
    m = re.search(r"(?:和|跟|与)\s*([0-9一两二三四五六七八九十]+)\s*(?:个|位)?\s*(?:同事|朋友|同学|人)", text)
    if m:
        return 1 + _to_int(m.group(1))
    # 3个人 / 三个人
    m = re.search(r"([0-9一两二三四五六七八九十]+)\s*(?:个|位)?人", text)
    if m:
        return _to_int(m.group(1))
    return None


def _to_int(token: str) -> int:
    token = token.strip()
    if token.isdigit():
        return int(token)
    return CN_NUMBERS.get(token, 0)


# --------------------------------------------------------------------------- #
# Plan-mode routing
# --------------------------------------------------------------------------- #
def _parse_span_days(text: str) -> int | None:
    """Return the number of days to schedule, or None for a single-day request."""
    if WEEK_RE.search(text):
        # 「这周」按工作日排程；周末门店时段差异大，交给用户明确指定。
        return 5
    for pat in MULTI_DAY_RE:
        m = pat.search(text)
        if not m:
            continue
        n = _to_int(m.group(1))
        # 1 天不是排程；超过 7 天超出预约可及范围（门店预约时段按日返回）。
        if 2 <= n <= 7:
            return n
    return None


def parse_plan_mode(text: str) -> dict[str, Any]:
    """Classify the request into ``single`` / ``week`` / ``cross_store``.

    Precedence is **week > cross_store > single**: a multi-day request that also
    mentions stores is still a schedule, not a one-off store comparison.

    Note that a span request usually carries no date word (「接下来三天中午」has
    no 今天/明天), so ``parse_time`` would mark it ``pending`` and the caller
    would route it into reverse mode. The orchestrator must therefore check
    ``planMode`` **before** ``timeStatus`` — see `scripts/run_scenario.py`.
    """
    span = _parse_span_days(text)
    if span is not None:
        return {"planMode": "week", "spanDays": span,
                "note": f"识别为多日排程意图：连续 {span} 天"}
    if CROSS_STORE_RE.search(text):
        return {"planMode": "cross_store", "spanDays": None,
                "note": "识别为跨门店择优意图"}
    return {"planMode": "single", "spanDays": None, "note": None}


# --------------------------------------------------------------------------- #
# Location & budget
# --------------------------------------------------------------------------- #
def parse_location(text: str) -> dict[str, Any]:
    """Extract city / keyword hints for `query-nearby-stores`."""
    city = None
    m = re.search(r"(北京|上海|广州|深圳|成都|杭州|南京|武汉|西安|重庆|苏州|天津|长沙|青岛|宁波|厦门)", text)
    if m:
        city = m.group(1)

    keyword = None
    m = re.search(r"(?:在|到|去)\s*([^，,。\s]{2,10}?)(?:楼下|附近|店|餐厅|门店)?\s*(?:吃|吃饭|点|约)", text)
    if m:
        keyword = m.group(1)
    if keyword is None:
        m = re.search(r"公司(?:楼下|附近)?", text)
        if m:
            keyword = "公司"
    if keyword is None and city:
        keyword = city
    return {"city": city, "keyword": keyword}


def parse_budget(text: str, now_people: int = 1) -> float | None:
    """Extract an upper budget bound."""
    m = re.search(r"预算\s*(?:是|控制在|不超过|在|约)?\s*(\d+(?:\.\d+)?)\s*(?:元|块(?:钱)?|¥)?", text)
    if m:
        return float(m.group(1))
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:元|块(?:钱)?)\s*(?:以内|以下|之内|搞定)", text)
    if m:
        return float(m.group(1))
    return None


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def parse_request(text: str, now: datetime | None = None) -> dict[str, Any]:
    """Parse an utterance into the constraint object consumed by `plan_meal.plan()`.

    Beyond the constraint fields this also returns the **routing decision**
    (``planMode`` / ``spanDays``) that tells the orchestrator which solver to
    run. Callers that only need constraints can ignore it.
    """
    now = now or datetime.now()
    warnings: list[str] = []

    t = parse_time(text, now)
    people, w = parse_people(text)
    warnings.extend(w)
    mode = parse_plan_mode(text)

    # A multi-day request has no single时间点, so parse_time's "reverse mode"
    # note would be actively misleading here. Replace it with the real intent.
    if mode["planMode"] == "week":
        status = "span"
        warnings.append(f"多日排程：将按连续 {mode['spanDays']} 天分别求解并做跨天去重")
    else:
        status = t["status"]
        if t.get("note"):
            warnings.append(t["note"])

    if mode["planMode"] == "cross_store" and status == "pending":
        warnings.append("跨门店择优需要一个具体时间；未识别到时间，将先按营养目标反推最优时段")

    loc = parse_location(text)
    budget = parse_budget(text, len(people))

    if not any(p.get("goals") for p in people):
        warnings.append("未识别到营养目标，将按门店该餐段的均衡搭配输出")

    return {
        "rawText": text,
        "timeStatus": status,
        "targetTime": t["targetTime"],
        "planMode": mode["planMode"],
        "spanDays": mode["spanDays"],
        "city": loc["city"],
        "keyword": loc["keyword"],
        "people": people,
        "budget": budget,
        "warnings": warnings,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Parse a McDonald's booking request")
    ap.add_argument("--text", required=True, help="user utterance")
    ap.add_argument("--now", default=None,
                    help="current time 'yyyy-MM-dd HH:MM' (defaults to system clock)")
    args = ap.parse_args()

    now = (datetime.strptime(args.now, "%Y-%m-%d %H:%M") if args.now else datetime.now())
    json.dump(parse_request(args.text, now), sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
