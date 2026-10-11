#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Render a Markdown reservation plan card.

Input is a plan dict (or a list of plan dicts) with keys such as:
``rank``, ``slotName``, ``slotRange``, ``storeName``, ``storeCode``,
``items``, ``totals``, ``missing``, ``score``, ``price``, ``payable``,
``coupon``, ``campaignHint``, ``reason``, ``perPerson`` (group mode).

Usage:
    python scripts/render_card.py < plan.json > card.md
"""

from __future__ import annotations

import json
import sys
from typing import Any

NUTRITION_LABELS = [
    ("energyKcal", "能量", "kcal"),
    ("protein", "蛋白质", "g"),
    ("fat", "脂肪", "g"),
    ("carbohydrate", "碳水", "g"),
    ("sodium", "钠", "mg"),
    ("calcium", "钙", "mg"),
]

DISCLAIMER = (
    "> ℹ️ 营养数据来自麦当劳官方 `list-nutrition-foods` 接口，为**单餐参考值**；"
    "本工具提供的是**均衡膳食搭配建议**，不构成医疗或临床营养建议。"
)

# 每餐钠参考值：< 700 mg（按《中国居民膳食指南》每日 < 2000 mg 的 1/3 计）
SODIUM_PER_MEAL_REFERENCE = 700.0

KEY_LABELS = {
    "energy": ("能量", "kcal"), "protein": ("蛋白质", "g"),
    "fat": ("脂肪", "g"), "carbohydrate": ("碳水", "g"),
    "sodium": ("钠", "mg"), "calcium": ("钙", "mg"),
}


def _num(value: Any, unit: str, digits: int = 1) -> str:
    if value is None:
        return "暂无数据"
    try:
        return f"{round(float(value), digits)} {unit}"
    except (TypeError, ValueError):
        return "暂无数据"


def _achieve_text(detail: dict[str, Any]) -> str:
    """Render a per-diner achievement summary, e.g. ``蛋白质 70%``."""
    if not detail:
        return ""
    return "、".join(
        f"{KEY_LABELS.get(k, (k, ''))[0]} {round(float(d.get('achieved', 0)) * 100)}%"
        for k, d in detail.items()
    )


def render_plan_card(plan: dict[str, Any]) -> str:
    """Render a single plan dict as a Markdown card."""
    lines: list[str] = []
    rank = plan.get("rank", "?")
    slot = plan.get("slotName", "未知餐段")
    rng = plan.get("slotRange") or ""
    lines.append(f"### 方案 {rank}｜{slot}{rng}")
    lines.append("")

    if plan.get("storeName"):
        lines.append(f"**门店**：{plan['storeName']}（{plan.get('storeCode', '-')}）")
        lines.append("")

    when = plan.get("targetTime") or plan.get("slotRecommendTime")
    if when:
        lines.append(f"**预约时间**：{when}")
        lines.append("")

    # ---- group mode: one line per diner -------------------------------
    per_person = plan.get("perPerson")
    if per_person:
        lines.append("**每人组合**")
        lines.append("")
        for p in per_person:
            goals = p.get("goalText") or "无特殊目标"
            achieve = _achieve_text(p.get("detail"))
            suffix = f" · {achieve}" if achieve else ""
            lines.append(
                f"- **{p.get('name', '?')}**（{goals}）：{p.get('mealName', '-')}"
                f" · ¥{p.get('price', 0):g}{suffix}"
            )
            if p.get("items"):
                lines.append(f"  - 组成：{'、'.join(p['items'])}")
        lines.append("")

        # A diner whose goal is unreachable here must be called out beside
        # their own line, not buried in the closing "why this time" sentence.
        # The per-person entry already prints "蛋白质 70%", but nothing there
        # says whether that is a near miss or an impossibility in this slot.
        if plan.get("relaxedFor"):
            lines.append(
                f"> ⚠️ **{'、'.join(plan['relaxedFor'])}** 的目标在该餐段内"
                "没有任何组合能完全满足，已按相对最优分配；"
                "要完全达标需放宽该目标或改约其他餐段。"
            )
            lines.append("")
    else:
        lines.append("**推荐组合**")
        lines.append("")
        items = plan.get("items") or []
        if items:
            for item in items:
                lines.append(f"- {item}")
        else:
            lines.append("- （暂无推荐项）")
        lines.append("")

    # ---- nutrition -----------------------------------------------------
    title = "**营养核算（整单）**" if per_person else "**营养核算（整单）**"
    lines.append(title)
    lines.append("")
    lines.append("| 营养素 | 数值 |")
    lines.append("|---|---|")
    totals = plan.get("totals") or {}
    for key, label, unit in NUTRITION_LABELS:
        lines.append(f"| {label} | {_num(totals.get(key), unit, 0 if unit in ('kcal', 'mg') else 1)} |")
    lines.append("")

    sodium = totals.get("sodium")
    if sodium is not None and float(sodium) > SODIUM_PER_MEAL_REFERENCE:
        share = float(sodium) / 2000.0 * 100
        lines.append("")
        lines.append(
            f"> 🧂 本方案钠 {float(sodium):.0f} mg，高于每餐参考值"
            f"（< {SODIUM_PER_MEAL_REFERENCE:.0f} mg，按每日 2000 mg 的 1/3 计），"
            f"约占每日上限的 {share:.0f}%。麦当劳单品钠普遍偏高，"
            "这是同一餐段内的相对更优解，如需更低钠可对比其他方案。"
        )
    lines.append("")

    if plan.get("missing"):
        lines.append(
            "> ⚠️ 部分餐品暂无官方营养数据："
            + "，".join(plan["missing"])
            + "，本方案营养核算**不完整**，仅供参考。"
        )
        lines.append("")

    # ---- goal achievement ----------------------------------------------
    detail = plan.get("detail") or {}
    if detail:
        lines.append("**目标达成**")
        lines.append("")
        lines.append("| 目标 | 要求 | 实际 | 达成 |")
        lines.append("|---|---|---|---|")
        for key, d in detail.items():
            label, unit = KEY_LABELS.get(key, (key, ""))
            op_text = "≤" if d.get("op") == "max" else "≥"
            lines.append(
                f"| {label} | {op_text} {d.get('target'):g} {unit} | "
                f"{d.get('actual'):g} {unit} | {round(float(d.get('achieved', 0)) * 100)}% |"
            )
        lines.append("")

    score = plan.get("score")
    if score is not None:
        lines.append(f"**达成度评分**：{score}")
        lines.append("")

    # ---- price ----------------------------------------------------------
    price = plan.get("price")
    payable = plan.get("payable")
    if payable is not None and price is not None and abs(payable - price) > 1e-6:
        lines.append(f"**价格**：¥{price:g} → 券后 **¥{payable:g}**")
    elif price is not None:
        lines.append(f"**价格**：¥{price:g}")
    if plan.get("coupon"):
        lines.append("")
        lines.append(f"🎫 匹配券：{plan['coupon'].get('name', '-')}（{plan['coupon'].get('discount', '-')}）")
    if price is not None or payable is not None:
        lines.append("")

    if plan.get("campaignHint"):
        lines.append(f"🎉 {plan['campaignHint']}")
        lines.append("")

    # ---- replacement suggestions ----------------------------------------
    swaps = plan.get("swaps")
    if swaps:
        lines.append("**可优化替换**")
        lines.append("")
        for s in swaps:
            delta = s.get("deltaText") or ""
            lines.append(f"- {s.get('round', '')}：{s.get('from', '')} → **{s.get('to', '')}**（{s.get('diffPrice', '')}）{delta}")
        lines.append("")

    if plan.get("reason"):
        lines.append(f"**为什么选这个时间**：{plan['reason']}")
        lines.append("")

    return "\n".join(lines)


def render_cards(plans: list[dict[str, Any]], header: str | None = None,
                 warnings: list[str] | None = None) -> str:
    """Render a full report: header, plans, warnings and the disclaimer."""
    out: list[str] = []
    if header:
        out.append(f"## {header}")
        out.append("")
    for p in plans:
        out.append(render_plan_card(p))
        out.append("---")
        out.append("")
    if warnings:
        out.append("**提示**")
        out.append("")
        for w in warnings:
            out.append(f"- {w}")
        out.append("")
    out.append(DISCLAIMER)
    out.append("")
    return "\n".join(out)


def render_slot_recommendation(ranking: list[dict[str, Any]]) -> str:
    """Render the reverse-mode output: which time slot to book."""
    lines: list[str] = ["## 反向时段推荐：按你的营养目标，该约几点？", ""]
    if not ranking:
        lines.append("> ⚠️ 该门店当日所有可预约餐段内均未找到满足目标的组合，"
                     "建议放宽目标或改选其他门店。")
        lines.append("")
        return "\n".join(lines)

    lines.append("| 排名 | 餐段 | 建议预约时间 | 最佳组合 | 能量 | 蛋白 | 钠 | 价格 | 评分 |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for i, r in enumerate(ranking, start=1):
        best = r.get("bestPlan") or {}
        t = best.get("totals") or {}
        lines.append(
            f"| {i} | {r.get('slotName', '-')}{r.get('slotRange', '')} | "
            f"{r.get('suggestTime', '-')} | {best.get('mealName', '无解')} | "
            f"{_num(t.get('energyKcal'), 'kcal', 0)} | {_num(t.get('protein'), 'g')} | "
            f"{_num(t.get('sodium'), 'mg', 0)} | ¥{best.get('price', 0):g} | {r.get('score', 0)} |"
        )
    lines.append("")

    top = ranking[0]
    best = top.get("bestPlan") or {}
    lines.append(
        f"**结论**：建议预约 **{top.get('suggestTime', '-')}**（{top.get('slotName', '')}"
        f"{top.get('slotRange', '')}），点 **{best.get('mealName', '-')}**"
    )
    if top.get("reason"):
        lines.append("")
        lines.append(f"> {top['reason']}")
    lines.append("")
    lines.append(DISCLAIMER)
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    payload = json.loads(sys.stdin.read() or "[]")
    if isinstance(payload, dict):
        if payload.get("mode") == "slot":
            sys.stdout.write(render_slot_recommendation(payload.get("ranking") or []))
            return 0
        payload = [payload]
    for p in payload:
        sys.stdout.write(render_plan_card(p))
        sys.stdout.write("\n---\n\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
