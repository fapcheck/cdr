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
SKILL = ROOT / "code-rot-cleaner"
SCRIPTS = SKILL / "scripts"
TS_FIXTURE = ROOT / "tests" / "fixtures" / "typescript-project"
PY_FIXTURE = ROOT / "tests" / "fixtures" / "python-audit-project"

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

    def test_knip_collector_is_mockable_and_adds_independent_evidence(self) -> None:
        typescript = load_module("typescript_collector", SCRIPTS / "collectors" / "typescript.py")
        payload = {
            "issues": [{
                "file": "src/orphan.ts",
                "files": [{"name": "src/orphan.ts"}],
                "exports": [],
                "dependencies": [],
            }]
        }
        fake_result = {
            "exit_code": 1,
            "stdout": json.dumps(payload),
            "stderr": "",
            "timed_out": False,
            "redacted": False,
        }

        with mock.patch.object(typescript, "run_approved_tool", return_value=fake_result):
            signals, tool_run = typescript.collect_knip(TS_FIXTURE, Path("knip"), 30)

        self.assertEqual(tool_run["tool"], "knip")
        self.assertEqual(signals[0]["affected"]["path"], "src/orphan.ts")
        self.assertEqual(signals[0]["evidence"]["family"], "knip")

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
        self.assertFalse(any("__pycache__" in path or path.endswith(".pyc") for path in packaged_paths))

    def test_installer_preserves_skill_architecture_without_bytecode(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            skills_dir = Path(temp) / "skills"
            result = subprocess.run(
                ["node", str(ROOT / "scripts" / "install.js"), "--skills-dir", str(skills_dir)],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            installed = skills_dir / "code-rot-cleaner"
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertTrue((installed / "SKILL.md").is_file())
            self.assertTrue((installed / "agents" / "openai.yaml").is_file())
            self.assertTrue((installed / "scripts" / "audit.py").is_file())
            self.assertEqual(list(installed.rglob("__pycache__")), [])
            self.assertEqual(list(installed.rglob("*.pyc")), [])
            self.assertIn("audit possible code rot", result.stdout)

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
            "tool_runs": [],
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
