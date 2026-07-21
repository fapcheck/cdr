"""Optional supporting Git evidence. Age is never treated as removal proof."""

from __future__ import annotations

import subprocess
import tempfile
import re
from pathlib import Path
from typing import Any

from security import sanitized_environment


def _git(root: Path, *args: str, timeout: int = 20) -> subprocess.CompletedProcess[str]:
    with tempfile.TemporaryDirectory(prefix="code-rot-git-home-") as temporary_home:
        environment, _ = sanitized_environment(Path(temporary_home))
        return subprocess.run(
            ["git", "-C", str(root), *args],
            shell=False,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )


def collect(root: Path, candidates: list[dict[str, Any]]) -> dict[str, Any]:
    probe = _git(root, "rev-parse", "--show-toplevel")
    if probe.returncode != 0:
        return {"status": "unavailable", "reason": "Project is not a Git worktree.", "files": 0}
    touched = 0
    for candidate in candidates:
        affected = candidate.get("affected") or {}
        path = affected.get("path")
        if affected.get("kind") != "file" or not path:
            continue
        last = _git(root, "log", "-1", "--format=%aI", "--", path)
        first = _git(root, "log", "--follow", "--reverse", "--format=%aI", "--", path)
        count = _git(root, "rev-list", "--count", "HEAD", "--", path)
        blame = _git(root, "blame", "--line-porcelain", "--", path)
        if last.returncode != 0 or not last.stdout.strip():
            continue
        first_dates = [line for line in first.stdout.splitlines() if line.strip()]
        blame_commits = {
            match.group(1)
            for line in blame.stdout.splitlines()
            if (match := re.match(r"^([0-9a-f^]{40})\s+\d+\s+\d+(?:\s+\d+)?$", line))
        }
        detail = (
            f"Git history: first recorded {first_dates[0] if first_dates else 'unknown'}, "
            f"last modified {last.stdout.strip() or 'unknown'}, {count.stdout.strip() or '0'} commits, "
            f"{len(blame_commits)} blame commits."
        )
        candidate["evidence_sources"].append({
            "family": "git",
            "source": "git.history",
            "signal": "supporting-history-only",
            "detail": detail,
        })
        touched += 1
    return {
        "status": "completed",
        "files": touched,
        "limitations": ["Old or infrequently changed code is not evidence that code is removable."],
    }
