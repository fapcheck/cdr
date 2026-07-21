---
name: code-rot-cleaner
description: Legacy compatibility alias for the cdr code-rot audit skill. Use only when a user explicitly invokes $code-rot-cleaner; delegate to the canonical $cdr workflow while preserving its report-only defaults and approval boundaries. New documentation should use $cdr.
---

# Legacy Code Rot Cleaner alias

Immediately load and follow the installed sibling skill at `../cdr/SKILL.md`. Treat `$cdr`, its scripts, references, and approval rules as the canonical implementation and source of truth.

- Preserve `$cdr` report-only defaults. Do not run project commands or change project files without the approvals required by the canonical skill.
- Do not emulate or duplicate the analyzer when the sibling `cdr` skill is unavailable. Stop and ask the user to reinstall the package.
- Keep `$code-rot-cleaner` working for compatibility, but use `$cdr` in all new documentation and examples.
