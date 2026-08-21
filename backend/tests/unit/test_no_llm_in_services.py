"""Architectural boundary test.

Deterministic services must never import an LLM client. This is the boundary the whole
design rests on, so it is asserted mechanically rather than trusted to review.

Applies to app/services/, app/assurance/ and app/policy/: none of them may reason with a
model. The three reasoning agents live in app/agents/ and are the only exception.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[2]

FORBIDDEN_MODULES = {"groq", "openai", "anthropic", "litellm", "ollama"}
FORBIDDEN_PREFIXES = ("app.llm",)

PROTECTED_DIRS = ["app/services", "app/assurance", "app/policy", "app/orchestrator"]


def _imported_modules(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def _protected_files() -> list[pathlib.Path]:
    files: list[pathlib.Path] = []
    for rel in PROTECTED_DIRS:
        files.extend(sorted((BACKEND / rel).rglob("*.py")))
    assert files, "no protected files discovered — check PROTECTED_DIRS"
    return files


@pytest.mark.parametrize("path", _protected_files(), ids=lambda p: p.name)
def test_no_llm_imports(path: pathlib.Path):
    imported = _imported_modules(path)

    banned = {m for m in imported if m.split(".")[0] in FORBIDDEN_MODULES}
    assert not banned, f"{path.relative_to(BACKEND)} imports an LLM client: {banned}"

    internal = {m for m in imported if m.startswith(FORBIDDEN_PREFIXES)}
    assert not internal, f"{path.relative_to(BACKEND)} imports the LLM layer: {internal}"


def test_agents_are_the_only_reasoning_layer():
    """Sanity check that the protected set does not accidentally include app/agents."""
    assert not any("agents" in str(p) for p in _protected_files())
