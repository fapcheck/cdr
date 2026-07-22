#!/usr/bin/env python3
"""Shared process, environment, redaction, and path safety helpers."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


SAFE_ENVIRONMENT_KEYS = {
    "COMSPEC", "LANG", "LC_ALL", "PATH", "PATHEXT", "SYSTEMDRIVE",
    "SYSTEMROOT", "TEMP", "TMP", "WINDIR",
}
SECRET_KEY = re.compile(
    r"(?:token|secret|pass(?:word|wd)?|credential|auth|cookie|session|private[_-]?key|api[_-]?key|database[_-]?url|connection[_-]?string)",
    re.IGNORECASE,
)
SECRET_PATTERNS = (
    re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9_]{8,}|sk-[A-Za-z0-9_-]{12,})\b"),
    re.compile(r"\b(?:npm_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16})\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"(?i)(Authorization\s*:\s*Bearer\s+)[A-Za-z0-9._~+/-]{8,}"),
    re.compile(r"(?i)\b(Bearer\s+)[A-Za-z0-9._~+/-]{8,}"),
    re.compile(r'''(?i)(["']?(?:password|passwd|pwd|token|secret|api[_-]?key|access[_-]?key|client[_-]?secret)["']?\s*(?:=|:)\s*["']?)[^\s,;"']+'''),
    re.compile(r"(?i)((?:--password|--token|--secret|--api[_-]?key|--access[_-]?key)\s+)[^\s]+"),
    re.compile(r"(?i)(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis)://[^\s/@:]+:[^\s/@]+@"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
)


def sanitized_environment(work_home: Path, source: Mapping[str, str] | None = None) -> tuple[dict[str, str], dict[str, Any]]:
    source = source if source is not None else os.environ
    work_home = work_home.resolve()
    work_home.mkdir(parents=True, exist_ok=True)
    environment = {
        key: value
        for key, value in source.items()
        if key.upper() in SAFE_ENVIRONMENT_KEYS and not SECRET_KEY.search(key)
    }
    inherited_variables = sorted(environment)
    environment.update({
        "CI": "1",
        "CODE_ROT_CLEANER_DISPOSABLE_COPY": "1",
        "HOME": str(work_home),
        "NO_COLOR": "1",
        "NPM_CONFIG_CACHE": str(work_home / "npm-cache"),
        "PIP_CACHE_DIR": str(work_home / "pip-cache"),
        "USERPROFILE": str(work_home),
        "XDG_CACHE_HOME": str(work_home / "cache"),
    })
    policy = {
        "name": "sanitized-allowlist-v1",
        "inherited_variables": inherited_variables,
        "inherited_secret_variables": 0,
        "synthetic_home": str(work_home),
        "network_isolation": "not-enforced",
    }
    return environment, policy


def mask_secrets(text: str, source: Mapping[str, str] | None = None) -> tuple[str, bool]:
    masked = text
    redacted = False
    source = source if source is not None else os.environ
    for key, value in source.items():
        if SECRET_KEY.search(key) and value and len(value) >= 6 and value in masked:
            masked = masked.replace(value, "[REDACTED]")
            redacted = True
    for pattern in SECRET_PATTERNS:
        replacement = r"\1[REDACTED]" if pattern.groups else "[REDACTED]"
        updated, count = pattern.subn(replacement, masked)
        if count:
            masked = updated
            redacted = True
    return masked, redacted


def display_command(argv: Sequence[str]) -> str:
    return subprocess.list2cmdline([str(part) for part in argv])


def run_approved_tool(
    argv: Sequence[str],
    cwd: Path,
    timeout: int,
    *,
    accepted_exit_codes: Iterable[int] = (0, 1),
) -> dict[str, Any]:
    started = time.monotonic()
    try:
        with tempfile.TemporaryDirectory(prefix="code-rot-tool-home-") as temporary_home:
            environment, policy = sanitized_environment(Path(temporary_home))
            completed = subprocess.run(
                [str(part) for part in argv],
                cwd=cwd,
                shell=False,
                env=environment,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout,
            )
        stdout, stdout_redacted = mask_secrets(completed.stdout or "")
        stderr, stderr_redacted = mask_secrets(completed.stderr or "")
        return {
            "argv": [str(part) for part in argv],
            "command": display_command(argv),
            "execution_mode": "argv-no-shell",
            "environment_policy": policy,
            "exit_code": completed.returncode,
            "duration_seconds": round(time.monotonic() - started, 3),
            "timed_out": False,
            "stdout": stdout[-200_000:],
            "stderr": stderr[-20_000:],
            "redacted": stdout_redacted or stderr_redacted,
            "completed": completed.returncode in set(accepted_exit_codes),
        }
    except subprocess.TimeoutExpired as error:
        policy = {"name": "sanitized-allowlist-v1", "network_isolation": "not-enforced"}
        stdout = error.stdout.decode(errors="replace") if isinstance(error.stdout, bytes) else (error.stdout or "")
        stderr = error.stderr.decode(errors="replace") if isinstance(error.stderr, bytes) else (error.stderr or "")
        stdout, stdout_redacted = mask_secrets(stdout)
        stderr, stderr_redacted = mask_secrets(stderr)
        return {
            "argv": [str(part) for part in argv],
            "command": display_command(argv),
            "execution_mode": "argv-no-shell",
            "environment_policy": policy,
            "exit_code": None,
            "duration_seconds": round(time.monotonic() - started, 3),
            "timed_out": True,
            "stdout": stdout[-200_000:],
            "stderr": stderr[-20_000:],
            "redacted": stdout_redacted or stderr_redacted,
            "completed": False,
        }


def discover_executable(root: Path, name: str) -> Path | None:
    suffixes = (".cmd", ".exe", "") if os.name == "nt" else ("",)
    local_bases = [root / "node_modules" / ".bin" / name]
    for environment_dir in (root / ".venv", root / "venv"):
        local_bases.extend((environment_dir / "Scripts" / name, environment_dir / "bin" / name))
    for base in local_bases:
        for suffix in suffixes:
            candidate = Path(f"{base}{suffix}")
            if candidate.is_file() and not candidate.is_symlink():
                return candidate.resolve()
    found = shutil.which(name)
    return Path(found).resolve() if found else None
