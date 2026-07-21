"""Conservative manifest and requirements evidence."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


def _candidate(path: str, name: str, ecosystem: str) -> dict[str, Any]:
    return {
        "category": "dependency-no-usage-evidence",
        "affected": {"kind": "dependency", "path": path, "dependency": name},
        "evidence_sources": [{
            "family": "builtin",
            "source": f"builtin.{ecosystem}-manifest-search",
            "signal": "no-usage-evidence",
            "detail": "No static import, script, or repository-text reference was found outside dependency declarations.",
        }],
        "confidence": "medium",
        "risk": "medium",
        "unresolved_questions": ["Could a CLI, plugin, loader, preset, peer relationship, or deployment step require this dependency?"],
        "proof_status": "NOT_RUN",
        "recommendation": "REVIEW",
        "proof_eligible": False,
        "safety": {"dynamic_usage": "unknown", "external_api": "none-known", "convention_role": "manifest-entry"},
        "why_suspicious": "No usage evidence found in the inspected static surfaces.",
        "why_might_still_be_needed": "Dependency use can be configuration-driven or operational and manifest edits affect lockfiles.",
        "loc": 0,
        "bytes": 0,
    }


def collect_javascript(root: Path, packages: list[dict[str, Any]], repository_text_without_manifests: str,
                       imported_packages: set[str], framework_packages: set[str]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for package in packages:
        manifest_path: Path = package["path"]
        manifest = package["data"]
        scripts = json.dumps(manifest.get("scripts") or {})
        searchable = scripts + "\n" + repository_text_without_manifests
        for section in ("dependencies", "devDependencies", "optionalDependencies"):
            for name in sorted((manifest.get(section) or {})):
                pattern = re.compile(rf"(?<![\w@/-]){re.escape(name)}(?![\w@-])")
                if name in imported_packages or name in framework_packages or pattern.search(searchable):
                    continue
                candidates.append(_candidate(manifest_path.relative_to(root).as_posix(), name, "javascript"))
    return candidates


def collect_python(root: Path, pyproject: dict[str, Any], imported_packages: set[str], repository_text: str) -> list[dict[str, Any]]:
    project = pyproject.get("project") or {}
    dependencies = project.get("dependencies") or []
    candidates: list[dict[str, Any]] = []
    for declaration in dependencies:
        if not isinstance(declaration, str):
            continue
        name = re.split(r"[<>=!~;\[\s]", declaration, maxsplit=1)[0].strip()
        module = name.lower().replace("-", "_")
        if not name or module in {item.lower() for item in imported_packages}:
            continue
        if re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", repository_text, flags=re.IGNORECASE):
            continue
        candidates.append(_candidate("pyproject.toml", name, "python"))
    for requirements_path in sorted(root.glob("requirements*.txt")):
        if not requirements_path.is_file() or requirements_path.is_symlink():
            continue
        try:
            lines = requirements_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            declaration = line.split("#", 1)[0].strip()
            if not declaration or declaration.startswith(("-", "http://", "https://", "git+")):
                continue
            name = re.split(r"[<>=!~;\[\s]", declaration, maxsplit=1)[0].strip()
            module = name.lower().replace("-", "_")
            if not name or module in {item.lower() for item in imported_packages}:
                continue
            if re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", repository_text, flags=re.IGNORECASE):
                continue
            candidates.append(_candidate(requirements_path.relative_to(root).as_posix(), name, "python"))
    return candidates
