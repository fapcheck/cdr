---
name: code-rot-cleaner
description: Audit code a repository may no longer need by combining conservative built-in analysis, existing Knip, Vulture, Ruff, or deptry evidence, optional Git context, and one-candidate disposable-copy proof. Use when Codex is asked to investigate dead code, orphan files, exports or dependencies with no usage evidence, duplicate implementations, stale commented code, removable LOC, repository bloat, cleanup risk, or code rot in JavaScript, TypeScript, Python, workspaces, or monorepos. Default to report-only mode, never treat regex or age as removal proof, require approval before project commands, and require exact candidate-ID approval before real edits.
---

# Code Rot Audit

Audit possible cleanup like a senior engineer: collect independent evidence, expose uncertainty, prove one removal at a time in disposable copies, and optimize against incorrect deletion. Keep `SKILL.md` as the behavioral entry point and keep the real repository unchanged until the user explicitly approves exact candidates.

## Enforce report-only mode

Treat `Use $code-rot-cleaner` as permission only to inspect the repository and write audit artifacts.

- Permit repository inspection, built-in static collection, read-only Git evidence, and report generation.
- Do not delete, edit, rename, format, uninstall, regenerate lockfiles, or reorganize project files.
- Do not run tests, builds, typechecks, linters, package managers, project-local analyzers, or configuration that can execute project code without explicit approval.
- Do not install or download Knip, Vulture, Ruff, deptry, or any other analyzer. Use an existing executable only.
- Preserve dirty worktrees and unrelated user changes. Never reset, clean, stash, or rewrite history.
- Store artifacts in `outputs/code-rot-cleaner/` unless the user chooses another path.

## Run the audit

### 1. Map the repository

Inspect Git status, languages, manifests, lockfiles, source roots, entry points, frameworks, generated paths, tests, and build commands. Read [references/detection-playbook.md](references/detection-playbook.md) before judging candidates in JavaScript, TypeScript, Python, or a framework-driven repository.

Run the dependency-free collector from this skill directory:

```bash
python3 scripts/audit.py /absolute/path/to/project \
  /absolute/path/to/project/outputs/code-rot-cleaner/analysis.json
```

This command stays in report-only mode. Its built-in import graph, manifest search, convention map, and text search are one fallback evidence family. They can produce `REVIEW`, never `SAFE TO REMOVE`.

Use optional read-only Git context only when it is relevant:

```bash
python3 scripts/audit.py /absolute/path/to/project \
  /absolute/path/to/project/outputs/code-rot-cleaner/analysis.json \
  --include-git-history
```

Treat file age, last modification, commit frequency, and blame context only as supporting evidence. Old code is not proof of dead code.

### 2. Add mature analyzer evidence only after approval

Prefer existing ecosystem tools to reimplementing their analysis:

- JavaScript or TypeScript: Knip, TypeScript compiler or project-native typecheck.
- Python: Vulture, Ruff, and deptry.
- Built-in collector: fallback and cross-check only.

First show the exact analyzer command, explain that project configuration can execute code, and request approval. Then name only approved, already-installed tools:

```bash
python3 scripts/audit.py /absolute/path/to/project \
  /absolute/path/to/project/outputs/code-rot-cleaner/analysis.json \
  --allow-tool knip --allow-tool vulture
```

The script uses argv execution without a shell, a sanitized allow-list environment, secret masking, and no automatic installation. Record missing tools as unavailable instead of downloading them. Treat analyzer disagreement as `REVIEW`.

### 3. Inspect every candidate

For each candidate, confirm:

- `candidate_id`, category, and exact affected file, symbol, or dependency;
- independent evidence sources rather than multiple regex observations;
- confidence, risk, unresolved questions, and proof status;
- route, plugin, migration, worker, script, generated, config-driven, package-export, CLI-entry, dynamic-import, barrel-export, alias, workspace, and external-API concerns;
- why it is suspicious and why it might still be needed.

Never describe a candidate as unused before proof. Say `No usage evidence found.`

Use only these recommendations:

- `SAFE TO REMOVE`: multiple independent evidence families including a mature analyzer; no known dynamic, convention, or external-API concern; low risk; successful untouched baseline; successful one-candidate disposable-copy proof; all approved relevant checks passed.
- `REVIEW`: any incomplete, conflicting, dynamic, convention-sensitive, public, or unproved case.
- `KEEP`: a reference was found, manual review rejected the claim, or an approved check failed after isolated removal.

Regex evidence, Git age, a green build alone, or manual optimism can never promote a candidate.

Record confirmed manual downgrades in an optional sidecar:

```json
{
  "decisions": [
    {
      "candidate_id": "CRT-004",
      "recommendation": "KEEP",
      "reason": "Loaded by config/plugins.json."
    }
  ]
}
```

Save it as `outputs/code-rot-cleaner/review.json`. Manual decisions may use only `KEEP` or `REVIEW`; they can never promote a candidate.

### 4. Ask before proof commands

Show the exact tests, build, typecheck, and lint commands; why they cover the candidate; expected duration; and the fact that they execute project code in temporary copies. Stop for approval.

After approval, encode commands as argv JSON so shell parsing is avoided:

```bash
python3 scripts/proof.py \
  /absolute/path/to/project \
  /absolute/path/to/project/outputs/code-rot-cleaner/analysis.json \
  /absolute/path/to/project/outputs/code-rot-cleaner/proof.json \
  --confirm-run-project-code \
  --candidate-id CRT-001 \
  --command-json '{"kind":"typecheck","argv":["npm","run","typecheck"]}' \
  --command-json '{"kind":"test","argv":["npm","test"]}'
```

The proof workflow must:

1. Validate project paths and reject symlink escapes.
2. Create a disposable untouched baseline copy.
3. Run the approved checks with a sanitized environment.
4. Stop candidate evaluation if the baseline fails.
5. Create a fresh copy per candidate.
6. Remove only that candidate in the copy.
7. Run the same checks and record commands, execution mode, environment policy, output redaction, and limitations.

Use `--shell-command` only when argv cannot express a required command. Explain the risk and require a separate approval represented by `--confirm-shell-execution`.

### 5. Generate Codex-native artifacts

```bash
python3 scripts/report.py \
  /absolute/path/to/project/outputs/code-rot-cleaner/analysis.json \
  /absolute/path/to/project/outputs/code-rot-cleaner/CODE-ROT-REPORT.md \
  /absolute/path/to/project/outputs/code-rot-cleaner/cleanup-plan.csv \
  --proof /absolute/path/to/project/outputs/code-rot-cleaner/proof.json \
  --review /absolute/path/to/project/outputs/code-rot-cleaner/review.json
```

Omit `--proof` before approved proof and omit `--review` when there are no manual decisions.

Create:

- `CODE-ROT-REPORT.md`
- `analysis.json`
- `proof.json` after approved proof
- `cleanup-plan.csv`

The report must summarize scanned files, candidates, proven-removable count, and review-required count. For each candidate show ID, type, location, evidence, confidence, risk, suspicious rationale, possible legitimate use, proof, and recommendation. Keep command, execution mode, environment policy, and limitations visible.

Read [references/evidence-schema.md](references/evidence-schema.md) only when consuming or extending JSON.

## Stop before real changes

Present `Proposed cleanup` with exact candidate IDs, exact files or manifest entries, expected impact, relevant verification commands, and residual risks. Then stop.

Require an approval such as:

```text
Approve CRT-001, CRT-004
```

Approval applies only to the displayed IDs and paths. Ask again if evidence, commands, or scope changes. After approval, edit only those items, update manifests or lockfiles only when explicitly included, run approved checks, inspect the final diff, and report remaining uncertainty.

Lead the final audit response with `REPORT READY`, `PROOF COMPLETE`, `CLEANUP VERIFIED`, or `INCONCLUSIVE`. Never claim the whole repository is clean merely because the scanned scope produced no candidate.
