from __future__ import annotations

import csv
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "cdr"
SCRIPTS = SKILL / "scripts"
TS_FIXTURE = ROOT / "tests" / "fixtures" / "typescript-project"
PY_FIXTURE = ROOT / "tests" / "fixtures" / "python-audit-project"
TOOL_OUTPUT = ROOT / "tests" / "fixtures" / "tool-output"

sys.path.insert(0, str(SCRIPTS))


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CodeRotCleanerTests(unittest.TestCase):
    maxDiff = None

    def run_script(self, name: str, *args: str, expected: int = 0) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [sys.executable, str(SCRIPTS / name), *map(str, args)],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        self.assertEqual(result.returncode, expected, result.stdout)
        return result

    def audit(self, fixture: Path, output: Path, *args: str) -> dict:
        self.run_script("audit.py", fixture, output, *args)
        return json.loads(output.read_text(encoding="utf-8"))

    @staticmethod
    def by_path(data: dict, category: str = "orphan-file") -> dict[str, dict]:
        return {
            item["affected"]["path"]: item
            for item in data["candidates"]
            if item["category"] == category and item["affected"].get("path")
        }

    def test_typescript_aliases_monorepos_barrels_dynamic_imports_and_routes_are_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            data = self.audit(TS_FIXTURE, Path(temp) / "analysis.json")
            orphans = self.by_path(data)

            self.assertIn("src/orphan.ts", orphans)
            self.assertNotIn("packages/core/src/live.ts", orphans)
            self.assertNotIn("src/barrel/index.ts", orphans)
            self.assertNotIn("src/barrel/feature.ts", orphans)
            self.assertNotIn("src/lazy-worker.ts", orphans)
            self.assertNotIn("src/app/api/health/route.ts", orphans)
            self.assertNotIn("src/pages/about.tsx", orphans)
            self.assertNotIn("src/plugins/runtime.ts", orphans)
            self.assertNotIn("src/generated/schema.ts", orphans)
            export_paths = {
                item["affected"]["path"]
                for item in data["candidates"]
                if item["category"] == "export-no-usage-evidence"
            }
            self.assertNotIn("src/orphan.ts", export_paths)
            self.assertNotIn("src/app/api/health/route.ts", export_paths)
            self.assertNotIn("src/pages/about.tsx", export_paths)
            self.assertNotIn("src/plugins/runtime.ts", export_paths)
            self.assertNotIn("src/generated/schema.ts", export_paths)

            ecosystem = data["ecosystems"]["typescript"]
            self.assertEqual(ecosystem["package_manager"], "pnpm")
            self.assertTrue(ecosystem["monorepo"])
            self.assertIn("@core/*", ecosystem["tsconfig_paths"])
            self.assertTrue(ecosystem["vite"])
            self.assertTrue(ecosystem["nextjs"])

    def test_python_packages_relative_imports_namespace_packages_and_cli_entries_are_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            temp_path = Path(temp)
            analysis_path = temp_path / "analysis.json"
            data = self.audit(PY_FIXTURE, analysis_path)
            orphans = self.by_path(data)

            self.assertIn("app/abandoned.py", orphans)
            self.assertNotIn("app/live.py", orphans)
            self.assertNotIn("app/cli.py", orphans)
            self.assertNotIn("app/__main__.py", orphans)
            self.assertNotIn("namespace_pkg/live.py", orphans)
            self.assertNotIn("src/toolpkg/live.py", orphans)
            self.assertIn("audit-cli", data["ecosystems"]["python"]["cli_entry_points"])
            dependency_locations = {
                (item["affected"].get("dependency"), item["affected"].get("path"))
                for item in data["candidates"]
                if item["category"] == "dependency-no-usage-evidence"
            }
            self.assertIn(("pytest", "requirements-dev.txt"), dependency_locations)
            report_path = temp_path / "report.md"
            csv_path = temp_path / "plan.csv"
            self.run_script("report.py", analysis_path, report_path, csv_path)
            report = report_path.read_text(encoding="utf-8")
            self.assertIn("`pyproject.toml::legacy-package`", report)
            self.assertIn("`requirements-dev.txt::pytest`", report)

    def test_analysis_uses_structured_evidence_and_never_promotes_builtin_results(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            data = self.audit(TS_FIXTURE, Path(temp) / "analysis.json")
            orphan = self.by_path(data)["src/orphan.ts"]

            self.assertRegex(orphan["candidate_id"], r"^CRT-\d{3}$")
            self.assertEqual(orphan["recommendation"], "REVIEW")
            self.assertEqual(orphan["proof_status"], "NOT_RUN")
            self.assertIn("evidence_sources", orphan)
            self.assertIn("unresolved_questions", orphan)
            self.assertEqual({item["family"] for item in orphan["evidence_sources"]}, {"builtin"})

    @staticmethod
    def tool_result(stdout: str, *, exit_code: int = 0, stderr: str = "") -> dict:
        return {
            "completed": exit_code in {0, 1},
            "exit_code": exit_code,
            "stdout": stdout,
            "stderr": stderr,
            "timed_out": False,
            "redacted": "[REDACTED]" in stderr,
            "command": "captured fixture",
            "execution_mode": "argv-no-shell",
            "environment_policy": {"name": "sanitized-allowlist-v1"},
        }

    def test_realistic_knip_v6_contract_is_normalized_and_allows_additional_fields(self) -> None:
        typescript = load_module("typescript_collector", SCRIPTS / "collectors" / "typescript.py")
        payload = (TOOL_OUTPUT / "knip-v6.json").read_text(encoding="utf-8")

        with mock.patch.object(typescript, "run_approved_tool", side_effect=[
            self.tool_result("6.0.0\n"),
            self.tool_result(payload, exit_code=1),
        ]):
            signals, tool_run = typescript.collect_knip(TS_FIXTURE, Path("knip"), 30)

        self.assertEqual(tool_run["tool"], "knip")
        self.assertEqual(tool_run["status"], "available and succeeded")
        self.assertEqual(tool_run["version"], "6.0.0")
        self.assertEqual(signals[0]["affected"]["path"], "src/legacy.ts")
        self.assertEqual(signals[0]["evidence"]["family"], "knip")
        self.assertIn("lodash", {item["affected"].get("dependency") for item in signals})

    def test_machine_readable_contract_fixtures_are_normalized(self) -> None:
        python_collector = load_module("python_contract_collector", SCRIPTS / "collectors" / "python.py")
        cases = (
            ("ruff", python_collector.collect_ruff, "ruff-0.15.json", "0.15.10", 2, 1),
            ("vulture", python_collector.collect_vulture, "vulture-api-v1.json", "2.14", 8, 0),
        )
        for name, collector, fixture_name, version, expected_count, finding_exit in cases:
            with self.subTest(tool=name), mock.patch.object(
                python_collector,
                "run_approved_tool",
                side_effect=[self.tool_result(f"{name} {version}\n"), self.tool_result((TOOL_OUTPUT / fixture_name).read_text(encoding="utf-8"), exit_code=finding_exit)],
            ):
                signals, tool_run = collector(PY_FIXTURE, Path(name), 30)
            self.assertEqual(len(signals), expected_count)
            self.assertEqual(tool_run["status"], "available and succeeded")
            self.assertEqual(tool_run["version"], version)

        deptry_payload = (TOOL_OUTPUT / "deptry.json").read_text(encoding="utf-8")

        def deptry_runner(argv, *_args, **_kwargs):
            if "--version" in argv:
                return self.tool_result("deptry 0.23.1\n")
            output = Path(argv[argv.index("--json-output") + 1])
            output.write_text(deptry_payload, encoding="utf-8")
            return self.tool_result("", exit_code=1)

        with mock.patch.object(python_collector, "run_approved_tool", side_effect=deptry_runner):
            signals, tool_run = python_collector.collect_deptry(PY_FIXTURE, Path("deptry"), 30)
        self.assertEqual(len(signals), 1)
        self.assertEqual(tool_run["status"], "available and succeeded")
        self.assertEqual(tool_run["version"], "0.23.1")

    def test_malformed_and_unknown_json_fail_closed(self) -> None:
        typescript = load_module("typescript_schema_collector", SCRIPTS / "collectors" / "typescript.py")
        python_collector = load_module("python_schema_collector", SCRIPTS / "collectors" / "python.py")
        cases = (
            (typescript.collect_knip, "{not json"),
            (typescript.collect_knip, json.dumps({"unexpected": []})),
            (python_collector.collect_ruff, "{not json"),
            (python_collector.collect_ruff, json.dumps({"issues": []})),
            (python_collector.collect_deptry, "{not json"),
            (python_collector.collect_deptry, json.dumps({"issues": []})),
            (python_collector.collect_vulture, "{not json"),
            (python_collector.collect_vulture, json.dumps({"items": []})),
        )
        for collector, stdout in cases:
            module = typescript if collector.__name__ == "collect_knip" else python_collector
            with self.subTest(collector=collector.__name__, stdout=stdout), mock.patch.object(
                module,
                "run_approved_tool",
                side_effect=[self.tool_result("tool 1.0\n"), self.tool_result(stdout)],
            ):
                signals, tool_run = collector(PY_FIXTURE, Path("tool"), 30)
            self.assertEqual(signals, [])
            self.assertEqual(tool_run["status"], "unsupported output schema")

    def test_process_failure_is_distinct_from_successful_findings(self) -> None:
        typescript = load_module("typescript_exit_collector", SCRIPTS / "collectors" / "typescript.py")
        payload = (TOOL_OUTPUT / "knip-v6.json").read_text(encoding="utf-8")
        with mock.patch.object(typescript, "run_approved_tool", side_effect=[
            self.tool_result("6.0.0\n"),
            self.tool_result(payload, exit_code=2, stderr="internal error"),
        ]):
            signals, tool_run = typescript.collect_knip(TS_FIXTURE, Path("knip"), 30)
        self.assertEqual(signals, [])
        self.assertEqual(tool_run["status"], "failed")
        self.assertEqual(tool_run["stderr"], "internal error")

    def test_known_pre_contract_tool_versions_are_rejected_without_scanning(self) -> None:
        typescript = load_module("typescript_old_version_collector", SCRIPTS / "collectors" / "typescript.py")
        python_collector = load_module("python_old_version_collector", SCRIPTS / "collectors" / "python.py")
        cases = (
            (typescript, typescript.collect_knip, "knip 5.99.0\n"),
            (python_collector, python_collector.collect_deptry, "deptry 0.9.0\n"),
            (python_collector, python_collector.collect_vulture, "vulture 1.4\n"),
        )
        for module, collector, version_output in cases:
            with self.subTest(collector=collector.__name__), mock.patch.object(
                module, "run_approved_tool", return_value=self.tool_result(version_output),
            ) as runner:
                signals, tool_run = collector(PY_FIXTURE, Path("tool"), 30)
            self.assertEqual(signals, [])
            self.assertEqual(tool_run["status"], "unsupported output schema")
            self.assertEqual(runner.call_count, 1)

    def test_external_tool_inventory_reports_unavailable_and_unapproved_states(self) -> None:
        audit_module = load_module("audit_inventory", SCRIPTS / "audit.py")
        candidates: list[dict] = []
        available = {"knip": None, "vulture": "vulture", "ruff": None, "deptry": None}
        runs = audit_module.collect_external_tools(Path.cwd(), candidates, {"knip"}, 30, available)
        by_tool = {run["tool"]: run for run in runs}
        self.assertEqual(by_tool["knip"]["status"], "unavailable")
        self.assertEqual(by_tool["vulture"]["status"], "skipped because approval was not granted")

    def test_one_failed_collector_does_not_discard_another_collectors_evidence(self) -> None:
        audit_module = load_module("audit_isolation", SCRIPTS / "audit.py")
        signal = {
            "category": "orphan-file",
            "affected": {"kind": "file", "path": "stale.py"},
            "evidence": {"family": "ruff", "source": "ruff.F401", "signal": "no-usage-evidence", "detail": "captured"},
        }
        failed = mock.Mock(side_effect=RuntimeError("collector exploded"))
        succeeded = mock.Mock(return_value=([signal], {"tool": "ruff", "status": "available and succeeded"}))
        with mock.patch.dict(audit_module.EXTERNAL_COLLECTORS, {"knip": failed, "ruff": succeeded}, clear=True):
            runs = audit_module.collect_external_tools(
                Path.cwd(), [], {"knip", "ruff"}, 30, {"knip": "knip", "ruff": "ruff"},
            )
        self.assertEqual([run["status"] for run in runs], ["failed", "available and succeeded"])

    def test_missing_knip_configuration_keeps_file_finding_unresolved(self) -> None:
        typescript = load_module("typescript_config_collector", SCRIPTS / "collectors" / "typescript.py")
        payload = (TOOL_OUTPUT / "knip-v6.json").read_text(encoding="utf-8")
        with mock.patch.object(typescript, "run_approved_tool", side_effect=[
            self.tool_result("6.0.0\n"), self.tool_result(payload, exit_code=1),
        ]):
            signals, _ = typescript.collect_knip(TS_FIXTURE, Path("knip"), 30)
        file_signal = next(item for item in signals if item["category"] == "orphan-file")
        self.assertTrue(file_signal["unresolved_questions"])

    def test_knip_optional_peer_is_not_removal_evidence_and_dependency_paths_stay_distinct(self) -> None:
        typescript = load_module("typescript_semantics_collector", SCRIPTS / "collectors" / "typescript.py")
        audit_module = load_module("audit_dependency_identity", SCRIPTS / "audit.py")
        payload = {
            "issues": [{
                "file": "packages/a/package.json",
                "files": [],
                "dependencies": [{"name": "lodash"}],
                "devDependencies": [],
                "optionalPeerDependencies": [{"name": "react"}],
            }]
        }
        signals = typescript.parse_knip_output(json.dumps(payload), TS_FIXTURE)
        self.assertNotIn("react", {item["affected"].get("dependency") for item in signals})
        candidates = [
            {"affected": {"kind": "dependency", "path": "packages/a/package.json", "dependency": "lodash"}, "evidence_sources": [], "unresolved_questions": []},
            {"affected": {"kind": "dependency", "path": "packages/b/package.json", "dependency": "lodash"}, "evidence_sources": [], "unresolved_questions": []},
        ]
        audit_module.merge_external_signals(candidates, signals)
        self.assertEqual(len(candidates[0]["evidence_sources"]), 1)
        self.assertEqual(candidates[1]["evidence_sources"], [])

    def test_captured_vulture_e2e_keeps_dynamic_cli_and_public_surfaces_review_only(self) -> None:
        audit_module = load_module("audit_vulture_e2e", SCRIPTS / "audit.py")
        builtin = load_module("builtin_vulture_e2e", SCRIPTS / "collectors" / "builtin.py")
        python_collector = load_module("python_vulture_e2e", SCRIPTS / "collectors" / "python.py")
        collected = builtin.collect(PY_FIXTURE)
        signals, version = python_collector.parse_vulture_output(
            (TOOL_OUTPUT / "vulture-api-v1.json").read_text(encoding="utf-8"), PY_FIXTURE,
        )
        audit_module.merge_external_signals(collected["candidates"], signals)
        audit_module.finalize_candidates(collected["candidates"])
        by_symbol = {
            (item["affected"].get("path"), item["affected"].get("symbol")): item
            for item in collected["candidates"]
            if item["affected"].get("symbol")
        }
        self.assertEqual(version, "2.14")
        self.assertIn(("app/stale.py", "stale_function"), by_symbol)
        for key in (
            ("app/cli.py", "main"),
            ("app/dynamic_loader.py", "dynamic_hook"),
            ("public_pkg/exported.py", "public_hook"),
        ):
            candidate = by_symbol[key]
            self.assertEqual(candidate["recommendation"], "REVIEW")
            self.assertTrue(candidate["unresolved_questions"])
            self.assertFalse(candidate["proof_eligible"])
        self.assertNotIn(("app/used_across_modules.py", "used_across_modules"), by_symbol)

    def test_npm_package_dry_run_excludes_python_bytecode(self) -> None:
        npm = shutil.which("npm")
        self.assertIsNotNone(npm)
        result = subprocess.run(
            [str(npm), "pack", "--dry-run", "--json"],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        self.assertEqual(result.returncode, 0, result.stdout)
        payload = json.loads(result.stdout)
        packaged_paths = {item["path"] for item in payload[0]["files"]}
        self.assertIn("cdr/SKILL.md", packaged_paths)
        self.assertIn("cdr/scripts/audit.py", packaged_paths)
        self.assertIn("cdr/references/detection-playbook.md", packaged_paths)
        self.assertIn("code-rot-cleaner/SKILL.md", packaged_paths)
        self.assertFalse(any(path.startswith("code-rot-cleaner/scripts/") for path in packaged_paths))
        self.assertFalse(any(path.startswith("code-rot-cleaner/references/") for path in packaged_paths))
        self.assertFalse(any("__pycache__" in path or path.endswith(".pyc") for path in packaged_paths))
        forbidden_fragments = ("outputs/", "analysis.json", "proof.json", "cleanup-plan", ".env", "secret")
        self.assertFalse(any(fragment in path.lower() for path in packaged_paths for fragment in forbidden_fragments))

    def test_package_identity_lockfile_and_install_docs_use_supaboiclean_scope(self) -> None:
        package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
        self.assertEqual(package["name"], "@supaboiclean/cdr")
        self.assertEqual(package["version"], "0.2.2")
        self.assertEqual(package["bin"], {"cdr": "scripts/install.js"})
        self.assertEqual(package["publishConfig"], {
            "access": "public",
            "registry": "https://registry.npmjs.org/",
        })

        lock_path = ROOT / "package-lock.json"
        self.assertTrue(lock_path.is_file())
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        self.assertEqual(lock["name"], package["name"])
        self.assertEqual(lock["version"], package["version"])
        self.assertEqual(lock["packages"][""]["name"], package["name"])
        self.assertEqual(lock["packages"][""]["version"], package["version"])
        self.assertEqual(lock["packages"][""]["bin"], package["bin"])

        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        installer = (ROOT / "scripts" / "install.js").read_text(encoding="utf-8")
        self.assertIn("npx --yes @supaboiclean/cdr@0.2.2", readme)
        self.assertIn("npx --yes @supaboiclean/cdr@0.2.2", installer)
        self.assertNotRegex(readme, r"npx\s+--yes\s+codex-code-rot-cleaner")
        self.assertNotRegex(installer, r"npx\s+--yes\s+codex-code-rot-cleaner")

    def test_release_layout_has_one_canonical_implementation_and_a_delegating_alias(self) -> None:
        canonical = ROOT / "cdr"
        legacy = ROOT / "code-rot-cleaner"

        canonical_skill = (canonical / "SKILL.md").read_text(encoding="utf-8")
        legacy_skill = (legacy / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("\nname: cdr\n", canonical_skill)
        self.assertIn("\nname: code-rot-cleaner\n", legacy_skill)
        self.assertIn("$cdr", legacy_skill)
        self.assertIn("report-only", legacy_skill.lower())
        self.assertIn("approval", legacy_skill.lower())
        self.assertTrue((canonical / "agents" / "openai.yaml").is_file())
        self.assertTrue((canonical / "scripts" / "audit.py").is_file())
        self.assertTrue((canonical / "scripts" / "proof.py").is_file())
        self.assertTrue((canonical / "scripts" / "report.py").is_file())
        self.assertTrue((canonical / "references" / "detection-playbook.md").is_file())
        self.assertFalse((legacy / "scripts").exists())
        self.assertFalse((legacy / "references").exists())

    def test_installer_creates_both_skills_replaces_only_them_and_supports_spaces(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cdr installer smoke ") as temp:
            skills_dir = Path(temp) / "skills with spaces"
            unrelated = skills_dir / "unrelated-skill"
            unrelated.mkdir(parents=True)
            (unrelated / "SKILL.md").write_text("unrelated\n", encoding="utf-8")

            first = subprocess.run(
                ["node", str(ROOT / "scripts" / "install.js"), "--skills-dir", str(skills_dir)],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            self.assertEqual(first.returncode, 0, first.stdout)

            canonical = skills_dir / "cdr"
            legacy = skills_dir / "code-rot-cleaner"
            (canonical / "stale.txt").write_text("replace me\n", encoding="utf-8")
            (legacy / "stale.txt").write_text("replace me\n", encoding="utf-8")
            second = subprocess.run(
                ["node", str(ROOT / "scripts" / "install.js"), "--skills-dir", str(skills_dir)],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )

            self.assertEqual(second.returncode, 0, second.stdout)
            self.assertTrue((canonical / "SKILL.md").is_file())
            self.assertTrue((canonical / "agents" / "openai.yaml").is_file())
            self.assertTrue((canonical / "scripts" / "audit.py").is_file())
            self.assertTrue((canonical / "references" / "evidence-schema.md").is_file())
            self.assertTrue((legacy / "SKILL.md").is_file())
            self.assertFalse((legacy / "scripts").exists())
            self.assertFalse((legacy / "references").exists())
            self.assertFalse((canonical / "stale.txt").exists())
            self.assertFalse((legacy / "stale.txt").exists())
            self.assertEqual((unrelated / "SKILL.md").read_text(encoding="utf-8"), "unrelated\n")
            self.assertEqual(list(canonical.rglob("__pycache__")), [])
            self.assertEqual(list(canonical.rglob("*.pyc")), [])
            self.assertIn("$cdr", second.stdout)
            self.assertIn("primary", second.stdout.lower())
            self.assertIn("$code-rot-cleaner", second.stdout)
            self.assertIn("legacy alias", second.stdout.lower())

    def test_installed_canonical_skill_runs_report_only_audit_and_report(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cdr report smoke ") as temp:
            root = Path(temp)
            skills_dir = root / "installed skills"
            project = root / "source project"
            project.mkdir()
            source = project / "live.py"
            source.write_text("print('live')\n", encoding="utf-8")
            before = source.read_bytes()
            install = subprocess.run(
                ["node", str(ROOT / "scripts" / "install.js"), "--skills-dir", str(skills_dir)],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            self.assertEqual(install.returncode, 0, install.stdout)

            installed_scripts = skills_dir / "cdr" / "scripts"
            analysis = root / "analysis.json"
            report = root / "report.md"
            plan = root / "plan.csv"
            audit = subprocess.run(
                [sys.executable, str(installed_scripts / "audit.py"), str(project), str(analysis)],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            self.assertEqual(audit.returncode, 0, audit.stdout)
            generate = subprocess.run(
                [sys.executable, str(installed_scripts / "report.py"), str(analysis), str(report), str(plan)],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            self.assertEqual(generate.returncode, 0, generate.stdout)
            self.assertEqual(json.loads(analysis.read_text(encoding="utf-8"))["mode"], "report-only")
            self.assertTrue(report.is_file())
            self.assertTrue(plan.is_file())
            self.assertEqual(source.read_bytes(), before)

    def test_git_history_is_supporting_and_skips_untracked_files(self) -> None:
        git_history = load_module("git_history_collector", SCRIPTS / "collectors" / "git_history.py")
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            subprocess.run(["git", "init", "-q", str(project)], check=True)
            subprocess.run(["git", "-C", str(project), "config", "user.email", "fixture@example.test"], check=True)
            subprocess.run(["git", "-C", str(project), "config", "user.name", "Fixture"], check=True)
            (project / "tracked.py").write_text("VALUE = 1\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(project), "add", "tracked.py"], check=True)
            subprocess.run(["git", "-C", str(project), "commit", "-qm", "fixture"], check=True)
            (project / "candidate.py").write_text("VALUE = 2\n", encoding="utf-8")
            candidates = [{
                "affected": {"kind": "file", "path": "candidate.py"},
                "evidence_sources": [],
            }]

            result = git_history.collect(project, candidates)

            self.assertEqual(result["files"], 0)
            self.assertEqual(candidates[0]["evidence_sources"], [])
            tracked_candidates = [{
                "affected": {"kind": "file", "path": "tracked.py"},
                "evidence_sources": [],
            }]
            tracked_result = git_history.collect(project, tracked_candidates)
            self.assertEqual(tracked_result["files"], 1)
            self.assertEqual(tracked_candidates[0]["evidence_sources"][0]["family"], "git")
            self.assertIn("blame commits", tracked_candidates[0]["evidence_sources"][0]["detail"])

    def test_security_filters_dangerous_environment_and_masks_secret_output(self) -> None:
        security = load_module("skill_security", SCRIPTS / "security.py")
        fake_token = "ghp_" + "super_secret_value"
        source = {
            "PATH": os.environ.get("PATH", ""),
            "SYSTEMROOT": os.environ.get("SYSTEMROOT", "C:\\Windows"),
            "USERPROFILE": "C:\\Users\\real-profile",
            "GITHUB_TOKEN": fake_token,
            "DATABASE_URL": "postgres://user:password@example/db",
        }
        with tempfile.TemporaryDirectory() as temp:
            environment, policy = security.sanitized_environment(Path(temp), source)

        self.assertNotIn("GITHUB_TOKEN", environment)
        self.assertNotIn("DATABASE_URL", environment)
        self.assertEqual(policy["inherited_secret_variables"], 0)
        self.assertNotIn("USERPROFILE", policy["inherited_variables"])
        with tempfile.TemporaryDirectory() as temp:
            empty_environment, _ = security.sanitized_environment(Path(temp), {})
        self.assertNotIn("PATH", empty_environment)
        masked, redacted = security.mask_secrets(
            f"token={fake_token} Authorization: Bearer abcdefghijklmnop",
            source,
        )
        self.assertTrue(redacted)
        self.assertNotIn(fake_token, masked)
        self.assertNotIn("abcdefghijklmnop", masked)
        generic_secret = "correct-horse-battery-staple"
        masked, redacted = security.mask_secrets(
            f"PASSWORD={generic_secret} --api-key {generic_secret} npm_abcdefghijklmnopqrstuvwxyz",
            {},
        )
        self.assertTrue(redacted)
        self.assertNotIn(generic_secret, masked)
        with tempfile.TemporaryDirectory() as temp:
            result = security.run_approved_tool(
                [sys.executable, "-c", f"import sys; sys.stderr.write('token={generic_secret}')"],
                Path(temp),
                10,
                accepted_exit_codes=(0,),
            )
        self.assertTrue(result["completed"])
        self.assertEqual(result["stderr"], "token=[REDACTED]")
        self.assertTrue(result["redacted"])

    def test_symlink_escape_guard_rejects_targets_outside_project(self) -> None:
        proof = load_module("proof_module", SCRIPTS / "proof.py")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project"
            outside = Path(temp) / "outside.txt"
            root.mkdir()
            outside.write_text("secret", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Symlink escapes project root"):
                proof.ensure_symlink_target_inside(root.resolve(), root / "escape", outside.resolve())

    def test_candidate_path_traversal_is_rejected(self) -> None:
        proof = load_module("proof_path_module", SCRIPTS / "proof.py")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            with self.assertRaisesRegex(ValueError, "escapes disposable root"):
                proof.safe_candidate_path(root, "../outside.py")

    def make_analysis(self, project: Path, candidate_path: str) -> Path:
        candidate = project / candidate_path
        analysis = {
            "schema_version": "2.0",
            "project_root": str(project.resolve()),
            "generated_at": "2026-01-01T00:00:00Z",
            "mode": "report-only",
            "scope": {"source_files": 1, "source_loc": 1, "source_bytes": candidate.stat().st_size},
            "summary": {"candidates": 1},
            "ecosystems": {},
            "tool_runs": [{
                "tool": "knip",
                "executable": "knip",
                "version": "6.0.0",
                "status": "available and succeeded",
                "approval_granted": True,
            }],
            "candidates": [{
                "candidate_id": "CRT-001",
                "category": "orphan-file",
                "affected": {"kind": "file", "path": candidate_path},
                "evidence_sources": [
                    {"family": "builtin", "source": "builtin.import-graph", "signal": "no-inbound-reference", "detail": "No static inbound reference found."},
                    {"family": "knip", "source": "knip.files", "signal": "no-usage-evidence", "detail": "Knip reported the file."},
                ],
                "confidence": "high",
                "risk": "low",
                "unresolved_questions": [],
                "proof_status": "NOT_RUN",
                "recommendation": "REVIEW",
                "proof_eligible": True,
                "safety": {"dynamic_usage": "none-found", "external_api": "none-known", "convention_role": "none-found"},
                "why_suspicious": "No usage evidence found from two independent collectors.",
                "why_might_still_be_needed": "Static and test evidence can miss rare runtime paths.",
                "loc": 1,
                "bytes": candidate.stat().st_size,
            }],
            "limitations": ["Static analysis is incomplete."],
        }
        path = project.parent / "analysis.json"
        path.write_text(json.dumps(analysis), encoding="utf-8")
        return path

    def prove(self, project: Path, analysis: Path, proof: Path, code: str, expected: int = 0) -> None:
        command = json.dumps({"kind": "test", "argv": [sys.executable, "-c", code]})
        self.run_script(
            "proof.py", project, analysis, proof,
            "--confirm-run-project-code", "--candidate-id", "CRT-001",
            "--command-json", command, expected=expected,
        )

    def test_disposable_proof_success_can_produce_safe_recommendation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            project = base / "project"
            project.mkdir()
            (project / "orphan.py").write_text("VALUE = 1\n", encoding="utf-8")
            analysis = self.make_analysis(project, "orphan.py")
            proof_path = base / "proof.json"
            report_path = base / "CODE-ROT-REPORT.md"
            csv_path = base / "cleanup-plan.csv"

            self.prove(project, analysis, proof_path, "from pathlib import Path; assert Path('.').is_dir()")
            self.run_script("report.py", analysis, report_path, csv_path, "--proof", proof_path)

            proof = json.loads(proof_path.read_text(encoding="utf-8"))
            self.assertEqual(proof["results"][0]["outcome"], "PASSED_IN_DISPOSABLE_COPY")
            report = report_path.read_text(encoding="utf-8")
            self.assertIn("SAFE TO REMOVE", report)
            self.assertIn("Environment policy", report)
            with csv_path.open(encoding="utf-8") as handle:
                row = next(csv.DictReader(handle))
            self.assertEqual(row["recommendation"], "SAFE TO REMOVE")

    def test_tampered_or_cross_project_proof_never_promotes_safe(self) -> None:
        mutations = (
            lambda analysis, proof: analysis["limitations"].append("changed after proof"),
            lambda analysis, proof: proof.__setitem__("project_root", str(Path(analysis["project_root"]).parent / "other")),
            lambda analysis, proof: proof["results"][0].__setitem__("path", "different.py"),
            lambda analysis, proof: proof["results"][0].__setitem__("commands", []),
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temp:
                base = Path(temp)
                project = base / "project"
                project.mkdir()
                (project / "orphan.py").write_text("VALUE = 1\n", encoding="utf-8")
                analysis_path = self.make_analysis(project, "orphan.py")
                proof_path = base / "proof.json"
                self.prove(project, analysis_path, proof_path, "from pathlib import Path; assert Path('.').is_dir()")
                analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
                proof = json.loads(proof_path.read_text(encoding="utf-8"))
                mutation(analysis, proof)
                analysis_path.write_text(json.dumps(analysis), encoding="utf-8")
                proof_path.write_text(json.dumps(proof), encoding="utf-8")
                report_path = base / "report.md"
                self.run_script("report.py", analysis_path, report_path, base / "plan.csv", "--proof", proof_path)
                report = report_path.read_text(encoding="utf-8")
                self.assertNotIn("### CRT-001 - SAFE TO REMOVE", report)

    def test_failed_external_collector_cannot_supply_mature_safe_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            project = base / "project"
            project.mkdir()
            (project / "orphan.py").write_text("VALUE = 1\n", encoding="utf-8")
            analysis_path = self.make_analysis(project, "orphan.py")
            analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
            analysis["tool_runs"][0]["status"] = "failed"
            analysis_path.write_text(json.dumps(analysis), encoding="utf-8")
            proof_path = base / "proof.json"
            self.prove(project, analysis_path, proof_path, "from pathlib import Path; assert Path('.').is_dir()")
            report_path = base / "report.md"
            self.run_script("report.py", analysis_path, report_path, base / "plan.csv", "--proof", proof_path)
            self.assertNotIn("### CRT-001 - SAFE TO REMOVE", report_path.read_text(encoding="utf-8"))

    def test_wrong_top_level_proof_schema_is_inconclusive_not_a_crash(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            project = base / "project"
            project.mkdir()
            (project / "orphan.py").write_text("VALUE = 1\n", encoding="utf-8")
            analysis_path = self.make_analysis(project, "orphan.py")
            proof_path = base / "proof.json"
            proof_path.write_text("[]", encoding="utf-8")
            report_path = base / "report.md"
            self.run_script("report.py", analysis_path, report_path, base / "plan.csv", "--proof", proof_path)
            report = report_path.read_text(encoding="utf-8")
            self.assertIn("**INCONCLUSIVE**", report)
            self.assertNotIn("### CRT-001 - SAFE TO REMOVE", report)

    def test_disposable_proof_failure_keeps_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            project = base / "project"
            project.mkdir()
            (project / "orphan.py").write_text("VALUE = 1\n", encoding="utf-8")
            analysis = self.make_analysis(project, "orphan.py")
            proof_path = base / "proof.json"
            report_path = base / "report.md"
            csv_path = base / "plan.csv"

            self.prove(project, analysis, proof_path, "from pathlib import Path; assert Path('orphan.py').is_file()")
            self.run_script("report.py", analysis, report_path, csv_path, "--proof", proof_path)

            proof = json.loads(proof_path.read_text(encoding="utf-8"))
            self.assertEqual(proof["results"][0]["outcome"], "FAILED_AFTER_REMOVAL")
            self.assertIn("KEEP", report_path.read_text(encoding="utf-8"))

    def test_proof_masks_secrets_in_commands_and_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            project = base / "project"
            project.mkdir()
            (project / "orphan.py").write_text("VALUE = 1\n", encoding="utf-8")
            analysis = self.make_analysis(project, "orphan.py")
            proof_path = base / "proof.json"
            secret = "ghp_" + "abcdefghijklmnop"

            self.prove(project, analysis, proof_path, f"print('{secret}')")

            serialized = proof_path.read_text(encoding="utf-8")
            self.assertNotIn(secret, serialized)
            self.assertIn("[REDACTED]", serialized)

    def test_failed_baseline_does_not_test_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            project = base / "project"
            project.mkdir()
            (project / "orphan.py").write_text("VALUE = 1\n", encoding="utf-8")
            analysis = self.make_analysis(project, "orphan.py")
            proof_path = base / "proof.json"

            self.prove(project, analysis, proof_path, "raise SystemExit(7)", expected=2)
            proof = json.loads(proof_path.read_text(encoding="utf-8"))
            self.assertFalse(proof["baseline"]["passed"])
            self.assertEqual(proof["results"], [])

    def test_proof_requires_explicit_project_code_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            project = base / "project"
            project.mkdir()
            (project / "orphan.py").write_text("VALUE = 1\n", encoding="utf-8")
            analysis = self.make_analysis(project, "orphan.py")
            command = json.dumps({"kind": "test", "argv": [sys.executable, "-c", "pass"]})
            result = self.run_script(
                "proof.py", project, analysis, base / "proof.json",
                "--command-json", command, expected=1,
            )
            self.assertIn("Refusing to run project commands", result.stdout)

    def test_shell_execution_requires_separate_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            project = base / "project"
            project.mkdir()
            (project / "orphan.py").write_text("VALUE = 1\n", encoding="utf-8")
            analysis = self.make_analysis(project, "orphan.py")
            result = self.run_script(
                "proof.py", project, analysis, base / "proof.json",
                "--confirm-run-project-code", "--shell-command", "echo ok",
                expected=1,
            )
            self.assertIn("--confirm-shell-execution", result.stdout)

    def test_legacy_script_names_remain_compatible(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            analysis = Path(temp) / "analysis.json"
            self.run_script("analyze_code_rot.py", TS_FIXTURE, analysis)
            self.assertEqual(json.loads(analysis.read_text(encoding="utf-8"))["schema_version"], "2.0")

    def test_report_only_audit_does_not_write_bytecode_into_installed_skill(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            copied_skill = base / "code-rot-cleaner"
            shutil.copytree(SKILL, copied_skill, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            output = base / "analysis.json"

            result = subprocess.run(
                [sys.executable, str(copied_skill / "scripts" / "audit.py"), str(TS_FIXTURE), str(output)],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )

            report_result = subprocess.run(
                [
                    sys.executable,
                    str(copied_skill / "scripts" / "report.py"),
                    str(output),
                    str(base / "CODE-ROT-REPORT.md"),
                    str(base / "cleanup-plan.csv"),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )

            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertEqual(report_result.returncode, 0, report_result.stdout)
            self.assertEqual(list(copied_skill.rglob("__pycache__")), [])


if __name__ == "__main__":
    unittest.main()
