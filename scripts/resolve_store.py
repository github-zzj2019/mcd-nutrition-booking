#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Resolve a McDonald's store and its reservation meal segments.

The agent calls `mcp__mcd-mcp__query-nearby-stores` and pipes the returned
JSON into this module (or imports it) to:

1. Filter stores that support reservation (`reservation=true`).
2. Parse `reservationTimeOptions[*].reservationOptionText` into structured
   meal segments with start / end clock times.
3. Match a target clock time to a segment, and detect segment gaps.

Measured text sample::

    早餐(06:14至10:15)，午餐(10:44至14:15)，下午茶(14:44至16:45)，夜市(17:14至21:45)

Usage:
    python scripts/resolve_store.py < stores.json
"""

from __future__ import annotations

import json
import re
import sys
from typing import Any

# Matches segments like "早餐(06:14至10:15)" or "午餐(10:44至14:15)".
SEGMENT_RE = re.compile(r"([^\(（,，]+)[（(](\d{2}:\d{2})至(\d{2}:\d{2})[)）]")


def parse_time_options(text: str) -> list[dict[str, Any]]:
    """Parse a reservationOptionText into structured meal segments."""
    segments: list[dict[str, Any]] = []
    for m in SEGMENT_RE.finditer(text or ""):
        segments.append(
            {
                "name": m.group(1).strip(),
                "start": m.group(2),
                "end": m.group(3),
            }
        )
    return segments


def match_segment(segments: list[dict[str, Any]], hhmm: str) -> dict[str, Any]:
    """Match a clock time (``HH:MM``) to a meal segment.

    Returns ``{"segment": <segment or None>, "status": "in"|"gap"|"closed"}``.
    A ``gap`` status means the time falls in the kitchen switching window
    between two segments (~29 min).
    """
    for seg in segments:
        if seg["start"] <= hhmm <= seg["end"]:
            return {"segment": seg, "status": "in"}

    ordered = sorted(segments, key=lambda s: s["start"])
    prev_end = None
    for seg in ordered:
        if prev_end is not None and prev_end < hhmm < seg["start"]:
            return {"segment": None, "status": "gap", "gap": [prev_end, seg["start"]]}
        prev_end = seg["end"]
    return {"segment": None, "status": "closed"}


def extract_reservable_stores(stores_payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract stores that support reservation, with parsed meal segments.

    ``stores_payload`` is the dict returned by `query-nearby-stores`.
    """
    raw_stores = stores_payload.get("data") or stores_payload.get("stores") or []
    if isinstance(raw_stores, dict):
        raw_stores = list(raw_stores.values())

    result: list[dict[str, Any]] = []
    for s in raw_stores:
        if not s.get("reservation"):
            continue
        options = s.get("reservationTimeOptions") or []
        parsed_options = [
            {
                "date": opt.get("date"),
                "today": opt.get("today"),
                "segments": parse_time_options(opt.get("reservationOptionText", "")),
                "raw": opt.get("reservationOptionText", ""),
            }
            for opt in options
        ]
        result.append(
            {
                "storeCode": s.get("storeCode"),
                "storeName": s.get("storeName"),
                "address": s.get("address"),
                "distance": s.get("distance"),
                "businessStartTime": s.get("businessStartTime"),
                "businessEndTime": s.get("businessEndTime"),
                "businessStatus": s.get("businessStatus"),
                "options": parsed_options,
            }
        )
    return result


def main() -> int:
    payload = json.loads(sys.stdin.read() or "{}")
    stores = extract_reservable_stores(payload)
    json.dump(stores, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
