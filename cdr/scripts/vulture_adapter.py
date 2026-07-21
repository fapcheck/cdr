#!/usr/bin/env python3
"""Serialize Vulture's Python API into the skill's private JSON contract."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path)
    parser.add_argument("manifest", type=Path, help="JSON array of Python source paths")
    parser.add_argument("--min-confidence", type=int, default=60)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    import vulture

    project = args.project.resolve(strict=True)
    source_paths = json.loads(args.manifest.read_text(encoding="utf-8"))
    if not isinstance(source_paths, list) or not all(isinstance(path, str) for path in source_paths):
        raise SystemExit("Invalid source manifest.")
    analyzer = vulture.Vulture()
    analyzer.scavenge([str((project / path).resolve()) for path in source_paths])
    items = []
    for item in analyzer.get_unused_code(min_confidence=args.min_confidence):
        items.append({
            "name": item.name,
            "type": item.typ,
            "filename": str(item.filename),
            "first_lineno": item.first_lineno,
            "last_lineno": item.last_lineno,
            "size": item.size,
            "confidence": item.confidence,
        })
    print(json.dumps({
        "schema_version": "vulture-api-v1",
        "tool_version": importlib.metadata.version("vulture"),
        "items": items,
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
