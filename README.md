# Codex Code Rot Cleaner

A Codex skill that finds code your project may no longer need, separates static suspicion from credible removal evidence, proves eligible file deletions in disposable copies, and asks for exact approval before touching the real project.

It looks for orphan modules, unused dependencies and exports, exact duplicate implementations, and stale commented code. Every finding is classified as `SAFE TO REMOVE`, `REVIEW`, or `KEEP` in a native Markdown report that opens directly in Codex and renders on GitHub.

## What it does

- Maps JavaScript, TypeScript, and Python source files and entry points
- Finds files with no resolved inbound import
- Checks package manifests, conventional routes, scripts, config, and string references
- Flags unused dependencies and exports conservatively
- Detects exact duplicate source files and code-like comment blocks
- Estimates removable LOC and bytes
- Runs approved build and test commands against deletions in fresh disposable copies
- Keeps the real project unchanged during scanning and proof
- Requires candidate-by-candidate approval before real cleanup
- Creates a Markdown report and CSV cleanup plan

## Install

```bash
npx --yes codex-code-rot-cleaner@latest
```

Restart Codex after installation.

## Use

Open a software project in Codex and call the skill:

```text
Use $code-rot-cleaner to find code this project no longer needs. Start in report-only mode and do not change project files.
```

You can also ask naturally:

```text
Find orphan files, unused dependencies, duplicate implementations, and stale exports in this codebase. Show me what is safe to remove, what needs review, and how many LOC could be deleted.
```

The first pass only inspects the repository and writes the report. Before running project-controlled build or test commands, Codex shows the exact commands and waits for approval. Proof removes one candidate at a time in a disposable copy. Before any real cleanup, Codex shows exact candidate IDs and waits for a second approval.

## Output

```text
outputs/code-rot-cleaner/analysis.json
outputs/code-rot-cleaner/review.json
outputs/code-rot-cleaner/proof.json
outputs/code-rot-cleaner/CODE-ROT-REPORT.md
outputs/code-rot-cleaner/cleanup-plan.csv
```

The report includes:

- Total scanned source files, LOC, and bytes
- `SAFE TO REMOVE`, `REVIEW`, and `KEEP` counts
- Potential removable LOC and storage
- Evidence, caveats, confidence, and risk for every candidate
- Baseline and disposable-copy proof results
- Exact candidate IDs for the approval checkpoint

## Safety model

Calling the skill allows inspection and report generation only. It does not allow source edits, dependency removal, formatting, or cleanup. Repository-controlled commands require approval because they can execute arbitrary project code. Real changes require a separate approval covering exact IDs and files.

No static analyzer can prove every dynamic, reflective, framework, operational, platform-specific, or external use. A green build is evidence only for the behavior that build exercises. The skill keeps these limitations visible instead of turning weak signals into deletion claims.

## Manual installation

```bash
git clone https://github.com/Kappaemme-git/codex-code-rot-cleaner.git
mkdir -p ~/.codex/skills
cp -R codex-code-rot-cleaner/code-rot-cleaner ~/.codex/skills/code-rot-cleaner
```

## License

MIT
