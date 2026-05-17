"""Intra-symbol identifier reference extraction.

For a given symbol (byte range within a file), enumerate every
identifier reference that we want to resolve via the LSP, along with
the *kind* of edge it should produce in the final graph.

We deduplicate by (name, edge_kind) within a symbol so that a component
that renders `<Card>` ten times produces a single `renders` edge.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator

from tree_sitter import Node

from .parser import ParsedFile
from .queries import references_query
from .symbols import Symbol


@dataclass(frozen=True)
class Reference:
    name: str
    kind: str  # "calls" | "renders" | "uses-type"
    line: int  # 0-indexed for LSP
    column: int  # 0-indexed for LSP


_CAPTURE_KIND = {
    "call_ref": "calls",
    "jsx_ref": "renders",
    "type_ref": "uses-type",
}


def _walk_in_range(node: Node, start: int, end: int) -> Iterator[Node]:
    """Yield nodes whose byte range lies inside [start, end)."""
    if node.end_byte <= start or node.start_byte >= end:
        return
    yield node
    for child in node.children:
        yield from _walk_in_range(child, start, end)


def _lang_name(pf: ParsedFile) -> str:
    return "tsx" if pf.path.suffix == ".tsx" else "ts"


def extract_references(pf: ParsedFile, sym: Symbol) -> list[Reference]:
    q = references_query(_lang_name(pf))
    captures = q.captures(pf.tree.root_node)
    # tree-sitter 0.22+: dict[capture_name, list[Node]]
    if not isinstance(captures, dict):
        # Fallback for older API: list[(node, capture_name)]
        as_dict: dict[str, list[Node]] = {}
        for node, name in captures:
            as_dict.setdefault(name, []).append(node)
        captures = as_dict

    seen: dict[tuple[str, str], Reference] = {}
    for cap_name, kind in _CAPTURE_KIND.items():
        for node in captures.get(cap_name, []):
            if node.start_byte < sym.start_byte or node.end_byte > sym.end_byte:
                continue
            name = pf.text(node)
            # Skip the symbol's own name (e.g. `function Foo() { Foo(); }`)
            if name == sym.name and kind == "calls":
                # Still record (self-recursion) but as a distinct edge kind?
                # Keep it — produces a self-loop in the graph, which is fine.
                pass
            # Skip bare JSX intrinsics ("div", "span", ...) — they start lowercase.
            if kind == "renders" and not name[:1].isupper():
                continue
            key = (name, kind)
            if key in seen:
                continue
            seen[key] = Reference(
                name=name,
                kind=kind,
                line=node.start_point[0],
                column=node.start_point[1],
            )
    return list(seen.values())
