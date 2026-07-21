#!/usr/bin/env python3
"""Collect conservative code-rot evidence without changing project files."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

sys.dont_write_bytecode = True

from collectors import builtin, git_history, python as python_collector, typescript
from security import discover_executable


EXTERNAL_COLLECTORS: dict[str, Callable[[Path, Path, int], tuple[list[dict[str, Any]], dict[str, Any]]]] = {
    "knip": typescript.collect_knip,
    "vulture": python_collector.collect_vulture,
    "ruff": python_collector.collect_ruff,
    "deptry": python_collector.collect_deptry,
}
CATEGORY_ORDER = {
    "orphan-file": 0,
    "dependency-no-usage-evidence": 1,
    "export-no-usage-evidence": 2,
    "symbol-no-usage-evidence": 3,
    "duplicate-file": 4,
    "commented-code": 5,
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def candidate_key(candidate: dict[str, Any]) -> tuple[str, ...]:
    affected = candidate.get("affected") or {}
    kind = str(affected.get("kind") or "unknown")
    if kind == "dependency":
        return kind, str(affected.get("dependency") or "").lower()
    if kind == "symbol":
        return kind, str(affected.get("path") or "").lower(), str(affected.get("symbol") or "")
    return kind, str(affected.get("path") or "").lower()


def merge_external_signals(candidates: list[dict[str, Any]], signals: list[dict[str, Any]]) -> None:
    by_key = {candidate_key(candidate): candidate for candidate in candidates}
    for signal in signals:
        key = candidate_key(signal)
        existing = by_key.get(key)
        if existing:
            evidence = signal["evidence"]
            if evidence not in existing["evidence_sources"]:
                existing["evidence_sources"].append(evidence)
            continue
        affected = signal["affected"]
        candidate = {
            "category": signal["category"],
            "affected": affected,
            "evidence_sources": [signal["evidence"]],
            "confidence": "medium",
            "risk": "medium",
            "unresolved_questions": ["What runtime, framework, public API, or operational surfaces are outside this collector's model?"],
            "proof_status": "NOT_RUN",
            "recommendation": "REVIEW",
            "proof_eligible": False,
            "safety": {"dynamic_usage": "unknown", "external_api": "unknown", "convention_role": "unknown"},
            "why_suspicious": "A mature project analyzer reported no usage evidence for this item.",
            "why_might_still_be_needed": "One analyzer is not removal proof and can miss dynamic, external, or configuration-driven use.",
            "loc": 0,
            "bytes": 0,
        }
        candidates.append(candidate)
        by_key[key] = candidate


def finalize_candidates(candidates: list[dict[str, Any]]) -> None:
    candidates.sort(key=lambda item: (
        CATEGORY_ORDER.get(item["category"], 99),
        str((item.get("affected") or {}).get("path") or ""),
        str((item.get("affected") or {}).get("dependency") or (item.get("affected") or {}).get("symbol") or ""),
    ))
    for index, candidate in enumerate(candidates, start=1):
        candidate["candidate_id"] = f"CRT-{index:03d}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path, help="Project root to inspect")
    parser.add_argument("output", type=Path, help="analysis.json path")
    parser.add_argument(
        "--allow-tool", action="append", choices=sorted(EXTERNAL_COLLECTORS), default=[],
        help="Run one explicitly approved project-aware analyzer; repeat as needed",
    )
    parser.add_argument("--include-git-history", action="store_true", help="Add supporting read-only Git age, frequency, and blame evidence")
    parser.add_argument("--tool-timeout", type=int, default=120)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.tool_timeout < 1:
        raise SystemExit("--tool-timeout must be positive.")
    project = args.project.resolve(strict=True)
    collected = builtin.collect(project)
    candidates = collected["candidates"]
    tool_runs: list[dict[str, Any]] = []
    available_tools: dict[str, str | None] = {}
    for name in EXTERNAL_COLLECTORS:
        executable = discover_executable(project, name)
        available_tools[name] = str(executable) if executable else None

    for name in dict.fromkeys(args.allow_tool):
        executable = Path(available_tools[name]) if available_tools[name] else None
        if executable is None:
            tool_runs.append({
                "tool": name,
                "status": "unavailable",
                "execution_mode": "not-run",
                "environment_policy": {"name": "not-applicable"},
                "limitations": ["The tool was approved but no existing executable was found; nothing was installed or downloaded."],
            })
            continue
        signals, tool_run = EXTERNAL_COLLECTORS[name](project, executable, args.tool_timeout)
        merge_external_signals(candidates, signals)
        tool_runs.append(tool_run)

    finalize_candidates(candidates)
    git_run = git_history.collect(project, candidates) if args.include_git_history else {"status": "not-requested"}
    source_files = collected["source_files"]
    texts = collected["texts"]
    result = {
        "schema_version": "2.0",
        "mode": "report-only",
        "project_root": str(project),
        "generated_at": utc_now(),
        "scope": {
            "source_extensions": sorted(builtin.SOURCE_EXTENSIONS),
            "excluded_directories": sorted(builtin.EXCLUDED_DIRECTORIES),
            "source_files": len(source_files),
            "source_loc": sum(builtin.line_count(texts.get(path, "")) for path in source_files),
            "source_bytes": sum(path.stat().st_size for path in source_files),
            "skipped_symlinks": collected["skipped_symlinks"],
        },
        "summary": {
            "candidates": len(candidates),
            "proof_eligible": sum(1 for candidate in candidates if candidate["proof_eligible"]),
            "proven_removable": 0,
            "review_required": len(candidates),
            "by_category": {
                category: sum(1 for candidate in candidates if candidate["category"] == category)
                for category in CATEGORY_ORDER
            },
        },
        "ecosystems": collected["ecosystems"],
        "available_external_tools": available_tools,
        "tool_runs": tool_runs,
        "git_history": git_run,
        "candidates": candidates,
        "limitations": collected["limitations"] + [
            "External analyzer availability is only reported; tools run only when named with --allow-tool after explicit approval.",
            "Git history is supporting context only and never upgrades a recommendation.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"Analyzed {result['scope']['source_files']} source files in report-only mode.")
    print(f"Collected {result['summary']['candidates']} candidates; none were promoted to SAFE TO REMOVE.")
    print(f"Evidence: {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
