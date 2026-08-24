"""Architectural boundary test.

Deterministic services must never import an LLM client. This is the boundary the whole
design rests on, so it is asserted mechanically rather than trusted to review.

Applies to app/services/, app/assurance/ and app/policy/: none of them may reason with a
model. The three reasoning agents live in app/agents/ and are the only exception.

The orchestrator (app/orchestrator/) may call agents through DEFERRED imports inside function
bodies (Phase 3), but must never import them at module scope — otherwise `LLM_MODE=off` would
require the SDK to be importable just to start, which defeats the design rule that the system
works without any model.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[2]

FORBIDDEN_MODULES = {"groq", "openai", "anthropic", "litellm", "ollama"}
FORBIDDEN_PREFIXES = ("app.llm",)

# Services, assurance and policy: no LLM imports at all (any scope).
STRICT_DIRS = ["app/services", "app/assurance", "app/policy"]
# Orchestrator: no LLM imports at MODULE scope (deferred inside methods is fine for Phase 3).
MODULE_SCOPE_DIRS = ["app/orchestrator"]


def _imported_modules(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def _module_scope_imports(path: pathlib.Path) -> set[str]:
    """Only top-level imports — not those inside function/method bodies."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
        # Also check class-level (methods are inside classes at the top level)
        if isinstance(node, ast.ClassDef):
            for class_node in ast.iter_child_nodes(node):
                if isinstance(class_node, ast.Import):
                    found.update(alias.name for alias in class_node.names)
                elif isinstance(class_node, ast.ImportFrom) and class_node.module:
                    found.add(class_node.module)
    return found


def _strict_files() -> list[pathlib.Path]:
    files: list[pathlib.Path] = []
    for rel in STRICT_DIRS:
        files.extend(sorted((BACKEND / rel).rglob("*.py")))
    assert files, "no strict-protected files discovered"
    return files


def _module_scope_files() -> list[pathlib.Path]:
    files: list[pathlib.Path] = []
    for rel in MODULE_SCOPE_DIRS:
        files.extend(sorted((BACKEND / rel).rglob("*.py")))
    assert files, "no module-scope-protected files discovered"
    return files


@pytest.mark.parametrize("path", _strict_files(), ids=lambda p: p.name)
def test_no_llm_imports_in_services(path: pathlib.Path):
    """Services, assurance and policy must never import an LLM layer at any scope."""
    imported = _imported_modules(path)

    banned = {m for m in imported if m.split(".")[0] in FORBIDDEN_MODULES}
    assert not banned, f"{path.relative_to(BACKEND)} imports an LLM client: {banned}"

    internal = {m for m in imported if m.startswith(FORBIDDEN_PREFIXES)}
    assert not internal, f"{path.relative_to(BACKEND)} imports the LLM layer: {internal}"


@pytest.mark.parametrize("path", _module_scope_files(), ids=lambda p: p.name)
def test_no_llm_at_module_scope_in_orchestrator(path: pathlib.Path):
    """The orchestrator may call agents (Phase 3) through deferred imports only.

    A top-level import would mean the engine cannot start without the SDK installed, which
    breaks `LLM_MODE=off` — the design rule that the system works without any model.
    """
    imported = _module_scope_imports(path)

    banned = {m for m in imported if m.split(".")[0] in FORBIDDEN_MODULES}
    assert not banned, f"{path.relative_to(BACKEND)} has a top-level LLM SDK import: {banned}"

    internal = {m for m in imported if m.startswith(FORBIDDEN_PREFIXES)}
    assert not internal, f"{path.relative_to(BACKEND)} has a top-level app.llm import: {internal}"

    agent_imports = {
        m for m in imported if m.startswith("app.agents") and m != "app.agents.contract"
    }
    assert not agent_imports, (
        f"{path.relative_to(BACKEND)} has a top-level app.agents import: {agent_imports}. "
        "Use a deferred import inside the method that calls the agent. "
        "app.agents.contract (type definitions) is exempt."
    )


def test_agents_are_the_only_reasoning_layer():
    """Sanity check that the protected set does not accidentally include app/agents."""
    all_protected = _strict_files() + _module_scope_files()
    assert not any("agents" in str(p) for p in all_protected)
