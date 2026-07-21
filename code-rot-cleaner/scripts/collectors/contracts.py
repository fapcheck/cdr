"""Fail-closed helpers for versioned external-tool contracts."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable, Sequence


class UnsupportedOutputSchema(ValueError):
    """Raised when a successful tool run does not match its documented contract."""


def load_json(text: str, tool: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError as error:
        raise UnsupportedOutputSchema(f"{tool} returned malformed JSON: {error.msg}.") from error


def require_mapping(value: Any, description: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise UnsupportedOutputSchema(f"{description} must be an object.")
    return value


def require_list(value: Any, description: str) -> list[Any]:
    if not isinstance(value, list):
        raise UnsupportedOutputSchema(f"{description} must be an array.")
    return value


def require_string(value: Any, description: str) -> str:
    if not isinstance(value, str) or not value:
        raise UnsupportedOutputSchema(f"{description} must be a non-empty string.")
    return value


def require_positive_line(value: Any, description: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise UnsupportedOutputSchema(f"{description} must be a positive integer.")
    return value


def extract_version(stdout: str, tool: str) -> str:
    match = re.search(r"(?<!\w)v?(\d+(?:\.\d+)+(?:[-+][0-9A-Za-z.-]+)?)", stdout)
    if not match:
        raise UnsupportedOutputSchema(f"{tool} --version did not contain a semantic version.")
    return match.group(1)


def probe_version(
    runner: Callable[..., dict[str, Any]], executable: Path, root: Path, timeout: int, tool: str,
) -> tuple[str | None, dict[str, Any], str | None]:
    result = runner([str(executable), "--version"], root, timeout, accepted_exit_codes=(0,))
    if not result.get("completed", False):
        return None, result, f"{tool} --version failed."
    try:
        return extract_version(result.get("stdout") or "", tool), result, None
    except UnsupportedOutputSchema as error:
        return None, result, str(error)


def tool_run(
    *,
    name: str,
    executable: Path,
    version: str | None,
    result: dict[str, Any],
    argv: Sequence[str],
    limitation: str,
    status: str,
    contract_error: str | None = None,
) -> dict[str, Any]:
    limitations = [limitation]
    if contract_error:
        limitations.append(contract_error)
    return {
        "tool": name,
        "executable": str(executable),
        "version": version,
        "command": result.get("command", " ".join(str(part) for part in argv)),
        "execution_mode": result.get("execution_mode", "argv-no-shell"),
        "environment_policy": result.get("environment_policy", {"name": "sanitized-allowlist-v1"}),
        "exit_code": result.get("exit_code"),
        "timed_out": result.get("timed_out", False),
        "redacted": result.get("redacted", False),
        "stderr": result.get("stderr") or "",
        "status": status,
        "approval_granted": True,
        "limitations": limitations,
    }
