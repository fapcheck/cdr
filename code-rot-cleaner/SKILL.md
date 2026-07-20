---
name: code-rot-cleaner
description: Find code a software project may no longer need, distinguish credible dead-code evidence from framework and dynamic-loading false positives, prove eligible file removals in a disposable copy, and create a native Markdown cleanup report. Use when Codex is asked to find dead code, unused files, orphan modules, unused exports or dependencies, duplicate implementation, stale generated-looking code, cleanup opportunities, removable LOC, repository bloat, or code rot. Default to report-only mode, never change real project source without explicit candidate-by-candidate approval, and verify approved cleanup with relevant tests and builds.
---

# Code Rot Cleaner

Find removable code without confusing “not found by a regex” with “safe to delete.” Keep the real project unchanged during discovery and proof. Treat deletion as a claim that must survive static checks, dynamic-reference checks, and relevant project commands.

## Enforce the safety boundary

Treat invocation of `$code-rot-cleaner` as consent to inspect the project and create report artifacts only. It is never consent to delete, edit, rename, uninstall, format, or reorganize project code.

- Default to `report-only` mode.
- Never alter source, manifests, lockfiles, tests, configuration, generated files, or dependencies during discovery.
- Save generated evidence under `outputs/code-rot-cleaner/` unless the user chooses another location.
- Require approval before executing repository-controlled build, test, lint, typecheck, or package-manager commands; those commands can run arbitrary code.
- Run removal experiments only in a disposable copy. Never present that copy as the user's working tree.
- Require a second, explicit approval before changing the real project. Approval must name exact candidate IDs and files.
- Preserve dirty worktrees and unrelated user changes. Never reset, clean, or rewrite history.

Before real cleanup, present and stop at:

```markdown
## Proposed cleanup

| ID | File or dependency | Evidence | Proof | Risk |
|---|---|---|---|---|
| CRT-... | ... | ... | ... | low/medium/high |

- Exact files or manifest entries to change:
- Expected removable LOC / bytes:
- Commands to run afterward:
- Main residual risk:

The real project has not been changed. Do you approve applying only these candidate IDs?
```

Approval applies only to the displayed candidates and files. Ask again if scope, evidence, commands, or affected files change.

## Choose a mode

- `report-only` — Default. Inventory and rank candidates, generate the report, and make no project changes.
- `prove` — After command approval, try eligible file removals in a disposable copy and regenerate the report with proof results.
- `apply-approved` — After the cleanup checkpoint is approved, modify only the named real files, run the approved checks, and report the final diff.

Do not jump directly from invocation to `apply-approved`, even if the request says “clean everything,” “fix it,” or “do it.”

## Run the workflow

### 1. Map the repository

- Identify languages, source roots, entry points, package manifests, framework conventions, generated directories, tests, build commands, and the current Git state.
- Exclude dependencies, build output, caches, vendored code, snapshots, coverage, minified bundles, and generated files unless the user explicitly puts them in scope.
- Read `references/detection-playbook.md` for language and framework false-positive checks.

### 2. Generate deterministic evidence

Run the bundled scanner from the skill directory:

```bash
python3 scripts/analyze_code_rot.py /absolute/path/to/project \
  outputs/code-rot-cleaner/analysis.json
```

Use its candidates as leads, not truth. Supplement them with existing project-native analyzers when already installed, such as Knip, ts-prune, Vulture, deadcode, compiler warnings, or coverage. Do not install a new analyzer or dependency without approval.

Inspect every top candidate manually. Search routes, package scripts, config, templates, string references, registries, plugin loading, reflection, dependency injection, glob imports, generated imports, CLI entry points, migrations, tests, and deployment files. Git age alone is not evidence of dead code.

### 3. Classify conservatively

Use exactly these user-facing states:

- `SAFE TO REMOVE` — Strong static evidence, no dynamic or convention-based reference, eligible removal passed approved commands in a disposable copy, and residual risk is low.
- `REVIEW` — Plausibly removable but unproved, dynamically reachable, convention-sensitive, duplicated rather than dead, or dependent on incomplete tests.
- `KEEP` — A reference was found, baseline or removal proof failed for a relevant reason, the file is an entry point, or the evidence was rejected.

Static inspection alone cannot produce `SAFE TO REMOVE`. A passing build alone also cannot produce it. Unused exports and dependencies remain `REVIEW` unless project-native tooling and a focused proof support the exact removal.

Record manual rejections or unresolved caveats in `outputs/code-rot-cleaner/review.json`:

```json
{"decisions": [{"candidate_id": "CRT-004", "status": "KEEP", "reason": "Loaded by the plugin registry in config/plugins.json."}]}
```

Manual review may downgrade a candidate to `KEEP` or leave it at `REVIEW`; it may never promote one to `SAFE TO REMOVE`.

### 4. Ask before running project code

Show the exact commands, why they are relevant, estimated scope, and the fact that they will execute inside a temporary copy. Stop and request approval. After approval, run:

```bash
python3 scripts/prove_candidates.py \
  /absolute/path/to/project \
  outputs/code-rot-cleaner/analysis.json \
  outputs/code-rot-cleaner/proof.json \
  --confirm-run-project-code \
  --command "npm test" \
  --command "npm run build"
```

Use the smallest credible command set. The script first proves the untouched baseline, then tests each eligible candidate in a fresh disposable copy. If the baseline fails, do not classify a deletion from that command. Never weaken or skip checks merely to produce green proof.

### 5. Generate the native report

Create the Markdown report and cleanup CSV:

```bash
python3 scripts/generate_report.py \
  outputs/code-rot-cleaner/analysis.json \
  outputs/code-rot-cleaner/CODE-ROT-REPORT.md \
  outputs/code-rot-cleaner/cleanup-plan.csv \
  --proof outputs/code-rot-cleaner/proof.json \
  --review outputs/code-rot-cleaner/review.json
```

Omit `--proof` in `report-only` mode and omit `--review` when there are no manual decisions. Open or link `CODE-ROT-REPORT.md` directly in Codex. It must lead with the amount of code identified, explain what was and was not proven, show candidate IDs, and keep limitations visible.

Read `references/evidence-schema.md` only when consuming or extending the JSON format.

### 6. Apply only approved cleanup

- Reconfirm the candidate IDs and exact paths.
- Prefer one small causal batch, beginning with `SAFE TO REMOVE` candidates.
- Use targeted edits rather than a blanket cleanup command.
- Update manifests and lockfiles only when specifically approved.
- Run the approved focused checks and the broadest relevant suite.
- Inspect the final diff for scope creep, public API changes, lost side effects, and unrelated formatting.
- If checks fail, restore only the skill's own approved edits and classify the candidate `KEEP` or `REVIEW`; never discard unrelated user work.

## Report honestly

Lead with one of:

- `REPORT READY` — Candidates ranked; real project unchanged.
- `PROOF COMPLETE` — Disposable-copy experiments completed; real project unchanged.
- `CLEANUP VERIFIED` — Approved real changes applied and relevant checks passed.
- `INCONCLUSIVE` — Evidence or baseline was insufficient.

Then provide removable LOC and bytes by status, the strongest candidate, commands and results, limitations, and a link to `outputs/code-rot-cleaner/CODE-ROT-REPORT.md`. Never claim the entire codebase is clean merely because the scanned scope produced no candidate.
