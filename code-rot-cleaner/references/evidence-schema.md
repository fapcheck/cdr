# Evidence schema 2.0

Use this reference when consuming or extending `analysis.json`, `proof.json`, or `cleanup-plan.csv`.

## Analysis

`analysis.json` contains:

- `schema_version`: `2.0`.
- `mode`: always `report-only` from `audit.py`.
- `project_root` and `generated_at`.
- `scope`: extensions, exclusions, scanned file/LOC/byte counts, and skipped symlinks.
- `summary`: candidate, proof-eligible, proven-removable, review-required, and category counts.
- `ecosystems`: TypeScript/JavaScript package manager, workspaces, aliases, references, Next/Vite detection, plus Python pyproject, requirements, namespace-package, and CLI-entry data.
- `available_external_tools`: discovered executable paths or `null`; discovery never installs a tool.
- `tool_runs`: detected executable, version, exact command, execution mode, sanitized environment policy, exit result, stderr, redaction flag, limitations, and the fail-closed collector status.
- `git_history`: optional supporting collector status.
- `limitations`: scan-wide uncertainty.

Every `candidates[]` item contains:

- `candidate_id`: stable run-local ID such as `CRT-001`.
- `category`: finding type.
- `affected`: `kind` plus exact `path`, `symbol`, `dependency`, and optional `line`.
- `evidence_sources[]`: structured `family`, `source`, `signal`, and `detail` values.
- `confidence`: `high`, `medium`, or `low`.
- `risk`: `low`, `medium`, or `high`.
- `unresolved_questions[]`.
- `proof_status`: initially `NOT_RUN`.
- `recommendation`: initially `REVIEW`.

External collector status is exactly one of:

- `available and succeeded`
- `unavailable`
- `failed`
- `unsupported output schema`
- `skipped because approval was not granted`

Only `available and succeeded` evidence may be merged or count as a mature family. Additional documented fields are tolerated, but malformed JSON, wrong top-level types, missing required fields, and unknown schemas are rejected.

`proof.json` includes `analysis_sha256` and must match the exact analysis bytes, `project_root`, candidate ID and path, and the full approved/baseline/candidate command identities. A missing or mismatched binding is `INCONCLUSIVE`, never removal proof.
- `proof_eligible`: whether file-level disposable proof is mechanically supported.
- `safety`: known state for dynamic usage, external API, and convention roles.
- `why_suspicious` and `why_might_still_be_needed`.
- `loc` and `bytes`: possible impact, not promised savings.

Multiple observations from the built-in fallback still count as one evidence family. Git is supporting evidence and does not count toward removal confidence.

## Proof

`proof.json` contains:

- `schema_version`: `2.0`.
- project, analysis, generation, command-policy, and copy-exclusion metadata.
- `commands_requested[]`: check kind, exact display command, and execution mode.
- `baseline`: untouched-copy command results and aggregate pass state.
- `results[]`: candidate ID, removed path, outcome, duration, and command results.
- `limitations[]`.

Each command result records kind, exact command, argv when shell-free, execution mode, environment policy, exit code, timeout, duration, masked output, redaction state, and pass state.

Outcomes are:

- `PASSED_IN_DISPOSABLE_COPY`
- `FAILED_AFTER_REMOVAL`
- `INCONCLUSIVE`

## Recommendation derivation

`report.py` can derive `SAFE TO REMOVE` only when all conditions hold:

1. At least two non-Git evidence families agree.
2. At least one family is a mature analyzer or approved project-native source.
3. Confidence is high and risk is low.
4. No unresolved question remains.
5. No dynamic, convention, or external-API concern is known.
6. The untouched baseline passed.
7. The one-candidate fresh-copy removal passed every approved relevant check.

A removal check failure becomes `KEEP`. Any missing condition remains `REVIEW`. Manual decisions may downgrade to `KEEP` or retain `REVIEW`; they cannot promote to `SAFE TO REMOVE`.

## Optional manual review

`review.json` contains `decisions[]`. Each decision requires:

- `candidate_id`
- `recommendation`: only `KEEP` or `REVIEW`
- `reason`: the exact evidence supporting the decision

`report.py --review review.json` applies these downgrades before generating Markdown and CSV. Manual review cannot produce `SAFE TO REMOVE`.
