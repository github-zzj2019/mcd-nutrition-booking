#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Parse the McDonald's MCP nutrition text table into structured JSON.

`mcp__mcd-mcp__list-nutrition-foods` returns `data` as a **string** text table,
not JSON. Example::

    [160]{productName,nutritionDescription,energyKj,energyKcal,protein,fat,carbohydrate,sodium,calcium}:
      猪柳麦满分,null,1288,308,16,16,24,781,213
      巨无霸,null,2146,513,27,26,42,961,171

This module converts that text into a list of dicts, and builds exact /
fuzzy indexes keyed by product name.

Usage (smoke test):
    python scripts/parse_nutrition.py < input.txt > nutrition.json
"""

from __future__ import annotations

import json
import re
import sys
from typing import Any

FIELDS = [
    "productName",
    "nutritionDescription",
    "energyKj",
    "energyKcal",
    "protein",
    "fat",
    "carbohydrate",
    "sodium",
    "calcium",
]

# Fields 2..8 are numeric and should be converted to float (None when missing).
NUMERIC_FIELDS = set(FIELDS[2:])

HEADER_RE = re.compile(r"^\[\d+\]\{.*\}\s*:\s*$")

SPEC_WORDS = ("中杯", "小杯", "大杯", "盒装", "袋装", "份", "个", "条", "块")


def _to_number(raw: str) -> float | None:
    """Convert a raw table cell to float; return None for empty/null."""
    raw = (raw or "").strip()
    if raw == "" or raw.lower() == "null":
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _clean_text(raw: str) -> str | None:
    """Clean a text cell; return None for empty/null."""
    v = (raw or "").strip()
    return None if v.lower() == "null" else v


def parse_nutrition_text(raw: str) -> list[dict[str, Any]]:
    """Parse the raw text table into a list of nutrition records."""
    records: list[dict[str, Any]] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or HEADER_RE.match(line):
            continue
        parts = line.split(",")
        if len(parts) < len(FIELDS):
            # Skip malformed lines instead of raising, to stay robust.
            continue
        if len(parts) > len(FIELDS):
            # Extra commas: merge the leading extras back into productName.
            extra = len(parts) - len(FIELDS)
            parts = [",".join(parts[: extra + 1])] + parts[extra + 1 :]
        record: dict[str, Any] = {}
        for field, value in zip(FIELDS, parts):
            record[field] = _to_number(value) if field in NUMERIC_FIELDS else _clean_text(value)
        if record.get("productName"):
            records.append(record)
    return records


def build_index(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Build a product-name -> record index (exact match)."""
    return {r["productName"]: r for r in records if r.get("productName")}


def normalize_name(name: str) -> str:
    """Normalize a product name for fuzzy matching.

    Strips whitespace, full-width spaces, bracketed content and common
    specification words.
    """
    if not name:
        return ""
    s = name.strip()
    s = re.sub(r"[\s\u3000]+", "", s)
    s = re.sub(r"[（(].*?[)）]", "", s)
    for word in SPEC_WORDS:
        s = s.replace(word, "")
    return s


def build_fuzzy_index(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Build a normalized-name -> record index (fuzzy match)."""
    idx: dict[str, dict[str, Any]] = {}
    for r in records:
        key = normalize_name(r.get("productName", ""))
        if key and key not in idx:
            idx[key] = r
    return idx


def lookup(item_name: str, index: dict[str, dict], fuzzy_index: dict[str, dict]) -> dict[str, Any]:
    """Look up a single item's nutrition data.

    Returns a dict with ``status``: ``exact`` / ``fuzzy`` / ``missing``.
    """
    if item_name in index:
        return {"status": "exact", "record": index[item_name]}
    key = normalize_name(item_name)
    if key and key in fuzzy_index:
        return {"status": "fuzzy", "record": fuzzy_index[key]}
    return {"status": "missing", "record": None}


def main() -> int:
    raw = sys.stdin.read()
    records = parse_nutrition_text(raw)
    json.dump(records, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
