# Codex Code Rot Cleaner

A conservative Codex Skill for auditing code a repository may no longer need. It collects multiple evidence signals, keeps uncertainty visible, proves eligible removals one at a time in disposable copies, and requires exact approval before touching the real project.

This remains a Codex-native Skill. `SKILL.md` is the behavioral entry point, `agents/openai.yaml` supplies UI metadata, and the Python scripts provide deterministic collection, proof, and reporting helpers. It is not a standalone cleanup application or an automatic deletion tool.

## Safety model

Calling `$code-rot-cleaner` starts in report-only mode.

Allowed by default:

- inspect repository files and Git state;
- run the dependency-free built-in collector;
- collect optional read-only Git context;
- write reports under `outputs/code-rot-cleaner/`.

Requires explicit approval:

- running existing Knip, Vulture, Ruff, or deptry executables;
- running project tests, builds, typechecks, linters, or shell commands;
- applying exact candidate removals to the real repository.

The built-in scanner is a fallback. Its regex and import-graph observations can never independently produce `SAFE TO REMOVE`. Git age is supporting context only. No static audit guarantees perfect reachability proof; intentional uncertainty protects dynamic, framework, external, and rare operational paths.

## Install

```bash
npx --yes codex-code-rot-cleaner@latest
```

Or install manually:

```bash
git clone https://github.com/Kappaemme-git/codex-code-rot-cleaner.git
mkdir -p ~/.codex/skills
cp -R codex-code-rot-cleaner/code-rot-cleaner ~/.codex/skills/code-rot-cleaner
```

Restart Codex after installation.

## Use

Open a repository in Codex and invoke:

```text
Use $code-rot-cleaner to audit this repository for code rot. Stay in report-only mode and do not change project files.
```

Codex first maps entry points, aliases, workspaces, routes, package exports, Python CLI entries, dynamic loading, generated areas, and other false-positive surfaces. It can compare the fallback evidence with already-installed ecosystem analyzers after showing the exact command and receiving approval.

Before proof, Codex asks permission for exact project checks. The proof helper creates an untouched baseline copy, then a fresh copy for each candidate, removes only that candidate, and repeats the same checks. Commands use argv without a shell by default, a sanitized environment, secret masking, path validation, and symlink-escape protection.

Before any real cleanup, Codex stops at `Proposed cleanup` and requires an exact response such as:

```text
Approve CRT-001, CRT-004
```

## Skill architecture

```text
code-rot-cleaner/
|-- SKILL.md
|-- agents/openai.yaml
|-- references/
|   |-- detection-playbook.md
|   `-- evidence-schema.md
`-- scripts/
    |-- audit.py
    |-- proof.py
    |-- report.py
    |-- security.py
    `-- collectors/
        |-- builtin.py
        |-- typescript.py
        |-- python.py
        |-- dependencies.py
        `-- git_history.py
```

The legacy script names remain as compatibility wrappers.

## Outputs

```text
outputs/code-rot-cleaner/analysis.json
outputs/code-rot-cleaner/proof.json
outputs/code-rot-cleaner/CODE-ROT-REPORT.md
outputs/code-rot-cleaner/cleanup-plan.csv
```

Every candidate records an ID, affected item, evidence sources, confidence, risk, unresolved questions, proof status, and recommendation. The report uses `No usage evidence found` until the full removal standard is satisfied.

When manual inspection confirms a false positive, Codex can record a `KEEP` or `REVIEW` decision with its reason in an optional `review.json` sidecar and pass it to `report.py --review`. Manual review cannot promote a candidate.

## Development

```bash
npm test
```

Tests use local fixtures and mocked external collectors; they do not require globally installed analysis tools.

## License

MIT
