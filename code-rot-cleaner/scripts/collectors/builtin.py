"""Dependency-free fallback collector. Its findings are leads, never proof."""

from __future__ import annotations

import hashlib
import os
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from collectors import dependencies, python as python_collector, typescript


SOURCE_EXTENSIONS = typescript.JS_EXTENSIONS | {".py"}
TEXT_EXTENSIONS = SOURCE_EXTENSIONS | {
    ".json", ".jsonc", ".yaml", ".yml", ".toml", ".md", ".html", ".css",
    ".scss", ".ini", ".cfg", ".txt",
}
EXCLUDED_DIRECTORIES = {
    ".git", ".hg", ".svn", ".next", ".nuxt", ".svelte-kit", ".venv", "venv",
    "node_modules", "vendor", "dist", "build", "coverage", "target", "out",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "outputs",
}


def safe_text(path: Path, limit: int = 2_000_000) -> str:
    try:
        if path.is_symlink() or path.stat().st_size > limit:
            return ""
        return path.read_text(encoding="utf-8", errors="ignore")
    except (OSError, UnicodeError):
        return ""


def inventory(root: Path) -> tuple[list[Path], list[Path], list[str]]:
    source_files: list[Path] = []
    text_files: list[Path] = []
    skipped_symlinks: list[str] = []
    for current, directories, files in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        kept_directories = []
        for directory in directories:
            path = current_path / directory
            if directory in EXCLUDED_DIRECTORIES or directory.startswith(".git"):
                continue
            if path.is_symlink():
                skipped_symlinks.append(path.relative_to(root).as_posix())
                continue
            kept_directories.append(directory)
        directories[:] = kept_directories
        for name in files:
            path = current_path / name
            if path.is_symlink():
                skipped_symlinks.append(path.relative_to(root).as_posix())
                continue
            extension = path.suffix.lower()
            if extension in TEXT_EXTENSIONS:
                text_files.append(path)
            if extension in SOURCE_EXTENSIONS:
                source_files.append(path)
    return sorted(source_files), sorted(text_files), sorted(skipped_symlinks)


def line_count(text: str) -> int:
    return len(text.splitlines()) if text else 0


def evidence(source: str, signal: str, detail: str) -> dict[str, str]:
    return {"family": "builtin", "source": source, "signal": signal, "detail": detail}


def file_candidate(
    category: str,
    path: Path,
    root: Path,
    text: str,
    *,
    confidence: str,
    risk: str,
    proof_eligible: bool,
    sources: list[dict[str, str]],
    questions: list[str],
    why_suspicious: str,
    why_needed: str,
    safety: dict[str, str],
    line: int | None = None,
    symbol: str | None = None,
    size_text: str | None = None,
) -> dict[str, Any]:
    relative = path.relative_to(root).as_posix()
    affected: dict[str, Any] = {"kind": "symbol" if symbol else "file", "path": relative}
    if symbol:
        affected["symbol"] = symbol
    if line is not None:
        affected["line"] = line
    measured = text if size_text is None else size_text
    return {
        "category": category,
        "affected": affected,
        "evidence_sources": sources,
        "confidence": confidence,
        "risk": risk,
        "unresolved_questions": questions,
        "proof_status": "NOT_RUN",
        "recommendation": "REVIEW",
        "proof_eligible": proof_eligible,
        "safety": safety,
        "why_suspicious": why_suspicious,
        "why_might_still_be_needed": why_needed,
        "loc": line_count(measured),
        "bytes": len(measured.encode("utf-8")),
    }


def collect(root: Path) -> dict[str, Any]:
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError(f"Project root is not a directory: {root}")
    source_files, text_files, skipped_symlinks = inventory(root)
    source_set = {path.resolve() for path in source_files}
    texts = {path: safe_text(path) for path in text_files}
    ts_context = typescript.build_context(root, source_set)
    py_context = python_collector.build_context(root, source_set, texts)
    inbound: dict[Path, set[Path]] = defaultdict(set)
    imported_js_packages: set[str] = set()
    js_dynamic_sources: list[str] = []

    for source in source_files:
        text = texts.get(source, "")
        if source.suffix.lower() in typescript.JS_EXTENSIONS:
            for specifier in typescript.js_specifiers(text):
                target = typescript.resolve_specifier(source, specifier, ts_context)
                if target:
                    inbound[target].add(source)
                elif not specifier.startswith((".", "/", "#")):
                    package_name = "/".join(specifier.split("/")[:2]) if specifier.startswith("@") else specifier.split("/")[0]
                    imported_js_packages.add(package_name)
            if typescript.has_nonliteral_dynamic_import(text):
                js_dynamic_sources.append(source.relative_to(root).as_posix())
        elif source.suffix.lower() == ".py":
            for module, level, names in py_context["imports"].get(source, []):
                for target in python_collector.resolve_import(
                    source, module, level, names, root, source_set, py_context["source_roots"],
                ):
                    inbound[target].add(source)

    candidates: list[dict[str, Any]] = []
    text_items = list(texts.items())
    for path in source_files:
        relative = path.relative_to(root).as_posix()
        text = texts.get(path, "")
        if path.suffix.lower() in typescript.JS_EXTENSIONS:
            role = typescript.convention_role(path, root, text, ts_context)
            dynamic_sources = js_dynamic_sources
        else:
            role = python_collector.convention_role(path, root, text, py_context)
            dynamic_sources = py_context["dynamic_sources"]
        if role or inbound.get(path.resolve()):
            continue

        outside_text = "\n".join(value for other, value in text_items if other != path)
        normalized = relative.rsplit(".", 1)[0]
        strong_tokens = {relative, normalized, f"./{normalized}"}
        if any(token in outside_text for token in strong_tokens):
            continue
        weak_tokens = {path.name, path.stem}
        weak_mentions = sorted(token for token in weak_tokens if len(token) >= 4 and token in outside_text)
        dynamic_uncertainty = bool(dynamic_sources)
        risk = "medium" if weak_mentions or dynamic_uncertainty else "low"
        confidence = "medium" if risk == "medium" else "high"
        questions = []
        if weak_mentions:
            questions.append(f"Do name references ({', '.join(weak_mentions)}) resolve to this file at runtime?")
        if dynamic_uncertainty:
            questions.append(f"Could a non-literal loader in {', '.join(dynamic_sources[:3])} resolve this file?")
        sources = [
            evidence("builtin.import-graph", "no-inbound-reference", "No resolved static inbound import or re-export was found."),
            evidence("builtin.entrypoint-map", "no-convention-role", "No package, CLI, route, worker, migration, plugin, generated-file, or framework entry role was detected."),
            evidence("builtin.repository-search", "no-path-reference", "No repository-text reference to the file path was found."),
        ]
        candidates.append(file_candidate(
            "orphan-file", path, root, text,
            confidence=confidence,
            risk=risk,
            proof_eligible=confidence == "high" and risk == "low",
            sources=sources,
            questions=questions,
            why_suspicious="No usage evidence found in the built-in import graph, entry-point map, or repository path search.",
            why_needed="Dynamic loading, external consumers, rare operational paths, and incomplete framework detection can hide reachability.",
            safety={
                "dynamic_usage": "unknown" if dynamic_uncertainty else "none-found",
                "external_api": "unknown" if _is_public_package(path, ts_context, py_context) else "none-known",
                "convention_role": "none-found",
            },
        ))

    duplicate_groups: dict[str, list[Path]] = defaultdict(list)
    for path in source_files:
        normalized = "\n".join(line.strip() for line in texts.get(path, "").splitlines() if line.strip())
        if line_count(normalized) >= 4 and len(normalized) >= 80:
            duplicate_groups[hashlib.sha256(normalized.encode()).hexdigest()].append(path)
    for paths in duplicate_groups.values():
        if len(paths) < 2:
            continue
        canonical = sorted(paths)[0]
        for path in sorted(paths)[1:]:
            candidates.append(file_candidate(
                "duplicate-file", path, root, texts.get(path, ""),
                confidence="high", risk="medium", proof_eligible=False,
                sources=[evidence("builtin.content-hash", "exact-duplicate", f"Normalized content matches {canonical.relative_to(root).as_posix()} exactly.")],
                questions=["Do callers rely on separate module boundaries or side-effect order?"],
                why_suspicious="The normalized implementation is byte-for-byte equivalent to another file.",
                why_needed="Duplication is not reachability proof and either module can have distinct callers.",
                safety={"dynamic_usage": "unknown", "external_api": "unknown", "convention_role": "none-found"},
            ))

    export_pattern = re.compile(r"\bexport\s+(?:default\s+)?(?:async\s+)?(?:function|class|const|let|var|type|interface|enum)\s+([A-Za-z_$][\w$]*)")
    all_source_text = "\n".join(texts.get(path, "") for path in source_files)
    orphan_paths = {
        item["affected"]["path"]
        for item in candidates
        if item["category"] == "orphan-file"
    }
    for path in source_files:
        if path.suffix.lower() not in typescript.JS_EXTENSIONS:
            continue
        relative = path.relative_to(root).as_posix()
        text = texts.get(path, "")
        if relative in orphan_paths or typescript.convention_role(path, root, text, ts_context):
            continue
        outside_repository_text = "\n".join(value for other, value in text_items if other != path)
        normalized_path = relative.rsplit(".", 1)[0]
        if any(token in outside_repository_text for token in (relative, normalized_path, f"./{normalized_path}")):
            continue
        outside = all_source_text.replace(text, "", 1)
        for match in export_pattern.finditer(text):
            symbol = match.group(1)
            if len(symbol) < 4 or re.search(rf"\b{re.escape(symbol)}\b", outside):
                continue
            candidates.append(file_candidate(
                "export-no-usage-evidence", path, root, text,
                confidence="medium", risk="medium", proof_eligible=False,
                sources=[evidence("builtin.symbol-search", "no-symbol-reference", "No reference to this exported symbol was found outside its defining file.")],
                questions=["Is this symbol part of a public API, re-export, declaration surface, template, or external consumer contract?"],
                why_suspicious="No usage evidence found for the exported symbol in repository source.",
                why_needed="In-repository symbol search cannot see downstream consumers or every generated and reflective access path.",
                safety={"dynamic_usage": "unknown", "external_api": "unknown", "convention_role": "export"},
                line=text[:match.start()].count("\n") + 1,
                symbol=symbol,
                size_text="",
            ))

    comment_pattern = re.compile(r"(?m)(?:^\s*//.*(?:[;{}()]|\b(?:if|for|return|const|let|var|function)\b).*$\n?){4,}")
    for path in source_files:
        if path.suffix.lower() not in typescript.JS_EXTENSIONS:
            continue
        text = texts.get(path, "")
        for match in comment_pattern.finditer(text):
            block = match.group(0)
            candidates.append(file_candidate(
                "commented-code", path, root, text,
                confidence="low", risk="medium", proof_eligible=False,
                sources=[evidence("builtin.comment-pattern", "code-like-comment-block", "A block of four or more code-like line comments was found.")],
                questions=["Is this block documentation, a protocol example, a compatibility note, or an operational procedure?"],
                why_suspicious="The comment block resembles disabled implementation code.",
                why_needed="Code-like comments can be intentional documentation or operational guidance.",
                safety={"dynamic_usage": "not-applicable", "external_api": "unknown", "convention_role": "comment"},
                line=text[:match.start()].count("\n") + 1,
                size_text=block,
            ))

    manifest_paths = {package["path"] for package in ts_context["packages"]}
    repository_without_manifests = "\n".join(text for path, text in text_items if path not in manifest_paths)
    frameworks = {name for name, enabled in (("next", ts_context["ecosystem"]["nextjs"]), ("vite", ts_context["ecosystem"]["vite"])) if enabled}
    candidates.extend(dependencies.collect_javascript(
        root, ts_context["packages"], repository_without_manifests, imported_js_packages, frameworks,
    ))
    python_manifest_paths = {root / "pyproject.toml", *root.glob("requirements*.txt")}
    python_repository_text = "\n".join(text for path, text in text_items if path not in python_manifest_paths)
    candidates.extend(dependencies.collect_python(
        root, py_context["pyproject"], py_context["imported_packages"], python_repository_text,
    ))

    return {
        "root": root,
        "source_files": source_files,
        "texts": texts,
        "skipped_symlinks": skipped_symlinks,
        "candidates": candidates,
        "ecosystems": {
            "typescript": ts_context["ecosystem"],
            "python": py_context["ecosystem"],
        },
        "limitations": [
            "The built-in collector is a conservative fallback and cannot independently justify SAFE TO REMOVE.",
            "Static analysis cannot prove absence of dynamic, reflective, operational, platform-specific, or external use.",
            "No project command, package manager, or project-controlled analyzer was executed by the built-in collector.",
        ] + ([f"Skipped {len(skipped_symlinks)} symbolic link(s) during report-only inspection."] if skipped_symlinks else []),
    }


def _is_public_package(path: Path, ts_context: dict[str, Any], py_context: dict[str, Any]) -> bool:
    for package in ts_context["packages"]:
        manifest = package["data"]
        if not manifest.get("private", False) and package["path"].parent in path.parents:
            return True
    project = py_context["pyproject"].get("project") or {}
    return bool(project.get("name") and "app" not in path.parts)
