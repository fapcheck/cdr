#!/usr/bin/env python3
"""Generate the Codex-native Markdown report and cleanup plan."""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True


MATURE_FAMILIES = {"knip", "vulture", "ruff", "deptry", "typescript-compiler", "project-native"}


def safe_wording(value: Any) -> str:
    text = str(value).replace("|", "\\|").replace("\n", " ")
    return re.sub(r"\bunused\b", "with no usage evidence", text, flags=re.IGNORECASE)


def format_bytes(value: int) -> str:
    if value < 1024:
        return f"{value} B"
    if value < 1024 * 1024:
        return f"{value / 1024:.1f} KB"
    return f"{value / (1024 * 1024):.1f} MB"


def affected_location(candidate: dict[str, Any]) -> str:
    affected = candidate.get("affected") or {}
    location = affected.get("path") or "unknown"
    if affected.get("dependency"):
        location = f"{location}::{affected['dependency']}"
    elif affected.get("symbol"):
        location = f"{location}::{affected['symbol']}"
    if affected.get("line"):
        location = f"{location}:{affected['line']}"
    return str(location)


def proof_result(candidate_id: str, proof: dict[str, Any] | None) -> dict[str, Any] | None:
    if not proof:
        return None
    return next((item for item in proof.get("results", []) if item.get("candidate_id") == candidate_id), None)


def derive_recommendation(candidate: dict[str, Any], proof: dict[str, Any] | None,
                          decisions: dict[str, dict[str, Any]]) -> tuple[str, str, str]:
    candidate_id = candidate["candidate_id"]
    decision = decisions.get(candidate_id)
    if decision:
        recommendation = decision.get("recommendation") or decision.get("status")
        reason = str(decision.get("reason") or "").strip()
        if recommendation not in {"KEEP", "REVIEW"} or not reason:
            raise ValueError(f"Manual decision for {candidate_id} must be KEEP or REVIEW and include a reason.")
        return recommendation, "MANUALLY_REVIEWED", reason
    result = proof_result(candidate_id, proof)
    if result and result.get("outcome") == "FAILED_AFTER_REMOVAL":
        return "KEEP", "FAILED", "An approved check failed after removing only this candidate."
    if not proof or not proof.get("baseline", {}).get("passed") or not result:
        return "REVIEW", "NOT_RUN", "No successful disposable-copy proof is available."
    if result.get("outcome") != "PASSED_IN_DISPOSABLE_COPY":
        return "REVIEW", "INCONCLUSIVE", str(result.get("reason") or "Disposable-copy proof was inconclusive.")

    families = {item.get("family") for item in candidate.get("evidence_sources", []) if item.get("family") != "git"}
    safety = candidate.get("safety") or {}
    commands = result.get("commands") or []
    safe = (
        len(families) >= 2
        and bool(families & MATURE_FAMILIES)
        and candidate.get("confidence") == "high"
        and candidate.get("risk") == "low"
        and candidate.get("proof_eligible")
        and not candidate.get("unresolved_questions")
        and safety.get("dynamic_usage") == "none-found"
        and safety.get("external_api") == "none-known"
        and safety.get("convention_role") == "none-found"
        and bool(commands)
        and all(command.get("passed") for command in commands)
    )
    if safe:
        return "SAFE TO REMOVE", "PASSED", "Independent static evidence and approved checks passed after isolated removal in a fresh disposable copy."
    return "REVIEW", "PASSED_WITH_LIMITATIONS", "Removal proof passed, but evidence independence, uncertainty, or residual-risk requirements are not fully satisfied."


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("analysis", type=Path)
    parser.add_argument("markdown", type=Path)
    parser.add_argument("csv", type=Path)
    parser.add_argument("--proof", type=Path)
    parser.add_argument("--review", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    analysis = json.loads(args.analysis.read_text(encoding="utf-8"))
    if analysis.get("schema_version") != "2.0":
        raise SystemExit("Report requires analysis schema_version 2.0.")
    proof = json.loads(args.proof.read_text(encoding="utf-8")) if args.proof else None
    review = json.loads(args.review.read_text(encoding="utf-8")) if args.review else {"decisions": []}
    decisions = {item["candidate_id"]: item for item in review.get("decisions", [])}
    rows: list[dict[str, Any]] = []
    for candidate in analysis.get("candidates", []):
        recommendation, status, reason = derive_recommendation(candidate, proof, decisions)
        rows.append({**candidate, "recommendation": recommendation, "proof_status": status, "recommendation_reason": reason})

    counts = Counter(row["recommendation"] for row in rows)
    safe_rows = [row for row in rows if row["recommendation"] == "SAFE TO REMOVE"]
    review_rows = [row for row in rows if row["recommendation"] == "REVIEW"]
    keep_rows = [row for row in rows if row["recommendation"] == "KEEP"]
    state = "PROOF COMPLETE" if proof else "REPORT READY"
    if proof and not proof.get("baseline", {}).get("passed"):
        state = "INCONCLUSIVE"
    lines = [
        "# Code Rot Audit Report",
        "",
        f"> **{state}** - The real project was not changed.",
        "",
        "## Summary",
        "",
        f"- Scanned files: {analysis['scope']['source_files']:,}",
        f"- Candidates: {len(rows):,}",
        f"- Proven removable: {len(safe_rows):,}",
        f"- Review required: {len(review_rows):,}",
        f"- Keep: {len(keep_rows):,}",
        "",
        "| Recommendation | Candidates | Potential LOC | Potential size |",
        "|---|---:|---:|---:|",
    ]
    for recommendation, selected in (("SAFE TO REMOVE", safe_rows), ("REVIEW", review_rows), ("KEEP", keep_rows)):
        lines.append(f"| {recommendation} | {counts[recommendation]} | {sum(item.get('loc', 0) for item in selected):,} | {format_bytes(sum(item.get('bytes', 0) for item in selected))} |")
    lines.append("")

    if analysis.get("tool_runs"):
        lines.extend(["## Evidence tool executions", ""])
        for run in analysis["tool_runs"]:
            policy = run.get("environment_policy") or {}
            lines.extend([
                f"### {safe_wording(run.get('tool', 'tool'))}",
                "",
                f"- Command executed: `{safe_wording(run.get('command', 'not run'))}`",
                f"- Execution mode: `{safe_wording(run.get('execution_mode', 'not-run'))}`",
                f"- Environment policy: `{safe_wording(policy.get('name', 'unknown'))}`",
                f"- Result: `{safe_wording(run.get('status', 'unknown'))}`",
            ])
            for limitation in run.get("limitations", []):
                lines.append(f"- Limitation: {safe_wording(limitation)}")
            lines.append("")

    if proof:
        lines.extend(["## Disposable-copy proof", ""])
        baseline = proof.get("baseline") or {}
        lines.append(f"Baseline: **{'PASSED' if baseline.get('passed') else 'FAILED'}**")
        lines.extend(["", "| Check | Command executed | Execution mode | Environment policy | Result |", "|---|---|---|---|---|"])
        for command in baseline.get("commands", []):
            policy = command.get("environment_policy") or {}
            lines.append(
                f"| {safe_wording(command.get('kind', 'other'))} | `{safe_wording(command.get('command', ''))}` | "
                f"`{safe_wording(command.get('execution_mode', 'unknown'))}` | `{safe_wording(policy.get('name', 'unknown'))}` | "
                f"{'PASS' if command.get('passed') else 'FAIL'} |"
            )
        lines.append("")
        for limitation in proof.get("limitations", []):
            lines.append(f"- Limitation: {safe_wording(limitation)}")
        lines.append("")

    lines.extend(["## Candidates", ""])
    for candidate in rows:
        lines.extend([
            f"### {candidate['candidate_id']} - {candidate['recommendation']}",
            "",
            f"- ID: `{candidate['candidate_id']}`",
            f"- Type: `{safe_wording(candidate['category'])}`",
            f"- Location: `{safe_wording(affected_location(candidate))}`",
            f"- Confidence: `{candidate['confidence']}`",
            f"- Risk: `{candidate['risk']}`",
            f"- Why suspicious: {safe_wording(candidate['why_suspicious'])}",
            f"- Why it might still be needed: {safe_wording(candidate['why_might_still_be_needed'])}",
            f"- Proof: `{candidate['proof_status']}` - {safe_wording(candidate['recommendation_reason'])}",
            f"- Recommendation: **{candidate['recommendation']}**",
            "- Evidence:",
        ])
        for item in candidate.get("evidence_sources", []):
            lines.append(f"  - `{safe_wording(item.get('source', 'unknown'))}`: {safe_wording(item.get('detail', ''))}")
        questions = candidate.get("unresolved_questions") or []
        if questions:
            lines.append("- Unresolved questions:")
            lines.extend(f"  - {safe_wording(question)}" for question in questions)
        lines.append("")

    lines.extend(["## Proposed cleanup", ""])
    if safe_rows:
        lines.extend([
            "The following items passed the automated minimums. This is a proposal, not authorization:",
            "",
            "| Candidate ID | Exact file | Expected impact | Risk |",
            "|---|---|---|---|",
        ])
        for row in safe_rows:
            lines.append(f"| {row['candidate_id']} | `{safe_wording(affected_location(row))}` | Remove {row.get('loc', 0)} LOC / {format_bytes(row.get('bytes', 0))} | {row['risk']} |")
        ids = ", ".join(row["candidate_id"] for row in safe_rows)
        lines.extend(["", f"To authorize real changes, reply exactly with the intended subset, for example: `Approve {ids}`."])
    else:
        lines.append("No candidate currently satisfies every SAFE TO REMOVE requirement. No real changes are proposed.")
    lines.extend([
        "",
        "## Scope and limitations",
        "",
        "- No static analyzer can guarantee perfect absence of dynamic, reflective, external, platform-specific, or operational use.",
        "- Uncertainty is intentional. The audit optimizes against incorrect deletion, not for maximum removable volume.",
    ])
    for limitation in analysis.get("limitations", []):
        lines.append(f"- {safe_wording(limitation)}")

    args.markdown.parent.mkdir(parents=True, exist_ok=True)
    args.markdown.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    args.csv.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "candidate_id", "recommendation", "category", "location", "confidence", "risk",
        "proof_status", "loc", "bytes", "evidence_sources", "unresolved_questions", "recommendation_reason",
    ]
    with args.csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({
                "candidate_id": row["candidate_id"],
                "recommendation": row["recommendation"],
                "category": row["category"],
                "location": affected_location(row),
                "confidence": row["confidence"],
                "risk": row["risk"],
                "proof_status": row["proof_status"],
                "loc": row.get("loc", 0),
                "bytes": row.get("bytes", 0),
                "evidence_sources": json.dumps(row.get("evidence_sources", []), ensure_ascii=False),
                "unresolved_questions": json.dumps(row.get("unresolved_questions", []), ensure_ascii=False),
                "recommendation_reason": row["recommendation_reason"],
            })
    print(f"Report: {args.markdown.resolve()}")
    print(f"Cleanup plan: {args.csv.resolve()}")
    print(f"SAFE TO REMOVE: {len(safe_rows)}; REVIEW: {len(review_rows)}; KEEP: {len(keep_rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
