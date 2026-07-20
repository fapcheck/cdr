#!/usr/bin/env python3
"""Test eligible file removals in fresh disposable project copies."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


COPY_EXCLUDES = {
    ".git", ".hg", ".svn", "node_modules", ".venv", "venv", "vendor", "target",
    "dist", "build", "coverage", "outputs", "__pycache__", ".pytest_cache",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def run_command(command: str, cwd: Path, timeout: int) -> dict[str, Any]:
    start = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            shell=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            env={**os.environ, "CODE_ROT_CLEANER_DISPOSABLE_COPY": "1"},
        )
        output = completed.stdout or ""
        return {
            "command": command,
            "exit_code": completed.returncode,
            "duration_seconds": round(time.monotonic() - start, 3),
            "timed_out": False,
            "output": output[-5000:],
            "passed": completed.returncode == 0,
        }
    except subprocess.TimeoutExpired as error:
        output = error.stdout or ""
        if isinstance(output, bytes):
            output = output.decode(errors="replace")
        return {
            "command": command,
            "exit_code": None,
            "duration_seconds": round(time.monotonic() - start, 3),
            "timed_out": True,
            "output": str(output)[-5000:],
            "passed": False,
        }


def copy_project(source: Path, destination: Path, include_dependencies: bool) -> None:
    excluded = {".git", ".hg", ".svn", "outputs", "__pycache__", ".pytest_cache"}
    if not include_dependencies:
        excluded |= COPY_EXCLUDES
    shutil.copytree(source, destination, ignore=shutil.ignore_patterns(*sorted(excluded)), symlinks=True)


def safe_candidate_path(root: Path, relative_path: str) -> Path:
    candidate = (root / relative_path).resolve()
    if candidate == root or root not in candidate.parents:
        raise ValueError(f"Candidate escapes disposable root: {relative_path}")
    return candidate


def run_all(commands: list[str], cwd: Path, timeout: int) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for command in commands:
        result = run_command(command, cwd, timeout)
        results.append(result)
        if not result["passed"]:
            break
    return results


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path)
    parser.add_argument("analysis", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--command", action="append", required=True, help="Approved command; repeat for multiple checks")
    parser.add_argument("--confirm-run-project-code", action="store_true", help="Required acknowledgement for executing repository-controlled commands")
    parser.add_argument("--candidate-id", action="append", help="Limit proof to selected IDs")
    parser.add_argument("--max-candidates", type=int, default=10)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--include-dependencies", action="store_true", help="Copy dependency directories; can be slow and large")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.confirm_run_project_code:
        raise SystemExit("Refusing to run project commands without --confirm-run-project-code after explicit user approval.")
    if args.max_candidates < 1 or args.timeout < 1:
        raise SystemExit("--max-candidates and --timeout must be positive.")

    project = args.project.resolve()
    analysis_path = args.analysis.resolve()
    data = json.loads(analysis_path.read_text(encoding="utf-8"))
    if Path(data["project_root"]).resolve() != project:
        raise SystemExit("Analysis project_root does not match the requested project.")

    selected_ids = set(args.candidate_id or [])
    candidates = [
        item for item in data.get("candidates", [])
        if item.get("proof_eligible") and item.get("path") and (not selected_ids or item.get("id") in selected_ids)
    ][:args.max_candidates]

    result: dict[str, Any] = {
        "schema_version": "1.0",
        "project_root": str(project),
        "analysis_file": str(analysis_path),
        "generated_at": utc_now(),
        "commands": args.command,
        "copy_excluded_directories": sorted(set() if args.include_dependencies else COPY_EXCLUDES),
        "baseline": {"passed": False, "commands": []},
        "results": [],
        "limitations": [
            "Commands ran in disposable copies, not the real project.",
            "A green command set proves only the behavior exercised by those commands.",
        ],
    }

    with tempfile.TemporaryDirectory(prefix="code-rot-cleaner-") as temp:
        temp_root = Path(temp)
        baseline_dir = temp_root / "baseline"
        copy_project(project, baseline_dir, args.include_dependencies)
        baseline_commands = run_all(args.command, baseline_dir, args.timeout)
        baseline_passed = bool(baseline_commands) and all(item["passed"] for item in baseline_commands)
        result["baseline"] = {"passed": baseline_passed, "commands": baseline_commands}

        if baseline_passed:
            for candidate in candidates:
                start = time.monotonic()
                candidate_dir = temp_root / candidate["id"]
                copy_project(project, candidate_dir, args.include_dependencies)
                target = safe_candidate_path(candidate_dir.resolve(), candidate["path"])
                if not target.is_file():
                    result["results"].append({
                        "candidate_id": candidate["id"],
                        "path": candidate["path"],
                        "outcome": "SKIPPED",
                        "reason": "Candidate file was absent from the disposable copy.",
                        "commands": [],
                    })
                    continue
                target.unlink()
                command_results = run_all(args.command, candidate_dir, args.timeout)
                passed = bool(command_results) and all(item["passed"] for item in command_results)
                result["results"].append({
                    "candidate_id": candidate["id"],
                    "path": candidate["path"],
                    "outcome": "PASSED_IN_DISPOSABLE_COPY" if passed else "FAILED_AFTER_REMOVAL",
                    "duration_seconds": round(time.monotonic() - start, 3),
                    "commands": command_results,
                })
        else:
            result["limitations"].append("The untouched baseline failed, so no removal candidate was classified.")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"Baseline: {'passed' if result['baseline']['passed'] else 'failed'}")
    print(f"Evaluated {len(result['results'])} removal candidate(s) in disposable copies.")
    print(f"Proof: {args.output.resolve()}")
    return 0 if result["baseline"]["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
