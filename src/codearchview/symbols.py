"""Top-level symbol extraction.

For each file we emit one record per top-level named declaration plus
one record for the default export (if any).  Each record carries:

    * `name`        — the symbol identifier
    * `kind`        — component | hook | function | const | class | interface | type | enum | default-export
    * `exported`    — True iff the declaration is exported (named or default)
    * `byte_range`  — (start_byte, end_byte) of the *body* we should scan
                       for references
    * `name_point`  — (line, column) of the name identifier (for LSP queries)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterator

from tree_sitter import Node

from .parser import ParsedFile
from .queries import jsx_body_query, top_level_query


PASCAL_CASE = re.compile(r"^[A-Z][A-Za-z0-9]*$")
HOOK_NAME = re.compile(r"^use[A-Z][A-Za-z0-9]*$")


@dataclass(frozen=True)
class Symbol:
    name: str
    kind: str
    exported: bool
    start_byte: int
    end_byte: int
    start_line: int  # 1-indexed for human display
    end_line: int
    name_line: int  # 0-indexed for LSP
    name_column: int  # 0-indexed for LSP


def _lang_name(pf: ParsedFile) -> str:
    return "tsx" if pf.path.suffix == ".tsx" else "ts"


def _has_jsx(pf: ParsedFile, node: Node) -> bool:
    q = jsx_body_query(_lang_name(pf))
    # Restrict query to the subtree by checking nodes' byte ranges.
    captures = q.captures(node)
    # tree-sitter 0.22+ returns dict[capture_name, list[Node]]
    if isinstance(captures, dict):
        return any(captures.values())
    return bool(captures)


def _classify(name: str, kind_hint: str, decl_node: Node, pf: ParsedFile) -> str:
    """Refine kind based on naming conventions and body content."""
    if kind_hint in ("interface", "type", "enum", "class"):
        return kind_hint
    if HOOK_NAME.match(name):
        return "hook"
    if kind_hint in ("function", "const") and PASCAL_CASE.match(name):
        if _has_jsx(pf, decl_node):
            return "component"
    return kind_hint


def _decl_kind(decl: Node) -> str:
    t = decl.type
    if t == "function_declaration":
        return "function"
    if t == "class_declaration":
        return "class"
    if t == "interface_declaration":
        return "interface"
    if t == "type_alias_declaration":
        return "type"
    if t == "enum_declaration":
        return "enum"
    if t == "lexical_declaration":
        return "const"
    if t == "variable_declarator":
        return "const"
    return "const"


def _is_exported(decl: Node) -> bool:
    p = decl.parent
    while p is not None:
        if p.type == "export_statement":
            return True
        p = p.parent
    return False


def _node_point(node: Node) -> tuple[int, int]:
    p = node.start_point
    return (p[0], p[1])


def extract_symbols(pf: ParsedFile) -> list[Symbol]:
    """Return one Symbol per top-level named declaration, deduped by (name, start_byte)."""
    q = top_level_query(_lang_name(pf))
    matches = q.matches(pf.tree.root_node)

    seen: dict[tuple[str, int], Symbol] = {}
    for _, captures in matches:
        name_nodes = captures.get("name") or []
        decl_nodes = captures.get("decl") or []
        if not name_nodes or not decl_nodes:
            continue
        name_node = name_nodes[0]
        decl_node = decl_nodes[0]
        name = pf.text(name_node)

        # If decl is the variable_declarator, walk up to its lexical_declaration
        # for the *export* check; classification uses the declarator subtree.
        kind_hint = _decl_kind(decl_node)
        kind = _classify(name, kind_hint, decl_node, pf)
        exported = _is_exported(decl_node)

        nl, nc = _node_point(name_node)
        sym = Symbol(
            name=name,
            kind=kind,
            exported=exported,
            start_byte=decl_node.start_byte,
            end_byte=decl_node.end_byte,
            start_line=decl_node.start_point[0] + 1,
            end_line=decl_node.end_point[0] + 1,
            name_line=nl,
            name_column=nc,
        )
        key = (name, decl_node.start_byte)
        # Prefer the exported variant if we capture both (export_statement match
        # and the underlying declaration match can both fire).
        existing = seen.get(key)
        if existing is None or (exported and not existing.exported):
            seen[key] = sym

    return sorted(seen.values(), key=lambda s: s.start_byte)


def iter_default_export(pf: ParsedFile) -> Iterator[Symbol]:
    """Yield a synthetic 'default-export' symbol if the file has `export default X`.

    The body covers the entire file so reference-walking captures whatever
    the default-exported expression contains.
    """
    root = pf.tree.root_node
    for child in root.children:
        if child.type != "export_statement":
            continue
        # `export default <expr>` shape: an export_statement with a 'default' keyword
        # and a value child.
        text = pf.text(child)
        if not text.lstrip().startswith("export default"):
            continue
        yield Symbol(
            name="default",
            kind="default-export",
            exported=True,
            start_byte=child.start_byte,
            end_byte=child.end_byte,
            start_line=child.start_point[0] + 1,
            end_line=child.end_point[0] + 1,
            name_line=child.start_point[0],
            name_column=child.start_point[1],
        )
        return
