"""Python import, package-entry, Vulture, Ruff, and deptry evidence."""

from __future__ import annotations

import ast
import json
import os
import re
import sys
import tempfile
import tomllib
from pathlib import Path
from typing import Any

from collectors.contracts import (
    UnsupportedOutputSchema,
    load_json,
    probe_version,
    require_list,
    require_mapping,
    require_positive_line,
    require_string,
    tool_run,
)
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


def _normal_path(filename: str, root: Path) -> str:
    path = Path(filename)
    try:
        resolved = path.resolve() if path.is_absolute() else (root / path).resolve()
        return resolved.relative_to(root).as_posix()
    except (OSError, ValueError):
        return filename.replace("\\", "/")


def _vulture_runtime(executable: Path) -> Path:
    names = ("python.exe", "python3.exe") if os.name == "nt" else ("python", "python3")
    for name in names:
        candidate = executable.parent / name
        if candidate.is_file():
            return candidate.resolve()
    if executable.is_file() and executable.suffix.lower() not in {".exe", ".cmd", ".bat"}:
        try:
            first_line = executable.read_text(encoding="utf-8", errors="replace").splitlines()[0]
        except (OSError, IndexError):
            first_line = ""
        if first_line.startswith("#!"):
            interpreter = Path(first_line[2:].strip().split()[0])
            if interpreter.is_file():
                return interpreter.resolve()
    return Path(sys.executable).resolve()


def _python_sources(root: Path) -> list[str]:
    excluded = {
        ".git", ".hg", ".svn", ".venv", "venv", "node_modules", "vendor",
        "dist", "build", "coverage", "__pycache__", ".tox", ".nox",
    }
    return sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*.py")
        if path.is_file() and not path.is_symlink() and not excluded.intersection(path.relative_to(root).parts)
    )


def parse_vulture_output(text: str, root: Path) -> tuple[list[dict[str, Any]], str]:
    payload = require_mapping(load_json(text, "Vulture API adapter"), "Vulture API output")
    if payload.get("schema_version") != "vulture-api-v1":
        raise UnsupportedOutputSchema("Vulture API output has an unsupported schema_version.")
    version = require_string(payload.get("tool_version"), "Vulture API tool_version")
    items = require_list(payload.get("items"), "Vulture API items")
    signals: list[dict[str, Any]] = []
    for index, raw_item in enumerate(items):
        item = require_mapping(raw_item, f"Vulture items[{index}]")
        name = require_string(item.get("name"), f"Vulture items[{index}].name")
        item_type = require_string(item.get("type"), f"Vulture items[{index}].type")
        filename = require_string(item.get("filename"), f"Vulture items[{index}].filename")
        line = require_positive_line(item.get("first_lineno"), f"Vulture items[{index}].first_lineno")
        confidence = item.get("confidence")
        if not isinstance(confidence, int) or isinstance(confidence, bool) or not 0 <= confidence <= 100:
            raise UnsupportedOutputSchema(f"Vulture items[{index}].confidence must be an integer from 0 to 100.")
        symbol = f"unreachable-code@{line}" if item_type == "unreachable_code" else name
        signals.append({
            "category": "symbol-no-usage-evidence",
            "affected": {"kind": "symbol", "path": _normal_path(filename, root), "symbol": symbol, "line": line},
            "evidence": {
                "family": "vulture",
                "source": f"vulture.{item_type}",
                "signal": "no-usage-evidence",
                "detail": f"Vulture API reported {item_type} at {confidence}% confidence.",
            },
            "unresolved_questions": [
                "Could implicit calls, decorators, framework hooks, public APIs, reflection, or same-name cross-module use make this symbol reachable?",
            ],
        })
    return signals, version


def collect_vulture(root: Path, executable: Path, timeout: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    version, version_result, version_error = probe_version(run_approved_tool, executable, root, timeout, "Vulture")
    if version_error:
        return [], tool_run(
            name="vulture", executable=executable, version=None, result=version_result,
            argv=[str(executable), "--version"], limitation="Vulture is supporting, scope-insensitive static evidence and can miss dynamic or implicit Python use.",
            status="failed", contract_error=version_error,
        )
    if int(version.split(".", 1)[0]) < 2:
        return [], tool_run(
            name="vulture", executable=executable, version=version, result=version_result,
            argv=[str(executable), "--version"], limitation="Vulture is supporting, scope-insensitive static evidence and can miss dynamic or implicit Python use.",
            status="unsupported output schema", contract_error="This API adapter supports Vulture 2.x and newer compatible API output.",
        )
    adapter = Path(__file__).resolve().parents[1] / "vulture_adapter.py"
    with tempfile.TemporaryDirectory(prefix="code-rot-vulture-") as temp:
        manifest = Path(temp) / "python-sources.json"
        manifest.write_text(json.dumps(_python_sources(root)), encoding="utf-8")
        argv = [str(_vulture_runtime(executable)), str(adapter), str(root), str(manifest), "--min-confidence", "60"]
        result = run_approved_tool(argv, root, timeout, accepted_exit_codes=(0,))
    if not result.get("completed", False) or (result.get("stderr") or "").strip():
        return [], tool_run(
            name="vulture", executable=executable, version=version, result=result, argv=argv,
            limitation="Vulture is supporting, scope-insensitive static evidence and can miss dynamic or implicit Python use.", status="failed",
        )
    try:
        signals, api_version = parse_vulture_output(result.get("stdout") or "", root)
    except UnsupportedOutputSchema as error:
        return [], tool_run(
            name="vulture", executable=executable, version=version, result=result, argv=argv,
            limitation="Vulture is supporting, scope-insensitive static evidence and can miss dynamic or implicit Python use.",
            status="unsupported output schema", contract_error=str(error),
        )
    return signals, tool_run(
        name="vulture", executable=executable, version=api_version, result=result, argv=argv,
        limitation="Vulture is supporting, scope-insensitive static evidence and can miss dynamic or implicit Python use.", status="available and succeeded",
    )


def collect_ruff(root: Path, executable: Path, timeout: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    version, version_result, version_error = probe_version(run_approved_tool, executable, root, timeout, "Ruff")
    limitation = "Ruff findings are local lint evidence, not proof that an enclosing file or symbol is removable."
    if version_error:
        return [], tool_run(name="ruff", executable=executable, version=None, result=version_result, argv=[str(executable), "--version"], limitation=limitation, status="failed", contract_error=version_error)
    argv = [str(executable), "check", ".", "--select", "F401,F811,F841", "--output-format", "json", "--no-cache", "--no-fix"]
    result = run_approved_tool(argv, root, timeout, accepted_exit_codes=(0, 1))
    if not result.get("completed", False):
        return [], tool_run(name="ruff", executable=executable, version=version, result=result, argv=argv, limitation=limitation, status="failed")
    try:
        payload = require_list(load_json(result.get("stdout") or "", "Ruff"), "Ruff output")
    except UnsupportedOutputSchema as error:
        return [], tool_run(name="ruff", executable=executable, version=version, result=result, argv=argv, limitation=limitation, status="unsupported output schema", contract_error=str(error))
    signals = []
    try:
        for index, raw_issue in enumerate(payload):
            issue = require_mapping(raw_issue, f"Ruff output[{index}]")
            code = require_string(issue.get("code"), f"Ruff output[{index}].code")
            filename = require_string(issue.get("filename"), f"Ruff output[{index}].filename")
            message = require_string(issue.get("message"), f"Ruff output[{index}].message")
            location = require_mapping(issue.get("location"), f"Ruff output[{index}].location")
            row = require_positive_line(location.get("row"), f"Ruff output[{index}].location.row")
            column = require_positive_line(location.get("column"), f"Ruff output[{index}].location.column")
            path = _normal_path(filename, root)
            signals.append({
                "category": "symbol-no-usage-evidence",
                "affected": {"kind": "symbol", "path": path, "symbol": f"{code}@{row}:{column}", "line": row, "column": column},
                "evidence": {
                    "family": "ruff", "source": f"ruff.{code}", "signal": "no-usage-evidence",
                    "detail": re.sub(r"\bunused\b", "with no usage evidence", message, flags=re.IGNORECASE),
                },
            })
    except UnsupportedOutputSchema as error:
        return [], tool_run(name="ruff", executable=executable, version=version, result=result, argv=argv, limitation=limitation, status="unsupported output schema", contract_error=str(error))
    return signals, tool_run(name="ruff", executable=executable, version=version, result=result, argv=argv, limitation=limitation, status="available and succeeded")


def parse_deptry_output(text: str) -> list[dict[str, Any]]:
    payload = require_list(load_json(text, "deptry"), "deptry output")
    signals: list[dict[str, Any]] = []
    for index, raw_issue in enumerate(payload):
        issue = require_mapping(raw_issue, f"deptry output[{index}]")
        error = require_mapping(issue.get("error"), f"deptry output[{index}].error")
        code = require_string(error.get("code"), f"deptry output[{index}].error.code")
        message = require_string(error.get("message"), f"deptry output[{index}].error.message")
        module = require_string(issue.get("module"), f"deptry output[{index}].module")
        location = require_mapping(issue.get("location"), f"deptry output[{index}].location")
        path = require_string(location.get("file"), f"deptry output[{index}].location.file").replace("\\", "/")
        if code != "DEP002":
            continue
        signals.append({
            "category": "dependency-no-usage-evidence",
            "affected": {"kind": "dependency", "path": path, "dependency": module},
            "evidence": {"family": "deptry", "source": "deptry.DEP002", "signal": "no-usage-evidence", "detail": message},
        })
    return signals


def collect_deptry(root: Path, executable: Path, timeout: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    version, version_result, version_error = probe_version(run_approved_tool, executable, root, timeout, "deptry")
    limitation = "deptry dependency reachability can be incomplete for plugins and non-literal dynamic imports."
    if version_error:
        return [], tool_run(name="deptry", executable=executable, version=None, result=version_result, argv=[str(executable), "--version"], limitation=limitation, status="failed", contract_error=version_error)
    version_parts = tuple(int(part) for part in version.split("-")[0].split(".")[:2])
    if version_parts < (0, 10):
        return [], tool_run(
            name="deptry", executable=executable, version=version, result=version_result,
            argv=[str(executable), "--version"], limitation=limitation, status="unsupported output schema",
            contract_error="This adapter supports deptry's 0.10+ error-code and location JSON contract.",
        )
    with tempfile.TemporaryDirectory(prefix="code-rot-deptry-") as temp:
        json_path = Path(temp) / "deptry.json"
        argv = [str(executable), ".", "--json-output", str(json_path), "--no-ansi"]
        result = run_approved_tool(argv, root, timeout, accepted_exit_codes=(0, 1))
        if not result.get("completed", False):
            return [], tool_run(name="deptry", executable=executable, version=version, result=result, argv=argv, limitation=limitation, status="failed")
        try:
            text = json_path.read_text(encoding="utf-8")
            signals = parse_deptry_output(text)
        except OSError as error:
            contract_error = f"deptry did not create its documented JSON output file: {error}."
            return [], tool_run(name="deptry", executable=executable, version=version, result=result, argv=argv, limitation=limitation, status="unsupported output schema", contract_error=contract_error)
        except UnsupportedOutputSchema as error:
            return [], tool_run(name="deptry", executable=executable, version=version, result=result, argv=argv, limitation=limitation, status="unsupported output schema", contract_error=str(error))
    return signals, tool_run(name="deptry", executable=executable, version=version, result=result, argv=argv, limitation=limitation, status="available and succeeded")
