"""Layer-boundary checker: `polsia.core` must not import upper layers.

Run: python scripts/check_layering.py  (exit 1 on violation)
"""

from __future__ import annotations

import ast
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent / "polsia"
FORBIDDEN_IN_CORE = {"polsia.api", "polsia.adapters", "polsia.services", "polsia.infra"}


def imports_of(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
    return found


def main() -> int:
    violations: list[str] = []
    for py in ROOT.rglob("*.py"):
        rel = py.relative_to(ROOT).as_posix()
        if rel.startswith("core/"):
            for mod in imports_of(py):
                if any(mod == f or mod.startswith(f + ".") for f in FORBIDDEN_IN_CORE):
                    violations.append(f"{rel}: imports {mod}")
    for v in violations:
        print(f"LAYER VIOLATION: {v}", file=sys.stderr)
    print(f"layering check: {len(violations)} violation(s)")
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
