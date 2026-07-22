# Codex Code Rot Cleaner

A conservative Codex Skill for auditing code a repository may no longer need. It collects multiple evidence signals, keeps uncertainty visible, proves eligible removals one at a time in disposable copies, and requires exact approval before touching the real project.

This remains a Codex-native Skill. `SKILL.md` is the behavioral entry point, `agents/openai.yaml` supplies UI metadata, and the Python scripts provide deterministic collection, proof, and reporting helpers. It is not a standalone cleanup application or an automatic deletion tool.

## Safety model

Calling `$cdr` starts in report-only mode. `$code-rot-cleaner` remains supported as a legacy alias and delegates to the same canonical workflow.

Allowed by default:

- inspect repository files and Git state;
- run the dependency-free built-in collector;
- collect optional read-only Git context;
- write reports under `outputs/code-rot-cleaner/`.

Requires explicit approval:

- running existing Knip, Vulture, Ruff, or deptry executables;
- running project tests, builds, typechecks, linters, or shell commands;
- applying exact candidate removals to the real repository.

The built-in scanner is a fallback. Its regex and import-graph observations can never independently produce `SAFE TO REMOVE`. Git age is supporting context only. Knip is the primary JS/TS project-graph authority. A second custom TypeScript Compiler API graph is intentionally not maintained because it would duplicate Knip's aliases, workspaces, exports, plugins, and framework model while creating inconsistent results; project-native typechecks remain proof commands, not a competing reachability collector.

For Python, Ruff remains complementary local lint evidence. Optional Vulture analysis uses its Python API through the interpreter associated with an already-installed Vulture executable, scans the selected project Python files together at confidence 60, and normalizes functions, classes, methods, properties, and unreachable code. Vulture is never installed automatically and never supplies removal proof by itself.

External adapters record executable, version, exit status, sanitized stderr, and one of five states: `available and succeeded`, `unavailable`, `failed`, `unsupported output schema`, or `skipped because approval was not granted`. Documented JSON structures are validated fail-closed; malformed output is not interpreted as an empty successful scan.

## Install

```bash
npx --yes @supaboiclean/cdr@0.2.2
```

The npm package name is `@supaboiclean/cdr`, and its canonical maintained repository is [fapcheck/cdr](https://github.com/fapcheck/cdr). This is a maintained and extended distribution of [the original project](https://github.com/Kappaemme-git/codex-code-rot-cleaner) by Francesco Mistero, released under the MIT License with the original author attribution preserved. The previous npm package `codex-code-rot-cleaner` is not maintained or published by this release.

Or install manually:

```bash
git clone https://github.com/fapcheck/cdr.git
mkdir -p ~/.codex/skills
cp -R cdr/cdr ~/.codex/skills/cdr
cp -R cdr/code-rot-cleaner ~/.codex/skills/code-rot-cleaner
```

Restart Codex after installation.

## Use

Open a repository in Codex and invoke:

```text
Use $cdr to audit this repository for code rot. Stay in report-only mode, do not run project commands, and do not change project files.
```

The supported short invocation is `$cdr`. `$code-rot-cleaner` remains available for compatibility. In clients that surface enabled skills in a slash picker, typing `/` may show the enabled `cdr` skill for discovery; this does not mean literal `/cdr` is a supported custom command. Custom prompts, where available, use `/prompts:<name>`, and this package does not install one.

The npm executable named `cdr` only installs the canonical `cdr` skill directory and the delegating `code-rot-cleaner` compatibility directory. No standalone CLI cleanup behavior was added.

Codex first maps entry points, aliases, workspaces, routes, package exports, Python CLI entries, dynamic loading, generated areas, and other false-positive surfaces. It can compare the fallback evidence with already-installed ecosystem analyzers after showing the exact command and receiving approval.

Before proof, Codex asks permission for exact project checks. The proof helper creates an untouched baseline copy, then a fresh copy for each candidate, removes only that candidate, and repeats the same checks. Commands use argv without a shell by default, a sanitized environment, secret masking, path validation, and symlink-escape protection. The proof is cryptographically bound to the exact analysis file, project root, candidate path, and approved command set before it can influence a recommendation.

This is conservatively designed, not a sandbox or a production-grade isolation boundary:

- network isolation is not enforced;
- approved project commands can execute arbitrary code with the current user's host filesystem and process permissions;
- a sanitized environment removes common credential variables but is not complete isolation;
- static analysis cannot observe every dynamic, reflective, external, framework, or operational use;
- passing checks prove only the behavior those checks exercised;
- timeouts are not guaranteed to terminate every descendant process on every platform.

Before any real cleanup, Codex stops at `Proposed cleanup` and requires an exact response such as:

```text
Approve CRT-001, CRT-004
```

## Skill architecture

```text
cdr/
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
    |-- vulture_adapter.py
    `-- collectors/
        |-- builtin.py
        |-- typescript.py
        |-- python.py
        |-- contracts.py
        |-- dependencies.py
        `-- git_history.py

code-rot-cleaner/
`-- SKILL.md  # minimal legacy alias delegating to $cdr
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
