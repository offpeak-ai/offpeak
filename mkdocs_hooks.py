"""Build hook: mirror root-level docs into the site without duplicating them.

SPEC.md is the artifact people cite and link to, so it stays at the repo root.
Copying it in at build time means the site renders the same file the repo
serves, rather than a second copy that quietly drifts out of date.

docs/spec.md is generated and gitignored — do not edit it.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent


def on_pre_build(config, **kwargs) -> None:
    spec = ROOT / "SPEC.md"
    if not spec.exists():  # pragma: no cover - repo always ships it
        raise FileNotFoundError(f"SPEC.md missing at {spec}; the docs nav expects it")
    (ROOT / "docs" / "spec.md").write_text(spec.read_text())
