#!/usr/bin/env python3
"""Generate a native Markdown code-rot report and cleanup-plan CSV."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any


def escape(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def format_bytes(value: int) -> str:
    if value < 1024:
        return f"{value} B"
    if value < 1024 * 1024:
        return f"{value / 1024:.1f} KB"
    return f"{value / (1024 * 1024):.1f} MB"


def status_for(candidate: dict[str, Any], proof: dict[str, Any] | None,
               decisions: dict[str, dict[str, str]]) -> tuple[str, str]:
    decision = decisions.get(candidate["id"])
    if decision:
        status = decision.get("status")
        reason = decision.get("reason", "").strip()
        if status not in {"KEEP", "REVIEW"}:
            raise ValueError(f"Manual review for {candidate['id']} may only use KEEP or REVIEW.")
        if not reason:
            raise ValueError(f"Manual review for {candidate['id']} requires a reason.")
        return status, reason
    if not proof or not proof.get("baseline", {}).get("passed"):
        return "REVIEW", "Not proven in a disposable copy."
    result = next((item for item in proof.get("results", []) if item.get("candidate_id") == candidate["id"]), None)
    if not result:
        return "REVIEW", "Not selected or not eligible for removal proof."
    if result.get("outcome") == "FAILED_AFTER_REMOVAL":
        return "KEEP", "An approved command failed after removal."
    if (
        result.get("outcome") == "PASSED_IN_DISPOSABLE_COPY"
        and candidate.get("proof_eligible")
        and candidate.get("confidence") == "high"
        and candidate.get("risk") == "low"
    ):
        return "SAFE TO REMOVE", "Strong static evidence and approved commands passed after removal in a disposable copy."
    return "REVIEW", "Disposable-copy proof passed, but static confidence or residual risk is insufficient."


def unique_size(rows: list[dict[str, Any]], excluded_paths: set[str] | None = None) -> tuple[int, int, set[str]]:
    excluded_paths = excluded_paths or set()
    by_path: dict[str, dict[str, Any]] = {}
    for item in rows:
        path = item.get("path")
        if not path or path in excluded_paths:
            continue
        current = by_path.get(path)
        if current is None or item.get("loc", 0) > current.get("loc", 0):
            by_path[path] = item
    return (
        sum(item.get("loc", 0) for item in by_path.values()),
        sum(item.get("bytes", 0) for item in by_path.values()),
        set(by_path),
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("analysis", type=Path)
    parser.add_argument("markdown", type=Path)
    parser.add_argument("csv", type=Path)
    parser.add_argument("--proof", type=Path)
    parser.add_argument("--review", type=Path, help="Optional manual KEEP/REVIEW decisions JSON")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    analysis = json.loads(args.analysis.read_text(encoding="utf-8"))
    proof = json.loads(args.proof.read_text(encoding="utf-8")) if args.proof else None
    review = json.loads(args.review.read_text(encoding="utf-8")) if args.review else {"decisions": []}
    decisions = {item["candidate_id"]: item for item in review.get("decisions", [])}
    candidates = analysis.get("candidates", [])
    rows: list[dict[str, Any]] = []
    for item in candidates:
        status, reason = status_for(item, proof, decisions)
        rows.append({**item, "final_status": status, "status_reason": reason})

    status_counts = Counter(item["final_status"] for item in rows)
    safe_rows = [item for item in rows if item["final_status"] == "SAFE TO REMOVE"]
    review_rows = [item for item in rows if item["final_status"] == "REVIEW"]
    keep_rows = [item for item in rows if item["final_status"] == "KEEP"]
    safe_loc, safe_bytes, safe_paths = unique_size(safe_rows)
    possible_loc, possible_bytes, review_paths = unique_size(review_rows, safe_paths)
    keep_loc, keep_bytes, _ = unique_size(keep_rows, safe_paths | review_paths)

    lines = [
        "# Code Rot Report",
        "",
        f"> **{'PROOF COMPLETE' if proof else 'REPORT READY'}** — The real project was not changed.",
        "",
        f"Project: `{analysis['project_root']}`  ",
        f"Generated: `{analysis['generated_at']}`",
        "",
        "## Executive summary",
        "",
        "| Result | Candidates | LOC | Size |",
        "|---|---:|---:|---:|",
        f"| SAFE TO REMOVE | {status_counts['SAFE TO REMOVE']} | {safe_loc:,} | {format_bytes(safe_bytes)} |",
        f"| REVIEW | {status_counts['REVIEW']} | {possible_loc:,} | {format_bytes(possible_bytes)} |",
        f"| KEEP | {status_counts['KEEP']} | {keep_loc:,} | {format_bytes(keep_bytes)} |",
        "",
    ]
    if proof:
        baseline = proof.get("baseline", {})
        lines.extend([
            "## Proof status",
            "",
            f"Baseline in disposable copy: **{'PASSED' if baseline.get('passed') else 'FAILED'}**",
            "",
            "| Command | Result | Duration |",
            "|---|---|---:|",
        ])
        for command in baseline.get("commands", []):
            lines.append(f"| `{escape(command.get('command', ''))}` | {'PASS' if command.get('passed') else 'FAIL'} | {command.get('duration_seconds', 0):.3f}s |")
        lines.append("")

    lines.extend([
        "## Ranked candidates",
        "",
        "| ID | Status | Category | Subject | Confidence | Risk | LOC | Proof |",
        "|---|---|---|---|---|---|---:|---|",
    ])
    for item in rows:
        lines.append(
            f"| {item['id']} | **{item['final_status']}** | {escape(item['category'])} | `{escape(item['subject'])}` | "
            f"{item['confidence']} | {item['risk']} | {item.get('loc', 0)} | {escape(item['status_reason'])} |"
        )

    lines.extend(["", "## Evidence by candidate", ""])
    for item in rows:
        location = item.get("path") or item["subject"]
        if item.get("line"):
            location = f"{location}:{item['line']}"
        lines.extend([
            f"### {item['id']} — {item['final_status']}",
            "",
            f"- Location: `{location}`",
            f"- Category: `{item['category']}`",
            f"- Potential size: {item.get('loc', 0):,} LOC / {format_bytes(item.get('bytes', 0))}",
            f"- Status reason: {item['status_reason']}",
            "- Evidence: " + " ".join(item.get("evidence", [])),
            "- Caveats: " + " ".join(item.get("caveats", [])),
            "",
        ])

    lines.extend([
        "## Cleanup approval checklist",
        "",
        "No cleanup has been applied. To continue, select exact candidate IDs and review their paths, evidence, proof, and residual risk. Manifest or lockfile changes require separate explicit approval.",
        "",
        "```text",
        "Approved candidate IDs: ____________________",
        "Approved files / manifest entries: __________",
        "Approved verification commands: _____________",
        "```",
        "",
        "## Scope and limitations",
        "",
        f"- Scanned {analysis['scope']['source_files']:,} source files, {analysis['scope']['source_loc']:,} LOC, {format_bytes(analysis['scope']['source_bytes'])}.",
    ])
    for limitation in analysis.get("limitations", []):
        lines.append(f"- {limitation}")
    if proof:
        for limitation in proof.get("limitations", []):
            lines.append(f"- {limitation}")

    args.markdown.parent.mkdir(parents=True, exist_ok=True)
    args.markdown.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

    args.csv.parent.mkdir(parents=True, exist_ok=True)
    fields = ["id", "final_status", "category", "subject", "path", "line", "confidence", "risk", "proof_eligible", "loc", "bytes", "status_reason"]
    with args.csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    print(f"Report: {args.markdown.resolve()}")
    print(f"Cleanup plan: {args.csv.resolve()}")
    print(f"SAFE TO REMOVE: {len(safe_rows)}; REVIEW: {len(review_rows)}; KEEP: {len(keep_rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
