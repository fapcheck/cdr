#!/usr/bin/env python3
"""Prove one candidate at a time in fresh disposable project copies."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

from security import display_command, mask_secrets, sanitized_environment


COPY_EXCLUDES = {
    ".git", ".hg", ".svn", "outputs", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", ".code-rot-home",
}
DEPENDENCY_EXCLUDES = {"node_modules", ".venv", "venv", "vendor", "target", "dist", "build", "coverage"}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def ensure_symlink_target_inside(root: Path, link: Path, target: Path) -> None:
    root = root.resolve()
    target = target.resolve()
    if target == root or root in target.parents:
        return
    raise ValueError(f"Symlink escapes project root: {link} -> {target}")


def validate_symlink_tree(root: Path) -> None:
    root = root.resolve(strict=True)
    for current, directories, files in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        for name in [*directories, *files]:
            path = current_path / name
            if not path.is_symlink():
                continue
            raw_target = Path(os.readlink(path))
            target = raw_target if raw_target.is_absolute() else path.parent / raw_target
            ensure_symlink_target_inside(root, path, target)
            if raw_target.is_absolute():
                raise ValueError(f"Absolute symlink is not safe in a disposable copy: {path}")


def safe_candidate_path(root: Path, relative_path: str) -> Path:
    relative = Path(relative_path)
    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
        raise ValueError(f"Candidate escapes disposable root: {relative_path}")
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"Candidate path traverses a symlink: {relative_path}")
    candidate = current.resolve()
    if candidate == root or root not in candidate.parents:
        raise ValueError(f"Candidate escapes disposable root: {relative_path}")
    return candidate


def copy_project(source: Path, destination: Path, include_dependencies: bool) -> None:
    excluded = set(COPY_EXCLUDES)
    if not include_dependencies:
        excluded |= DEPENDENCY_EXCLUDES
    shutil.copytree(
        source,
        destination,
        ignore=shutil.ignore_patterns(*sorted(excluded)),
        symlinks=True,
    )


def parse_command_json(value: str) -> dict[str, Any]:
    try:
        payload = json.loads(value)
    except json.JSONDecodeError as error:
        raise argparse.ArgumentTypeError(f"Invalid --command-json: {error}") from error
    if isinstance(payload, list):
        payload = {"kind": "other", "argv": payload}
    if not isinstance(payload, dict) or not isinstance(payload.get("argv"), list):
        raise argparse.ArgumentTypeError("--command-json must be an argv array or an object with an argv array.")
    argv = payload["argv"]
    if not argv or not all(isinstance(part, str) and part for part in argv):
        raise argparse.ArgumentTypeError("Command argv must contain one or more non-empty strings.")
    kind = payload.get("kind", "other")
    if kind not in {"test", "build", "typecheck", "lint", "other"}:
        raise argparse.ArgumentTypeError("Command kind must be test, build, typecheck, lint, or other.")
    return {"kind": kind, "argv": argv, "shell": False}


def run_command(spec: dict[str, Any], cwd: Path, timeout: int) -> dict[str, Any]:
    started = time.monotonic()
    shell = bool(spec.get("shell"))
    raw_command: str | list[str] = spec["command"] if shell else spec["argv"]
    unmasked_display = str(raw_command) if shell else display_command(raw_command)
    command_display, command_redacted = mask_secrets(unmasked_display)
    recorded_argv = None
    if not shell:
        recorded_argv = []
        for part in raw_command:
            masked_part, part_redacted = mask_secrets(str(part))
            recorded_argv.append(masked_part)
            command_redacted = command_redacted or part_redacted
    try:
        with tempfile.TemporaryDirectory(prefix="code-rot-command-home-") as temporary_home:
            environment, policy = sanitized_environment(Path(temporary_home))
            completed = subprocess.run(
                raw_command,
                cwd=cwd,
                shell=shell,
                env=environment,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=timeout,
            )
        output, redacted = mask_secrets(completed.stdout or "")
        return {
            "kind": spec.get("kind", "other"),
            "command": command_display,
            "argv": recorded_argv,
            "execution_mode": "shell" if shell else "argv-no-shell",
            "environment_policy": policy,
            "exit_code": completed.returncode,
            "duration_seconds": round(time.monotonic() - started, 3),
            "timed_out": False,
            "output": output[-10_000:],
            "output_redacted": redacted or command_redacted,
            "passed": completed.returncode == 0,
        }
    except subprocess.TimeoutExpired as error:
        output = error.stdout.decode(errors="replace") if isinstance(error.stdout, bytes) else (error.stdout or "")
        output, redacted = mask_secrets(output)
        return {
            "kind": spec.get("kind", "other"),
            "command": command_display,
            "argv": recorded_argv,
            "execution_mode": "shell" if shell else "argv-no-shell",
            "environment_policy": policy,
            "exit_code": None,
            "duration_seconds": round(time.monotonic() - started, 3),
            "timed_out": True,
            "output": output[-10_000:],
            "output_redacted": redacted or command_redacted,
            "passed": False,
        }


def run_all(commands: list[dict[str, Any]], cwd: Path, timeout: int) -> list[dict[str, Any]]:
    results = []
    for command in commands:
        result = run_command(command, cwd, timeout)
        results.append(result)
        if not result["passed"]:
            break
    return results


def command_request_record(spec: dict[str, Any]) -> dict[str, str]:
    shell = bool(spec.get("shell"))
    display = str(spec["command"]) if shell else display_command(spec["argv"])
    masked, _ = mask_secrets(display)
    return {
        "kind": spec.get("kind", "other"),
        "command": masked,
        "execution_mode": "shell" if shell else "argv-no-shell",
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path)
    parser.add_argument("analysis", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--command-json", action="append", type=parse_command_json, default=[], help='Approved command as JSON, e.g. {"kind":"test","argv":["npm","test"]}')
    parser.add_argument("--shell-command", action="append", default=[], help="Approved command that requires a shell; avoid when argv form works")
    parser.add_argument("--confirm-run-project-code", action="store_true")
    parser.add_argument("--confirm-shell-execution", action="store_true")
    parser.add_argument("--candidate-id", action="append", required=False)
    parser.add_argument("--max-candidates", type=int, default=10)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--include-dependencies", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.confirm_run_project_code:
        raise SystemExit("Refusing to run project commands without --confirm-run-project-code after explicit user approval.")
    if args.shell_command and not args.confirm_shell_execution:
        raise SystemExit("Shell execution is higher risk; add --confirm-shell-execution only after separate explicit approval.")
    if not args.command_json and not args.shell_command:
        raise SystemExit("At least one --command-json or --shell-command is required.")
    if args.timeout < 1 or args.max_candidates < 1:
        raise SystemExit("--timeout and --max-candidates must be positive.")

    project = args.project.resolve(strict=True)
    analysis_path = args.analysis.resolve(strict=True)
    data = json.loads(analysis_path.read_text(encoding="utf-8"))
    if Path(data["project_root"]).resolve() != project:
        raise SystemExit("Analysis project_root does not match the requested project.")
    if data.get("schema_version") != "2.0":
        raise SystemExit("Proof requires analysis schema_version 2.0.")
    try:
        validate_symlink_tree(project)
    except ValueError as error:
        raise SystemExit(str(error)) from error

    candidate_ids = [item.get("candidate_id") for item in data.get("candidates", [])]
    if any(not isinstance(candidate_id, str) or not re.fullmatch(r"CRT-\d+", candidate_id) for candidate_id in candidate_ids):
        raise SystemExit("Analysis contains an invalid candidate_id.")
    if len(candidate_ids) != len(set(candidate_ids)):
        raise SystemExit("Analysis contains duplicate candidate_id values.")
    selected_ids = set(args.candidate_id or [])
    known_ids = set(candidate_ids)
    unknown_ids = selected_ids - known_ids
    if unknown_ids:
        raise SystemExit(f"Unknown candidate IDs: {', '.join(sorted(unknown_ids))}")
    candidates = [
        item for item in data.get("candidates", [])
        if item.get("proof_eligible")
        and (item.get("affected") or {}).get("kind") == "file"
        and (not selected_ids or item.get("candidate_id") in selected_ids)
    ][:args.max_candidates]
    commands = list(args.command_json)
    commands.extend({"kind": "other", "command": command, "shell": True} for command in args.shell_command)
    command_policy = {
        "approved_project_code": True,
        "shell_approved": bool(args.shell_command),
        "default_execution_mode": "argv-no-shell",
        "environment": "sanitized-allowlist-v1",
        "network_isolation": "not-enforced",
    }
    result: dict[str, Any] = {
        "schema_version": "2.0",
        "project_root": str(project),
        "analysis_file": str(analysis_path),
        "analysis_sha256": hashlib.sha256(analysis_path.read_bytes()).hexdigest(),
        "generated_at": utc_now(),
        "command_policy": command_policy,
        "commands_requested": [command_request_record(command) for command in commands],
        "copy_excluded_directories": sorted(COPY_EXCLUDES | (set() if args.include_dependencies else DEPENDENCY_EXCLUDES)),
        "baseline": {"passed": False, "commands": []},
        "results": [],
        "limitations": [
            "Commands ran in disposable copies, not the real project.",
            "A green command set proves only the behavior exercised by those commands.",
            "Network isolation is not enforced; the sanitized environment removes common credential variables but cannot prevent unauthenticated network access.",
            "Disposable copies do not isolate the host filesystem or process namespace; approved commands retain the current user's operating-system permissions.",
            "A timeout stops the direct command but is not guaranteed to terminate every descendant process on every platform.",
        ],
    }
    if args.shell_command:
        result["limitations"].append("At least one approved command used shell execution; shell parsing expands the attack surface.")

    with tempfile.TemporaryDirectory(prefix="code-rot-proof-") as temporary:
        temporary_root = Path(temporary)
        baseline_dir = temporary_root / "baseline"
        copy_project(project, baseline_dir, args.include_dependencies)
        baseline_commands = run_all(commands, baseline_dir, args.timeout)
        baseline_passed = bool(baseline_commands) and all(command["passed"] for command in baseline_commands)
        result["baseline"] = {"passed": baseline_passed, "commands": baseline_commands}
        if baseline_passed:
            for candidate_index, candidate in enumerate(candidates, start=1):
                started = time.monotonic()
                candidate_dir = temporary_root / f"candidate-{candidate_index:04d}"
                copy_project(project, candidate_dir, args.include_dependencies)
                relative_path = candidate["affected"]["path"]
                try:
                    target = safe_candidate_path(candidate_dir.resolve(), relative_path)
                except ValueError as error:
                    result["results"].append({
                        "candidate_id": candidate["candidate_id"], "path": relative_path,
                        "outcome": "INCONCLUSIVE", "reason": str(error), "commands": [],
                    })
                    continue
                if not target.is_file() or target.is_symlink():
                    result["results"].append({
                        "candidate_id": candidate["candidate_id"], "path": relative_path,
                        "outcome": "INCONCLUSIVE", "reason": "Candidate was absent, not a regular file, or a symlink in the disposable copy.", "commands": [],
                    })
                    continue
                target.unlink()
                command_results = run_all(commands, candidate_dir, args.timeout)
                passed = bool(command_results) and all(command["passed"] for command in command_results)
                result["results"].append({
                    "candidate_id": candidate["candidate_id"],
                    "path": relative_path,
                    "outcome": "PASSED_IN_DISPOSABLE_COPY" if passed else "FAILED_AFTER_REMOVAL",
                    "duration_seconds": round(time.monotonic() - started, 3),
                    "commands": command_results,
                })
        else:
            result["limitations"].append("The untouched baseline failed, so no candidate removal was evaluated.")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"Baseline: {'passed' if result['baseline']['passed'] else 'failed'}")
    print(f"Evaluated {len(result['results'])} candidate(s) in fresh disposable copies.")
    print(f"Proof: {args.output.resolve()}")
    return 0 if result["baseline"]["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
