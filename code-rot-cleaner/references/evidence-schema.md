# Evidence schema

The bundled scripts exchange JSON with `schema_version: "1.0"`.

## Analysis

- `project_root`: absolute scanned root.
- `generated_at`: UTC timestamp.
- `scope`: extensions, excluded directories, file count, LOC, and bytes.
- `summary`: candidate counts and potential removable size.
- `candidates[]`:
  - `id`: stable run-local ID such as `CRT-001`.
  - `category`: `orphan-file`, `unused-export`, `unused-dependency`, `duplicate-file`, or `commented-code`.
  - `subject`: repository-relative file, symbol, dependency, or duplicate pair.
  - `path`: primary repository-relative path when applicable.
  - `line`: optional 1-based line.
  - `confidence`: `high`, `medium`, or `low`.
  - `initial_status`: always `REVIEW` from the static scanner.
  - `risk`: `low`, `medium`, or `high`.
  - `proof_eligible`: whether the disposable-copy script can test a file removal.
  - `loc` and `bytes`: potential removable size.
  - `evidence[]` and `caveats[]`: human-readable signals and limitations.
- `limitations[]`: scan-wide caveats.

## Proof

- `project_root`, `analysis_file`, `generated_at`, and `commands` identify the experiment.
- `baseline`: command results for the intact disposable copy and `passed`.
- `results[]`: candidate ID, removed path, `outcome`, command results, and duration.
- `outcome` is `PASSED_IN_DISPOSABLE_COPY`, `FAILED_AFTER_REMOVAL`, `SKIPPED`, or `INCONCLUSIVE`.
- Command results include command, exit code, duration, timeout, and truncated output.

## Final status derivation

The report generator derives `SAFE TO REMOVE` only when a high-confidence, low-risk, proof-eligible file candidate has `PASSED_IN_DISPOSABLE_COPY` and the baseline passed. A proof failure becomes `KEEP`. All other cases stay `REVIEW`.

An optional review JSON contains `decisions[]` with `candidate_id`, `status`, and `reason`. Manual decisions may use only `KEEP` or `REVIEW`; they cannot promote a candidate to `SAFE TO REMOVE`.
