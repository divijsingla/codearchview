"""S-expression tree-sitter queries for TS/TSX.

Queries are compiled lazily against either the TS or TSX language.
The same query source works for both grammars (tree-sitter-typescript
ships parallel grammars that agree on the relevant node types).
"""

from __future__ import annotations

from functools import lru_cache

from tree_sitter import Language, Node, Query

try:  # tree-sitter >= 0.25 moved match/capture iteration onto QueryCursor
    from tree_sitter import QueryCursor  # type: ignore[attr-defined]
except ImportError:  # pragma: no cover - older tree-sitter (<0.25)
    QueryCursor = None  # type: ignore[assignment]

from .parser import TS_LANGUAGE, TSX_LANGUAGE


# Top-level import statements:
#   import X from "foo"
#   import { a, b as c } from "foo"
#   import * as ns from "foo"
#   import "foo"
#   import type { T } from "foo"
IMPORTS_QUERY = """
(import_statement
  (import_clause)? @clause
  source: (string (string_fragment) @source)) @import
"""

# Top-level declarations we care about.  Captures one named declaration
# per match plus the surrounding statement so we can locate exports.
TOP_LEVEL_QUERY = """
(program
  (export_statement
    declaration: (lexical_declaration
      (variable_declarator name: (identifier) @name) @decl)) @export)

(program
  (export_statement
    declaration: (function_declaration name: (identifier) @name) @decl) @export)

(program
  (export_statement
    declaration: (class_declaration name: (type_identifier) @name) @decl) @export)

(program
  (export_statement
    declaration: (interface_declaration name: (type_identifier) @name) @decl) @export)

(program
  (export_statement
    declaration: (type_alias_declaration name: (type_identifier) @name) @decl) @export)

(program
  (export_statement
    declaration: (enum_declaration name: (identifier) @name) @decl) @export)

(program
  (lexical_declaration
    (variable_declarator name: (identifier) @name) @decl))

(program
  (function_declaration name: (identifier) @name) @decl)

(program
  (class_declaration name: (type_identifier) @name) @decl)

(program
  (interface_declaration name: (type_identifier) @name) @decl)

(program
  (type_alias_declaration name: (type_identifier) @name) @decl)
"""

# Identifier references inside any subtree.  We classify by parent node
# at extraction time (call_expression -> "calls", jsx_opening_element ->
# "renders", type_identifier -> "uses-type").  The non-JSX patterns
# work in both `.ts` and `.tsx`; the JSX patterns are appended only for
# the TSX grammar because plain TS doesn't define those node types.
REFERENCES_QUERY_COMMON = """
(call_expression function: (identifier) @call_ref)
(call_expression function: (member_expression object: (identifier) @call_ref))
(type_identifier) @type_ref
(new_expression constructor: (identifier) @call_ref)
"""

REFERENCES_QUERY_JSX = """
(jsx_opening_element name: (identifier) @jsx_ref)
(jsx_self_closing_element name: (identifier) @jsx_ref)
(jsx_opening_element name: (member_expression object: (identifier) @jsx_ref))
(jsx_self_closing_element name: (member_expression object: (identifier) @jsx_ref))
"""

# Detect whether a function/arrow body returns JSX (used to classify
# PascalCase symbols as React components).  `jsx_fragment` is not a
# named node in tree-sitter-typescript; `<>` fragments contain at least
# one `jsx_element` or `jsx_self_closing_element` child in practice.
JSX_BODY_QUERY = """
(jsx_element) @jsx
(jsx_self_closing_element) @jsx
"""


@lru_cache(maxsize=32)
def _compile(language_name: str, source: str) -> Query:
    lang: Language = TSX_LANGUAGE if language_name == "tsx" else TS_LANGUAGE
    return Query(lang, source)


def imports_query(language_name: str) -> Query:
    return _compile(language_name, IMPORTS_QUERY)


def top_level_query(language_name: str) -> Query:
    return _compile(language_name, TOP_LEVEL_QUERY)


def references_query(language_name: str) -> Query:
    if language_name == "tsx":
        return _compile("tsx", REFERENCES_QUERY_COMMON + REFERENCES_QUERY_JSX)
    return _compile("ts", REFERENCES_QUERY_COMMON)


def jsx_body_query(language_name: str) -> Query:
    return _compile(language_name, JSX_BODY_QUERY)


# ---------------------------------------------------------------------------
# Compatibility helpers: run a query against a node and return matches /
# captures in the same shape regardless of whether the installed
# `tree-sitter` exposes the methods on `Query` (<0.25) or on `QueryCursor`
# (>=0.25).
# ---------------------------------------------------------------------------


def run_matches(query: Query, node: Node):
    """Return a list of (pattern_index, dict[capture_name, list[Node]])."""
    if QueryCursor is not None:
        cursor = QueryCursor(query)
        return cursor.matches(node)
    return query.matches(node)  # type: ignore[attr-defined]


def run_captures(query: Query, node: Node):
    """Return a dict[capture_name, list[Node]] for the given subtree."""
    if QueryCursor is not None:
        cursor = QueryCursor(query)
        return cursor.captures(node)
    return query.captures(node)  # type: ignore[attr-defined]
