"""The layer rules: each package may import only from the layers below it.

    interfaces   mcp_server, api            may use pipeline, context, guardrails, core (api also the agent)
    pipeline                                may use rag, guardrails, context, core
    rag, guardrails, context                may use core (and themselves)
    core                                    uses nothing else in the package
    agent                                   talks to the server over MCP only; imports nothing but core

The rules are checked on the real imports, so a shortcut (say, ``guardrails`` importing ``pipeline``) fails this test
instead of slowly turning the layers back into a tangle.
"""

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src" / "identity_mcp"

ALLOWED = {
    "core": {"core"},
    "data": {"core", "data"},
    "rag": {"core", "rag"},
    "guardrails": {"core", "guardrails"},
    "context": {"core", "context"},
    "pipeline": {"core", "rag", "guardrails", "context", "pipeline"},
    "mcp_server": {"core", "context", "guardrails", "pipeline"},
    "api": {"core", "context", "pipeline", "mcp_server", "agent", "api"},
    "agent": {"core"},
}


def layer_of(parts: tuple[str, ...]) -> str:
    """The layer a module path (relative to the package, e.g. ``('rag', 'bank')``) belongs to."""
    return parts[0]


def imports_of(path: Path) -> set[str]:
    """The layers a file imports from, resolving relative imports against the file's own location."""
    package = path.relative_to(SRC).with_suffix("").parts[:-1]  # the file's package, relative to identity_mcp
    layers = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.ImportFrom) and node.level:
            base = package[: len(package) - (node.level - 1)] if node.level > 1 else package
            target = base + tuple(node.module.split(".")) if node.module else base
            if not target:  # `from . import x` / `from .. import x` at the package root: x is the module
                target = tuple(alias.name for alias in node.names[:1])
            layers.add(layer_of(target))
        elif isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0] == "identity_mcp":
            rest = tuple(node.module.split(".")[1:]) or tuple(alias.name for alias in node.names[:1])
            layers.add(layer_of(rest))
    return layers


def modules():
    return sorted(p for p in SRC.rglob("*.py") if "__pycache__" not in p.parts)


def own_layer(path: Path) -> str:
    parts = path.relative_to(SRC).with_suffix("").parts
    return parts[0] if len(parts) > 1 or parts[0] != "__init__" else "root"


@pytest.mark.parametrize("path", modules(), ids=lambda p: str(p.relative_to(SRC)))
def test_imports_only_point_down(path):
    layer = own_layer(path)
    if layer == "root":
        return
    assert layer in ALLOWED, f"{path.relative_to(SRC)} is in an unclassified layer {layer!r}: add it to ALLOWED"
    forbidden = imports_of(path) - ALLOWED[layer] - {layer}
    assert not forbidden, f"{path.relative_to(SRC)} ({layer}) imports from {sorted(forbidden)}, which it may not"


def test_every_top_level_package_is_classified():
    found = {own_layer(p) for p in modules()} - {"root"}
    assert found <= set(ALLOWED), f"unclassified: {sorted(found - set(ALLOWED))}"
