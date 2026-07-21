# Detection playbook

Use this checklist when manually reviewing candidates. Missing ordinary imports is a lead, not proof.

## Evidence ladder

1. Map source roots, manifests, package boundaries, entry points, generated areas, and Git state.
2. Resolve ordinary imports, aliases, relative imports, re-exports, and literal dynamic imports.
3. Search paths, basenames, symbols, registries, templates, package scripts, config, and deployment files.
4. Exclude framework, plugin, migration, worker, script, test-discovery, generated, and CLI conventions.
5. Compare the built-in fallback with an existing mature analyzer.
6. Inspect public API and external-consumer risk.
7. Establish an untouched disposable-copy baseline with approved relevant checks.
8. Remove one candidate in a fresh copy and repeat exactly those checks.

No single rung proves removal. Incomplete tests, optional features, rare operations, platform branches, and downstream consumers reduce confidence.

## JavaScript and TypeScript

Check:

- npm, pnpm, yarn, lockfiles, package-manager declarations, and lifecycle scripts;
- workspaces, monorepo package boundaries, package `main`, `module`, `browser`, `bin`, `exports`, `files`, peers, optionals, and side effects;
- `tsconfig` `baseUrl`, `paths`, `references`, and framework-specific config;
- barrel modules and re-exports;
- literal and non-literal `import()` or `require()` calls;
- Next.js app/pages/API routes, middleware, layouts, loaders, Vite entry HTML and config, workers, Storybook, migrations, plugins, seeds, and generated modules;
- public packages whose exports can be used outside the repository.

Prefer Knip and project-native TypeScript checks when already available. Knip config can execute JavaScript or TypeScript, so request approval before running it. Never use an autofix flag.

## Python

Check:

- regular, relative, and dynamic imports;
- packages with `__init__.py`, namespace packages without it, and multiple source roots;
- `pyproject.toml`, requirements files, dependency groups, setup metadata, and package-data loading;
- console and GUI scripts, plugin entry-point groups, management commands, migrations, workers, and test discovery;
- importlib, decorators, registries, reflection, string class paths, and framework conventions;
- downstream package consumers.

Prefer Vulture for symbol leads, Ruff for related local lint evidence, and deptry for dependency evidence when already installed. Their findings remain `REVIEW` until corroborated and proved.

## Git context

Use file age, first/last modification, commit frequency, and blame context to understand ownership and intent. Inspect the introducing change when it can clarify a migration or compatibility path. Never infer dead code from age, low churn, or an old author.

## Proof quality

Match commands to the candidate's actual surface:

- typecheck for type reachability;
- build for packaging and bundling;
- tests for behavior they actually exercise;
- lint for supported local consistency checks.

A frontend build does not prove a worker is irrelevant. Unit tests do not prove packaging. A typecheck does not execute side effects. Treat a failing untouched baseline separately and do not evaluate removals against it.

## Security review

- Use argv execution and `shell=False` by default.
- Sanitize the environment and mask secret values in recorded output.
- Reject traversal and symlink escapes before copying or removing candidates.
- Show exact commands and limitations before approval.
- Treat project configuration, plugins, package scripts, and test hooks as executable project code.
- Do not install analyzers, contact cloud APIs, or require a database.
