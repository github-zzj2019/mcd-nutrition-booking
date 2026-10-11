#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run every verification script in one go.

    python scripts/run_all_checks.py

Exits non-zero if any script reports a failure, so it can be used as a
pre-commit / CI gate.
"""

from __future__ import annotations

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)

SCRIPTS = [
    ("数据层", "verify_data_layer.py"),
    ("核心求解器", "verify_solver.py"),
    ("杀手功能", "verify_features.py"),
    ("边界与降级", "verify_edge_cases.py"),
    ("进阶功能", "verify_advanced.py"),
]


def main() -> int:
    ok = True
    for label, name in SCRIPTS:
        path = os.path.join(HERE, name)
        print("=" * 70)
        print(f"▶ {label} — {name}")
        print("=" * 70)
        proc = subprocess.run(
            [sys.executable, path],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        sys.stdout.write(proc.stdout)
        if proc.stderr:
            sys.stdout.write(proc.stderr)
        passed = proc.returncode == 0 and "SOME FAILED" not in proc.stdout
        print(f"\n  → {label}: {'PASS' if passed else 'FAIL'} (exit {proc.returncode})\n")
        ok = ok and passed

    print("=" * 70)
    print("OVERALL:", "ALL CHECKS PASSED" if ok else "SOME CHECKS FAILED")
    print("=" * 70)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
