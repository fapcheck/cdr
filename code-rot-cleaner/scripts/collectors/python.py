"""Python import, package-entry, Vulture, Ruff, and deptry evidence."""

from __future__ import annotations

import ast
import json
import re
import tempfile
import tomllib
from pathlib import Path
from typing import Any

from security import run_approved_tool


def load_pyproject(root: Path) -> dict[str, Any]:
    path = root / "pyproject.toml"
    try:
        return tomllib.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def collect_imports(text: str) -> tuple[list[tuple[str, int, list[str]]], bool, set[str]]:
    imports: list[tuple[str, int, list[str]]] = []
    packages: set[str] = set()
    dynamic_nonliteral = False
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return imports, dynamic_nonliteral, packages
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append((alias.name, 0, []))
                packages.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            names = [alias.name for alias in node.names if alias.name != "*"]
            imports.append((module, node.level, names))
            if node.level == 0 and module:
                packages.add(module.split(".")[0])
        elif isinstance(node, ast.Call):
            function = node.func
            is_dynamic = (
                isinstance(function, ast.Name) and function.id == "__import__"
            ) or (
                isinstance(function, ast.Attribute)
                and isinstance(function.value, ast.Name)
                and function.value.id == "importlib"
                and function.attr == "import_module"
            )
            if is_dynamic:
                if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                    module = node.args[0].value
                    imports.append((module, 0, []))
                    packages.add(module.split(".")[0])
                else:
                    dynamic_nonliteral = True
    return imports, dynamic_nonliteral, packages


def _module_options(base: Path, module: str) -> list[Path]:
    module_path = Path(*module.split(".")) if module else Path()
    return [(base / module_path).with_suffix(".py"), base / module_path / "__init__.py"]


def resolve_import(source: Path, module: str, level: int, names: list[str], root: Path,
                   source_set: set[Path], source_roots: list[Path] | None = None) -> set[Path]:
    if level == 0:
        bases = source_roots or [root, root / "src"]
    else:
        base = source.parent
        for _ in range(max(0, level - 1)):
            base = base.parent
        bases = [base]
    resolved: set[Path] = set()
    for base in bases:
        for option in _module_options(base, module):
            candidate = option.resolve()
            if candidate in source_set and (candidate == root or root in candidate.parents):
                resolved.add(candidate)
        for name in names:
            child_module = f"{module}.{name}" if module else name
            for option in _module_options(base, child_module):
                candidate = option.resolve()
                if candidate in source_set and (candidate == root or root in candidate.parents):
                    resolved.add(candidate)
    return resolved


def _entry_points(pyproject: dict[str, Any]) -> dict[str, str]:
    project = pyproject.get("project") or {}
    entries: dict[str, str] = {}
    for section in ("scripts", "gui-scripts"):
        entries.update({str(key): str(value) for key, value in (project.get(section) or {}).items()})
    for values in (project.get("entry-points") or {}).values():
        if isinstance(values, dict):
            entries.update({str(key): str(value) for key, value in values.items()})
    poetry_scripts = (((pyproject.get("tool") or {}).get("poetry") or {}).get("scripts") or {})
    entries.update({str(key): str(value) for key, value in poetry_scripts.items()})
    return entries


def build_context(root: Path, source_set: set[Path], texts: dict[Path, str]) -> dict[str, Any]:
    pyproject = load_pyproject(root)
    entries = _entry_points(pyproject)
    source_roots = [root]
    configured_roots: set[str] = set()
    package_dir = (((pyproject.get("tool") or {}).get("setuptools") or {}).get("package-dir") or {})
    if isinstance(package_dir, dict):
        configured_roots.update(str(value) for value in package_dir.values() if isinstance(value, str))
    poetry_packages = (((pyproject.get("tool") or {}).get("poetry") or {}).get("packages") or [])
    for package in poetry_packages:
        if isinstance(package, dict) and isinstance(package.get("from"), str):
            configured_roots.add(package["from"])
    if (root / "src").is_dir():
        configured_roots.add("src")
    for configured in sorted(configured_roots):
        candidate = (root / configured).resolve()
        if candidate.is_dir() and candidate not in source_roots and (candidate == root or root in candidate.parents):
            source_roots.append(candidate)
    entry_files: set[Path] = set()
    for value in entries.values():
        module = value.split(":", 1)[0].strip()
        for source_root in source_roots:
            for option in _module_options(source_root, module):
                candidate = option.resolve()
                if candidate in source_set:
                    entry_files.add(candidate)
    imported_packages: set[str] = set()
    dynamic_sources: list[str] = []
    import_records: dict[Path, list[tuple[str, int, list[str]]]] = {}
    for path, text in texts.items():
        if path.suffix.lower() != ".py":
            continue
        imports, dynamic, packages = collect_imports(text)
        import_records[path] = imports
        imported_packages.update(packages)
        if dynamic:
            dynamic_sources.append(path.relative_to(root).as_posix())
    requirement_files = sorted(
        path.relative_to(root).as_posix()
        for path in root.glob("requirements*.txt")
        if path.is_file() and not path.is_symlink()
    )
    return {
        "pyproject": pyproject,
        "entry_files": entry_files,
        "entries": entries,
        "source_roots": source_roots,
        "imports": import_records,
        "imported_packages": imported_packages,
        "dynamic_sources": dynamic_sources,
        "ecosystem": {
            "detected": any(path.suffix.lower() == ".py" for path in source_set),
            "pyproject": (root / "pyproject.toml").is_file(),
            "requirements_files": requirement_files,
            "cli_entry_points": sorted(entries),
            "namespace_packages_supported": True,
            "source_roots": [path.relative_to(root).as_posix() or "." for path in source_roots],
        },
    }


def convention_role(path: Path, root: Path, text: str, context: dict[str, Any]) -> str | None:
    name = path.name
    parts = {part.lower() for part in path.relative_to(root).parts[:-1]}
    if path.resolve() in context["entry_files"]:
        return "cli-entry-point"
    if name in {"__init__.py", "__main__.py", "conftest.py", "manage.py", "wsgi.py", "asgi.py"}:
        return "python-entry-or-package"
    if name.startswith("test_") or name.endswith("_test.py") or "tests" in parts:
        return "test-discovery"
    if parts.intersection({"migrations", "management", "commands", "plugins", "workers", "jobs", "scripts"}):
        return "config-or-runtime-convention"
    if any(marker in text[:600].lower() for marker in ("generated", "auto-generated", "do not edit")):
        return "generated"
    return None


def collect_vulture(root: Path, executable: Path, timeout: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    argv = [str(executable), ".", "--min-confidence", "80"]
    result = run_approved_tool(argv, root, timeout)
    signals: list[dict[str, Any]] = []
    pattern = re.compile(r"^(.*?):(\d+):\s+unused\s+.+?\s+'([^']+)'\s+\((\d+)% confidence\)$")
    for line in (result.get("stdout") or "").splitlines():
        match = pattern.match(line.strip())
        if not match:
            continue
        signals.append({
            "category": "symbol-no-usage-evidence",
            "affected": {"kind": "symbol", "path": match.group(1).replace("\\", "/"), "symbol": match.group(3), "line": int(match.group(2))},
            "evidence": {"family": "vulture", "source": "vulture.symbol", "signal": "no-usage-evidence", "detail": f"Vulture reported {match.group(4)}% confidence."},
        })
    return signals, _tool_run("vulture", result, argv, "Vulture uses static analysis and can miss dynamic Python access.")


def collect_ruff(root: Path, executable: Path, timeout: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    argv = [str(executable), "check", ".", "--select", "F401,F811,F841", "--output-format", "json", "--no-cache", "--no-fix"]
    result = run_approved_tool(argv, root, timeout)
    try:
        payload = json.loads(result.get("stdout") or "[]")
    except json.JSONDecodeError:
        payload = []
    signals = []
    for issue in payload if isinstance(payload, list) else []:
        location = issue.get("location") or {}
        path = str(issue.get("filename") or "").replace("\\", "/")
        try:
            path = Path(path).resolve().relative_to(root).as_posix()
        except (OSError, ValueError):
            pass
        signals.append({
            "category": "symbol-no-usage-evidence",
            "affected": {"kind": "symbol", "path": path, "symbol": issue.get("code", "ruff-finding"), "line": location.get("row")},
            "evidence": {
                "family": "ruff",
                "source": f"ruff.{issue.get('code', 'finding')}",
                "signal": "no-usage-evidence",
                "detail": re.sub(
                    r"\bunused\b",
                    "with no usage evidence",
                    str(issue.get("message") or "Ruff reported a related static finding."),
                    flags=re.IGNORECASE,
                ),
            },
        })
    return signals, _tool_run("ruff", result, argv, "Ruff findings are local lint evidence, not proof that an enclosing file is removable.")


def collect_deptry(root: Path, executable: Path, timeout: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    with tempfile.TemporaryDirectory(prefix="code-rot-deptry-") as temp:
        json_path = Path(temp) / "deptry.json"
        argv = [str(executable), ".", "--json-output", str(json_path), "--no-ansi"]
        result = run_approved_tool(argv, root, timeout)
        try:
            payload = json.loads(json_path.read_text(encoding="utf-8")) if json_path.is_file() else []
        except (OSError, json.JSONDecodeError):
            payload = []
    signals = []
    for issue in payload if isinstance(payload, list) else []:
        error = issue.get("error") or {}
        if error.get("code") != "DEP002":
            continue
        location = issue.get("location") or {}
        signals.append({
            "category": "dependency-no-usage-evidence",
            "affected": {"kind": "dependency", "path": str(location.get("file") or "pyproject.toml").replace("\\", "/"), "dependency": str(issue.get("module") or "<unknown>")},
            "evidence": {"family": "deptry", "source": "deptry.DEP002", "signal": "no-usage-evidence", "detail": str(error.get("message") or "deptry reported an obsolete dependency signal.")},
        })
    return signals, _tool_run("deptry", result, argv, "deptry dependency reachability can be incomplete for plugins and dynamic imports.")


def _tool_run(name: str, result: dict[str, Any], argv: list[str], limitation: str) -> dict[str, Any]:
    return {
        "tool": name,
        "command": result.get("command", " ".join(argv)),
        "execution_mode": result.get("execution_mode", "argv-no-shell"),
        "environment_policy": result.get("environment_policy", {"name": "sanitized-allowlist-v1"}),
        "exit_code": result.get("exit_code"),
        "timed_out": result.get("timed_out", False),
        "redacted": result.get("redacted", False),
        "status": "completed" if result.get("completed", True) else "failed",
        "limitations": [limitation],
    }
