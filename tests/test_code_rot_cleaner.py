from __future__ import annotations

import csv
import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "code-rot-cleaner"
SCRIPTS = SKILL / "scripts"
JS_FIXTURE = ROOT / "tests" / "fixtures" / "js-project"
PY_FIXTURE = ROOT / "tests" / "fixtures" / "python-project"


class CodeRotCleanerTests(unittest.TestCase):
    def run_script(self, name: str, *args: str, expected: int = 0) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            ["python3", str(SCRIPTS / name), *map(str, args)],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        self.assertEqual(result.returncode, expected, result.stdout)
        return result

    def analyze(self, fixture: Path, output: Path) -> dict:
        self.run_script("analyze_code_rot.py", fixture, output)
        return json.loads(output.read_text(encoding="utf-8"))

    def test_javascript_analysis_is_conservative_and_useful(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "analysis.json"
            data = self.analyze(JS_FIXTURE, output)
            by_subject = {item["subject"]: item for item in data["candidates"]}

            self.assertNotIn("src/index.js", by_subject)
            self.assertNotIn("src/live-feature.js", by_subject)
            self.assertIn("src/orphan.js", by_subject)
            self.assertTrue(by_subject["src/orphan.js"]["proof_eligible"])

            runtime = by_subject["src/runtime-plugin.js"]
            self.assertEqual(runtime["confidence"], "medium")
            self.assertFalse(runtime["proof_eligible"])

            categories = {item["category"] for item in data["candidates"]}
            self.assertIn("duplicate-file", categories)
            unused = {item["subject"] for item in data["candidates"] if item["category"] == "unused-dependency"}
            self.assertIn("left-pad (dependencies)", unused)
            self.assertNotIn("lodash (dependencies)", unused)
            self.assertNotIn("vitest (devDependencies)", unused)

    def test_python_import_graph_preserves_reachable_module(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            data = self.analyze(PY_FIXTURE, Path(temp) / "analysis.json")
            orphans = {item["path"] for item in data["candidates"] if item["category"] == "orphan-file"}
            self.assertIn("app/abandoned.py", orphans)
            self.assertNotIn("app/live.py", orphans)
            self.assertNotIn("app/__main__.py", orphans)

    def test_disposable_proof_and_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            temp_path = Path(temp)
            analysis_path = temp_path / "analysis.json"
            proof_path = temp_path / "proof.json"
            review_path = temp_path / "review.json"
            report_path = temp_path / "CODE-ROT-REPORT.md"
            csv_path = temp_path / "cleanup-plan.csv"
            analysis = self.analyze(JS_FIXTURE, analysis_path)
            orphan_id = next(item["id"] for item in analysis["candidates"] if item["subject"] == "src/orphan.js")
            runtime_id = next(item["id"] for item in analysis["candidates"] if item["subject"] == "src/runtime-plugin.js")
            review_path.write_text(json.dumps({
                "decisions": [{
                    "candidate_id": runtime_id,
                    "status": "KEEP",
                    "reason": "Loaded by registry.json.",
                }]
            }), encoding="utf-8")

            check = "python3 -c \"from pathlib import Path; assert Path('src/index.js').is_file()\""
            self.run_script(
                "prove_candidates.py", JS_FIXTURE, analysis_path, proof_path,
                "--confirm-run-project-code", "--candidate-id", orphan_id,
                "--command", check,
            )
            self.assertTrue((JS_FIXTURE / "src" / "orphan.js").is_file())

            self.run_script(
                "generate_report.py", analysis_path, report_path, csv_path,
                "--proof", proof_path, "--review", review_path,
            )
            report = report_path.read_text(encoding="utf-8")
            self.assertIn("SAFE TO REMOVE", report)
            self.assertIn(orphan_id, report)
            with csv_path.open(encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            orphan_row = next(row for row in rows if row["id"] == orphan_id)
            self.assertEqual(orphan_row["final_status"], "SAFE TO REMOVE")
            runtime_row = next(row for row in rows if row["id"] == runtime_id)
            self.assertEqual(runtime_row["final_status"], "KEEP")

    def test_proof_requires_explicit_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            analysis_path = Path(temp) / "analysis.json"
            self.analyze(JS_FIXTURE, analysis_path)
            result = self.run_script(
                "prove_candidates.py", JS_FIXTURE, analysis_path, Path(temp) / "proof.json",
                "--command", "true", expected=1,
            )
            self.assertIn("Refusing to run project commands", result.stdout)


if __name__ == "__main__":
    unittest.main()
